from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from services.testing_agent.app.clients import ServiceHealthClient


class RuntimeResultsService:
    def __init__(
        self,
        *,
        query_generator_data_dir: str,
        testing_data_dir: str,
        repair_data_dir: str,
        query_generator_base_url: str,
        testing_service_base_url: str,
        repair_service_base_url: str,
        knowledge_ops_base_url: str,
        qa_generator_base_url: str,
        health_client: ServiceHealthClient | None = None,
    ) -> None:
        self._questions_dir = Path(query_generator_data_dir) / "questions"
        self._runs_dir = Path(query_generator_data_dir) / "generation_runs"
        self._attempt_runs_dir = Path(query_generator_data_dir) / "generation_attempts"
        self._goldens_dir = Path(testing_data_dir) / "goldens"
        self._submissions_dir = Path(testing_data_dir) / "submissions"
        self._attempt_submissions_dir = Path(testing_data_dir) / "submission_attempts"
        self._tickets_dir = Path(testing_data_dir) / "issue_tickets"
        self._analyses_dir = Path(repair_data_dir) / "analyses"
        self._health_client = health_client or ServiceHealthClient()
        self._service_cards = [
            {
                "service_key": "cgs",
                "label_zh": "查询生成服务",
                "label_en": "Query Generator Service",
                "base_url": query_generator_base_url,
                "port": "8000",
                "description_zh": "接收问题、拉取提示词并生成 Cypher。",
            },
            {
                "service_key": "testing_service",
                "label_zh": "测试服务",
                "label_en": "Testing Service",
                "base_url": testing_service_base_url,
                "port": "8003",
                "description_zh": "执行 TuGraph、评测结果并触发失败闭环。",
            },
            {
                "service_key": "krss",
                "label_zh": "知识修复建议服务",
                "label_en": "Knowledge Repair Suggestion Service",
                "base_url": repair_service_base_url,
                "port": "8002",
                "description_zh": "分析失败样本并生成知识修复建议。",
            },
            {
                "service_key": "knowledge_ops",
                "label_zh": "知识运营服务",
                "label_en": "Knowledge Ops Service",
                "base_url": knowledge_ops_base_url,
                "port": "8010",
                "description_zh": "提供提示词包并接收知识修复建议。",
            },
            {
                "service_key": "qa_generator",
                "label_zh": "问答生成服务",
                "label_en": "QA Generator Service",
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

    def list_tasks(self) -> dict[str, Any]:
        tasks = []
        for path in sorted(self._questions_dir.glob("*.json")):
            task = self._build_task_summary(path)
            if task is not None:
                tasks.append(task)
        tasks.sort(key=lambda item: item["updated_at"], reverse=True)
        return {
            "title_zh": "运行结果中心",
            "title_en": "Runtime Results Center",
            "tasks": tasks,
        }

    def get_task_detail(self, id: str) -> dict[str, Any] | None:
        question = self._read_json(self._questions_dir / f"{id}.json")
        if question is None or not self._is_visible_task(id):
            return None
        generation = self._read_generation(id, question)
        golden = self._read_json(self._goldens_dir / f"{id}.json")
        submission = self._read_submission(id, question)
        ticket = self._read_ticket(submission)
        analysis = self._read_analysis(submission, id)
        stages = self._build_stages(generation, submission, ticket, analysis)
        quality = self._build_cypher_quality(generation, golden, submission, ticket)
        return {
            "id": id,
            "source": "qa_generator",
            "title_zh": "运行结果中心",
            "title_en": "Runtime Results Center",
            "question": question.get("question", ""),
            "attempt_no": int((generation or {}).get("attempt_no") or (submission or {}).get("attempt_no") or 1),
            "received_at": question.get("received_at"),
            "updated_at": self._latest_timestamp(question, generation, golden, submission, ticket, analysis),
            "generated_cypher": (generation or {}).get("generated_cypher") or (submission or {}).get("generated_cypher") or "",
            "final_verdict": self._final_verdict(stages),
            "stages": stages,
            "timeline": self._build_timeline(stages),
            "cypher_quality": quality,
            "improvement_assessment": (submission or {}).get("improvement_assessment")
            or self._fallback_improvement_assessment(submission),
            "artifacts": {
                "question": question,
                "generation": generation,
                "golden": golden,
                "submission": submission,
                "evaluation": ticket.get("evaluation") if ticket else self._pending_evaluation(submission),
                "execution": self._execution_snapshot(submission, ticket),
                "repair": {
                    "issue_ticket": ticket,
                    "krss_response": (submission or {}).get("krss_response"),
                    "analysis": analysis,
                },
            },
        }

    def _build_task_summary(self, question_path: Path) -> dict[str, Any] | None:
        question = self._read_json(question_path)
        if question is None:
            return None
        id = str(question.get("id") or question_path.stem)
        if not self._is_visible_task(id):
            return None
        generation = self._read_generation(id, question)
        submission = self._read_submission(id, question)
        ticket = self._read_ticket(submission)
        analysis = self._read_analysis(submission, id)
        stages = self._build_stages(generation, submission, ticket, analysis)
        quality = self._build_cypher_quality(generation, None, submission, ticket)
        return {
            "id": id,
            "source": "qa_generator",
            "question": question.get("question", ""),
            "attempt_no": int((generation or {}).get("attempt_no") or (submission or {}).get("attempt_no") or 1),
            "received_at": question.get("received_at"),
            "updated_at": self._latest_timestamp(question, generation, submission, ticket, analysis),
            "current_stage": self._current_stage(stages),
            "final_verdict": self._final_verdict(stages),
            "cypher_quality": quality["label"],
            "cypher_quality_label_zh": quality["label_zh"],
            "improvement_status": self._improvement_status_label(
                (submission or {}).get("improvement_assessment") or self._fallback_improvement_assessment(submission)
            ),
        }

    def _read_generation(self, id: str, question: dict[str, Any] | None) -> dict[str, Any] | None:
        preferred_attempt_no = int((question or {}).get("latest_attempt_no") or 0)
        if preferred_attempt_no > 0:
            preferred = self._read_json(self._attempt_runs_dir / f"{id}__attempt_{preferred_attempt_no}.json")
            if preferred is not None:
                return preferred
        return self._read_json(self._runs_dir / f"{id}.json")

    def _read_submission(self, id: str, question: dict[str, Any] | None) -> dict[str, Any] | None:
        preferred_attempt_no = int((question or {}).get("latest_attempt_no") or 0)
        if preferred_attempt_no > 0:
            preferred = self._read_json(self._attempt_submissions_dir / f"{id}__attempt_{preferred_attempt_no}.json")
            if preferred is not None:
                return preferred
        return self._read_json(self._submissions_dir / f"{id}.json")

    def _build_stages(
        self,
        generation: dict[str, Any] | None,
        submission: dict[str, Any] | None,
        ticket: dict[str, Any] | None,
        analysis: dict[str, Any] | None,
    ) -> dict[str, dict[str, str]]:
        generation_status = self._generation_stage_status(generation)
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

    def _generation_stage_status(self, generation: dict[str, Any] | None) -> str:
        if generation is None:
            return "pending"
        status = str(generation.get("generation_status") or "")
        if generation.get("failure_stage") or status in {
            "prompt_fetch_failed",
            "model_invocation_failed",
            "output_parsing_failed",
            "guardrail_rejected",
            "failed",
        }:
            return "failed"
        if status in {"generated", "submitted_to_testing"}:
            return "passed"
        if status in {"received", "prompt_ready"}:
            return "running"
        return "pending"

    def _evaluation_stage_status(self, submission: dict[str, Any] | None, ticket: dict[str, Any] | None) -> str:
        if ticket is not None:
            return "failed"
        if submission is None:
            return "pending"
        status = str(submission.get("status") or "")
        if status == "passed":
            return "passed"
        if status in {"issue_ticket_created"}:
            return "failed"
        if status == "repair_submission_failed":
            return "failed"
        if status in {"ready_to_evaluate", "waiting_for_golden", "received_submission_only"}:
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
        if analysis is not None or ((submission or {}).get("krss_response") is not None):
            return "passed"
        status = str((submission or {}).get("status") or "")
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
        response = (analysis or {}).get("knowledge_ops_response") or ((submission or {}).get("krss_response") or {}).get(
            "knowledge_ops_response"
        )
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

    def _build_cypher_quality(
        self,
        generation: dict[str, Any] | None,
        golden: dict[str, Any] | None,
        submission: dict[str, Any] | None,
        ticket: dict[str, Any] | None,
    ) -> dict[str, Any]:
        generated_cypher = (generation or {}).get("generated_cypher") or (submission or {}).get("generated_cypher") or ""
        if not generated_cypher:
            return {
                "label": "pending",
                "label_zh": "待评估",
                "summary_zh": "当前还没有可展示的 Cypher 结果。",
                "summary_en": "No generated Cypher is available yet.",
                "findings": [],
            }
        if ticket is None:
            status = str((submission or {}).get("status") or "")
            if status == "passed":
                return {
                    "label": "good",
                    "label_zh": "良好",
                    "summary_zh": "生成的 Cypher 已通过当前评测，可以直接作为本轮结果查看。",
                    "summary_en": "The generated Cypher passed the current evaluation.",
                    "findings": [],
                }
            return {
                "label": "pending",
                "label_zh": "待评估",
                "summary_zh": "Cypher 已生成，但评测或修复状态仍在更新中。",
                "summary_en": "Cypher is available, but evaluation or repair is still updating.",
                "findings": [],
            }

        expected_cypher = ((ticket.get("expected") or {}).get("cypher")) or ((golden or {}).get("golden_cypher")) or ""
        actual_cypher = ((ticket.get("actual") or {}).get("generated_cypher")) or generated_cypher
        findings: list[str] = []

        expected_limit = self._extract_limit(expected_cypher)
        actual_limit = self._extract_limit(actual_cypher)
        if "ORDER BY" in expected_cypher.upper() and "ORDER BY" not in actual_cypher.upper():
            findings.append("缺少 ORDER BY 排序语义，未能表达预期的排序条件。")
        if expected_limit and actual_limit and expected_limit != actual_limit:
            findings.append(f"结果条数不一致，预期 LIMIT {expected_limit}，实际为 LIMIT {actual_limit}。")
        if self._returns_full_node(expected_cypher) and self._returns_projection(actual_cypher):
            findings.append("返回结构不一致，预期返回完整节点，实际返回字段投影。")

        for evidence in ((ticket.get("evaluation") or {}).get("evidence") or []):
            if evidence not in findings:
                findings.append(str(evidence))

        summary_parts = []
        if any("ORDER BY" in finding for finding in findings):
            summary_parts.append("生成的 Cypher 缺少 ORDER BY 排序语义")
        if expected_limit and actual_limit and expected_limit != actual_limit:
            summary_parts.append(f"结果范围也没有对齐到 LIMIT {expected_limit}")
        if not summary_parts:
            summary_parts.append("生成的 Cypher 与预期语义仍有明显偏差")
        return {
            "label": "bad",
            "label_zh": "较差",
            "summary_zh": "，".join(summary_parts) + "。",
            "summary_en": "The generated Cypher still deviates from the expected semantics.",
            "findings": findings,
        }

    def _pending_evaluation(self, submission: dict[str, Any] | None) -> dict[str, Any]:
        if submission is None:
            return {
                "verdict": "pending",
                "dimensions": {},
                "symptom": "No evaluation snapshot is available yet.",
                "evidence": [],
            }
        return {
            "verdict": "pending",
            "dimensions": {},
            "symptom": "Evaluation is still pending or has not been persisted yet.",
            "evidence": [f"submission_status={submission.get('status')}"] if submission.get("status") else [],
        }

    def _execution_snapshot(self, submission: dict[str, Any] | None, ticket: dict[str, Any] | None) -> dict[str, Any]:
        ticket_execution = ((ticket or {}).get("actual") or {}).get("execution")
        if ticket_execution is not None:
            return ticket_execution
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
        if not ticket_json:
            return None
        return json.loads(ticket_json)

    def _read_analysis(self, submission: dict[str, Any] | None, id: str) -> dict[str, Any] | None:
        krss_response = (submission or {}).get("krss_response") or {}
        analysis_id = krss_response.get("analysis_id")
        if analysis_id:
            analysis = self._read_json(self._analyses_dir / f"{analysis_id}.json")
            if analysis is not None:
                return analysis
        for path in self._analyses_dir.glob("*.json"):
            analysis = self._read_json(path)
            if analysis and analysis.get("id") == id:
                return analysis
        return None

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

    def _fallback_improvement_assessment(self, submission: dict[str, Any] | None) -> dict[str, Any]:
        attempt_no = int((submission or {}).get("attempt_no") or 1)
        if attempt_no <= 1:
            return {
                "current_attempt_no": attempt_no,
                "previous_attempt_no": None,
                "status": "first_run",
                "summary_zh": "这是该 QA 的首轮运行，暂无上一轮可比较。",
                "dimensions": {},
                "highlights": [],
                "evidence": [],
            }
        return {
            "current_attempt_no": attempt_no,
            "previous_attempt_no": attempt_no - 1,
            "status": "not_comparable",
            "summary_zh": "上一轮改进评估尚未产出，当前暂不可比较。",
            "dimensions": {},
            "highlights": [],
            "evidence": [],
        }

    def _improvement_status_label(self, assessment: dict[str, Any] | None) -> str:
        mapping = {
            "first_run": "首轮",
            "improved": "已改善",
            "regressed": "已回退",
            "unchanged": "无明显变化",
            "not_comparable": "暂不可比较",
        }
        if not assessment:
            return "首轮"
        return mapping.get(assessment.get("status"), "暂不可比较")

    def _is_visible_task(self, id: str) -> bool:
        return id.startswith("qa_") and not id.startswith("qa-console")

    def _read_json(self, path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def _extract_limit(self, cypher: str) -> str | None:
        match = re.search(r"\bLIMIT\s+(\d+)\b", cypher, flags=re.IGNORECASE)
        if match is None:
            return None
        return match.group(1)

    def _returns_full_node(self, cypher: str) -> bool:
        normalized = " ".join(cypher.upper().split())
        return bool(re.search(r"\bRETURN\s+[A-Z_][A-Z0-9_]*\b", normalized)) and " AS " not in normalized

    def _returns_projection(self, cypher: str) -> bool:
        return " AS " in cypher.upper()
