from __future__ import annotations

import asyncio
import json
import logging
from functools import lru_cache
from typing import Any, Dict, Optional

from .clients import (
    GrammarExplanationClient,
    InvalidSemanticReviewResponse,
    OpenAIChatCompletionLLMClient,
    RepairServiceClient,
    SemanticReviewClient,
)
from .comparison import compare_answers
from .config import Settings, get_settings
from .grammar import Antlr4CypherParserAdapter, GrammarChecker, build_grammar_metric
from .models import (
    EvaluationStatusResponse,
    ExecutionResult,
    GenerationEvidence,
    GenerationRunFailureReport,
    GeneratedCypherSubmissionRequest,
    GrammarMetric,
    ImprovementAssessment,
    ImprovementMetricChange,
    ImprovementMetrics,
    IssueTicket,
    QAGoldenRequest,
    QAGoldenResponse,
    SemanticReviewArtifact,
    SemanticCheck,
    SubmissionReceipt,
)
from .repository import TestingRepository
from .summary import build_evaluation_summary, build_execution_accuracy, build_secondary_signals, is_order_sensitive
from .tugraph import TuGraphClient

logger = logging.getLogger("testing_agent")


class TestingAgentService:
    __test__ = False

    def __init__(
        self,
        *,
        repository: TestingRepository,
        repair_client: RepairServiceClient,
        tugraph_client: TuGraphClient,
        grammar_checker: GrammarChecker,
        grammar_explainer: GrammarExplanationClient,
        semantic_reviewer: SemanticReviewClient,
        settings: Settings,
    ) -> None:
        self.repository = repository
        self.repair_client = repair_client
        self.tugraph_client = tugraph_client
        self.grammar_checker = grammar_checker
        self.grammar_explainer = grammar_explainer
        self.semantic_reviewer = semantic_reviewer
        self.settings = settings
        self._background_tasks: dict[tuple[str, int], asyncio.Task[Any]] = {}

    async def ingest_golden(self, request: QAGoldenRequest) -> QAGoldenResponse:
        self.repository.save_golden(request)
        latest_submission = self.repository.get_submission(request.id)
        if latest_submission is None:
            return QAGoldenResponse(id=request.id, status="received_golden_only")

        if latest_submission["state"] in {"passed", "issue_ticket_created", "repair_submission_failed"}:
            return QAGoldenResponse(
                id=request.id,
                status=latest_submission["state"],
                verdict=latest_submission.get("evaluation", {}).get("verdict"),
                issue_ticket_id=latest_submission.get("issue_ticket_id"),
            )

        self.repository.update_submission_state(request.id, int(latest_submission["attempt_no"]), "ready_to_evaluate")
        evaluation = await self._evaluate_attempt(request.id, int(latest_submission["attempt_no"]))
        updated = self.repository.get_submission_attempt(request.id, int(latest_submission["attempt_no"])) or {}
        return QAGoldenResponse(
            id=request.id,
            status=updated.get("state", "ready_to_evaluate"),
            verdict=None if evaluation is None else evaluation.verdict,
            issue_ticket_id=updated.get("issue_ticket_id"),
        )

    async def ingest_submission(self, request: GeneratedCypherSubmissionRequest) -> SubmissionReceipt:
        state = "ready_to_evaluate" if self.repository.get_golden(request.id) else "received_submission_only"
        saved = self.repository.save_submission(request, state=state)
        if state == "ready_to_evaluate" and saved.created:
            self._schedule_attempt_evaluation(request.id, saved.attempt_no)
        return SubmissionReceipt(accepted=True)

    async def ingest_generation_failure(self, report: GenerationRunFailureReport) -> SubmissionReceipt:
        if report.generation_status == "service_failed":
            self.repository.save_generation_failure_report(report)
            return SubmissionReceipt(accepted=True)

        state = "ready_to_evaluate" if self.repository.get_golden(report.id) else "received_submission_only"
        saved = self.repository.save_generation_failure_submission(report, state=state)
        if state == "ready_to_evaluate" and saved.created:
            self._schedule_attempt_evaluation(report.id, saved.attempt_no)
        return SubmissionReceipt(accepted=True)

    def _schedule_attempt_evaluation(self, qa_id: str, attempt_no: int) -> None:
        key = (qa_id, attempt_no)
        existing = self._background_tasks.get(key)
        if existing is not None and not existing.done():
            return
        task = asyncio.create_task(self._evaluate_attempt(qa_id, attempt_no))
        self._background_tasks[key] = task
        task.add_done_callback(
            lambda completed: self._log_background_evaluation_failure(
                qa_id=qa_id,
                attempt_no=attempt_no,
                task=completed,
            )
        )

    def _log_background_evaluation_failure(self, *, qa_id: str, attempt_no: int, task: asyncio.Task[Any]) -> None:
        self._background_tasks.pop((qa_id, attempt_no), None)
        try:
            task.result()
        except Exception:
            logger.exception(
                "submission_evaluation_failed",
                extra={"qa_id": qa_id, "attempt_no": attempt_no},
            )

    async def resume_pending_evaluations(self) -> int:
        pending = self.repository.list_submission_attempts_by_state("ready_to_evaluate")
        resumed = 0
        for submission in pending:
            qa_id = str(submission["id"])
            attempt_no = int(submission["attempt_no"])
            self._schedule_attempt_evaluation(qa_id, attempt_no)
            resumed += 1
        if resumed:
            logger.info("resumed_pending_evaluations count=%s", resumed)
        return resumed

    async def _evaluate_attempt(self, qa_id: str, attempt_no: int):
        golden = self.repository.get_golden(qa_id)
        submission = self.repository.get_submission_attempt(qa_id, attempt_no)
        if golden is None or submission is None:
            raise RuntimeError(f"Expected golden and submission for id={qa_id} attempt_no={attempt_no}")

        if submission.get("generation_status") == "generation_failed":
            parser_error = None
            if submission["generated_cypher"]:
                _, parser_error = self.grammar_checker.check(submission["generated_cypher"])
            grammar = GrammarMetric(
                score=0,
                parser_error=parser_error,
                message=_generation_failure_message(submission),
            )
        else:
            grammar = await build_grammar_metric(
                generated_cypher=submission["generated_cypher"],
                checker=self.grammar_checker,
                explainer=self.grammar_explainer,
            )

        execution = None
        if grammar.score == 1:
            try:
                execution = await self.tugraph_client.execute(submission["generated_cypher"])
            except Exception as exc:
                execution = ExecutionResult(
                    success=False,
                    rows=None,
                    row_count=None,
                    error_message=f"TuGraph execution failed: {exc}",
                    elapsed_ms=0,
                )
            self.repository.save_execution(qa_id, attempt_no, execution)

        if execution is not None and not execution.success and grammar.score == 1:
            self.repository.update_submission_state(qa_id, attempt_no, "tugraph_execution_failed")
            return None

        if grammar.score == 0:
            strict_check = compare_answers(golden_answer=[], actual_answer=[], order_sensitive=False)
            strict_check.status = "not_run"
            strict_check.message = None
            semantic_check = SemanticCheck(status="not_run", message=None, raw_output=None)
        elif execution is None or not execution.success:
            strict_check = compare_answers(golden_answer=[], actual_answer=[], order_sensitive=False)
            strict_check.status = "not_run"
            strict_check.message = None
            semantic_check = SemanticCheck(status="not_run", message=None, raw_output=None)
        else:
            strict_check = compare_answers(
                golden_answer=golden["answer"],
                actual_answer=execution.rows or [],
                order_sensitive=is_order_sensitive(submission["question"], golden["cypher"]),
            )
            if strict_check.status == "pass":
                semantic_check = SemanticCheck(status="not_run", message=None, raw_output=None)
            else:
                try:
                    raw_output = await self.semantic_reviewer.review(
                        question=submission["question"],
                        gold_cypher=golden["cypher"],
                        gold_answer=golden["answer"],
                        generated_cypher=submission["generated_cypher"],
                        actual_answer=execution.rows or [],
                        strict_check_message=strict_check.message,
                        strict_diff=None if strict_check.evidence is None else strict_check.evidence.diff.model_dump(mode="json"),
                    )
                except InvalidSemanticReviewResponse as exc:
                    self.repository.save_semantic_review_artifact(
                        qa_id,
                        attempt_no,
                        SemanticReviewArtifact(
                            status="invalid",
                            raw_text=exc.raw_text,
                            payload=exc.payload,
                            request_id=exc.request_id,
                            model=exc.model,
                            prompt_snapshot=exc.prompt_snapshot,
                            message=str(exc),
                        ),
                    )
                    self.repository.update_submission_state(qa_id, attempt_no, "semantic_review_invalid")
                    return None
                payload = {key: value for key, value in raw_output.items() if not str(key).startswith("_")}
                self.repository.save_semantic_review_artifact(
                    qa_id,
                    attempt_no,
                    SemanticReviewArtifact(
                        status="accepted",
                        raw_text=str(raw_output.get("_raw_text") or json.dumps(payload, ensure_ascii=False)),
                        payload=payload,
                        request_id=raw_output.get("_request_id"),
                        model=raw_output.get("_model"),
                        prompt_snapshot=raw_output.get("_prompt_snapshot"),
                        normalized_judgement=raw_output["judgement"],
                        reasoning=str(raw_output.get("reasoning", "")).strip(),
                    ),
                )
                semantic_check = SemanticCheck(
                    status=raw_output["judgement"],
                    message=str(raw_output.get("reasoning", "")),
                    raw_output=payload,
                )

        execution_accuracy = build_execution_accuracy(
            grammar_score=grammar.score,
            strict_check_status=strict_check.status,
            semantic_check_status=semantic_check.status,
            strict_check=strict_check,
            semantic_check=semantic_check,
        )
        secondary_signals = build_secondary_signals(
            generated_cypher=submission["generated_cypher"],
            gold_cypher=golden["cypher"],
        )
        evaluation = build_evaluation_summary(
            grammar=grammar,
            execution_accuracy=execution_accuracy,
            secondary_signals=secondary_signals,
        )
        self.repository.save_evaluation(qa_id, attempt_no, evaluation)

        if evaluation.verdict == "pass":
            self.repository.update_submission_state(qa_id, attempt_no, "passed")
        else:
            ticket = IssueTicket(
                ticket_id=f"ticket-{qa_id}-attempt-{attempt_no}",
                id=qa_id,
                difficulty=golden["difficulty"],
                question=submission["question"],
                expected={"cypher": golden["cypher"], "answer": golden["answer"]},
                actual={
                    "generated_cypher": submission["generated_cypher"],
                    "execution": None if execution is None else execution.model_dump(mode="json"),
                },
                evaluation=evaluation,
                generation_evidence=GenerationEvidence(
                    generation_run_id=submission["generation_run_id"],
                    attempt_no=attempt_no,
                    input_prompt_snapshot=submission["input_prompt_snapshot"],
                    last_llm_raw_output=submission.get("last_llm_raw_output", ""),
                    generation_status=submission.get("generation_status", "generated"),
                    failure_reason=submission.get("failure_reason"),
                    generation_retry_count=int(submission.get("generation_retry_count", 0)),
                    generation_failure_reasons=submission.get("generation_failure_reasons", []),
                ),
            )
            self.repository.save_issue_ticket(ticket, attempt_no=attempt_no)
            self.repository.update_submission_state(qa_id, attempt_no, "repair_pending")
            try:
                response = await self.repair_client.submit_issue_ticket(ticket)
            except Exception:
                self.repository.update_submission_state(qa_id, attempt_no, "repair_submission_failed")
                raise
            self.repository.save_repair_response(qa_id, attempt_no, response)
            self.repository.update_submission_state(qa_id, attempt_no, "issue_ticket_created")

        previous = self._previous_evaluated_attempt(qa_id, attempt_no)
        if previous and previous.get("evaluation"):
            assessment = self._build_improvement_assessment(
                qa_id=qa_id,
                previous_attempt_no=int(previous["attempt_no"]),
                current_attempt_no=attempt_no,
                previous_evaluation=previous["evaluation"],
                current_evaluation=evaluation.model_dump(mode="json"),
            )
            self.repository.save_improvement_assessment(qa_id, attempt_no, assessment)

        return evaluation

    def _previous_evaluated_attempt(self, qa_id: str, current_attempt_no: int) -> dict[str, Any] | None:
        for attempt_no in range(current_attempt_no - 1, 0, -1):
            previous = self.repository.get_submission_attempt(qa_id, attempt_no)
            if previous and previous.get("evaluation"):
                return previous
        return None

    def _build_improvement_assessment(
        self,
        *,
        qa_id: str,
        previous_attempt_no: int,
        current_attempt_no: int,
        previous_evaluation: Dict[str, Any],
        current_evaluation: Dict[str, Any],
    ) -> ImprovementAssessment:
        def compare(previous: Any, current: Any) -> ImprovementMetricChange:
            if isinstance(previous, (int, float)) and isinstance(current, (int, float)):
                if current > previous:
                    status = "improved"
                elif current < previous:
                    status = "regressed"
                else:
                    status = "unchanged"
            else:
                if current == previous:
                    status = "unchanged"
                elif previous == 0 and current == 1:
                    status = "improved"
                else:
                    status = "regressed"
            return ImprovementMetricChange(previous=previous, current=current, status=status)

        metrics = ImprovementMetrics(
            grammar_score=compare(
                previous_evaluation["primary_metrics"]["grammar"]["score"],
                current_evaluation["primary_metrics"]["grammar"]["score"],
            ),
            execution_accuracy_score=compare(
                previous_evaluation["primary_metrics"]["execution_accuracy"]["score"],
                current_evaluation["primary_metrics"]["execution_accuracy"]["score"],
            ),
            gleu_score=compare(
                previous_evaluation["secondary_signals"]["gleu"]["score"],
                current_evaluation["secondary_signals"]["gleu"]["score"],
            ),
            jaro_winkler_similarity_score=compare(
                previous_evaluation["secondary_signals"]["jaro_winkler_similarity"]["score"],
                current_evaluation["secondary_signals"]["jaro_winkler_similarity"]["score"],
            ),
        )
        highlights = [
            f"grammar: {metrics.grammar_score.status}",
            f"execution_accuracy: {metrics.execution_accuracy_score.status}",
        ]
        return ImprovementAssessment(
            qa_id=qa_id,
            current_attempt_no=current_attempt_no,
            previous_attempt_no=previous_attempt_no,
            summary_zh="本轮结果与上一轮相比已完成改进评估。",
            metrics=metrics,
            highlights=highlights,
            evidence=[],
        )

    def get_evaluation_status(self, qa_id: str) -> EvaluationStatusResponse:
        submission = self.repository.get_submission(qa_id)
        if submission and submission.get("state") == "ready_to_evaluate":
            self._schedule_attempt_evaluation(str(submission["id"]), int(submission["attempt_no"]))
        issue_ticket = None
        if submission and submission.get("issue_ticket_id"):
            ticket = self.repository.get_issue_ticket(str(submission["issue_ticket_id"]))
            issue_ticket = None if ticket is None else ticket.model_dump(mode="json")
        return EvaluationStatusResponse(
            id=qa_id,
            golden=self.repository.get_golden(qa_id),
            submission=submission,
            attempts=self.repository.list_submission_attempts(qa_id),
            generation_failures=self.repository.list_generation_failure_reports(qa_id),
            issue_ticket=issue_ticket,
        )

    def get_issue_ticket(self, ticket_id: str) -> Optional[IssueTicket]:
        return self.repository.get_issue_ticket(ticket_id)

    def get_service_status(self) -> Dict[str, object]:
        return {
            "status": "ok",
            "storage": self.settings.data_dir,
            "repair_service_url": self.settings.repair_service_url,
            "llm_model": self.settings.llm_model,
            "llm_enabled": self.settings.llm_enabled,
        }


