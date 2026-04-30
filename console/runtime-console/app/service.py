from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
from pydantic import ValidationError

from services.repair_agent.app.models import RepairAnalysisRecord
from services.testing_agent.app.models import IssueTicket


class ServiceHealthClient:
    async def read_health(self, base_url: str, timeout_seconds: float) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.get(f"{base_url.rstrip('/')}/health")
            response.raise_for_status()
            return response.json()


class RuntimeResultsService:
    _DIFFICULTY_ORDER = ["L1", "L2", "L3", "L4", "L5", "L6", "L7", "L8"]
    _GENERATION_STATUS_LABELS = {
        "generated": "生成成功",
        "generation_failed": "生成失败",
        "service_failed": "服务失败",
    }
    _FINAL_VERDICT_LABELS = {
        "pass": "通过",
        "fail": "失败",
        "pending": "待定",
    }

    def __init__(
        self,
        *,
        testing_data_dir: str,
        repair_data_dir: str,
        cypher_generator_agent_base_url: str,
        testing_service_base_url: str,
        repair_service_base_url: str,
        knowledge_agent_base_url: str,
        qa_generator_base_url: str,
        health_client: ServiceHealthClient | None = None,
    ) -> None:
        self._goldens_dir = Path(testing_data_dir) / "goldens"
        self._submissions_dir = Path(testing_data_dir) / "submissions"
        self._attempt_submissions_dir = Path(testing_data_dir) / "submission_attempts"
        self._generation_failures_dir = Path(testing_data_dir) / "generation_failures"
        self._tickets_dir = Path(testing_data_dir) / "issue_tickets"
        self._analyses_dir = Path(repair_data_dir) / "analyses"
        self._health_client = health_client or ServiceHealthClient()
        self._service_cards = [
            {
                "service_key": "cypher-generator-agent",
                "label_zh": "Cypher 生成服务",
                "label_en": "cypher-generator-agent",
                "base_url": cypher_generator_agent_base_url,
                "port": "8000",
                "description_zh": "接收问题、获取上下文并生成 Cypher submission。",
            },
            {
                "service_key": "testing-agent",
                "label_zh": "测试服务",
                "label_en": "testing-agent",
                "base_url": testing_service_base_url,
                "port": "8003",
                "description_zh": "执行 TuGraph、评测结果并触发失败闭环。",
            },
            {
                "service_key": "repair-agent",
                "label_zh": "知识修复建议服务",
                "label_en": "repair-agent",
                "base_url": repair_service_base_url,
                "port": "8002",
                "description_zh": "分析失败样本并生成知识修复建议。",
            },
            {
                "service_key": "knowledge-agent",
                "label_zh": "知识运营服务",
                "label_en": "knowledge-agent",
                "base_url": knowledge_agent_base_url,
                "port": "8010",
                "description_zh": "提供提示词包并接收知识修复建议。",
            },
            {
                "service_key": "qa-agent",
                "label_zh": "问答生成服务",
                "label_en": "qa-agent",
                "base_url": qa_generator_base_url,
                "port": "8020",
                "description_zh": "负责产出并推送新的 QA 任务。",
            },
        ]

    async def get_runtime_services(self) -> dict[str, Any]:
        services = []
        for service in self._service_cards:
            status = "offline"
            try:
                await self._health_client.read_health(service["base_url"], timeout_seconds=1.0)
                status = "online"
            except Exception:
                status = "offline"
            services.append(
                {
                    **service,
                    "status": status,
                }
            )
        return {
            "title_zh": "服务运行状态",
            "title_en": "Runtime Service Status",
            "services": services,
        }

    def list_tasks(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        difficulty: str | None = None,
        q: str | None = None,
    ) -> dict[str, Any]:
        tasks = []
        for task_id in self._recent_task_ids():
            task = self._build_task_summary_lightweight(task_id)
            if task is not None and self._task_matches_filters(task, difficulty=difficulty, q=q):
                tasks.append(task)
        tasks.sort(key=lambda item: item["updated_at"], reverse=True)
        page = max(page, 1)
        page_size = min(max(page_size, 1), 100)
        total = len(tasks)
        total_pages = max((total + page_size - 1) // page_size, 1)
        if page > total_pages:
            page = total_pages
        start = (page - 1) * page_size
        end = start + page_size
        return {
            "title_zh": "运行结果中心",
            "title_en": "Runtime Results Center",
            "tasks": tasks[start:end],
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": total_pages,
                "has_previous": page > 1,
                "has_next": page < total_pages,
            },
        }

    def get_task_summary(self) -> dict[str, Any]:
        buckets = {
            difficulty: {
                "difficulty": difficulty,
                "total": 0,
                "pass": 0,
                "fail": 0,
                "pending": 0,
            }
            for difficulty in self._DIFFICULTY_ORDER
        }
        for task_id in self._recent_task_ids():
            task = self._build_task_summary_lightweight(task_id)
            if task is None:
                continue
            difficulty = task.get("difficulty")
            if difficulty not in buckets:
                continue
            status = str(task.get("final_verdict") or "pending")
            if status not in self._FINAL_VERDICT_LABELS:
                status = "pending"
            buckets[difficulty]["total"] += 1
            buckets[difficulty][status] += 1
        return {
            "title_zh": "难度结论概览",
            "title_en": "Final Verdict Summary by Difficulty",
            "difficulty_order": self._DIFFICULTY_ORDER,
            "statuses": [
                {"key": key, "label_zh": label}
                for key, label in self._FINAL_VERDICT_LABELS.items()
            ],
            "buckets": [buckets[difficulty] for difficulty in self._DIFFICULTY_ORDER],
        }

    def get_task_detail(self, id: str) -> dict[str, Any] | None:
        submission = self._read_submission(id)
        generation_failure = self._read_generation_failure_for_submission(id, submission)
        if submission is None and generation_failure is None:
            return None
        golden = self._read_json(self._goldens_dir / f"{id}.json")
        if not self._is_contract_task(golden=golden, submission=submission, generation_failure=generation_failure):
            return None
        ticket = self._read_ticket(submission)
        analysis = self._read_analysis(submission, id)
        stages = self._build_stages(submission, generation_failure, ticket, analysis)
        summary = self._build_summary(id, golden, submission, generation_failure, stages, ticket, analysis)
        return {
            "id": id,
            "source": "testing_agent",
            "title_zh": "运行结果中心",
            "title_en": "Runtime Results Center",
            "summary": summary,
            "question": summary["question"],
            "difficulty": summary["difficulty"],
            "attempt_no": summary["attempt_no"],
            "received_at": (submission or generation_failure or {}).get("received_at"),
            "updated_at": self._latest_timestamp(golden, submission, ticket, analysis),
            "final_verdict": self._final_verdict(stages),
            "stages": stages,
            "timeline": self._build_timeline(stages),
            "pipeline": {
                "cypher_generator_agent": self._build_generation_section(golden, submission, generation_failure),
                "testing_agent": self._build_testing_section(golden, submission, ticket),
                "repair_agent": self._build_repair_section(submission, ticket, analysis),
            },
        }

    def _build_task_summary(self, id: str) -> dict[str, Any] | None:
        submission = self._read_submission(id)
        generation_failure = self._read_generation_failure_for_submission(id, submission)
        if submission is None and generation_failure is None:
            return None
        golden = self._read_json(self._goldens_dir / f"{id}.json")
        if not self._is_contract_task(golden=golden, submission=submission, generation_failure=generation_failure):
            return None
        ticket = self._read_ticket(submission)
        analysis = self._read_analysis(submission, id)
        stages = self._build_stages(submission, generation_failure, ticket, analysis)
        summary = self._build_summary(id, golden, submission, generation_failure, stages, ticket, analysis)
        return {
            "id": id,
            "source": "testing_agent",
            "question": summary["question"],
            "difficulty": summary["difficulty"],
            "attempt_no": summary["attempt_no"],
            "generation_status": summary["generation_status"],
            "received_at": (submission or generation_failure or {}).get("received_at"),
            "updated_at": self._latest_timestamp(golden, submission, generation_failure, ticket, analysis),
            "current_stage": self._current_stage(stages),
            "final_verdict": self._final_verdict(stages),
        }

    def _build_task_summary_lightweight(self, id: str) -> dict[str, Any] | None:
        submission = self._read_submission(id)
        generation_failure = self._read_generation_failure_for_submission(id, submission)
        if submission is None and generation_failure is None:
            return None
        golden = self._read_json(self._goldens_dir / f"{id}.json")
        if not self._is_contract_task(golden=golden, submission=submission, generation_failure=generation_failure):
            return None
        record = submission or generation_failure or {}
        state = str((submission or {}).get("state") or "")
        evaluation = (submission or {}).get("evaluation") or {}
        verdict = evaluation.get("verdict")
        final_verdict = self._final_verdict_from_state(state=state, verdict=verdict, generation_failure=generation_failure)
        return {
            "id": id,
            "source": "testing_agent",
            "question": record.get("question", ""),
            "difficulty": (golden or {}).get("difficulty"),
            "attempt_no": int((submission or {}).get("attempt_no") or 0),
            "generation_status": record.get("generation_status"),
            "received_at": record.get("received_at"),
            "updated_at": self._latest_timestamp(golden, submission, generation_failure),
            "current_stage": self._current_stage_from_state(state=state, generation_failure=generation_failure),
            "final_verdict": final_verdict,
        }

    def _recent_task_ids(self) -> list[str]:
        task_candidates: dict[str, float] = {}
        for path in self._submissions_dir.glob("*.json"):
            task_candidates[path.stem] = max(task_candidates.get(path.stem, 0), path.stat().st_mtime)
        for path in self._generation_failures_dir.glob("*.json"):
            task_id = path.stem.split("__", 1)[0]
            task_candidates[task_id] = max(task_candidates.get(task_id, 0), path.stat().st_mtime)

        task_ids = [
            task_id
            for task_id, _ in sorted(
                task_candidates.items(),
                key=lambda item: item[1],
                reverse=True,
            )
        ]
        return task_ids

    def _task_matches_filters(self, task: dict[str, Any], *, difficulty: str | None, q: str | None) -> bool:
        if difficulty and task.get("difficulty") != difficulty:
            return False
        query = (q or "").strip().lower()
        if query and query not in str(task.get("id") or "").lower():
            return False
        return True

    def _is_contract_task(
        self,
        *,
        golden: dict[str, Any] | None,
        submission: dict[str, Any] | None,
        generation_failure: dict[str, Any] | None,
    ) -> bool:
        if (golden or {}).get("difficulty") not in self._DIFFICULTY_ORDER:
            return False
        status = (submission or generation_failure or {}).get("generation_status")
        return status in self._GENERATION_STATUS_LABELS

    def _current_stage_from_state(self, *, state: str, generation_failure: dict[str, Any] | None) -> str:
        if generation_failure is not None and not state:
            return "query_generation"
        if state in {"passed", "tugraph_execution_failed", "semantic_review_invalid"}:
            return "evaluation"
        if state in {"repair_pending", "repair_submission_failed", "issue_ticket_created"}:
            return "knowledge_repair"
        if state in {"received_golden_only", "received_submission_only", "ready_to_evaluate"}:
            return "evaluation"
        return "pending"

    def _final_verdict_from_state(self, *, state: str, verdict: Any, generation_failure: dict[str, Any] | None) -> str:
        if verdict in {"pass", "fail"}:
            return str(verdict)
        if state == "passed":
            return "pass"
        if state in {"tugraph_execution_failed", "semantic_review_invalid", "repair_submission_failed", "issue_ticket_created"}:
            return "fail"
        if generation_failure is not None:
            return "pending"
        return "pending"

    def _read_submission(self, id: str) -> dict[str, Any] | None:
        latest = self._read_json(self._submissions_dir / f"{id}.json")
        preferred_attempt_no = int((latest or {}).get("attempt_no") or 0)
        if preferred_attempt_no > 0:
            preferred = self._read_json(self._attempt_submissions_dir / f"{id}__attempt_{preferred_attempt_no}.json")
            if preferred is not None:
                return preferred
        if latest is not None:
            return latest
        attempts = sorted(self._attempt_submissions_dir.glob(f"{id}__attempt_*.json"))
        if attempts:
            return self._read_json(attempts[-1])
        return self._read_json(self._submissions_dir / f"{id}.json")

    def _read_generation_failure(self, id: str, generation_run_id: Any | None = None) -> dict[str, Any] | None:
        if generation_run_id:
            exact = self._read_json(self._generation_failures_dir / f"{id}__{generation_run_id}.json")
            return exact
        reports = [
            report
            for path in sorted(self._generation_failures_dir.glob(f"{id}__*.json"))
            if (report := self._read_json(path)) is not None
        ]
        if not reports:
            return None
        return sorted(
            reports,
            key=lambda item: (
                str(item.get("received_at", "")),
                str(item.get("generation_run_id", "")),
            ),
        )[-1]

    def _read_generation_failure_for_submission(self, id: str, submission: dict[str, Any] | None) -> dict[str, Any] | None:
        if submission is None:
            return self._read_generation_failure(id)
        if submission.get("generation_status") == "generated":
            return None
        return self._read_generation_failure(id, submission.get("generation_run_id"))

    def _build_summary(
        self,
        id: str,
        golden: dict[str, Any] | None,
        submission: dict[str, Any] | None,
        generation_failure: dict[str, Any] | None,
        stages: dict[str, dict[str, str]],
        ticket: dict[str, Any] | None,
        analysis: dict[str, Any] | None,
    ) -> dict[str, Any]:
        record = submission or generation_failure or {}
        return {
            "id": id,
            "question": record.get("question", ""),
            "difficulty": (golden or {}).get("difficulty") or (ticket or {}).get("difficulty"),
            "attempt_no": int((submission or {}).get("attempt_no") or 0),
            "generation_status": record.get("generation_status"),
            "current_stage": self._current_stage(stages),
            "final_verdict": self._final_verdict(stages),
            "received_at": record.get("received_at"),
            "updated_at": self._latest_timestamp(golden, submission, generation_failure, ticket, analysis),
        }

    def _build_generation_section(
        self,
        golden: dict[str, Any] | None,
        submission: dict[str, Any] | None,
        generation_failure: dict[str, Any] | None,
    ) -> dict[str, Any]:
        source = submission or generation_failure or {}
        generated_cypher = (submission or {}).get("generated_cypher") or ""
        generation_status = source.get("generation_status")
        gate_passed = (
            bool(generated_cypher)
            if submission is not None and submission.get("generation_status") == "generated"
            else bool((generation_failure or source).get("gate_passed"))
        )
        return {
            "question": source.get("question", ""),
            "difficulty": (golden or {}).get("difficulty"),
            "generation_run_id": source.get("generation_run_id"),
            "prompt_markdown": source.get("input_prompt_snapshot") or "",
            "last_llm_raw_output": source.get("last_llm_raw_output") or "",
            "parsed_cypher": generated_cypher or (generation_failure or {}).get("parsed_cypher"),
            "gate_passed": gate_passed,
            "failure_reason": source.get("failure_reason") or (generation_failure or {}).get("failure_reason"),
            "last_failure_reason": source.get("last_generation_failure_reason") or (generation_failure or {}).get("last_generation_failure_reason"),
            "retry_count": int(source.get("generation_retry_count") or (generation_failure or {}).get("generation_retry_count") or 0),
            "failure_reasons": source.get("generation_failure_reasons") or (generation_failure or {}).get("generation_failure_reasons") or [],
            "generation_status": generation_status,
        }

    def _build_testing_section(
        self,
        golden: dict[str, Any] | None,
        submission: dict[str, Any] | None,
        ticket: dict[str, Any] | None,
    ) -> dict[str, Any]:
        evaluation = (submission or {}).get("evaluation") or (ticket or {}).get("evaluation") or {}
        primary = evaluation.get("primary_metrics") or {}
        execution_accuracy = primary.get("execution_accuracy") or {}
        secondary = evaluation.get("secondary_signals") or {}
        semantic_review = (submission or {}).get("semantic_review") or {}
        return {
            "golden_cypher": ((golden or {}).get("cypher")) or (((ticket or {}).get("expected") or {}).get("cypher")),
            "golden_answer": ((golden or {}).get("answer")) if golden is not None else (((ticket or {}).get("expected") or {}).get("answer")),
            "actual_cypher": (submission or {}).get("generated_cypher") or (((ticket or {}).get("actual") or {}).get("generated_cypher")),
            "execution": self._execution_snapshot(submission, ticket),
            "grammar": primary.get("grammar") or {"score": None, "parser_error": None, "message": "未评测"},
            "execution_accuracy": {
                "score": execution_accuracy.get("score"),
                "reason": execution_accuracy.get("reason"),
                "semantic_check": execution_accuracy.get("semantic_check"),
            },
            "strict_check": execution_accuracy.get("strict_check") or {"status": "not_run", "message": "未执行严格比较"},
            "semantic_review": {
                "status": semantic_review.get("status") or "not_recorded",
                "prompt": semantic_review.get("prompt_snapshot"),
                "raw_output": semantic_review.get("raw_text"),
                "payload": semantic_review.get("payload"),
                "judgement": semantic_review.get("normalized_judgement"),
                "reasoning": semantic_review.get("reasoning"),
                "message": semantic_review.get("message"),
            },
            "secondary_metrics": {
                "gleu": ((secondary.get("gleu") or {}).get("score")),
                "similarity": ((secondary.get("jaro_winkler_similarity") or {}).get("score")),
            },
            "improvement": (submission or {}).get("improvement_assessment"),
        }

    def _build_repair_section(
        self,
        submission: dict[str, Any] | None,
        ticket: dict[str, Any] | None,
        analysis: dict[str, Any] | None,
    ) -> dict[str, Any]:
        repair_response = self._read_repair_response(submission)
        request = (analysis or {}).get("knowledge_repair_request") or {}
        status = (analysis or {}).get("status") or (repair_response or {}).get("status")
        non_repairable_reason = str((analysis or {}).get("non_repairable_reason") or "")
        not_repairable_request = None
        not_repairable_response = None
        if status == "not_repairable":
            request_message = "不修复"
            if non_repairable_reason:
                request_message = f"不修复：{non_repairable_reason}"
            not_repairable_request = {
                "status": "not_sent",
                "reason": "not_repairable",
                "message": request_message,
            }
            not_repairable_response = {
                "status": "not_sent",
                "reason": "not_repairable",
                "message": "不修复：repair-agent 判定该问题不是 knowledge-agent 知识缺口，因此没有发送请求。",
            }
        return {
            "issue_ticket_id": (ticket or {}).get("ticket_id") or (submission or {}).get("issue_ticket_id"),
            "analysis_id": (analysis or {}).get("analysis_id") or (repair_response or {}).get("analysis_id"),
            "status": status,
            "non_repairable_reason": non_repairable_reason,
            "llm_prompt_markdown": self._repair_llm_prompt_markdown(analysis),
            "raw_output": (analysis or {}).get("raw_output"),
            "suggestion": request.get("suggestion"),
            "knowledge_types": request.get("knowledge_types") or [],
            "knowledge_agent_request": request or not_repairable_request,
            "knowledge_agent_response": (analysis or {}).get("knowledge_agent_response") or not_repairable_response,
            "applied": (analysis or {}).get("applied") if analysis is not None else None,
        }

    def _repair_llm_prompt_markdown(self, analysis: dict[str, Any] | None) -> str:
        if analysis is None:
            return ""
        system_prompt = str(analysis.get("system_prompt_snapshot") or "")
        user_prompt = str(analysis.get("user_prompt_snapshot") or "")
        if not system_prompt and not user_prompt:
            return ""
        return "\n\n".join(
            part
            for part in [
                system_prompt,
                user_prompt,
            ]
            if part
        )

    def _build_stages(
        self,
        submission: dict[str, Any] | None,
        generation_failure: dict[str, Any] | None,
        ticket: dict[str, Any] | None,
        analysis: dict[str, Any] | None,
    ) -> dict[str, dict[str, str]]:
        generation_status = self._generation_stage_status(submission, generation_failure)
        evaluation_status = self._evaluation_stage_status(submission, ticket)
        repair_status = self._repair_stage_status(evaluation_status, submission, analysis)
        apply_status = self._apply_stage_status(repair_status, submission, analysis)
        return {
            "query_generation": {
                "label_zh": "Cypher 生成",
                "label_en": "Cypher Generation",
                "status": generation_status,
            },
            "evaluation": {
                "label_zh": "评测执行",
                "label_en": "Evaluation",
                "status": evaluation_status,
            },
            "knowledge_repair": {
                "label_zh": "知识修复",
                "label_en": "Knowledge Repair",
                "status": repair_status,
            },
            "knowledge_apply": {
                "label_zh": "知识运营接收",
                "label_en": "Knowledge Apply",
                "status": apply_status,
            },
        }

    def _generation_stage_status(
        self,
        submission: dict[str, Any] | None,
        generation_failure: dict[str, Any] | None,
    ) -> str:
        if generation_failure is not None:
            return "failed"
        if submission is None:
            return "pending"
        if submission.get("generation_status") == "generated" and submission.get("generated_cypher"):
            return "passed"
        if submission.get("generation_status") in {"generation_failed", "service_failed"}:
            return "failed"
        return "pending"

    def _evaluation_stage_status(self, submission: dict[str, Any] | None, ticket: dict[str, Any] | None) -> str:
        if ticket is not None:
            return "failed"
        if submission is None:
            return "pending"
        status = str(submission.get("state") or "")
        if status == "passed":
            return "passed"
        if status in {"issue_ticket_created"}:
            return "failed"
        if status == "repair_submission_failed":
            return "failed"
        if status in {"ready_to_evaluate", "received_submission_only"}:
            return "pending"
        if status == "repair_pending":
            return "failed"
        return "pending"

    def _repair_stage_status(
        self,
        evaluation_status: str,
        submission: dict[str, Any] | None,
        analysis: dict[str, Any] | None,
    ) -> str:
        if analysis is not None:
            return "passed"
        if (submission or {}).get("repair_response") is not None:
            return "failed"
        status = str((submission or {}).get("state") or "")
        if status == "repair_pending":
            return "running"
        if status == "repair_submission_failed":
            return "failed"
        if evaluation_status == "passed":
            return "not_started"
        if evaluation_status == "failed":
            return "failed"
        return "pending"

    def _apply_stage_status(
        self,
        repair_status: str,
        submission: dict[str, Any] | None,
        analysis: dict[str, Any] | None,
    ) -> str:
        response = (analysis or {}).get("knowledge_agent_response")
        if response and response.get("status") == "ok":
            return "passed"
        if repair_status == "passed":
            return "running"
        if repair_status in {"failed", "pending"}:
            return repair_status
        return "not_started"

    def _build_timeline(self, stages: dict[str, dict[str, str]]) -> list[dict[str, str]]:
        return [
            {
                "stage_key": stage_key,
                "label_zh": stage["label_zh"],
                "label_en": stage["label_en"],
                "status": stage["status"],
            }
            for stage_key, stage in stages.items()
        ]

    def _pending_evaluation(self, submission: dict[str, Any] | None) -> dict[str, Any]:
        if submission is None:
            return {
                "verdict": "pending",
                "primary_metrics": {},
                "secondary_signals": {},
                "symptom": "No evaluation snapshot is available yet.",
            }
        return {
            "verdict": "pending",
            "primary_metrics": {},
            "secondary_signals": {},
            "symptom": "Evaluation is still pending or has not been persisted yet.",
            "evidence": [f"submission_state={submission.get('state')}"]
            if submission.get("state")
            else [],
        }

    def _execution_snapshot(self, submission: dict[str, Any] | None, ticket: dict[str, Any] | None) -> dict[str, Any]:
        ticket_execution = ((ticket or {}).get("actual") or {}).get("execution")
        if ticket_execution is not None:
            return ticket_execution
        execution = (submission or {}).get("execution")
        if execution is not None:
            return execution
        raw = (submission or {}).get("execution_json")
        if raw:
            return json.loads(raw)
        return {
            "success": False,
            "rows": [],
            "row_count": 0,
            "error_message": "Execution not available.",
            "elapsed_ms": 0,
        }

    def _current_stage(self, stages: dict[str, dict[str, str]]) -> str:
        for stage_key in ["query_generation", "evaluation", "knowledge_repair", "knowledge_apply"]:
            status = stages[stage_key]["status"]
            if status in {"pending", "running", "failed"}:
                return stage_key
        return "done"

    def _final_verdict(self, stages: dict[str, dict[str, str]]) -> str:
        evaluation = stages["evaluation"]["status"]
        if evaluation == "passed":
            return "pass"
        if evaluation == "failed":
            return "fail"
        return "pending"

    def _read_ticket(self, submission: dict[str, Any] | None) -> dict[str, Any] | None:
        ticket_id = (submission or {}).get("issue_ticket_id")
        if not ticket_id:
            return None
        ticket_record = self._read_json(self._tickets_dir / f"{ticket_id}.json")
        if ticket_record is None:
            return None
        ticket_json = ticket_record.get("ticket_json")
        payload = json.loads(ticket_json) if ticket_json else ticket_record
        try:
            return IssueTicket.model_validate(payload).model_dump(mode="json")
        except ValidationError:
            return None

    def _read_analysis(self, submission: dict[str, Any] | None, id: str) -> dict[str, Any] | None:
        repair_response = self._read_repair_response(submission) or {}
        analysis_id = repair_response.get("analysis_id")
        if analysis_id:
            analysis = self._read_json(self._analyses_dir / f"{analysis_id}.json")
            if analysis is not None:
                try:
                    return RepairAnalysisRecord.model_validate(analysis).model_dump(mode="json")
                except ValidationError:
                    return None
        return None

    def _read_repair_response(self, submission: dict[str, Any] | None) -> dict[str, Any] | None:
        payload = (submission or {}).get("repair_response")
        if not isinstance(payload, dict):
            return None
        analysis_id = payload.get("analysis_id")
        if analysis_id is not None and not isinstance(analysis_id, str):
            return None
        return payload

    def _latest_timestamp(self, *records: dict[str, Any] | None) -> str:
        timestamps: list[str] = []
        for record in records:
            if not record:
                continue
            for key in ("updated_at", "finished_at", "applied_at", "created_at", "received_at"):
                value = record.get(key)
                if value:
                    timestamps.append(str(value))
        return max(timestamps) if timestamps else ""

    def _read_json(self, path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
