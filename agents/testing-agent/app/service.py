from __future__ import annotations

import asyncio
import json
import logging
from functools import lru_cache
from typing import Any, Dict, Optional

from .evaluation import evaluate_submission
from .models import (
    ActualAnswer,
    ImprovementAssessment,
    ImprovementDimensions,
    EvaluationSubmissionRequest,
    EvaluationSubmissionResponse,
    ExpectedAnswer,
    IssueTicket,
    QAGoldenRequest,
    QAGoldenResponse,
    QueryQuestionResponse,
)
from .tugraph import TuGraphClient

from .clients import LLMEvaluationClient, QueryGeneratorConsoleClient, RepairServiceClient, ServiceHealthClient
from .config import Settings, get_settings
from .repository import TestingRepository

logger = logging.getLogger("testing_service")

DEFAULT_CGS_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_KNOWLEDGE_OPS_BASE_URL = "http://127.0.0.1:8010"
DEFAULT_QA_GENERATOR_BASE_URL = "http://127.0.0.1:8020"


class EvaluationService:
    def __init__(
        self,
        repository: TestingRepository,
        repair_client: RepairServiceClient,
        tugraph_client: TuGraphClient,
        llm_client: Optional[LLMEvaluationClient] = None,
        console_query_client: Optional[QueryGeneratorConsoleClient] = None,
        health_client: Optional[ServiceHealthClient] = None,
        settings: Optional[Settings] = None,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.repair_client = repair_client
        self.tugraph_client = tugraph_client
        self.llm_client = llm_client
        effective_settings = settings or get_settings()
        self.console_query_client = console_query_client or QueryGeneratorConsoleClient(
            base_url=DEFAULT_CGS_BASE_URL,
            timeout_seconds=effective_settings.request_timeout_seconds,
        )
        self.health_client = health_client or ServiceHealthClient()

    async def ingest_golden(self, request: QAGoldenRequest) -> QAGoldenResponse:
        self.repository.save_golden(request)
        submission = self.repository.get_submission(request.id)
        if submission is None:
            return QAGoldenResponse(id=request.id, status="received_golden_only")
        return await self._evaluate_ready_pair(request.id)

    async def ingest_submission(self, request: EvaluationSubmissionRequest) -> EvaluationSubmissionResponse:
        golden = self.repository.get_golden(request.id)
        status = "ready_to_evaluate" if golden else "waiting_for_golden"
        self.repository.save_submission(request, status=status)
        if golden is None:
            return EvaluationSubmissionResponse(id=request.id, status="waiting_for_golden")
        return await self._evaluate_ready_pair(request.id)

    async def _evaluate_ready_pair(self, id: str) -> EvaluationSubmissionResponse | QAGoldenResponse:
        golden = self.repository.get_golden(id)
        submission = self.repository.get_submission(id)
        if not golden or not submission:
            raise RuntimeError(f"Expected both golden and submission before evaluating id={id}")
        attempt_no = int(submission.get("attempt_no") or 1)

        execution = await self.tugraph_client.execute(submission["generated_cypher"])
        self.repository.save_submission_execution(id, execution.model_dump_json(), attempt_no=attempt_no)
        expected_answer = json.loads(golden["golden_answer_json"])

        evaluation = evaluate_submission(
            question=submission["question"],
            expected_cypher=golden["golden_cypher"],
            expected_answer=expected_answer,
            actual_cypher=submission["generated_cypher"],
            execution=execution,
            loaded_knowledge_tags=[],
        )

        if evaluation.verdict != "pass" and self.llm_client is not None:
            evaluation = await self._llm_re_evaluate(
                evaluation=evaluation,
                qa_id=id,
                question=submission["question"],
                expected_cypher=golden["golden_cypher"],
                expected_answer=expected_answer,
                actual_cypher=submission["generated_cypher"],
                execution=execution,
            )

        if evaluation.verdict == "pass":
            self.repository.mark_submission_status(id, "passed", attempt_no=attempt_no)
            self._save_improvement_assessment(
                id=id,
                current_submission=self.repository.get_submission_attempt(id, attempt_no) or submission,
            )
            return EvaluationSubmissionResponse(id=id, status="passed", verdict=evaluation.verdict)

        ticket = IssueTicket(
            ticket_id=f"ticket-{id}-attempt-{attempt_no}",
            id=id,
            difficulty=golden["difficulty"],
            question=submission["question"],
            expected=ExpectedAnswer(cypher=golden["golden_cypher"], answer=expected_answer),
            actual=ActualAnswer(
                generated_cypher=submission["generated_cypher"],
                execution=execution,
            ),
            evaluation=evaluation,
            input_prompt_snapshot=submission.get("input_prompt_snapshot", ""),
        )
        self.repository.save_issue_ticket(ticket)
        self.repository.mark_submission_repair_pending(id, ticket.ticket_id, attempt_no=attempt_no)
        try:
            krss_response = await self.repair_client.submit_issue_ticket(ticket)
        except Exception as exc:
            logger.warning("repair_service_submit_failed id=%s attempt=%s error=%s", id, attempt_no, exc)
            self.repository.mark_submission_repair_submission_failed(id, ticket.ticket_id, attempt_no=attempt_no)
            raise
        self.repository.mark_submission_issue_ticket_created(id, ticket.ticket_id, attempt_no=attempt_no)
        self.repository.save_submission_krss_response(id, krss_response.model_dump(mode="json"), attempt_no=attempt_no)
        self._save_improvement_assessment(
            id=id,
            current_submission=self.repository.get_submission_attempt(id, attempt_no) or submission,
        )
        return EvaluationSubmissionResponse(
            id=id,
            status="issue_ticket_created",
            issue_ticket_id=ticket.ticket_id,
            verdict=evaluation.verdict,
        )

    async def _llm_re_evaluate(
        self,
        evaluation,
        *,
        qa_id: str | None = None,
        question: str,
        expected_cypher: str,
        expected_answer,
        actual_cypher: str,
        execution,
    ):
        logger.info("Triggering LLM re-evaluation for question: %s", question)
        llm_result = await self.llm_client.evaluate(
            qa_id=qa_id,
            question=question,
            expected_cypher=expected_cypher,
            expected_answer=expected_answer,
            actual_cypher=actual_cypher,
            actual_result=execution.rows,
            rule_based_verdict=evaluation.verdict,
            rule_based_dimensions=evaluation.dimensions.model_dump(),
        )
        dimensions = evaluation.dimensions
        llm_result_correctness = llm_result.get("result_correctness")
        llm_question_alignment = llm_result.get("question_alignment")
        reasoning = llm_result.get("reasoning", "")
        confidence = llm_result.get("confidence", 0.0)

        if llm_result_correctness == "pass" and dimensions.result_correctness == "fail":
            dimensions.result_correctness = "pass"
            evaluation.evidence.append(f"[LLM override] result_correctness flipped to pass: {reasoning}")
            logger.info("LLM overrode result_correctness to pass (confidence=%.2f)", confidence)

        if llm_question_alignment == "pass" and dimensions.question_alignment == "fail":
            dimensions.question_alignment = "pass"
            evaluation.evidence.append(f"[LLM override] question_alignment flipped to pass: {reasoning}")
            logger.info("LLM overrode question_alignment to pass (confidence=%.2f)", confidence)

        evaluation.dimensions = dimensions
        failures = [
            dimensions.syntax_validity,
            dimensions.schema_alignment,
            dimensions.result_correctness,
            dimensions.question_alignment,
        ].count("fail")

        if failures == 0:
            evaluation.verdict = "pass"
        elif failures == 4 or dimensions.syntax_validity == "fail":
            evaluation.verdict = "fail"
        else:
            evaluation.verdict = "partial_fail"

        return evaluation

    def get_evaluation_status(self, id: str) -> Dict[str, object]:
        golden = self.repository.get_golden(id)
        submission = self.repository.get_submission(id)
        attempts = self.repository.list_submission_attempts(id)
        return {
            "id": id,
            "has_golden": golden is not None,
            "has_submission": submission is not None,
            "golden": golden,
            "submission": submission,
            "attempts": attempts,
        }

    def get_issue_ticket(self, ticket_id: str) -> Optional[IssueTicket]:
        return self.repository.get_issue_ticket(ticket_id)

    def get_service_status(self) -> Dict[str, object]:
        settings = self.settings or get_settings()
        return {
            "storage": settings.data_dir,
            "repair_service_url": settings.repair_service_url,
            "llm_enabled": settings.llm_enabled,
            "llm_model": settings.llm_model,
            "llm_configured": True,
            "mode": "evaluation_router",
            "evaluation_mode": "llm",
        }

    async def get_runtime_architecture(self) -> Dict[str, object]:
        settings = self.settings or get_settings()
        services = [
            await self._build_service_card(
                service_key="cgs",
                label_zh="查询生成服务",
                label_en="Query Generator Service",
                base_url=DEFAULT_CGS_BASE_URL,
                port="8000",
                description_zh="接收自然语言问题并生成 Cypher 与提示快照。",
                description_en="Accepts natural-language questions and generates Cypher plus prompt snapshots.",
                key_endpoints=["POST /api/v1/qa/questions", "GET /api/v1/questions/{id}/prompt"],
            ),
            await self._build_service_card(
                service_key="testing_service",
                label_zh="测试服务",
                label_en="Testing Service",
                base_url=f"http://127.0.0.1:{settings.port}",
                port=str(settings.port),
                description_zh="聚合黄金样本、评测结果、问题票据与联调控制台视图。",
                description_en="Aggregates goldens, evaluation results, issue tickets, and console runtime views.",
                key_endpoints=["POST /api/v1/qa/goldens", "POST /api/v1/evaluations/submissions"],
            ),
            await self._build_service_card(
                service_key="krss",
                label_zh="知识修复建议服务",
                label_en="Knowledge Repair Suggestion Service",
                base_url=settings.repair_service_url,
                port="8002",
                description_zh="接收问题票据并生成知识修复建议。",
                description_en="Receives issue tickets and returns knowledge repair suggestions.",
                key_endpoints=["POST /api/v1/issue-tickets"],
            ),
            await self._build_service_card(
                service_key="knowledge_ops",
                label_zh="知识运营服务",
                label_en="Knowledge Ops Service",
                base_url=DEFAULT_KNOWLEDGE_OPS_BASE_URL,
                port="8010",
                description_zh="接收 KRSS 的正式知识修复请求并落地知识补丁。",
                description_en="Receives formal knowledge repair requests from KRSS and applies knowledge patches.",
                key_endpoints=["POST /api/knowledge/rag/prompt-package", "POST /api/knowledge/repairs/apply"],
            ),
            await self._build_service_card(
                service_key="qa_generator",
                label_zh="问答生成服务",
                label_en="QA Generator Service",
                base_url=DEFAULT_QA_GENERATOR_BASE_URL,
                port="8020",
                description_zh="提供题目与黄金样本生成能力，供联调与回归使用。",
                description_en="Provides question and golden-sample generation for integration and regression workflows.",
                key_endpoints=["POST /jobs", "POST /jobs/quick-run"],
            ),
        ]
        return {
            "title_zh": "系统运行架构",
            "title_en": "System Runtime Architecture",
            "services": services,
            "links": self._runtime_links(),
            "data_objects": self._runtime_data_objects(),
        }

    async def run_console_flow(self, *, id: str, question: str) -> Dict[str, object]:
        settings = self.settings or get_settings()
        self.repository.clear_console_run(id)
        architecture = await self.get_runtime_architecture()
        generation = await self._get_console_generation(id=id, question=question)
        generation_dict = generation.model_dump(mode="json")

        golden_record = self.repository.get_golden(id)
        if golden_record is None:
            raise ValueError(f"No golden answer found for id={id}. Please submit a golden first.")
        try:
            expected_answer = json.loads(golden_record.get("golden_answer_json") or "[]")
        except Exception:
            expected_answer = []
        golden = QAGoldenRequest(
            id=id,
            cypher=str(golden_record.get("golden_cypher") or ""),
            answer=expected_answer,
            difficulty=str(golden_record.get("difficulty") or "L3"),
        )

        submission_request = EvaluationSubmissionRequest(
            id=id,
            question=question,
            generation_run_id=generation.generation_run_id,
            attempt_no=generation.attempt_no,
            generated_cypher=generation.generated_cypher,
            parse_summary=generation.parse_summary,
            guardrail_summary=generation.guardrail_summary,
            raw_output_snapshot=generation.raw_output_snapshot,
            input_prompt_snapshot=generation.input_prompt_snapshot,
        )
        evaluation_response: EvaluationSubmissionResponse | None = None
        if not await self._is_service_online(settings.repair_service_url):
            evaluation_response = await self._evaluate_console_without_krss(
                request=submission_request,
                golden=golden,
            )
        else:
            try:
                evaluation_response = await asyncio.wait_for(
                    self.ingest_submission(submission_request),
                    timeout=min(20.0, float(settings.request_timeout_seconds)),
                )
            except asyncio.TimeoutError:
                logger.warning("console run timed out while waiting for evaluation for id=%s", id)
            except Exception as exc:
                logger.warning("console run encountered downstream failure for id=%s: %s", id, exc)

        submission_snapshot = self.repository.get_submission_snapshot(id)
        issue_snapshot = self.repository.get_issue_snapshot_by_submission_id(id)
        krss_snapshot = self.repository.get_krss_snapshot_by_submission_id(id)
        execution = self._execution_snapshot_from_submission(submission_snapshot)
        evaluation_snapshot = self._evaluation_snapshot(
            response=evaluation_response,
            submission_snapshot=submission_snapshot,
            issue_snapshot=issue_snapshot,
            execution_snapshot=execution,
        )
        evaluation_status = self._evaluation_stage_status(
            response=evaluation_response,
            submission_snapshot=submission_snapshot,
            issue_snapshot=issue_snapshot,
        )
        knowledge_repair_status = self._knowledge_repair_stage_status(
            evaluation_status=evaluation_status,
            submission_snapshot=submission_snapshot,
            issue_snapshot=issue_snapshot,
            krss_snapshot=krss_snapshot,
        )
        knowledge_apply_status = self._knowledge_apply_stage_status(knowledge_repair_status)

        stages = {
            "query_generation": {
                "label_zh": "查询生成",
                "label_en": "Query Generation",
                "status": "failed" if generation.failure_stage else "success",
            },
            "evaluation": {
                "label_zh": "评测执行",
                "label_en": "Evaluation",
                "status": evaluation_status,
            },
            "knowledge_repair": {
                "label_zh": "知识修复",
                "label_en": "Knowledge Repair",
                "status": knowledge_repair_status,
            },
        }
        return {
            "id": id,
            "question": question,
            "title_zh": "系统联调运行",
            "title_en": "System Integration Run",
            "service_cards": architecture["services"],
            "links": architecture["links"],
            "data_objects": architecture["data_objects"],
            "stages": stages,
            "timeline": [
                {
                    "stage_key": "query_generation",
                    "label_zh": "CGS 生成任务",
                    "label_en": "CGS generation task",
                    "status": stages["query_generation"]["status"],
                },
                {
                    "stage_key": "prompt_fetch",
                    "label_zh": "知识运营提示词获取",
                    "label_en": "Knowledge Ops prompt fetch",
                    "status": "success" if generation.input_prompt_snapshot else "failed",
                },
                {
                    "stage_key": "evaluation",
                    "label_zh": "Testing Service 评测",
                    "label_en": "Testing Service evaluation",
                    "status": stages["evaluation"]["status"],
                },
                {
                    "stage_key": "knowledge_repair",
                    "label_zh": "KRSS 问题诊断",
                    "label_en": "KRSS issue diagnosis",
                    "status": stages["knowledge_repair"]["status"],
                },
                {
                    "stage_key": "knowledge_apply",
                    "label_zh": "知识运营修复接收",
                    "label_en": "Knowledge Ops repair apply",
                    "status": knowledge_apply_status,
                },
            ],
            "artifacts": {
                "generation": generation_dict,
                "submission": submission_snapshot,
                "execution": execution,
                "evaluation": evaluation_snapshot,
                "knowledge_repair": {
                    "issue_ticket": issue_snapshot,
                    "krss_response": krss_snapshot,
                    "status": knowledge_repair_status,
                },
            },
        }

    async def _build_service_card(
        self,
        *,
        service_key: str,
        label_zh: str,
        label_en: str,
        base_url: str,
        port: str,
        description_zh: str,
        description_en: str,
        key_endpoints: list[str],
    ) -> Dict[str, Any]:
        status = "unknown"
        try:
            await self.health_client.read_health(base_url=base_url, timeout_seconds=1.0)
            status = "online"
        except Exception:
            status = "offline"
        return {
            "service_key": service_key,
            "label_zh": label_zh,
            "label_en": label_en,
            "base_url": base_url,
            "port": port,
            "status": status,
            "description_zh": description_zh,
            "description_en": description_en,
            "key_endpoints": key_endpoints,
        }

    def _runtime_links(self) -> list[Dict[str, str]]:
        return [
            {
                "source": "cgs",
                "target": "knowledge_ops",
                "label_zh": "获取提示词包",
                "label_en": "Fetch prompt package",
            },
            {
                "source": "cgs",
                "target": "testing_service",
                "label_zh": "提交评测结果",
                "label_en": "Submit evaluation payload",
            },
            {
                "source": "testing_service",
                "target": "krss",
                "label_zh": "发送问题票据",
                "label_en": "Send issue ticket",
            },
            {
                "source": "krss",
                "target": "cgs",
                "label_zh": "读取提示词快照",
                "label_en": "Read prompt snapshot",
            },
            {
                "source": "krss",
                "target": "knowledge_ops",
                "label_zh": "提交知识修复建议",
                "label_en": "Submit knowledge repair suggestion",
            },
            {
                "source": "qa_generator",
                "target": "testing_service",
                "label_zh": "提供黄金样本",
                "label_en": "Provide golden samples",
            },
            {
                "source": "testing_service",
                "target": "tugraph",
                "label_zh": "依赖 TuGraph 执行 Cypher",
                "label_en": "Depend on TuGraph for Cypher execution",
            },
        ]

    def _runtime_data_objects(self) -> list[Dict[str, str]]:
        return [
            {
                "object_key": "qa_question",
                "label_zh": "问答任务",
                "label_en": "QA Question",
                "source_zh": "外部调用方 / QA 生成器",
                "target_zh": "CGS",
                "meaning_zh": "一次待生成 Cypher 的问题输入，包含 id 与 question。",
            },
            {
                "object_key": "prompt_snapshot",
                "label_zh": "提示词快照",
                "label_en": "Prompt Snapshot",
                "source_zh": "CGS",
                "target_zh": "KRSS",
                "meaning_zh": "CGS 在本轮生成中实际使用的提示词原文。",
            },
            {
                "object_key": "evaluation_submission",
                "label_zh": "评测提交",
                "label_en": "Evaluation Submission",
                "source_zh": "CGS",
                "target_zh": "Testing Service",
                "meaning_zh": "生成结果与生成证据，用于执行与评测。",
            },
            {
                "object_key": "issue_ticket",
                "label_zh": "问题票据",
                "label_en": "Issue Ticket",
                "source_zh": "Testing Service",
                "target_zh": "KRSS",
                "meaning_zh": "失败样本与评测证据，用于知识根因分析。",
            },
            {
                "object_key": "knowledge_repair",
                "label_zh": "知识修复建议",
                "label_en": "Knowledge Repair Suggestion",
                "source_zh": "KRSS",
                "target_zh": "Knowledge Ops",
                "meaning_zh": "面向知识运营服务的正式知识修复请求。",
            },
        ]

    async def _get_console_generation(self, *, id: str, question: str) -> QueryQuestionResponse:
        settings = self.settings or get_settings()
        timeout_seconds = min(10.0, float(settings.request_timeout_seconds))
        try:
            if await self._is_service_online(DEFAULT_CGS_BASE_URL):
                return await asyncio.wait_for(
                    self.console_query_client.submit_question(id=id, question=question),
                    timeout=timeout_seconds,
                )
        except asyncio.TimeoutError:
            logger.warning("console run timed out while waiting for CGS generation for id=%s", id)
        except Exception:
            logger.warning("console run falling back to local generation for id=%s", id)

        try:
            return await asyncio.wait_for(self.console_query_client.get_question_run(id), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            logger.warning("console run timed out while reading CGS question run for id=%s", id)
        except Exception:
            generated_cypher = "MATCH (n:NetworkElement) RETURN n.id AS id, n.name AS name LIMIT 20"
            return QueryQuestionResponse(
                id=id,
                generation_run_id=f"console-{id}",
                attempt_no=1,
                generation_status="generated",
                generated_cypher=generated_cypher,
                parse_summary="console_fallback_generation",
                guardrail_summary="passed",
                raw_output_snapshot=json.dumps({"cypher": generated_cypher}, ensure_ascii=False),
                failure_stage=None,
                failure_reason_summary=None,
                input_prompt_snapshot="Console runtime fallback prompt snapshot.",
            )

    async def _is_service_online(self, base_url: str) -> bool:
        try:
            await self.health_client.read_health(base_url=base_url, timeout_seconds=1.0)
            return True
        except Exception:
            return False

    async def _evaluate_console_without_krss(
        self,
        *,
        request: EvaluationSubmissionRequest,
        golden: QAGoldenRequest,
    ) -> EvaluationSubmissionResponse:
        self.repository.save_submission(request, status="ready_to_evaluate")
        execution = await self.tugraph_client.execute(request.generated_cypher)
        self.repository.save_submission_execution(request.id, execution.model_dump_json(), attempt_no=request.attempt_no)
        evaluation = evaluate_submission(
            question=request.question,
            expected_cypher=golden.cypher,
            expected_answer=golden.answer,
            actual_cypher=request.generated_cypher,
            execution=execution,
            loaded_knowledge_tags=[],
        )
        if evaluation.verdict == "pass":
            self.repository.mark_submission_status(request.id, "passed", attempt_no=request.attempt_no)
            self._save_improvement_assessment(
                id=request.id,
                current_submission=self.repository.get_submission_attempt(request.id, request.attempt_no),
            )
            return EvaluationSubmissionResponse(id=request.id, status="passed", verdict=evaluation.verdict)

        ticket = IssueTicket(
            ticket_id=f"ticket-{request.id}-attempt-{request.attempt_no}",
            id=request.id,
            difficulty=golden.difficulty,
            question=request.question,
            expected=ExpectedAnswer(cypher=golden.cypher, answer=golden.answer),
            actual=ActualAnswer(generated_cypher=request.generated_cypher, execution=execution),
            evaluation=evaluation,
            input_prompt_snapshot=request.input_prompt_snapshot,
        )
        self.repository.save_issue_ticket(ticket)
        self._save_improvement_assessment(
            id=request.id,
            current_submission=self.repository.get_submission_attempt(request.id, request.attempt_no),
        )
        return EvaluationSubmissionResponse(
            id=request.id,
            status="issue_ticket_created",
            issue_ticket_id=ticket.ticket_id,
            verdict=evaluation.verdict,
        )

    def _save_improvement_assessment(self, *, id: str, current_submission: Optional[Dict[str, Any]]) -> None:
        if current_submission is None:
            return
        assessment = self._build_improvement_assessment(id=id, current_submission=current_submission)
        self.repository.save_improvement_assessment(id, assessment, attempt_no=assessment.current_attempt_no)

    def _build_improvement_assessment(
        self,
        *,
        id: str,
        current_submission: Dict[str, Any],
    ) -> ImprovementAssessment:
        current_attempt_no = int(current_submission.get("attempt_no") or 1)
        if current_attempt_no <= 1:
            return ImprovementAssessment(
                qa_id=id,
                current_attempt_no=current_attempt_no,
                previous_attempt_no=None,
                status="first_run",
                summary_zh="这是该 QA 的首轮运行，暂无上一轮可比较。",
            )

        previous_submission = self.repository.get_submission_attempt(id, current_attempt_no - 1)
        if previous_submission is None:
            return ImprovementAssessment(
                qa_id=id,
                current_attempt_no=current_attempt_no,
                previous_attempt_no=current_attempt_no - 1,
                status="not_comparable",
                summary_zh="缺少上一轮完整记录，当前暂不可比较。",
            )

        current_ticket = self.repository.get_issue_snapshot_by_submission_id(id)
        previous_ticket = None
        previous_ticket_id = previous_submission.get("issue_ticket_id")
        if previous_ticket_id:
            ticket = self.repository.get_issue_ticket(previous_ticket_id)
            previous_ticket = None if ticket is None else ticket.model_dump(mode="json")

        dimensions = ImprovementDimensions(
            verdict_change=self._compare_verdict(previous_submission, current_submission, previous_ticket, current_ticket),
            execution_change=self._compare_execution(previous_submission, current_submission),
            syntax_change=self._compare_syntax(previous_submission, current_submission, previous_ticket, current_ticket),
            semantic_change=self._compare_semantics(previous_submission, current_submission, previous_ticket, current_ticket),
            repair_effectiveness=self._compare_repair_effectiveness(previous_submission, current_submission, previous_ticket, current_ticket),
        )
        status = self._overall_improvement_status(dimensions)
        highlights = self._build_improvement_highlights(previous_ticket, current_ticket)
        summary = self._build_improvement_summary(status, current_attempt_no, current_attempt_no - 1, highlights)
        evidence = []
        for ticket in [previous_ticket, current_ticket]:
            if ticket:
                evidence.extend((ticket.get("evaluation") or {}).get("evidence") or [])
        return ImprovementAssessment(
            qa_id=id,
            current_attempt_no=current_attempt_no,
            previous_attempt_no=current_attempt_no - 1,
            status=status,
            summary_zh=summary,
            dimensions=dimensions,
            highlights=highlights,
            evidence=evidence[:6],
        )

    def _compare_verdict(
        self,
        previous_submission: Dict[str, Any],
        current_submission: Dict[str, Any],
        previous_ticket: Optional[Dict[str, Any]],
        current_ticket: Optional[Dict[str, Any]],
    ) -> str:
        prev_score = self._verdict_score(previous_submission, previous_ticket)
        curr_score = self._verdict_score(current_submission, current_ticket)
        if prev_score is None or curr_score is None:
            return "not_comparable"
        if curr_score > prev_score:
            return "improved"
        if curr_score < prev_score:
            return "regressed"
        return "unchanged"

    def _compare_execution(self, previous_submission: Dict[str, Any], current_submission: Dict[str, Any]) -> str:
        previous = self._execution_snapshot_from_submission(previous_submission)
        current = self._execution_snapshot_from_submission(current_submission)
        prev_success = previous.get("success")
        curr_success = current.get("success")
        if prev_success is None or curr_success is None:
            return "not_comparable"
        if not prev_success and curr_success:
            return "improved"
        if prev_success and not curr_success:
            return "regressed"
        return "unchanged"

    def _compare_syntax(
        self,
        previous_submission: Dict[str, Any],
        current_submission: Dict[str, Any],
        previous_ticket: Optional[Dict[str, Any]],
        current_ticket: Optional[Dict[str, Any]],
    ) -> str:
        previous_failures = self._syntax_failures(previous_submission, previous_ticket)
        current_failures = self._syntax_failures(current_submission, current_ticket)
        if previous_failures is None or current_failures is None:
            return "not_comparable"
        if current_failures < previous_failures:
            return "improved"
        if current_failures > previous_failures:
            return "regressed"
        return "unchanged"

    def _compare_semantics(
        self,
        previous_submission: Dict[str, Any],
        current_submission: Dict[str, Any],
        previous_ticket: Optional[Dict[str, Any]],
        current_ticket: Optional[Dict[str, Any]],
    ) -> str:
        previous_score = self._semantic_error_score(previous_submission, previous_ticket)
        current_score = self._semantic_error_score(current_submission, current_ticket)
        if previous_score is None or current_score is None:
            return "not_comparable"
        if current_score < previous_score:
            return "improved"
        if current_score > previous_score:
            return "regressed"
        return "unchanged"

    def _compare_repair_effectiveness(
        self,
        previous_submission: Dict[str, Any],
        current_submission: Dict[str, Any],
        previous_ticket: Optional[Dict[str, Any]],
        current_ticket: Optional[Dict[str, Any]],
    ) -> str:
        if previous_submission.get("krss_response") is None or previous_submission.get("issue_ticket_id") is None:
            return "not_comparable"
        semantic_change = self._compare_semantics(previous_submission, current_submission, previous_ticket, current_ticket)
        if semantic_change != "unchanged":
            return semantic_change
        return self._compare_verdict(previous_submission, current_submission, previous_ticket, current_ticket)

    def _overall_improvement_status(self, dimensions: ImprovementDimensions) -> str:
        if dimensions.verdict_change == "improved":
            return "improved"
        if dimensions.verdict_change == "regressed":
            return "regressed"
        if dimensions.semantic_change == "improved":
            return "improved"
        if dimensions.semantic_change == "regressed":
            return "regressed"
        if "improved" in {dimensions.execution_change, dimensions.syntax_change}:
            return "improved"
        if "regressed" in {dimensions.execution_change, dimensions.syntax_change}:
            return "regressed"
        if all(
            value == "not_comparable"
            for value in dimensions.model_dump().values()
        ):
            return "not_comparable"
        return "unchanged"

    def _build_improvement_summary(
        self,
        status: str,
        current_attempt_no: int,
        previous_attempt_no: int,
        highlights: list[str],
    ) -> str:
        prefix = f"第 {current_attempt_no} 轮相较第 {previous_attempt_no} 轮"
        if status == "improved":
            return prefix + "已改善。" + (f" 关键变化：{'；'.join(highlights[:2])}。" if highlights else "")
        if status == "regressed":
            return prefix + "出现回退。" + (f" 关键变化：{'；'.join(highlights[:2])}。" if highlights else "")
        if status == "unchanged":
            return prefix + "无明显变化。"
        return prefix + "当前暂不可比较。"

    def _build_improvement_highlights(
        self,
        previous_ticket: Optional[Dict[str, Any]],
        current_ticket: Optional[Dict[str, Any]],
    ) -> list[str]:
        previous_evidence = list((previous_ticket or {}).get("evaluation", {}).get("evidence", []) or [])
        current_evidence = list((current_ticket or {}).get("evaluation", {}).get("evidence", []) or [])
        highlights = []
        for item in previous_evidence:
            if item not in current_evidence:
                highlights.append(f"上一轮问题已不再出现: {item}")
        for item in current_evidence:
            if item not in previous_evidence:
                highlights.append(f"当前仍存在或新增问题: {item}")
        return highlights[:4]

    def _verdict_score(
        self,
        submission: Dict[str, Any],
        ticket: Optional[Dict[str, Any]],
    ) -> Optional[int]:
        if ticket is not None:
            verdict = ((ticket.get("evaluation") or {}).get("verdict")) or "fail"
        else:
            submission_status = submission.get("status")
            verdict = "pass" if submission_status == "passed" else None
        mapping = {"fail": 1, "partial_fail": 2, "pass": 3}
        return mapping.get(verdict) if verdict else None

    def _syntax_failures(
        self,
        submission: Dict[str, Any],
        ticket: Optional[Dict[str, Any]],
    ) -> Optional[int]:
        if ticket is not None:
            dimensions = (ticket.get("evaluation") or {}).get("dimensions") or {}
        else:
            execution = self._execution_snapshot_from_submission(submission)
            if not execution:
                return None
            dimensions = {
                "syntax_validity": "pass" if execution.get("success") else "fail",
                "schema_alignment": "pass" if execution.get("success") else "fail",
            }
        return [dimensions.get("syntax_validity"), dimensions.get("schema_alignment")].count("fail")

    def _semantic_error_score(
        self,
        submission: Dict[str, Any],
        ticket: Optional[Dict[str, Any]],
    ) -> Optional[int]:
        if ticket is None:
            if submission.get("status") == "passed":
                return 0
            return None
        evaluation = ticket.get("evaluation") or {}
        dimensions = evaluation.get("dimensions") or {}
        score = 0
        if dimensions.get("result_correctness") == "fail":
            score += 1
        if dimensions.get("question_alignment") == "fail":
            score += 1
        score += min(3, len(evaluation.get("evidence") or []))
        return score

    def _execution_snapshot_from_submission(self, submission_snapshot: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not submission_snapshot or not submission_snapshot.get("execution_json"):
            return {
                "success": False,
                "rows": [],
                "row_count": 0,
                "error_message": "Execution not available.",
                "elapsed_ms": 0,
            }
        return json.loads(submission_snapshot["execution_json"])

    def _evaluation_snapshot(
        self,
        *,
        response: Optional[EvaluationSubmissionResponse],
        submission_snapshot: Optional[Dict[str, Any]],
        issue_snapshot: Optional[Dict[str, Any]],
        execution_snapshot: Dict[str, Any],
    ) -> Dict[str, Any]:
        if issue_snapshot is not None:
            return issue_snapshot["evaluation"]
        submission_status = (submission_snapshot or {}).get("status")
        if response is None or response.status in {"waiting_for_golden", "ready_to_evaluate", "repair_pending"}:
            evidence: list[str] = []
            if submission_status:
                evidence.append(f"submission_status={submission_status}")
            error_message = execution_snapshot.get("error_message")
            if error_message:
                evidence.append(f"execution_error={error_message}")
            return {
                "verdict": "pending",
                "dimensions": {
                    "syntax_validity": "pass" if execution_snapshot.get("success") else "fail",
                    "schema_alignment": "pass" if execution_snapshot.get("success") else "fail",
                    "result_correctness": "fail" if execution_snapshot.get("success") else "pending",
                    "question_alignment": "pending",
                },
                "symptom": "Evaluation is still in progress or downstream status has not been fully persisted yet.",
                "evidence": evidence,
            }
        return {
            "verdict": response.verdict or "pending",
            "dimensions": {
                "syntax_validity": "pass" if execution_snapshot.get("success") else "fail",
                "schema_alignment": "pass" if execution_snapshot.get("success") else "fail",
                "result_correctness": "pass",
                "question_alignment": "pass",
            },
            "symptom": "Console success path completed without generating an issue ticket.",
            "evidence": [],
        }

    def _evaluation_stage_status(
        self,
        *,
        response: Optional[EvaluationSubmissionResponse],
        submission_snapshot: Optional[Dict[str, Any]],
        issue_snapshot: Optional[Dict[str, Any]],
    ) -> str:
        if issue_snapshot is not None:
            return "failed"
        if response is not None and response.verdict == "pass":
            return "success"
        submission_status = (submission_snapshot or {}).get("status")
        if response is None or submission_status in {None, "waiting_for_golden", "ready_to_evaluate"}:
            return "pending"
        if submission_status == "repair_pending":
            return "failed"
        return "failed"

    def _knowledge_repair_stage_status(
        self,
        *,
        evaluation_status: str,
        submission_snapshot: Optional[Dict[str, Any]],
        issue_snapshot: Optional[Dict[str, Any]],
        krss_snapshot: Optional[Dict[str, Any]],
    ) -> str:
        if krss_snapshot is not None:
            return "success"
        submission_status = (submission_snapshot or {}).get("status")
        if submission_status == "repair_submission_failed":
            return "failed"
        if submission_status == "repair_pending":
            return "pending"
        if issue_snapshot is not None:
            return "failed"
        if evaluation_status == "pending":
            return "pending"
        return "skipped"

    def _knowledge_apply_stage_status(self, knowledge_repair_status: str) -> str:
        if knowledge_repair_status == "success":
            return "success"
        if knowledge_repair_status in {"pending", "failed"}:
            return knowledge_repair_status
        return "skipped"


def build_validation_service(settings: Settings) -> EvaluationService:
    return EvaluationService(
        repository=TestingRepository(data_dir=settings.data_dir),
        repair_client=RepairServiceClient(
            base_url=settings.repair_service_url,
            timeout_seconds=settings.request_timeout_seconds,
        ),
        tugraph_client=TuGraphClient(
            base_url=settings.tugraph_url,
            username=settings.tugraph_username,
            password=settings.tugraph_password,
            graph=settings.tugraph_graph,
            mock_mode=settings.mock_tugraph,
        ),
        llm_client=LLMEvaluationClient(
            base_url=settings.llm_base_url or "",
            api_key=settings.llm_api_key or "",
            model=settings.llm_model or "",
            timeout_seconds=settings.request_timeout_seconds,
            temperature=settings.llm_temperature,
            max_retries=settings.llm_max_retries,
            retry_base_delay_seconds=settings.llm_retry_base_delay_seconds,
            max_concurrency=settings.llm_max_concurrency,
        ),
        console_query_client=QueryGeneratorConsoleClient(
            base_url=DEFAULT_CGS_BASE_URL,
            timeout_seconds=settings.request_timeout_seconds,
        ),
        health_client=ServiceHealthClient(),
        settings=settings,
    )


@lru_cache(maxsize=1)
def get_validation_service() -> EvaluationService:
    return build_validation_service(get_settings())