@lru_cache(maxsize=1)
def get_testing_service() -> TestingAgentService:
    settings = get_settings()
    llm = OpenAIChatCompletionLLMClient(
        base_url=str(settings.llm_base_url),
        api_key=str(settings.llm_api_key),
        model=str(settings.llm_model),
        timeout_seconds=settings.request_timeout_seconds,
        temperature=settings.llm_temperature,
    )
    return TestingAgentService(
        repository=TestingRepository(settings.data_dir),
        repair_client=RepairServiceClient(settings.repair_service_url, settings.request_timeout_seconds),
        tugraph_client=TuGraphClient(
            base_url=settings.tugraph_url,
            username=settings.tugraph_username,
            password=settings.tugraph_password,
            graph=settings.tugraph_graph,
            mock_mode=settings.mock_tugraph,
        ),
        grammar_checker=GrammarChecker(Antlr4CypherParserAdapter()),
        grammar_explainer=GrammarExplanationClient(llm),
        semantic_reviewer=SemanticReviewClient(llm),
        settings=settings,
    )


def _generation_failure_message(submission: Dict[str, Any]) -> str:
    failure_reason = submission.get("failure_reason") or "generation_failed"
    last_reason = None
    reasons = submission.get("generation_failure_reasons") or []
    if reasons:
        last_reason = reasons[-1]
    if last_reason and last_reason != failure_reason:
        return f"Generation failed before testing-agent evaluation: {failure_reason}; last generation failure: {last_reason}."
    return f"Generation failed before testing-agent evaluation: {failure_reason}."
