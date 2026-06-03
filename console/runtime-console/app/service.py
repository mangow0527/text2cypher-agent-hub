from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from pydantic import ValidationError

from services.testing_agent.app.models import IssueTicket


class ServiceHealthClient:
    async def read_health(self, base_url: str, timeout_seconds: float) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.get(f"{base_url.rstrip('/')}/health")
            response.raise_for_status()
            return response.json()


class RuntimeResultsService:
    _TOP_LEVEL_JSON_FIELD_RE = re.compile(r'^\s{2}"(?P<key>[^"]+)":\s*(?P<value>.*)$')
    _CGA_SNAPSHOT_STARTED_AT_RE = re.compile(r'(?:\\"|")started_at(?:\\"|")\s*:\s*(?:\\"|")(?P<value>[^"\\]*)')
    _CGA_SNAPSHOT_FINISHED_AT_RE = re.compile(r'(?:\\"|")finished_at(?:\\"|")\s*:\s*(?:\\"|")(?P<value>[^"\\]*)')
    _CGA_SNAPSHOT_STAGE_DURATION_RE = re.compile(r'(?:\\"|")duration_ms(?:\\"|")\s*:\s*(?P<value>-?\d+(?:\.\d+)?)')
    _LIGHTWEIGHT_SUBMISSION_KEYS = {
        "id",
        "attempt_no",
        "question",
        "generation_run_id",
        "generation_status",
        "state",
        "received_at",
        "updated_at",
        "trace_profile",
        "cga_trace_profile",
        "clarification",
    }
    _LIGHTWEIGHT_FAILURE_KEYS = {
        "id",
        "question",
        "generation_run_id",
        "generation_status",
        "failure_reason",
        "received_at",
        "updated_at",
        "trace_profile",
        "cga_trace_profile",
        "clarification",
    }
    _LIGHTWEIGHT_QUESTION_RECEIPT_KEYS = {
        "id",
        "question",
        "generation_run_id",
        "generation_status",
        "received_at",
        "updated_at",
        "trace_profile",
        "cga_trace_profile",
    }
    _LIGHTWEIGHT_GOLDEN_KEYS = {"id", "difficulty", "updated_at"}
    _DIFFICULTY_ORDER = ["L1", "L2", "L3", "L4", "L5", "L6", "L7", "L8"]
    _GENERATION_STATUS_LABELS = {
        "generated": "生成成功",
        "generation_pending": "生成中",
        "clarification_required": "需要澄清",
        "generation_failed": "生成失败",
        "unsupported_query_shape": "不支持的查询形态",
        "service_failed": "服务失败",
    }
    _CGA_GRAPH_STAGE_TITLES = {
        "graph_model_loader": "语义模型加载",
        "input_clarification_gate": "输入澄清门",
        "question_decomposer": "问题结构化拆解",
        "candidate_retrieval": "语义候选召回",
        "literal_resolver": "字面值解析",
        "grounded_understanding": "语义落地理解",
        "semantic_binder": "语义绑定计划",
        "semantic_validator": "语义正确性校验",
        "repair_controller": "修复与澄清决策",
        "dsl_builder": "受限 DSL 构建",
        "dsl_parser": "DSL 解析",
        "dsl_structural_coverage_gate": "DSL 结构覆盖闸门",
        "cypher_compiler": "Cypher 编译",
        "cypher_self_validation": "Cypher 自校验",
        "output": "服务输出",
    }
    _FINAL_VERDICT_LABELS = {
        "pass": "通过",
        "fail": "失败",
        "clarification_required": "需要澄清",
        "unsupported_query_shape": "不支持",
        "pending": "待定",
    }

    def __init__(
        self,
        *,
        testing_data_dir: str,
        cypher_generator_agent_base_url: str,
        testing_service_base_url: str,
        qa_generator_base_url: str,
        cga_trace_profile: str = "all",
        task_index_cache_ttl_seconds: float = 10.0,
        health_client: ServiceHealthClient | None = None,
    ) -> None:
        self._goldens_dir = Path(testing_data_dir) / "goldens"
        self._question_receipts_dir = Path(testing_data_dir) / "question_receipts"
        self._submissions_dir = Path(testing_data_dir) / "submissions"
        self._attempt_submissions_dir = Path(testing_data_dir) / "submission_attempts"
        self._generation_failures_dir = Path(testing_data_dir) / "generation_failures"
        self._tickets_dir = Path(testing_data_dir) / "issue_tickets"
        self._cga_trace_profile = cga_trace_profile
        self._health_client = health_client or ServiceHealthClient()
        self._task_index_lock = threading.RLock()
        self._task_index_cache_signature: tuple[tuple[str, int, float], ...] | None = None
        self._task_index_cache: list[dict[str, Any]] | None = None
        self._task_index_cache_checked_at = 0.0
        self._task_index_cache_ttl_seconds = max(0.0, float(task_index_cache_ttl_seconds))
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
        tasks = [
            task
            for task in self._task_index()
            if self._task_matches_filters(task, difficulty=difficulty, q=q)
        ]
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
            "title_zh": "NL2Cypher 工作台",
            "title_en": "NL2Cypher Workbench",
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
                "clarification_required": 0,
                "unsupported_query_shape": 0,
                "pending": 0,
                "cga_duration_total_ms": 0.0,
                "cga_duration_count": 0,
                "avg_cga_duration_ms": None,
            }
            for difficulty in self._DIFFICULTY_ORDER
        }
        for task in self._task_index():
            difficulty = task.get("difficulty")
            if difficulty not in buckets:
                continue
            status = str(task.get("final_verdict") or "pending")
            if status not in self._FINAL_VERDICT_LABELS:
                status = "pending"
            buckets[difficulty]["total"] += 1
            buckets[difficulty][status] += 1
            duration_ms = self._coerce_positive_number(task.get("cga_duration_ms"))
            if duration_ms is not None:
                buckets[difficulty]["cga_duration_total_ms"] += duration_ms
                buckets[difficulty]["cga_duration_count"] += 1
        for bucket in buckets.values():
            count = bucket["cga_duration_count"]
            bucket["avg_cga_duration_ms"] = round(bucket["cga_duration_total_ms"] / count) if count else None
            bucket.pop("cga_duration_total_ms", None)
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

    def _task_index(self) -> list[dict[str, Any]]:
        now = time.monotonic()
        with self._task_index_lock:
            if (
                self._task_index_cache is not None
                and self._task_index_cache_ttl_seconds > 0
                and now - self._task_index_cache_checked_at <= self._task_index_cache_ttl_seconds
            ):
                return [dict(task) for task in self._task_index_cache]
        signature = self._task_index_signature()
        with self._task_index_lock:
            if self._task_index_cache_signature == signature and self._task_index_cache is not None:
                self._task_index_cache_checked_at = now
                return [dict(task) for task in self._task_index_cache]
            tasks: list[dict[str, Any]] = []
            for task_id in self._recent_task_ids():
                task = self._build_task_summary_lightweight(task_id)
                if task is not None and self._task_matches_profile(task):
                    tasks.append(task)
            tasks.sort(key=lambda item: item["updated_at"], reverse=True)
            self._task_index_cache_signature = signature
            self._task_index_cache = [dict(task) for task in tasks]
            self._task_index_cache_checked_at = now
            return [dict(task) for task in tasks]

    def _task_index_signature(self) -> tuple[tuple[str, int, float], ...]:
        return (
            self._directory_signature("goldens", self._goldens_dir, "*.json"),
            self._directory_signature("question_receipts", self._question_receipts_dir, "*.json"),
            self._directory_signature("submissions", self._submissions_dir, "*.json"),
            self._directory_signature("submission_attempts", self._attempt_submissions_dir, "*.json"),
            self._directory_signature("generation_failures", self._generation_failures_dir, "*.json"),
        )

    def _directory_signature(self, name: str, directory: Path, pattern: str) -> tuple[str, int, float]:
        count = 0
        latest_mtime = 0.0
        for path in directory.glob(pattern):
            try:
                stat = path.stat()
            except OSError:
                continue
            count += 1
            latest_mtime = max(latest_mtime, stat.st_mtime)
        return (name, count, latest_mtime)

    def get_task_detail(self, id: str) -> dict[str, Any] | None:
        submission = self._read_submission(id)
        submission, generation_failure = self._select_profile_records(id, submission)
        question_receipt = self._read_question_receipt(id)
        if submission is None and generation_failure is None and question_receipt is None:
            return None
        golden = self._read_json(self._goldens_dir / f"{id}.json")
        if not self._is_contract_task(
            golden=golden,
            submission=submission,
            generation_failure=generation_failure,
            question_receipt=question_receipt,
        ):
            return None
        ticket = self._read_ticket(submission)
        stages = self._build_stages(submission, generation_failure, ticket)
        summary = self._build_summary(id, golden, submission, generation_failure, question_receipt, stages, ticket)
        return {
            "id": id,
            "source": "testing_agent",
            "title_zh": "NL2Cypher 工作台",
            "title_en": "NL2Cypher Workbench",
            "summary": summary,
            "question": summary["question"],
            "difficulty": summary["difficulty"],
            "attempt_no": summary["attempt_no"],
            "received_at": (submission or generation_failure or question_receipt or {}).get("received_at"),
            "updated_at": self._latest_timestamp(golden, submission, generation_failure, question_receipt, ticket),
            "final_verdict": self._final_verdict(stages),
            "stages": stages,
            "timeline": self._build_timeline(stages),
            "pipeline": {
                "cypher_generator_agent": self._build_generation_section(golden, submission, generation_failure, question_receipt),
                "testing_agent": self._build_testing_section(golden, submission, ticket),
            },
        }

    def _build_task_summary(self, id: str) -> dict[str, Any] | None:
        submission = self._read_submission(id)
        submission, generation_failure = self._select_profile_records(id, submission)
        question_receipt = self._read_question_receipt(id)
        if submission is None and generation_failure is None and question_receipt is None:
            return None
        golden = self._read_json(self._goldens_dir / f"{id}.json")
        if not self._is_contract_task(
            golden=golden,
            submission=submission,
            generation_failure=generation_failure,
            question_receipt=question_receipt,
        ):
            return None
        ticket = self._read_ticket(submission)
        stages = self._build_stages(submission, generation_failure, ticket)
        summary = self._build_summary(id, golden, submission, generation_failure, question_receipt, stages, ticket)
        return {
            "id": id,
            "source": "testing_agent",
            "question": summary["question"],
            "difficulty": summary["difficulty"],
            "attempt_no": summary["attempt_no"],
            "generation_status": summary["generation_status"],
            "clarification": summary["clarification"],
            "clarification_summary": self._clarification_summary(summary["clarification"]),
            "received_at": (submission or generation_failure or question_receipt or {}).get("received_at"),
            "updated_at": self._latest_timestamp(golden, submission, generation_failure, question_receipt, ticket),
            "current_stage": self._current_stage(stages),
            "final_verdict": self._final_verdict(stages),
        }

    def _build_task_summary_lightweight(self, id: str) -> dict[str, Any] | None:
        submission = self._read_submission_metadata(id)
        submission, generation_failure = self._select_profile_records_lightweight(id, submission)
        question_receipt = self._read_question_receipt_metadata(id)
        if submission is None and generation_failure is None and question_receipt is None:
            return None
        golden = self._read_json_metadata(self._goldens_dir / f"{id}.json", self._LIGHTWEIGHT_GOLDEN_KEYS)
        if not self._is_contract_task(
            golden=golden,
            submission=submission,
            generation_failure=generation_failure,
            question_receipt=question_receipt,
        ):
            return None
        record = submission or generation_failure or question_receipt or {}
        trace_profile = self._record_trace_profile(record)
        state = str((submission or {}).get("state") or "")
        evaluation = (submission or {}).get("evaluation") or {}
        verdict = evaluation.get("verdict")
        final_verdict = self._final_verdict_from_state(state=state, verdict=verdict, generation_failure=generation_failure)
        clarification = self._clarification_from(record, generation_failure)
        cga_duration_ms = self._cga_duration_ms_from_record(record)
        return {
            "id": id,
            "source": "testing_agent",
            "cga_trace_profile": trace_profile,
            "question": record.get("question", ""),
            "difficulty": (golden or {}).get("difficulty"),
            "attempt_no": int((submission or {}).get("attempt_no") or 0),
            "generation_status": record.get("generation_status"),
            "clarification": clarification,
            "clarification_summary": self._clarification_summary(clarification),
            "received_at": record.get("received_at"),
            "updated_at": self._latest_timestamp(golden, submission, generation_failure, question_receipt),
            "current_stage": "query_generation"
            if question_receipt is not None and submission is None and generation_failure is None
            else self._current_stage_from_state(state=state, generation_failure=generation_failure),
            "final_verdict": final_verdict,
            "cga_duration_ms": cga_duration_ms,
        }

    def _recent_task_ids(self) -> list[str]:
        task_candidates: dict[str, float] = {}
        for path in self._submissions_dir.glob("*.json"):
            task_candidates[path.stem] = max(task_candidates.get(path.stem, 0), path.stat().st_mtime)
        for path in self._question_receipts_dir.glob("*.json"):
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
        question_receipt: dict[str, Any] | None = None,
    ) -> bool:
        if (golden or {}).get("difficulty") not in self._DIFFICULTY_ORDER:
            return False
        status = (submission or generation_failure or question_receipt or {}).get("generation_status")
        return status in self._GENERATION_STATUS_LABELS

    def _current_stage_from_state(self, *, state: str, generation_failure: dict[str, Any] | None) -> str:
        if generation_failure is not None and not state:
            return "query_generation"
        if state in {"passed", "tugraph_execution_failed", "semantic_review_invalid"}:
            return "evaluation"
        if state in {"repair_pending", "repair_submission_failed", "issue_ticket_created"}:
            return "evaluation"
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
            generation_status = str(generation_failure.get("generation_status") or "")
            if generation_status in {"clarification_required", "unsupported_query_shape"}:
                return generation_status
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

    def _read_submission_metadata(self, id: str) -> dict[str, Any] | None:
        latest = self._read_json_metadata(self._submissions_dir / f"{id}.json", self._LIGHTWEIGHT_SUBMISSION_KEYS)
        if latest is not None:
            return latest
        attempts = sorted(
            self._attempt_submissions_dir.glob(f"{id}__attempt_*.json"),
            key=self._attempt_path_sort_key,
        )
        if attempts:
            return self._read_json_metadata(attempts[-1], self._LIGHTWEIGHT_SUBMISSION_KEYS)
        return None

    def _attempt_path_sort_key(self, path: Path) -> tuple[int, str]:
        match = re.search(r"__attempt_(\d+)\.json$", path.name)
        return (int(match.group(1)) if match else 0, path.name)

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

    def _read_generation_failure_metadata(self, id: str, generation_run_id: Any | None = None) -> dict[str, Any] | None:
        if generation_run_id:
            return self._read_json_metadata(
                self._generation_failures_dir / f"{id}__{generation_run_id}.json",
                self._LIGHTWEIGHT_FAILURE_KEYS,
            )
        reports = [
            report
            for path in sorted(self._generation_failures_dir.glob(f"{id}__*.json"))
            if (report := self._read_json_metadata(path, self._LIGHTWEIGHT_FAILURE_KEYS)) is not None
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

    def _read_generation_failure_for_submission_metadata(
        self,
        id: str,
        submission: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if submission is None:
            return self._read_generation_failure_metadata(id)
        if submission.get("generation_status") == "generated":
            return None
        return self._read_generation_failure_metadata(id, submission.get("generation_run_id"))

    def _read_question_receipt(self, id: str) -> dict[str, Any] | None:
        return self._read_json(self._question_receipts_dir / f"{id}.json")

    def _read_question_receipt_metadata(self, id: str) -> dict[str, Any] | None:
        return self._read_json_metadata(
            self._question_receipts_dir / f"{id}.json",
            self._LIGHTWEIGHT_QUESTION_RECEIPT_KEYS,
        )

    def _select_profile_records(
        self,
        id: str,
        submission: dict[str, Any] | None,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        profile = str(self._cga_trace_profile or "all").strip().lower()
        if profile in {"", "all"}:
            return submission, self._read_generation_failure_for_submission(id, submission)

        submission_matches = submission is not None and self._record_trace_profile(submission) == profile
        latest_failure = self._read_generation_failure(id)
        failure_matches = latest_failure is not None and self._record_trace_profile(latest_failure) == profile
        if submission_matches and failure_matches:
            if submission.get("generation_run_id") == latest_failure.get("generation_run_id"):
                return submission, latest_failure
            if self._latest_timestamp(submission) >= self._latest_timestamp(latest_failure):
                return submission, None
            return None, latest_failure
        if failure_matches:
            same_run_submission = (
                submission
                if submission_matches and submission.get("generation_run_id") == latest_failure.get("generation_run_id")
                else None
            )
            return same_run_submission, latest_failure
        if submission_matches:
            failure = self._read_generation_failure_for_submission(id, submission)
            if failure is not None and self._record_trace_profile(failure) != profile:
                failure = None
            return submission, failure
        return None, None

    def _select_profile_records_lightweight(
        self,
        id: str,
        submission: dict[str, Any] | None,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        profile = str(self._cga_trace_profile or "all").strip().lower()
        if profile in {"", "all"}:
            return submission, self._read_generation_failure_for_submission_metadata(id, submission)

        submission_matches = submission is not None and self._record_trace_profile(submission) == profile
        latest_failure = self._read_generation_failure_metadata(id)
        failure_matches = latest_failure is not None and self._record_trace_profile(latest_failure) == profile
        if submission_matches and failure_matches:
            if submission.get("generation_run_id") == latest_failure.get("generation_run_id"):
                return submission, latest_failure
            if self._latest_timestamp(submission) >= self._latest_timestamp(latest_failure):
                return submission, None
            return None, latest_failure
        if failure_matches:
            same_run_submission = (
                submission
                if submission_matches and submission.get("generation_run_id") == latest_failure.get("generation_run_id")
                else None
            )
            return same_run_submission, latest_failure
        if submission_matches:
            failure = self._read_generation_failure_for_submission_metadata(id, submission)
            if failure is not None and self._record_trace_profile(failure) != profile:
                failure = None
            return submission, failure
        return None, None

    def _build_summary(
        self,
        id: str,
        golden: dict[str, Any] | None,
        submission: dict[str, Any] | None,
        generation_failure: dict[str, Any] | None,
        question_receipt: dict[str, Any] | None,
        stages: dict[str, dict[str, str]],
        ticket: dict[str, Any] | None,
    ) -> dict[str, Any]:
        record = submission or generation_failure or question_receipt or {}
        clarification = self._clarification_from(record, generation_failure)
        snapshot = self._decode_generation_snapshot(record.get("input_prompt_snapshot") or "")
        cga_duration_ms = self._cga_duration_ms_from_record(record)
        if self._is_cga_graph_trace(snapshot):
            clarification = self._graph_clarification_from_flow(self._graph_generation_flow(snapshot), clarification)
        return {
            "id": id,
            "question": record.get("question", ""),
            "difficulty": (golden or {}).get("difficulty") or (ticket or {}).get("difficulty"),
            "attempt_no": int((submission or {}).get("attempt_no") or 0),
            "generation_status": record.get("generation_status"),
            "clarification": clarification,
            "clarification_summary": self._clarification_summary(clarification),
            "current_stage": self._current_stage(stages),
            "final_verdict": self._final_verdict(stages),
            "cga_duration_ms": cga_duration_ms,
            "received_at": record.get("received_at"),
            "updated_at": self._latest_timestamp(golden, submission, generation_failure, question_receipt, ticket),
        }

    def _clarification_from(
        self,
        source: dict[str, Any] | None,
        generation_failure: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        clarification = (source or {}).get("clarification") or (generation_failure or {}).get("clarification")
        if not isinstance(clarification, dict):
            snapshot = self._decode_generation_snapshot((source or generation_failure or {}).get("input_prompt_snapshot"))
            if self._looks_like_clarification(snapshot):
                clarification = snapshot
            else:
                snapshot_clarification = snapshot.get("clarification")
                clarification = snapshot_clarification if isinstance(snapshot_clarification, dict) else None
        return self._normalize_clarification(clarification)

    def _clarification_summary(self, clarification: dict[str, Any] | None) -> str:
        if not clarification:
            return ""
        question = clarification.get("question_zh") or clarification.get("user_message") or clarification.get("reason")
        return str(question) if question else ""

    def _normalize_clarification(self, clarification: dict[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(clarification, dict):
            return None
        result = dict(clarification)
        question = self._first_text(
            result,
            "question_zh",
            "user_message",
            "question",
            "message",
            "reason",
        )
        if question:
            result["question_zh"] = question
        source_step = self._first_text(result, "source_step", "source_stage")
        if source_step:
            result["source_step"] = source_step
            result["source_stage"] = source_step
        options = self._normalize_clarification_options(result.get("options"))
        result["options"] = options
        result.setdefault("expected_answer_type", "single_choice" if options else "free_text")
        return result

    def _looks_like_clarification(self, value: dict[str, Any]) -> bool:
        return any(key in value for key in ("user_message", "question_zh", "reason_code", "missing_information", "raw_clarification"))

    def _normalize_clarification_options(self, value: Any) -> list[dict[str, str]]:
        if not isinstance(value, list):
            return []
        options: list[dict[str, str]] = []
        for index, item in enumerate(value, start=1):
            if isinstance(item, dict):
                label = self._first_text(item, "label", "summary", "description", "text", "option_id", "id")
                option_id = self._first_text(item, "id", "option_id", "candidate_id") or f"option_{index}"
            else:
                label = str(item).strip()
                option_id = f"option_{index}"
            if not label:
                continue
            options.append({"id": option_id, "label": label})
        return options

    def _first_text(self, payload: dict[str, Any], *keys: str) -> str:
        for key in keys:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    def _build_generation_section(
        self,
        golden: dict[str, Any] | None,
        submission: dict[str, Any] | None,
        generation_failure: dict[str, Any] | None,
        question_receipt: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        source = submission or generation_failure or question_receipt or {}
        generation_status = source.get("generation_status")
        generated_cypher = (submission or {}).get("generated_cypher") or ""
        parsed_cypher = ""
        if generation_status == "generation_failed":
            parsed_cypher = (generation_failure or {}).get("parsed_cypher") or generated_cypher
        display_cypher = generated_cypher or parsed_cypher
        gate_passed = (
            bool(generated_cypher)
            if submission is not None and submission.get("generation_status") == "generated"
            else bool((generation_failure or source).get("gate_passed"))
        )
        prompt_snapshot = source.get("input_prompt_snapshot") or ""
        snapshot = self._decode_generation_snapshot(prompt_snapshot)
        cga_duration_ms = self._cga_duration_ms_from_record(source)
        clarification = self._clarification_from(source, generation_failure)
        trace_schema_version = self._generation_trace_schema_version(snapshot)
        failure_reason = source.get("failure_reason") or (generation_failure or {}).get("failure_reason")
        if self._is_cga_graph_trace(snapshot):
            cga_flow = self._graph_generation_flow(snapshot)
            clarification = self._graph_clarification_from_flow(cga_flow, clarification)
            if clarification is not None:
                cga_flow["artifacts"]["clarification"] = clarification
            return {
                "question": source.get("question", "") or snapshot.get("source_question", ""),
                "difficulty": (golden or {}).get("difficulty"),
                "golden_cypher": (golden or {}).get("cypher"),
                "generated_cypher": display_cypher,
                "generation_run_id": source.get("generation_run_id") or snapshot.get("generation_run_id"),
                "prompt_markdown": prompt_snapshot,
                "parsed_cypher": parsed_cypher,
                "gate_passed": gate_passed,
                "failure_reason": failure_reason or ((snapshot.get("final_outputs") or {}).get("failure") or {}).get("reason"),
                "clarification": clarification,
                "generation_status": generation_status or snapshot.get("final_status"),
                "trace_profile": "graph",
                "trace_schema_version": trace_schema_version,
                "cga_duration_ms": cga_duration_ms,
                "cga_flow": cga_flow,
                "trace_layers": [],
                "llm_prompts": self._graph_generation_llm_prompts(cga_flow.get("llm_calls") or []),
            }
        return {
            "question": source.get("question", ""),
            "difficulty": (golden or {}).get("difficulty"),
            "golden_cypher": (golden or {}).get("cypher"),
            "generated_cypher": display_cypher,
            "generation_run_id": source.get("generation_run_id"),
            "prompt_markdown": prompt_snapshot,
            "parsed_cypher": parsed_cypher,
            "gate_passed": gate_passed,
            "failure_reason": failure_reason,
            "clarification": clarification,
            "generation_status": generation_status,
            "trace_profile": self._record_trace_profile(source),
            "trace_schema_version": trace_schema_version,
            "cga_duration_ms": cga_duration_ms,
            "trace_layers": self._generation_trace_layers(
                snapshot=snapshot,
                source=source,
                generation_status=str(generation_status or ""),
                failure_reason=failure_reason,
                gate_passed=gate_passed,
            ),
            "chain_summary": self._generation_chain_summary(
                snapshot=snapshot,
                generation_status=str(generation_status or ""),
                failure_reason=failure_reason,
                gate_passed=gate_passed,
            ),
            "llm_prompts": self._generation_llm_prompts(snapshot),
        }

    def _generation_trace_schema_version(self, snapshot: dict[str, Any]) -> str | None:
        if self._is_cga_graph_trace(snapshot):
            return "cga_graph_trace_v1"
        if self._is_cga_trace_v2(snapshot):
            return str(snapshot.get("schema_version") or "cga_trace_v2")
        return None

    def _decode_generation_snapshot(self, prompt_snapshot: Any) -> dict[str, Any]:
        if isinstance(prompt_snapshot, dict):
            return prompt_snapshot
        if not isinstance(prompt_snapshot, str) or not prompt_snapshot.strip():
            return {}
        try:
            parsed = json.loads(prompt_snapshot)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def _is_cga_graph_trace(self, snapshot: dict[str, Any]) -> bool:
        return snapshot.get("trace_schema_version") == "cga_graph_trace_v1"

    def _graph_generation_flow(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        stages = [self._graph_stage_display(stage) for stage in self._trace_dicts(snapshot.get("stages"))]
        llm_calls: list[dict[str, Any]] = []
        for stage in stages:
            llm_calls.extend(self._graph_stage_llm_calls(stage))
        final_outputs = self._trace_object(snapshot.get("final_outputs"))
        semantic_model = self._trace_object(snapshot.get("semantic_model"))
        current_stage = self._graph_current_stage(stages, str(snapshot.get("final_status") or ""))
        cga_duration_ms = self._cga_duration_ms_from_snapshot(snapshot)
        return {
            "schema_version": "cga_graph_trace_v1",
            "trace_id": snapshot.get("trace_id"),
            "question_id": snapshot.get("question_id"),
            "generation_run_id": snapshot.get("generation_run_id"),
            "source_question": snapshot.get("source_question"),
            "started_at": snapshot.get("started_at"),
            "finished_at": snapshot.get("finished_at"),
            "final_status": snapshot.get("final_status"),
            "semantic_model": semantic_model,
            "summary": {
                "final_status": snapshot.get("final_status"),
                "stage_count": len(stages),
                "current_stage": current_stage.get("key"),
                "current_stage_title_zh": current_stage.get("title_zh"),
                "llm_call_count": len(llm_calls),
                "duration_ms": round(cga_duration_ms) if cga_duration_ms is not None else None,
                "semantic_model": semantic_model.get("name") or "未记录",
                "model_checksum": semantic_model.get("checksum"),
            },
            "stages": stages,
            "llm_calls": llm_calls,
            "artifacts": {
                "dsl": final_outputs.get("dsl"),
                "cypher": final_outputs.get("cypher"),
                "clarification": final_outputs.get("clarification"),
                "failure": final_outputs.get("failure"),
                "user_visible_notices": final_outputs.get("user_visible_notices") or [],
                "compiler": self._graph_stage_output(snapshot, "cypher_compiler"),
                "self_validation": self._graph_stage_output(snapshot, "cypher_self_validation"),
            },
        }

    def _graph_current_stage(self, stages: list[dict[str, Any]], final_status: str) -> dict[str, str]:
        by_key = {str(stage.get("key") or ""): stage for stage in stages}
        if final_status == "clarification_required" and "repair_controller" in by_key:
            stage = by_key["repair_controller"]
            return {"key": "repair_controller", "title_zh": str(stage.get("title_zh") or "修复与澄清决策")}
        if final_status == "generated" and "cypher_self_validation" in by_key:
            stage = by_key["cypher_self_validation"]
            return {"key": "cypher_self_validation", "title_zh": str(stage.get("title_zh") or "Cypher 自校验")}
        for stage in reversed(stages):
            key = str(stage.get("key") or "")
            if key and key != "output":
                return {"key": key, "title_zh": str(stage.get("title_zh") or key)}
        return {"key": "", "title_zh": "未记录"}

    def _graph_stage_display(self, stage: dict[str, Any]) -> dict[str, Any]:
        key = str(stage.get("stage") or "")
        return {
            "key": key,
            "title_zh": self._CGA_GRAPH_STAGE_TITLES.get(key, key or "未命名阶段"),
            "status": stage.get("status"),
            "started_at": stage.get("started_at"),
            "duration_ms": stage.get("duration_ms"),
            "input": self._trace_ref_value(stage.get("input_ref")),
            "output": self._trace_ref_value(stage.get("output_ref")),
            "metrics": stage.get("metrics") or {},
            "errors": stage.get("errors") or [],
            "warnings": stage.get("warnings") or [],
        }

    def _trace_ref_value(self, ref: Any) -> Any:
        if not isinstance(ref, dict):
            return None
        ref_type = ref.get("type")
        if ref_type == "inline":
            return ref.get("value")
        if ref_type == "redacted":
            return {"type": "redacted", "reason": ref.get("reason") or "redacted"}
        if ref_type == "artifact":
            return {"type": "artifact", "artifact_uri": ref.get("artifact_uri")}
        return ref

    def _graph_stage_llm_calls(self, stage: dict[str, Any]) -> list[dict[str, Any]]:
        calls: list[dict[str, Any]] = []
        for payload in (stage.get("input"), stage.get("output"), stage.get("metrics")):
            calls.extend(self._llm_call_payloads(payload))
        normalized: list[dict[str, Any]] = []
        for index, call in enumerate(calls, start=1):
            normalized.append(self._normalize_graph_llm_call(call, stage=stage, index=index))
        return normalized

    def _llm_call_payloads(self, payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, dict):
            for key in (
                "llm_calls",
                "llm_call",
                "llm_attempts",
                "llm_primary_attempts",
                "llm_secondary_attempts",
                "llm_disambiguation_attempts",
            ):
                value = payload.get(key)
                if value is None:
                    continue
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
                if isinstance(value, dict):
                    return [value]
            return []
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict) and self._looks_like_llm_call(item)]
        return []

    def _looks_like_llm_call(self, value: dict[str, Any]) -> bool:
        return any(key in value for key in ("prompt", "prompt_markdown", "raw_output", "raw_response", "raw_text"))

    def _normalize_graph_llm_call(
        self,
        call: dict[str, Any],
        *,
        stage: dict[str, Any],
        index: int,
    ) -> dict[str, Any]:
        stage_key = str(call.get("stage") or stage.get("key") or "")
        prompt = self._trace_text(
            call.get("prompt_markdown")
            or call.get("rendered_prompt")
            or call.get("prompt")
        )
        raw_output = self._trace_text(
            call.get("raw_output")
            or call.get("raw_response")
            or call.get("raw_text")
            or call.get("output")
        )
        error = call.get("error")
        if error is None and call.get("error_type"):
            error = {
                "type": call.get("error_type"),
                "message": call.get("message"),
            }
        return {
            "call_id": call.get("call_id") or f"{stage.get('key') or 'llm'}-{index}",
            "stage": stage_key or stage.get("key"),
            "stage_title_zh": self._CGA_GRAPH_STAGE_TITLES.get(stage_key) or stage.get("title_zh"),
            "schema_name": call.get("schema_name"),
            "attempt": call.get("attempt"),
            "model": call.get("model"),
            "prompt": prompt,
            "raw_output": raw_output,
            "parsed_output": call.get("parsed_output") or call.get("payload"),
            "status": call.get("status") or ("failed" if error else "success"),
            "error": error,
        }

    def _graph_stage_output(self, snapshot: dict[str, Any], stage_name: str) -> Any:
        for stage in self._trace_dicts(snapshot.get("stages")):
            if stage.get("stage") == stage_name:
                return self._trace_ref_value(stage.get("output_ref"))
        return None

    def _graph_clarification_from_flow(
        self,
        flow: dict[str, Any],
        base: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        result: dict[str, Any] = {}
        artifacts = self._trace_object(flow.get("artifacts"))
        artifact_clarification = self._trace_object(artifacts.get("clarification"))
        if artifact_clarification:
            result.update(artifact_clarification)
        if isinstance(base, dict):
            result.update(base)

        stage_by_key = {str(stage.get("key") or ""): stage for stage in self._trace_dicts(flow.get("stages"))}
        repair_stage = stage_by_key.get("repair_controller") or {}
        repair_input = self._trace_object(repair_stage.get("input"))
        repair_output = self._trace_object(repair_stage.get("output"))
        repair_clarification = self._trace_object(repair_output.get("clarification"))
        for key, value in repair_clarification.items():
            if value not in (None, "", [], {}):
                result.setdefault(key, value)
        for key in ("reason_code", "expected_answer_type", "source_stage", "source_step"):
            value = repair_output.get(key)
            if value not in (None, "", [], {}):
                result.setdefault(key, value)
        decision = repair_output.get("decision")
        if isinstance(decision, dict):
            decision_label = self._first_text(decision, "decision", "action", "kind", "status")
        elif decision not in (None, ""):
            decision_label = str(decision)
        else:
            decision_label = ""
        if decision_label:
            result.setdefault("decision", decision_label)

        validation_errors = self._graph_validation_errors(repair_input, repair_output)
        unresolved_items = self._graph_unresolved_items(stage_by_key, validation_errors)
        if validation_errors:
            result["validation_errors"] = validation_errors
        if unresolved_items:
            result["unresolved_items"] = unresolved_items

        if not result:
            return None
        normalized = self._normalize_clarification(result)
        if not normalized:
            return None
        source_stage = self._first_text(normalized, "source_stage", "source_step")
        if source_stage:
            normalized["source_stage_label_zh"] = self._CGA_GRAPH_STAGE_TITLES.get(source_stage, source_stage)
        if not normalized.get("options") and normalized.get("expected_answer_type") == "free_text":
            normalized.setdefault("no_option_reason", "当前澄清需要用户补充文本，不是固定选项选择。")
        return normalized

    def _graph_validation_errors(self, repair_input: dict[str, Any], repair_output: dict[str, Any]) -> list[dict[str, Any]]:
        raw_errors = repair_input.get("validator_errors")
        if not isinstance(raw_errors, list):
            raw_errors = repair_output.get("validator_errors")
        errors: list[dict[str, Any]] = []
        for error in self._trace_dicts(raw_errors):
            details = error.get("details")
            detail_items = self._trace_dicts(details) if isinstance(details, list) else [details] if isinstance(details, dict) else [{}]
            for detail in detail_items:
                normalized = {
                    "code": error.get("code") or detail.get("code"),
                    "message": error.get("message") or detail.get("message"),
                    "action": error.get("action") or detail.get("action"),
                    "literal": detail.get("literal") or error.get("literal") or detail.get("raw_literal"),
                    "property": self._graph_expected_label(detail) or self._graph_expected_label(error),
                    "alternatives": self._graph_alternatives(detail.get("alternatives") or error.get("alternatives")),
                }
                errors.append({key: value for key, value in normalized.items() if value not in (None, "", {})})
        return errors

    def _graph_unresolved_items(
        self,
        stage_by_key: dict[str, dict[str, Any]],
        validation_errors: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        unresolved: list[dict[str, Any]] = []
        literal_stage = stage_by_key.get("literal_resolver") or {}
        literal_output = literal_stage.get("output")
        if isinstance(literal_output, dict):
            raw_items = (
                literal_output.get("items")
                or literal_output.get("results")
                or literal_output.get("resolved_literals")
                or literal_output.get("unresolved_literals")
                or []
            )
        else:
            raw_items = literal_output if isinstance(literal_output, list) else []
        for item in self._trace_dicts(raw_items):
            is_unresolved = item.get("resolved") is False or bool(item.get("error_code")) or bool(item.get("value_index_miss"))
            if not is_unresolved:
                continue
            unresolved.append(
                {
                    "term": self._graph_term_label(item),
                    "expected": self._graph_expected_label(item),
                    "code": item.get("error_code") or item.get("code"),
                    "alternatives": self._graph_alternatives(item.get("alternatives")),
                    "value_index_miss": bool(item.get("value_index_miss")),
                }
            )
        for error in validation_errors:
            if not (error.get("literal") or error.get("property")):
                continue
            unresolved.append(
                {
                    "term": error.get("literal"),
                    "expected": error.get("property"),
                    "code": error.get("code"),
                    "alternatives": self._graph_alternatives(error.get("alternatives")),
                    "value_index_miss": False,
                }
            )
        return self._dedupe_graph_unresolved_items(unresolved)

    def _dedupe_graph_unresolved_items(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        seen: dict[tuple[str, str], int] = {}
        for item in items:
            clean_item = {key: value for key, value in item.items() if value not in (None, "", {})}
            term = str(clean_item.get("term") or "")
            expected = str(clean_item.get("expected") or "")
            if not term and not expected:
                continue
            key = (term, expected.rsplit(".", 1)[-1] if expected else "")
            if key in seen:
                existing = result[seen[key]]
                existing_expected = str(existing.get("expected") or "")
                if expected and len(expected) > len(existing_expected):
                    existing["expected"] = expected
                if clean_item.get("value_index_miss"):
                    existing["value_index_miss"] = True
                if not existing.get("code") and clean_item.get("code"):
                    existing["code"] = clean_item["code"]
                if not existing.get("alternatives") and clean_item.get("alternatives"):
                    existing["alternatives"] = clean_item["alternatives"]
                continue
            seen[key] = len(result)
            result.append(clean_item)
        return result

    def _graph_term_label(self, item: dict[str, Any]) -> str:
        return self._first_text(item, "raw_literal", "literal", "term", "raw", "value")

    def _graph_expected_label(self, item: dict[str, Any]) -> str:
        direct = self._first_text(
            item,
            "expected",
            "expected_field",
            "field",
            "property",
            "semantic_id",
            "target",
        )
        if direct:
            return direct
        property_value = item.get("property")
        if isinstance(property_value, dict):
            owner = self._first_text(property_value, "owner", "dataset", "vertex", "node")
            name = self._first_text(property_value, "name", "property", "field")
            return ".".join(part for part in (owner, name) if part)
        owner = self._first_text(item, "expected_owner", "owner", "dataset", "vertex")
        name = self._first_text(item, "expected_property", "property_name", "name")
        return ".".join(part for part in (owner, name) if part)

    def _graph_alternatives(self, value: Any) -> list[Any]:
        return value if isinstance(value, list) else []

    def _graph_generation_llm_prompts(self, llm_calls: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        prompts: dict[str, dict[str, Any]] = {}
        for index, call in enumerate(llm_calls, start=1):
            key = str(call.get("call_id") or f"llm_call_{index}")
            prompts[key] = {
                "key": key,
                "title_zh": f"{call.get('stage_title_zh') or call.get('stage') or 'LLM 调用'}",
                "raw_output_title_zh": "LLM 原始返回",
                "triggered": True,
                "prompt": call.get("prompt") or "",
                "raw_output": call.get("raw_output") or "",
                "attempts": [call],
                "empty_label_zh": "本次未触发",
                "empty_raw_output_label_zh": "本次未触发或未记录返回",
            }
        return prompts

    def _is_cga_trace_v2(self, snapshot: dict[str, Any]) -> bool:
        return snapshot.get("schema_version") == "cga_trace_v2" or self._is_ontology_cga_trace(snapshot)

    def _is_ontology_cga_trace(self, snapshot: dict[str, Any]) -> bool:
        return str(snapshot.get("trace_profile") or "").strip().lower() == "ontology" or all(
            key in snapshot for key in ("preprocessing", "lexer", "intent")
        )

    def _task_matches_profile(self, task: dict[str, Any]) -> bool:
        profile = str(self._cga_trace_profile or "all").strip().lower()
        if profile in {"", "all"}:
            return True
        return task.get("cga_trace_profile") == profile

    def _record_matches_profile(self, record: dict[str, Any]) -> bool:
        profile = str(self._cga_trace_profile or "all").strip().lower()
        if profile in {"", "all"}:
            return True
        return self._record_trace_profile(record) == profile

    def _record_trace_profile(self, record: dict[str, Any]) -> str:
        if record.get("generation_status") == "generation_pending":
            return "graph"
        explicit_profile = str(record.get("trace_profile") or record.get("cga_trace_profile") or "").strip().lower()
        if explicit_profile in {"graph", "ontology", "legacy"}:
            return explicit_profile
        metadata_profile_hint = str(record.get("_trace_profile_hint") or "").strip().lower()
        if record.get("_metadata_only") and metadata_profile_hint in {"graph", "ontology", "legacy"}:
            return metadata_profile_hint
        if record.get("_metadata_only"):
            profile = str(self._cga_trace_profile or "all").strip().lower()
            if (
                profile == "ontology"
                and record.get("generation_status") == "clarification_required"
                and isinstance(record.get("clarification"), dict)
            ):
                return "ontology"
            return "legacy"
        snapshot = self._decode_generation_snapshot(record.get("input_prompt_snapshot") or "")
        if self._is_cga_graph_trace(snapshot):
            return "graph"
        if self._is_ontology_cga_trace(snapshot):
            return "ontology"
        profile = str(self._cga_trace_profile or "all").strip().lower()
        if (
            profile == "ontology"
            and record.get("generation_status") == "clarification_required"
            and (isinstance(record.get("clarification"), dict) or self._looks_like_clarification(snapshot))
        ):
            return "ontology"
        return "legacy"

    def _cga_duration_ms_from_record(self, record: dict[str, Any] | None) -> int | None:
        if not isinstance(record, dict):
            return None
        hint = self._coerce_positive_number(record.get("_cga_duration_ms_hint"))
        if hint is not None:
            return round(hint)
        snapshot = self._decode_generation_snapshot(record.get("input_prompt_snapshot") or "")
        duration_ms = self._cga_duration_ms_from_snapshot(snapshot)
        return round(duration_ms) if duration_ms is not None else None

    def _cga_duration_ms_from_snapshot(self, snapshot: dict[str, Any]) -> float | None:
        if not isinstance(snapshot, dict) or not self._is_cga_graph_trace(snapshot):
            return None
        started_at = self._parse_iso_datetime(snapshot.get("started_at"))
        finished_at = self._parse_iso_datetime(snapshot.get("finished_at"))
        if started_at is not None and finished_at is not None:
            try:
                duration_ms = (finished_at - started_at).total_seconds() * 1000
            except TypeError:
                duration_ms = -1
            if duration_ms >= 0:
                return duration_ms
        total_duration_ms = 0.0
        has_stage_duration = False
        for stage in self._trace_dicts(snapshot.get("stages")):
            duration_ms = self._coerce_positive_number(stage.get("duration_ms"))
            if duration_ms is None:
                continue
            total_duration_ms += duration_ms
            has_stage_duration = True
        return total_duration_ms if has_stage_duration else None

    def _cga_duration_ms_from_snapshot_line(self, raw_value: str) -> int | None:
        if "cga_graph_trace_v1" not in raw_value:
            return None
        started_match = self._CGA_SNAPSHOT_STARTED_AT_RE.search(raw_value)
        finished_match = self._CGA_SNAPSHOT_FINISHED_AT_RE.search(raw_value)
        if started_match is not None and finished_match is not None:
            started_at = self._parse_iso_datetime(started_match.group("value"))
            finished_at = self._parse_iso_datetime(finished_match.group("value"))
            if started_at is not None and finished_at is not None:
                try:
                    duration_ms = (finished_at - started_at).total_seconds() * 1000
                except TypeError:
                    duration_ms = -1
                if duration_ms >= 0:
                    return round(duration_ms)
        durations = [
            self._coerce_positive_number(match.group("value"))
            for match in self._CGA_SNAPSHOT_STAGE_DURATION_RE.finditer(raw_value)
        ]
        durations = [duration for duration in durations if duration is not None]
        return round(sum(durations)) if durations else None

    def _parse_iso_datetime(self, value: Any) -> datetime | None:
        if not isinstance(value, str) or not value.strip():
            return None
        try:
            return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None

    def _coerce_positive_number(self, value: Any) -> float | None:
        if isinstance(value, bool) or value is None:
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if number >= 0 else None

    def _trace_object(self, value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}

    def _trace_field(self, label_zh: str, value: Any) -> dict[str, Any]:
        return {
            "label_zh": label_zh,
            "value": "未记录" if value is None or value == "" else value,
        }

    def _trace_count(self, value: Any) -> str:
        return f"{len(value)} 条" if isinstance(value, list) else "0 条"

    def _trace_text(self, value: Any) -> str:
        if isinstance(value, str):
            return value.strip()
        if value is None:
            return ""
        return json.dumps(value, ensure_ascii=False, indent=2)

    def _semantic_view_trace(
        self,
        semantic_view_matching: dict[str, Any],
        semantic_result: dict[str, Any],
    ) -> dict[str, Any]:
        raw_trace = semantic_result.get("trace")
        trace = dict(raw_trace) if isinstance(raw_trace, dict) else {}
        raw_stages = semantic_view_matching.get("stages")
        if isinstance(raw_stages, dict):
            trace = {**raw_stages, **trace}
        attempts = semantic_view_matching.get("llm_disambiguation_attempts")
        if "llm_disambiguation_attempts" not in trace and attempts is not None:
            trace["llm_disambiguation_attempts"] = attempts
        candidate_trace = semantic_result.get("candidate_trace")
        if isinstance(candidate_trace, list):
            trace["candidate_generation"] = candidate_trace
        return trace

    def _logical_plan_path_refs(self, logical_query_plan: dict[str, Any]) -> list[Any]:
        path_refs = logical_query_plan.get("path_refs")
        if isinstance(path_refs, list):
            return path_refs
        schema_path_ref = logical_query_plan.get("schema_path_ref")
        if schema_path_ref:
            return [schema_path_ref]
        trace_refs = logical_query_plan.get("trace_refs")
        if isinstance(trace_refs, list):
            return [item for item in trace_refs if isinstance(item, str) and item.startswith("schema_path:")]
        return []

    def _selected_schema_path_id(self, schema_path_planning: dict[str, Any]) -> str | None:
        selected_path = schema_path_planning.get("selected_path")
        if isinstance(selected_path, dict):
            path_id = selected_path.get("id") or selected_path.get("path_id")
            return str(path_id) if path_id else None
        selected_paths = schema_path_planning.get("selected_paths")
        if isinstance(selected_paths, list) and selected_paths:
            first = selected_paths[0]
            if isinstance(first, dict):
                path_id = first.get("id") or first.get("path_id")
                return str(path_id) if path_id else None
        path_id = schema_path_planning.get("path_id")
        return str(path_id) if path_id else None

    def _generation_trace_layers(
        self,
        *,
        snapshot: dict[str, Any],
        source: dict[str, Any],
        generation_status: str,
        failure_reason: Any,
        gate_passed: bool,
    ) -> list[dict[str, Any]]:
        if not self._is_cga_trace_v2(snapshot):
            return []
        if self._is_ontology_cga_trace(snapshot):
            return self._ontology_generation_trace_layers(
                snapshot=snapshot,
                source=source,
                generation_status=generation_status,
                failure_reason=failure_reason,
                gate_passed=gate_passed,
            )

        service_context = self._trace_object(snapshot.get("service_context"))
        intent_recognition = self._trace_object(snapshot.get("intent_recognition"))
        intent_result = self._trace_object(intent_recognition.get("result"))
        intent_diagnostics = self._trace_object(intent_recognition.get("diagnostics"))
        semantic_view_matching = self._trace_object(snapshot.get("semantic_view_matching"))
        semantic_result = self._trace_object(semantic_view_matching.get("result"))
        semantic_trace = self._semantic_view_trace(semantic_view_matching, semantic_result)
        logical_query_plan = self._trace_object(snapshot.get("logical_query_plan") or snapshot.get("logical_plan"))
        schema_path_planning = self._trace_object(snapshot.get("schema_path_planning"))
        knowledge_selection = self._trace_object(snapshot.get("knowledge_selection"))
        generation = self._trace_object(snapshot.get("generation"))
        renderer = self._trace_object(generation.get("renderer"))
        parser = self._trace_object(generation.get("parser"))
        preflight = self._trace_object(snapshot.get("preflight"))
        clarification = self._trace_object(snapshot.get("clarification"))
        delivery = self._trace_object(snapshot.get("delivery"))
        cypher_fallback_llm = generation.get("cypher_fallback_llm")

        return [
            {
                "key": "orchestration",
                "title_zh": "服务编排层",
                "fields": [
                    self._trace_field("自然语言问题", snapshot.get("question") or source.get("question")),
                    self._trace_field(
                        "生成运行 ID",
                        snapshot.get("generation_run_id") or source.get("generation_run_id"),
                    ),
                    self._trace_field("生成状态", snapshot.get("generation_status") or generation_status),
                    self._trace_field("运行模式", service_context.get("active_mode")),
                    self._trace_field("模型", service_context.get("model")),
                    self._trace_field("语义视图版本", service_context.get("semantic_view_version")),
                    self._trace_field(
                        "RAG 来源",
                        service_context.get("rag_source") or service_context.get("knowledge_context_source"),
                    ),
                ],
                "raw": {
                    "question": snapshot.get("question") or source.get("question"),
                    "generation_run_id": snapshot.get("generation_run_id") or source.get("generation_run_id"),
                    "generation_status": snapshot.get("generation_status") or generation_status,
                    "service_context": service_context,
                },
            },
            {
                "key": "intent_recognition",
                "title_zh": "意图识别层",
                "fields": [
                    self._trace_field("一级意图", intent_result.get("primary_intent")),
                    self._trace_field("二级意图", intent_result.get("secondary_intent")),
                    self._trace_field("来源", intent_result.get("source")),
                    self._trace_field(
                        "判定结果",
                        self._intent_decision_label(str(intent_result.get("decision") or "")),
                    ),
                    self._trace_field("置信度", intent_result.get("confidence")),
                    self._trace_field("规则命中", intent_diagnostics.get("rule_hit")),
                    self._trace_field(
                        "向量召回候选",
                        self._trace_count(intent_diagnostics.get("embedding_candidates")),
                    ),
                    self._trace_field(
                        "一级 LLM 调用",
                        self._trace_count(intent_diagnostics.get("llm_primary_attempts")),
                    ),
                    self._trace_field(
                        "二级 LLM 调用",
                        self._trace_count(intent_diagnostics.get("llm_secondary_attempts")),
                    ),
                ],
                "raw": intent_recognition,
            },
            {
                "key": "semantic_view_matching",
                "title_zh": "语义视图匹配层",
                "fields": [
                    self._trace_field(
                        "匹配实体",
                        self._trace_count(semantic_result.get("matched_entities") or semantic_result.get("entities")),
                    ),
                    self._trace_field("过滤条件", self._trace_count(semantic_result.get("filters"))),
                    self._trace_field(
                        "路径语义",
                        self._trace_count(semantic_result.get("path_semantics") or semantic_result.get("paths")),
                    ),
                    self._trace_field(
                        "返回对象",
                        self._trace_count(semantic_result.get("return_objects") or semantic_result.get("returns")),
                    ),
                    self._trace_field("置信度", semantic_result.get("confidence")),
                    self._trace_field(
                        "歧义",
                        semantic_result.get("ambiguity")
                        or semantic_result.get("clarification_type")
                        or semantic_result.get("rejection_reason")
                        or "无",
                    ),
                    self._trace_field("候选生成", self._trace_count(semantic_trace.get("candidate_generation"))),
                    self._trace_field("候选打分", self._trace_count(semantic_trace.get("candidate_scores"))),
                    self._trace_field(
                        "LLM 消歧调用",
                        self._trace_count(semantic_trace.get("llm_disambiguation_attempts")),
                    ),
                ],
                "raw": semantic_view_matching,
            },
            {
                "key": "planning",
                "title_zh": "规划层",
                "fields": [
                    self._trace_field("答案形态", logical_query_plan.get("answer_shape")),
                    self._trace_field(
                        "操作数",
                        self._trace_count(logical_query_plan.get("operations") or logical_query_plan.get("operators")),
                    ),
                    self._trace_field("路径引用", self._trace_count(self._logical_plan_path_refs(logical_query_plan))),
                    self._trace_field(
                        "选中 Schema 路径",
                        self._selected_schema_path_id(schema_path_planning),
                    ),
                    self._trace_field(
                        "候选路径",
                        self._trace_count(schema_path_planning.get("candidate_paths") or schema_path_planning.get("selected_paths")),
                    ),
                    self._trace_field("拒绝路径", self._trace_count(schema_path_planning.get("rejected_paths"))),
                    self._trace_field(
                        "知识来源",
                        self._knowledge_source_label(str(knowledge_selection.get("source") or "")),
                    ),
                    self._trace_field(
                        "选中知识",
                        self._trace_count(knowledge_selection.get("selected_items") or knowledge_selection.get("fragments")),
                    ),
                    self._trace_field("过滤知识", self._trace_count(knowledge_selection.get("rejected_items"))),
                ],
                "raw": {
                    "logical_query_plan": logical_query_plan,
                    "schema_path_planning": schema_path_planning,
                    "knowledge_selection": knowledge_selection,
                },
            },
            {
                "key": "generation",
                "title_zh": "生成与提交层",
                "fields": [
                    self._trace_field("渲染器类型", renderer.get("family")),
                    self._trace_field(
                        "渲染器结果",
                        self._accepted_label(
                            renderer.get("accepted"),
                            accepted="渲染成功",
                            rejected="渲染未通过",
                        ),
                    ),
                    self._trace_field(
                        "兜底 LLM",
                        "已触发" if isinstance(cypher_fallback_llm, dict) else "本次未触发",
                    ),
                    self._trace_field("解析结果", parser.get("parse_summary")),
                    self._trace_field(
                        "预检结果",
                        self._accepted_label(
                            preflight.get("accepted"),
                            accepted="预检通过",
                            rejected="预检未通过",
                        ),
                    ),
                    self._trace_field(
                        "失败原因",
                        self._generation_failure_label(
                            str(failure_reason or preflight.get("reason") or renderer.get("failure_reason") or "")
                        ),
                    ),
                    self._trace_field("生成门禁", "生成门禁通过" if gate_passed else "生成门禁未通过"),
                    self._trace_field("澄清问题", clarification.get("question_zh")),
                    self._trace_field("投递状态", delivery.get("status")),
                ],
                "raw": {
                    "generation": generation,
                    "preflight": preflight,
                    "clarification": clarification or None,
                    "delivery": delivery,
                },
            },
        ]

    def _ontology_generation_trace_layers(
        self,
        *,
        snapshot: dict[str, Any],
        source: dict[str, Any],
        generation_status: str,
        failure_reason: Any,
        gate_passed: bool,
    ) -> list[dict[str, Any]]:
        preprocessing = self._trace_object(snapshot.get("preprocessing"))
        lexer = self._trace_object(snapshot.get("lexer"))
        intent_output = self._trace_object(snapshot.get("intent"))
        intent = self._trace_object(intent_output.get("intent"))
        intent_details = self._intent_taxonomy_details(
            str(intent.get("primary") or intent.get("primary_intent") or ""),
            str(intent.get("secondary") or intent.get("secondary_intent") or ""),
        )
        object_role_selection = self._trace_object(snapshot.get("object_role_selection"))
        ontology_mapping = self._trace_object(snapshot.get("ontology_mapping"))
        ontology_path_selection = self._trace_object(snapshot.get("ontology_path_selection"))
        coreference = self._trace_object(snapshot.get("coreference"))
        binding = self._trace_object(snapshot.get("binding"))
        shape_finalization = self._trace_object(snapshot.get("shape_finalization"))
        validator = self._trace_object(snapshot.get("validator"))
        compiler = self._trace_object(snapshot.get("compiler"))
        mapping_categories = self._ontology_mapping_categories(ontology_mapping)
        logical_plan = self._trace_object(shape_finalization.get("logical_plan") or shape_finalization.get("logical_plan_draft"))
        core_question = preprocessing.get("core_question") or lexer.get("question")
        question_framing_layer = self._question_framing_trace_layer(lexer)

        layers = [
            {
                "key": "preprocessing",
                "title_zh": "自然语言问题预处理",
                "fields": [
                    self._trace_field("原始问题", preprocessing.get("original_question") or source.get("question")),
                    self._trace_field("输出给下一阶段的 core_question", core_question),
                    self._trace_field("是否通过", "通过" if preprocessing.get("accepted") is True else "未通过"),
                ],
                "sections": [],
            },
            *([question_framing_layer] if question_framing_layer is not None else []),
            {
                "key": "lexical",
                "title_zh": "词法层",
                "fields": [
                    self._trace_field("输入 core_question", core_question),
                    self._trace_field("mentions 数量", self._trace_count(lexer.get("mentions"))),
                    self._trace_field("AC 命中数量", self._trace_count(lexer.get("ac_matches"))),
                    self._trace_field("结构化命中数量", self._trace_count(lexer.get("structured_matches"))),
                    self._trace_field("向量召回数量", self._trace_count(lexer.get("vector_recalls"))),
                    self._trace_field("未匹配残片数量", self._trace_count(lexer.get("unmatched_fragments"))),
                    self._trace_field("上下文信号数量", self._trace_count(lexer.get("context_signals"))),
                    self._trace_field("答案形态信号数量", self._trace_count(lexer.get("shape_signals"))),
                ],
                "sections": [
                    {
                        "title_zh": "词法层明细",
                        "tables": self._lexical_trace_tables(lexer),
                        "blocks": [
                            self._trace_section("词法层完整输出", lexer),
                        ],
                    },
                ],
            },
            {
                "key": "intent_shape",
                "title_zh": "意图识别与答案形态",
                "fields": [
                    self._trace_field("一层意图字段名称", intent.get("primary") or intent.get("primary_intent")),
                    self._trace_field("一层意图中文解释", self._intent_explanation(intent_details.get("primary"))),
                    self._trace_field("二层意图字段名称", intent.get("secondary") or intent.get("secondary_intent")),
                    self._trace_field("二层意图中文解释", self._intent_explanation(intent_details.get("secondary"))),
                    self._trace_field("最终意图来源阶段", intent.get("source")),
                    self._trace_field("判定结果", self._intent_decision_label(str(intent.get("decision") or ""))),
                    self._trace_field("置信度", intent.get("confidence")),
                    self._trace_field("答案形态摘要", self._shape_summary(intent_output.get("initial_shape"))),
                ],
                "sections": [],
            },
            {
                "key": "ontology",
                "title_zh": "本体层",
                "sections": [
                    self._trace_step(
                        "3.1 对象提取与角色标注",
                        fields=[
                            self._trace_field("对象候选", self._items_summary(object_role_selection.get("object_candidates"))),
                            self._trace_field("角色标注", self._object_role_summary(object_role_selection)),
                        ],
                        blocks=[
                            self._trace_section("输出", object_role_selection),
                            self._trace_section("LLM 原始输入提示词", object_role_selection.get("llm_prompt") or "未触发"),
                            self._trace_section("LLM 原始输出", object_role_selection.get("llm_raw_output") or "未触发"),
                        ],
                    ),
                    self._trace_step(
                        "3.2 Mention 映射到本体",
                        fields=[
                            self._trace_field("对象映射", self._ontology_item_summary(ontology_mapping.get("ontology_objects"))),
                            self._trace_field("关系线索", self._ontology_item_summary(ontology_mapping.get("ontology_relation_hints"))),
                            self._trace_field("属性映射", self._ontology_item_summary(ontology_mapping.get("ontology_attributes"))),
                            self._trace_field("取值映射", self._ontology_item_summary(ontology_mapping.get("ontology_values"))),
                        ],
                        blocks=[
                            self._trace_section("分类说明", mapping_categories),
                            self._trace_section("输出", ontology_mapping),
                        ],
                    ),
                    self._trace_step(
                        "3.3 本体路径选择",
                        fields=[
                            self._trace_field("路径请求", self._items_summary(ontology_path_selection.get("path_requests"), keys=("request_id", "source_id", "target_id"))),
                            self._trace_field("候选路径", self._items_summary(ontology_path_selection.get("candidate_paths"), keys=("path_id", "path_label", "relation_chain_type"))),
                            self._trace_field("选中路径", self._selected_path_summary(ontology_path_selection.get("selected_paths"))),
                        ],
                        blocks=[
                            self._trace_section("输出", ontology_path_selection),
                            self._trace_section("LLM 原始输入提示词", ontology_path_selection.get("llm_prompt") or "未触发"),
                            self._trace_section("LLM 原始输出", ontology_path_selection.get("llm_raw_output") or "未触发"),
                        ],
                    ),
                    self._trace_step(
                        "3.4 指代消解选择",
                        fields=[
                            self._trace_field("指代消解决策", self._coreference_summary(coreference.get("resolved_pairs"))),
                            self._trace_field("未消解项", self._items_summary(coreference.get("unresolved_items"))),
                            self._trace_field("合并节点", self._items_summary(coreference.get("merged_nodes"), keys=("node_id", "canonical_id", "surface"))),
                        ],
                        blocks=[
                            self._trace_section(
                                "输出",
                                {
                                    "resolved_pairs": coreference.get("resolved_pairs") or [],
                                    "unresolved_items": coreference.get("unresolved_items") or [],
                                    "merged_nodes": coreference.get("merged_nodes") or [],
                                },
                            ),
                            self._trace_section("LLM 调用明细", self._coreference_llm_attempts(coreference) or "未触发"),
                        ],
                    ),
                    self._trace_step(
                        "3.5 字段绑定",
                        fields=[
                            self._trace_field("过滤绑定", self._binding_summary(binding.get("filters"))),
                            self._trace_field("投影绑定", self._binding_summary(binding.get("projections"))),
                            self._trace_field("未绑定项", self._items_summary(binding.get("unresolved_items"))),
                        ],
                        blocks=[
                            self._trace_section("输出", binding),
                        ],
                    ),
                    self._trace_step(
                        "3.6 最终回填结构",
                        fields=[
                            self._trace_field("根操作", logical_plan.get("root_operation")),
                            self._trace_field("投影字段", self._projection_summary(logical_plan)),
                        ],
                        blocks=[
                            self._trace_section("Step 3 输出结构", shape_finalization),
                        ],
                    ),
                ],
            },
            {
                "key": "validation",
                "title_zh": "校验层",
                "fields": [
                    self._trace_field(
                        "校验结果",
                        self._accepted_label(validator.get("accepted"), accepted="校验通过", rejected="校验未通过"),
                    ),
                    self._trace_field("校验项摘要", self._validation_checks_summary(validator.get("checks"))),
                    self._trace_field("生成门禁", "生成门禁通过" if gate_passed else "生成门禁未通过"),
                    self._trace_field("生成状态", snapshot.get("generation_status") or generation_status),
                    self._trace_field("失败原因", self._generation_failure_label(str(failure_reason or ""))),
                ],
                "sections": [
                    self._trace_section("校验明细", validator),
                ],
            },
            {
                "key": "compilation",
                "title_zh": "编译层",
                "fields": [
                    self._trace_field("编译器类型", compiler.get("renderer_family")),
                    self._trace_field("映射版本", compiler.get("mapping_version")),
                    self._trace_field("物理 Schema 版本", compiler.get("physical_schema_version")),
                    self._trace_field("节点物理绑定", self._binding_summary(compiler.get("physical_bindings"))),
                    self._trace_field("属性物理绑定", self._binding_summary(compiler.get("attribute_bindings"))),
                    self._trace_field("编译结果", "已输出 Cypher" if compiler.get("cypher") else "未输出 Cypher"),
                    self._trace_field("Cypher 摘要", self._cypher_summary(compiler.get("cypher"))),
                ],
                "sections": [
                    self._trace_section("编译输出 Cypher", compiler.get("cypher") or "未生成"),
                    self._trace_section("编译层完整输出", compiler),
                ],
            },
        ]
        return layers

    def _question_framing_trace_layer(self, lexer: dict[str, Any]) -> dict[str, Any] | None:
        question_framing = self._trace_object(lexer.get("question_framing"))
        if not question_framing:
            return None
        prompt = question_framing.get("prompt") or question_framing.get("prompt_markdown")
        return {
            "key": "question_framing",
            "title_zh": "Step 0 问题框定 / 检索计划",
            "fields": [
                self._trace_field("输入问题", question_framing.get("question") or lexer.get("question")),
            ],
            "sections": [
                self._trace_section("发给 LLM 的完整提示词", prompt or "历史记录未保存"),
                self._trace_section("LLM 原始返回", question_framing.get("raw_response") or "未记录"),
                self._trace_section("原子问题拆分结果", question_framing.get("atoms") or []),
                self._trace_section("结构化检索计划", question_framing.get("retrieval_plan") or {}),
                self._trace_section("Step 1 消费情况摘要", self._question_framing_step1_consumption_summary(lexer)),
            ],
        }

    def _question_framing_step1_consumption_summary(self, lexer: dict[str, Any]) -> dict[str, Any]:
        recalls: list[dict[str, Any]] = []
        for recall in self._trace_list(lexer.get("vector_recalls")):
            if not isinstance(recall, dict) or recall.get("source") != "question_framing_retrieval_plan":
                continue
            candidates: list[dict[str, Any]] = []
            for candidate in self._trace_list(recall.get("candidates"))[:5]:
                if not isinstance(candidate, dict):
                    continue
                candidates.append(
                    {
                        "canonical_id": candidate.get("canonical_id"),
                        "mention_type": candidate.get("mention_type"),
                        "score": candidate.get("score"),
                        "matched_surface": candidate.get("matched_surface"),
                    }
                )
            recalls.append(
                {
                    "query_id": recall.get("query_id"),
                    "retrieval_text": recall.get("fragment"),
                    "span": recall.get("span"),
                    "provider": recall.get("provider"),
                    "candidate_count": len(self._trace_list(recall.get("candidates"))),
                    "top_candidates": candidates,
                }
            )
        return {
            "retrieval_plan_vector_recall_count": len(recalls),
            "retrieval_plan_vector_recalls": recalls,
        }

    def _lexical_trace_tables(self, lexer: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            self._trace_table(
                "mentions 明细",
                columns=[
                    ("surface", "surface", 170),
                    ("mention_type", "mention_type", 150),
                    ("canonical_id", "canonical_id", 280),
                    ("span", "span", 120),
                    ("metadata", "metadata", 380),
                ],
                rows=[
                    {
                        "surface": item.get("surface"),
                        "mention_type": item.get("mention_type"),
                        "canonical_id": item.get("canonical_id"),
                        "span": self._display_json(item.get("span")),
                        "metadata": self._display_json(item.get("metadata")),
                    }
                    for item in self._trace_dicts(lexer.get("mentions"))
                ],
            ),
            self._trace_table(
                "AC / 结构化命中明细",
                columns=[
                    ("surface", "surface", 170),
                    ("mention_type", "mention_type", 150),
                    ("canonical_id", "canonical_id", 280),
                    ("span", "span", 120),
                    ("match_source", "match_source", 190),
                    ("score", "score", 90),
                ],
                rows=[
                    {
                        "surface": item.get("surface"),
                        "mention_type": item.get("mention_type"),
                        "canonical_id": item.get("canonical_id"),
                        "span": self._display_json(item.get("span")),
                        "match_source": item.get("match_source"),
                        "score": item.get("score"),
                    }
                    for item in (
                        *self._trace_dicts(lexer.get("ac_matches")),
                        *self._trace_dicts(lexer.get("structured_matches")),
                    )
                ],
            ),
            self._trace_table(
                "向量召回明细",
                columns=[
                    ("fragment", "fragment", 300),
                    ("span", "span", 120),
                    ("expected_mention_type", "expected_mention_type", 210),
                    ("source", "source", 260),
                    ("query_id", "query_id", 110),
                    ("provider", "provider", 170),
                    ("top_candidates", "top candidates", 560),
                ],
                rows=[
                    {
                        "fragment": item.get("fragment"),
                        "span": self._display_json(item.get("span")),
                        "expected_mention_type": item.get("expected_mention_type"),
                        "source": item.get("source"),
                        "query_id": item.get("query_id"),
                        "provider": item.get("provider"),
                        "top_candidates": self._vector_candidate_summary(item.get("candidates")),
                    }
                    for item in self._trace_dicts(lexer.get("vector_recalls"))
                ],
            ),
            self._trace_table(
                "未匹配残片",
                columns=[
                    ("surface", "surface", 320),
                    ("span", "span", 140),
                    ("expected_mention_type", "expected_mention_type", 240),
                ],
                rows=[
                    {
                        "surface": item.get("surface"),
                        "span": self._display_json(item.get("span")),
                        "expected_mention_type": item.get("expected_mention_type"),
                    }
                    for item in self._trace_dicts(lexer.get("unmatched_fragments"))
                ],
            ),
            self._trace_table("context signals", columns=self._signal_table_columns(), rows=self._signal_table_rows(lexer.get("context_signals"))),
            self._trace_table("shape signals", columns=self._signal_table_columns(), rows=self._signal_table_rows(lexer.get("shape_signals"))),
        ]

    def _trace_table(
        self,
        title_zh: str,
        *,
        columns: list[tuple[str, str] | tuple[str, str, int]],
        rows: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "title_zh": title_zh,
            "columns": [
                {
                    "key": column[0],
                    "label_zh": column[1],
                    **({"width": column[2]} if len(column) > 2 else {}),
                }
                for column in columns
            ],
            "rows": rows,
        }

    def _trace_dicts(self, value: Any) -> list[dict[str, Any]]:
        return [item for item in self._trace_list(value) if isinstance(item, dict)]

    def _display_json(self, value: Any) -> str:
        if value is None or value == "" or value == {} or value == []:
            return "未记录"
        return json.dumps(value, ensure_ascii=False)

    def _vector_candidate_summary(self, value: Any) -> str:
        parts: list[str] = []
        for candidate in self._trace_dicts(value)[:5]:
            canonical_id = self._compact_text(candidate.get("canonical_id"))
            mention_type = self._compact_text(candidate.get("mention_type"))
            score = self._compact_text(candidate.get("score"))
            if not canonical_id:
                continue
            details = ", ".join(part for part in (mention_type, score) if part)
            parts.append(f"{canonical_id}({details})" if details else canonical_id)
        return " / ".join(parts) if parts else "未记录"

    def _signal_table_columns(self) -> list[tuple[str, str, int]]:
        return [
            ("signal_id", "signal_id", 110),
            ("type", "type", 220),
            ("text", "text", 280),
            ("span", "span", 120),
            ("supports", "supports", 420),
            ("strength", "strength", 100),
        ]

    def _signal_table_rows(self, value: Any) -> list[dict[str, Any]]:
        return [
            {
                "signal_id": item.get("signal_id"),
                "type": item.get("type"),
                "text": item.get("text"),
                "span": self._display_json(item.get("span")),
                "supports": self._summary_join([self._compact_text(part) for part in self._trace_list(item.get("supports"))]),
                "strength": item.get("strength"),
            }
            for item in self._trace_dicts(value)
        ]

    def _trace_section(self, title_zh: str, value: Any) -> dict[str, Any]:
        return {"title_zh": title_zh, "value": value if value is not None else "未记录"}

    def _trace_step(
        self,
        title_zh: str,
        *,
        fields: list[dict[str, Any]] | None = None,
        blocks: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return {
            "title_zh": title_zh,
            "fields": fields or [],
            "blocks": blocks or [],
        }

    def _trace_count_dict(self, value: Any) -> str:
        return f"{len(value)} 项" if isinstance(value, dict) else "0 项"

    def _trace_list(self, value: Any) -> list[Any]:
        return value if isinstance(value, list) else []

    def _compact_text(self, value: Any) -> str:
        if value is None or value == "":
            return ""
        return str(value)

    def _summary_join(self, values: list[str], *, empty: str = "未记录", limit: int = 6) -> str:
        clean_values = [value for value in values if value]
        if not clean_values:
            return empty
        shown = clean_values[:limit]
        suffix = f" 等 {len(clean_values)} 项" if len(clean_values) > limit else ""
        return " / ".join(shown) + suffix

    def _item_value(self, item: Any, keys: tuple[str, ...]) -> str:
        if not isinstance(item, dict):
            return self._compact_text(item)
        for key in keys:
            value = item.get(key)
            if value is not None and value != "":
                return self._compact_text(value)
        return ""

    def _items_summary(
        self,
        value: Any,
        *,
        keys: tuple[str, ...] = (
            "surface",
            "label_zh",
            "label",
            "name",
            "node_id",
            "candidate_id",
            "canonical_id",
            "id",
            "value",
        ),
        empty: str = "未记录",
        limit: int = 6,
    ) -> str:
        return self._summary_join([self._item_value(item, keys) for item in self._trace_list(value)], empty=empty, limit=limit)

    def _mention_summary(self, mentions: Any) -> str:
        values: list[str] = []
        for mention in self._trace_list(mentions):
            if not isinstance(mention, dict):
                continue
            label = self._item_value(mention, ("surface", "canonical_id", "mention_id", "id"))
            mention_type = self._item_value(mention, ("mention_type", "type"))
            values.append(f"{label}({mention_type})" if label and mention_type else label)
        return self._summary_join(values)

    def _signal_summary(self, signals: Any) -> str:
        values: list[str] = []
        for signal in self._trace_list(signals):
            if not isinstance(signal, dict):
                continue
            text = self._item_value(signal, ("text", "surface", "label_zh", "label", "signal_id", "id"))
            signal_type = self._item_value(signal, ("type", "signal_type"))
            values.append(f"{text}({signal_type})" if text and signal_type else text)
        return self._summary_join(values)

    def _shape_summary(self, shape: Any) -> str:
        if not isinstance(shape, dict):
            return "未记录"
        values: list[str] = []
        for key, payload in shape.items():
            if isinstance(payload, dict):
                value = payload.get("value") or payload.get("label_zh") or payload.get("field") or payload.get("answer_type")
            else:
                value = payload
            text = self._compact_text(value)
            if text:
                values.append(f"{key}={text}")
        return self._summary_join(values)

    def _object_role_summary(self, object_role_selection: dict[str, Any]) -> str:
        role_selection = self._trace_object(object_role_selection.get("object_role_selection"))
        values: list[str] = []
        for item in self._trace_list(role_selection.get("selected_objects")):
            if not isinstance(item, dict):
                continue
            label = self._item_value(item, ("surface", "candidate_id", "canonical_id", "object_id", "id"))
            roles = item.get("roles")
            if isinstance(roles, list):
                role_text = ", ".join(str(role) for role in roles if role)
            else:
                role_text = self._compact_text(item.get("role"))
            values.append(f"{label}: {role_text}" if label and role_text else label)
        return self._summary_join(values)

    def _ontology_item_summary(self, value: Any) -> str:
        return self._items_summary(
            value,
            keys=(
                "ontology_id",
                "class_id",
                "relation_id",
                "attribute_id",
                "value_id",
                "canonical_id",
                "field",
                "attribute",
                "relation",
                "surface",
                "mapping_id",
                "id",
            ),
        )

    def _selected_path_summary(self, value: Any) -> str:
        values: list[str] = []
        for item in self._trace_list(value):
            if not isinstance(item, dict):
                continue
            request_id = self._item_value(item, ("request_id", "source_request_id"))
            path_id = self._item_value(item, ("path_id", "selected_path_id", "candidate_path_id"))
            if request_id and path_id:
                values.append(f"{request_id} -> {path_id}")
            else:
                values.append(path_id or request_id)
        return self._summary_join(values)

    def _coreference_summary(self, value: Any) -> str:
        values: list[str] = []
        for item in self._trace_list(value):
            if not isinstance(item, dict):
                continue
            pair_id = self._item_value(item, ("candidate_pair_id", "pair_id", "id"))
            decision = self._item_value(item, ("decision", "label", "status"))
            values.append(f"{pair_id}: {decision}" if pair_id and decision else pair_id or decision)
        return self._summary_join(values)

    def _binding_summary(self, value: Any) -> str:
        if isinstance(value, dict):
            values = [f"{key}={self._compact_text(item)}" for key, item in value.items() if self._compact_text(item)]
            return self._summary_join(values)
        values: list[str] = []
        for item in self._trace_list(value):
            if not isinstance(item, dict):
                text = self._compact_text(item)
                if text:
                    values.append(text)
                continue
            result = self._trace_object(item.get("result"))
            attribute = self._item_value(result, ("attribute", "field", "value", "expression", "name"))
            alias = self._item_value(result, ("alias",))
            if attribute:
                values.append(f"{attribute} AS {alias}" if alias else attribute)
                continue
            values.append(self._item_value(item, ("attribute", "ontology_id", "field", "name", "path", "expression", "target", "physical_name", "id")))
        if values:
            return self._summary_join(values)
        return self._items_summary(
            value,
            keys=("attribute", "ontology_id", "field", "name", "path", "expression", "target", "physical_name", "id"),
        )

    def _projection_summary(self, logical_plan: dict[str, Any]) -> str:
        projections = logical_plan.get("projection") or logical_plan.get("projections")
        return self._binding_summary(projections)

    def _validation_checks_summary(self, value: Any) -> str:
        values: list[str] = []
        for item in self._trace_list(value):
            if not isinstance(item, dict):
                continue
            name = self._item_value(item, ("name", "check", "id"))
            accepted = item.get("accepted")
            if accepted is True:
                status = "通过"
            elif accepted is False:
                status = "未通过"
            else:
                status = self._item_value(item, ("status", "decision"))
            values.append(f"{name}: {status}" if name and status else name or status)
        return self._summary_join(values)

    def _cypher_summary(self, value: Any) -> str:
        cypher = self._compact_text(value).strip()
        if not cypher:
            return "未生成"
        return " ".join(line.strip() for line in cypher.splitlines() if line.strip())

    def _ontology_mapping_categories(self, ontology_mapping: dict[str, Any]) -> list[dict[str, Any]]:
        definitions = {
            "ontology_objects": "对象类或语义对象，表示后续查询围绕哪些业务实体展开。",
            "ontology_relation_hints": "关系线索，表示 mention 指向的本体关系或路径连接提示。",
            "ontology_attributes": "属性字段，表示要投影、过滤、排序或聚合的本体字段。",
            "ontology_values": "属性值或枚举值，表示用户问题里的限定值。",
            "evidence": "映射证据，记录 mention 到本体项的来源和中间判断。",
        }
        return [
            {
                "category": key,
                "description_zh": description,
                "items": ontology_mapping.get(key) if isinstance(ontology_mapping.get(key), list) else [],
            }
            for key, description in definitions.items()
        ]

    def _intent_taxonomy_details(self, primary: str, secondary: str) -> dict[str, Any]:
        taxonomy = {
            "record_retrieval_query": {
                "name_zh": "明细/清单查询",
                "description_zh": "返回实体、资源、记录或属性明细，不以统计值、路径结构或布尔判断为最终答案。",
                "secondary": {
                    "entity_list_query": {
                        "name_zh": "实体列表查询",
                        "description_zh": "返回实体、资源或记录列表。",
                    },
                    "entity_detail_query": {
                        "name_zh": "实体详情查询",
                        "description_zh": "返回实体完整信息或较完整字段集合。",
                    },
                    "attribute_projection_query": {
                        "name_zh": "属性投影查询",
                        "description_zh": "返回实体的指定属性。",
                    },
                    "related_record_query": {
                        "name_zh": "关联明细查询",
                        "description_zh": "沿关系或固定路径返回相关实体或属性明细，但不把图结构本身作为答案。",
                    },
                },
            },
            "relationship_path_query": {
                "name_zh": "关系/路径查询",
                "description_zh": "返回关系、路径、可达结果或拓扑结构，图结构本身是答案的一部分。",
                "secondary": {},
            },
        }
        primary_payload: dict[str, Any] = {}
        secondary_payload: dict[str, Any] = {}
        item = taxonomy.get(primary)
        if isinstance(item, dict):
            primary_payload = {
                "field": primary,
                "name_zh": item.get("name_zh"),
                "description_zh": item.get("description_zh"),
            }
            secondary_item = self._trace_object(self._trace_object(item.get("secondary")).get(secondary))
            if secondary_item:
                secondary_payload = {
                    "field": secondary,
                    "name_zh": secondary_item.get("name_zh"),
                    "description_zh": secondary_item.get("description_zh"),
                }
        return {
            "primary_name_zh": primary_payload.get("name_zh") or "未记录",
            "secondary_name_zh": secondary_payload.get("name_zh") or "未记录",
            "primary": primary_payload or {"field": primary or "未记录", "description_zh": "未记录"},
            "secondary": secondary_payload or {"field": secondary or "未记录", "description_zh": "未记录"},
        }

    def _intent_explanation(self, value: Any) -> str:
        payload = value if isinstance(value, dict) else {}
        name = str(payload.get("name_zh") or "未记录")
        description = str(payload.get("description_zh") or "未记录")
        return f"{name}\n说明：{description}"

    def _generation_llm_prompts(self, snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
        if self._is_cga_trace_v2(snapshot):
            return self._generation_llm_prompts_v2(snapshot)

        raw_prompts = snapshot.get("llm_prompts") if isinstance(snapshot.get("llm_prompts"), dict) else {}
        raw_responses = snapshot.get("llm_responses") if isinstance(snapshot.get("llm_responses"), dict) else {}

        def prompt_item(key: str, title_zh: str, raw_output_title_zh: str) -> dict[str, Any]:
            raw_prompt = raw_prompts.get(key)
            prompt = raw_prompt.strip() if isinstance(raw_prompt, str) else ""
            raw_response = raw_responses.get(key)
            raw_output = raw_response.strip() if isinstance(raw_response, str) else ""
            return {
                "key": key,
                "title_zh": title_zh,
                "raw_output_title_zh": raw_output_title_zh,
                "triggered": bool(prompt),
                "prompt": prompt,
                "raw_output": raw_output,
                "empty_label_zh": "本次未触发",
                "empty_raw_output_label_zh": "本次未触发或未记录返回",
            }

        return {
            "intent_recognition_fallback": prompt_item(
                "intent_recognition_fallback",
                "意图识别 LLM 兜底提示词",
                "意图识别 LLM 原始返回",
            ),
            "cypher_generation_fallback": prompt_item(
                "cypher_generation_fallback",
                "Renderer 失败后的 Cypher 兜底提示词",
                "Cypher 生成 LLM 原始返回",
            ),
        }

    def _generation_llm_prompts_v2(self, snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
        if self._is_ontology_cga_trace(snapshot):
            return self._ontology_generation_llm_prompts(snapshot)

        intent_recognition = self._trace_object(snapshot.get("intent_recognition"))
        intent_diagnostics = self._trace_object(intent_recognition.get("diagnostics"))
        semantic_view_matching = self._trace_object(snapshot.get("semantic_view_matching"))
        semantic_result = self._trace_object(semantic_view_matching.get("result"))
        semantic_trace = self._semantic_view_trace(semantic_view_matching, semantic_result)
        generation = self._trace_object(snapshot.get("generation"))
        return {
            "intent_primary_classification": self._llm_prompt_item_from_attempts(
                "intent_primary_classification",
                "意图识别：一级分类 LLM 判定",
                "意图识别：一级分类 LLM 原始返回",
                intent_diagnostics.get("llm_primary_attempts"),
            ),
            "intent_secondary_classification": self._llm_prompt_item_from_attempts(
                "intent_secondary_classification",
                "意图识别：二级分类 LLM 判定",
                "意图识别：二级分类 LLM 原始返回",
                intent_diagnostics.get("llm_secondary_attempts"),
            ),
            "semantic_view_disambiguation": self._llm_prompt_item_from_attempts(
                "semantic_view_disambiguation",
                "语义视图匹配：受控 LLM 消歧",
                "语义视图匹配：受控 LLM 消歧原始返回",
                semantic_trace.get("llm_disambiguation_attempts"),
            ),
            "cypher_generation_fallback": self._llm_prompt_item_from_attempts(
                "cypher_generation_fallback",
                "Renderer 失败后的 Cypher 兜底生成",
                "Cypher 兜底生成 LLM 原始返回",
                generation.get("cypher_fallback_llm"),
            ),
        }

    def _ontology_generation_llm_prompts(self, snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
        intent_output = self._trace_object(snapshot.get("intent"))
        intent_diagnostics = self._trace_object(intent_output.get("diagnostics"))
        llm_stages = intent_diagnostics.get("llm_stages")
        if not isinstance(llm_stages, list):
            llm_stages = []
        object_role_selection = self._trace_object(snapshot.get("object_role_selection"))
        ontology_path_selection = self._trace_object(snapshot.get("ontology_path_selection"))
        coreference = self._trace_object(snapshot.get("coreference"))
        return {
            "intent_primary_classification": self._llm_prompt_item_from_attempts(
                "intent_primary_classification",
                "一层意图 LLM 判定",
                "一层意图 LLM 原始输出",
                self._intent_stage_attempt(llm_stages, 0, "intent.primary"),
            ),
            "intent_secondary_classification": self._llm_prompt_item_from_attempts(
                "intent_secondary_classification",
                "二层意图 LLM 判定",
                "二层意图 LLM 原始输出",
                self._intent_stage_attempt(llm_stages, 1, "intent.secondary"),
            ),
            "object_role_selection": self._llm_prompt_item_from_attempts(
                "object_role_selection",
                "3.1 对象提取与角色标注 LLM 输入提示词",
                "3.1 对象提取与角色标注 LLM 原始输出",
                self._single_llm_attempt(
                    "object_role_selection",
                    object_role_selection.get("llm_prompt"),
                    object_role_selection.get("llm_raw_output"),
                ),
            ),
            "ontology_path_selection": self._llm_prompt_item_from_attempts(
                "ontology_path_selection",
                "3.3 本体路径选择 LLM 输入提示词",
                "3.3 本体路径选择 LLM 原始输出",
                self._single_llm_attempt(
                    "ontology_path_selection",
                    ontology_path_selection.get("llm_prompt"),
                    ontology_path_selection.get("llm_raw_output"),
                ),
            ),
            "coreference_selection": self._llm_prompt_item_from_attempts(
                "coreference_selection",
                "3.4 指代消解选择 LLM 输入提示词",
                "3.4 指代消解选择 LLM 原始输出",
                self._coreference_llm_attempts(coreference),
            ),
        }

    def _intent_stage_attempt(self, stages: list[Any], index: int, stage_name: str) -> dict[str, Any] | None:
        if index >= len(stages) or not isinstance(stages[index], dict):
            return None
        stage = stages[index]
        return {
            "call_id": f"{stage_name}-{index + 1}",
            "stage": stage_name,
            "prompt": stage.get("rendered_prompt"),
            "raw_output": stage.get("raw_response"),
            "parsed_output": {
                "decision": stage.get("decision"),
                "candidate_id": stage.get("candidate_id"),
            },
        }

    def _single_llm_attempt(self, stage: str, prompt: Any, raw_output: Any) -> dict[str, Any] | None:
        if not prompt and not raw_output:
            return None
        return {"call_id": stage, "stage": stage, "prompt": prompt, "raw_output": raw_output}

    def _coreference_llm_attempts(self, coreference: dict[str, Any]) -> list[dict[str, Any]]:
        traces = coreference.get("llm_decision_traces")
        if not isinstance(traces, list):
            return []
        attempts: list[dict[str, Any]] = []
        for index, trace in enumerate(traces, start=1):
            if not isinstance(trace, dict):
                continue
            attempts.append(
                {
                    "call_id": trace.get("candidate_pair_id") or f"coreference-{index}",
                    "stage": "coreference_selection",
                    "prompt": trace.get("llm_prompt"),
                    "raw_output": trace.get("llm_raw_output"),
                    "parsed_output": trace.get("validated_output"),
                }
            )
        return attempts

    def _llm_attempts_with_stage(self, raw_attempts: Any, stages: set[str]) -> list[dict[str, Any]]:
        attempts = self._llm_attempts(raw_attempts)
        return [attempt for attempt in attempts if attempt.get("stage") in stages]

    def _llm_attempts_without_stage(self, raw_attempts: Any, stages: set[str]) -> list[dict[str, Any]]:
        attempts = self._llm_attempts(raw_attempts)
        return [attempt for attempt in attempts if attempt.get("stage") not in stages]

    def _llm_attempts(self, raw_attempts: Any) -> list[dict[str, Any]]:
        if isinstance(raw_attempts, list):
            return [attempt for attempt in raw_attempts if isinstance(attempt, dict)]
        if isinstance(raw_attempts, dict):
            return [raw_attempts]
        return []

    def _llm_prompt_item_from_attempts(
        self,
        key: str,
        title_zh: str,
        raw_output_title_zh: str,
        raw_attempts: Any,
    ) -> dict[str, Any]:
        attempts = self._llm_attempts(raw_attempts)

        normalized_attempts = []
        for index, attempt in enumerate(attempts, start=1):
            normalized_attempts.append(
                {
                    "call_id": attempt.get("call_id") or f"{key}-{index}",
                    "stage": attempt.get("stage"),
                    "model": attempt.get("model"),
                    "prompt": self._trace_text(attempt.get("prompt_markdown") or attempt.get("prompt")),
                    "raw_output": self._trace_text(
                        attempt.get("raw_output") or attempt.get("raw_text") or attempt.get("output")
                    ),
                    "parsed_output": attempt.get("parsed_output"),
                    "accepted": attempt.get("accepted"),
                    "rejected_reason": attempt.get("rejected_reason"),
                }
            )
        selected = next(
            (
                attempt
                for attempt in reversed(normalized_attempts)
                if attempt.get("prompt") or attempt.get("raw_output")
            ),
            None,
        )
        return {
            "key": key,
            "title_zh": title_zh,
            "raw_output_title_zh": raw_output_title_zh,
            "triggered": bool(normalized_attempts),
            "prompt": selected.get("prompt") if selected else "",
            "raw_output": selected.get("raw_output") if selected else "",
            "attempts": normalized_attempts,
            "empty_label_zh": "本次未触发",
            "empty_raw_output_label_zh": "本次未触发或未记录返回",
        }

    def _generation_chain_summary(
        self,
        *,
        snapshot: dict[str, Any],
        generation_status: str,
        failure_reason: Any,
        gate_passed: bool,
    ) -> dict[str, Any]:
        if self._is_cga_trace_v2(snapshot):
            return self._generation_chain_summary_v2(
                snapshot=snapshot,
                generation_status=generation_status,
                failure_reason=failure_reason,
                gate_passed=gate_passed,
            )

        intent = snapshot.get("intent") if isinstance(snapshot.get("intent"), dict) else {}
        validation = snapshot.get("validation") if isinstance(snapshot.get("validation"), dict) else {}
        selected_knowledge = snapshot.get("selected_knowledge") if isinstance(snapshot.get("selected_knowledge"), dict) else {}
        preflight = snapshot.get("preflight") if isinstance(snapshot.get("preflight"), dict) else {}
        generation_mode = str(snapshot.get("generation_mode") or "")
        preflight_accepted = preflight.get("accepted")
        return {
            "generation_status": {
                "value": generation_status or None,
                "label_zh": self._generation_status_label(generation_status),
            },
            "generation_mode": {
                "value": generation_mode or None,
                "label_zh": self._generation_mode_label(generation_mode),
            },
            "gate": {
                "accepted": gate_passed,
                "label_zh": "生成门禁通过" if gate_passed else "生成门禁未通过",
            },
            "failure_reason": {
                "value": failure_reason,
                "label_zh": self._generation_failure_label(str(failure_reason or "")),
            },
            "intent": {
                "primary_intent": intent.get("primary_intent"),
                "secondary_intent": intent.get("secondary_intent"),
                "source": intent.get("source"),
                "decision": intent.get("decision"),
                "decision_label_zh": self._intent_decision_label(str(intent.get("decision") or "")),
                "confidence": intent.get("confidence"),
            },
            "validation": {
                "accepted": validation.get("accepted"),
                "label_zh": self._accepted_label(validation.get("accepted"), accepted="语义校验通过", rejected="语义校验未通过"),
                "diagnostics": validation.get("diagnostics") or [],
            },
            "knowledge": {
                "source": selected_knowledge.get("source"),
                "source_label_zh": self._knowledge_source_label(str(selected_knowledge.get("source") or "")),
                "selection_trace": selected_knowledge.get("selection_trace") or [],
            },
            "preflight": {
                "accepted": preflight_accepted,
                "label_zh": self._accepted_label(preflight_accepted, accepted="预检通过", rejected="预检未通过"),
                "reason": preflight.get("reason"),
                "reason_label_zh": self._generation_failure_label(str(preflight.get("reason") or "")),
            },
        }

    def _generation_chain_summary_v2(
        self,
        *,
        snapshot: dict[str, Any],
        generation_status: str,
        failure_reason: Any,
        gate_passed: bool,
    ) -> dict[str, Any]:
        if self._is_ontology_cga_trace(snapshot):
            return self._ontology_generation_chain_summary_v2(
                snapshot=snapshot,
                generation_status=generation_status,
                failure_reason=failure_reason,
                gate_passed=gate_passed,
            )

        intent_recognition = self._trace_object(snapshot.get("intent_recognition"))
        intent = self._trace_object(intent_recognition.get("result"))
        semantic_view_matching = self._trace_object(snapshot.get("semantic_view_matching"))
        semantic_result = self._trace_object(semantic_view_matching.get("result"))
        knowledge_selection = self._trace_object(snapshot.get("knowledge_selection"))
        generation = self._trace_object(snapshot.get("generation"))
        renderer = self._trace_object(generation.get("renderer"))
        preflight = self._trace_object(snapshot.get("preflight"))
        generation_mode = "controlled_llm_fallback" if isinstance(generation.get("cypher_fallback_llm"), dict) else ""
        if not generation_mode and renderer.get("family"):
            generation_mode = f"{renderer.get('family')}_renderer"
        preflight_accepted = preflight.get("accepted")
        reason = failure_reason or preflight.get("reason") or renderer.get("failure_reason")
        return {
            "generation_status": {
                "value": generation_status or None,
                "label_zh": self._generation_status_label(generation_status),
            },
            "generation_mode": {
                "value": generation_mode or None,
                "label_zh": self._generation_mode_label(generation_mode),
            },
            "gate": {
                "accepted": gate_passed,
                "label_zh": "生成门禁通过" if gate_passed else "生成门禁未通过",
            },
            "failure_reason": {
                "value": reason,
                "label_zh": self._generation_failure_label(str(reason or "")),
            },
            "intent": {
                "primary_intent": intent.get("primary_intent"),
                "secondary_intent": intent.get("secondary_intent"),
                "source": intent.get("source"),
                "decision": intent.get("decision"),
                "decision_label_zh": self._intent_decision_label(str(intent.get("decision") or "")),
                "confidence": intent.get("confidence"),
            },
            "validation": {
                "accepted": semantic_result.get("accepted"),
                "label_zh": self._accepted_label(
                    semantic_result.get("accepted"),
                    accepted="语义视图匹配通过",
                    rejected="语义视图匹配未通过",
                ),
                "diagnostics": semantic_result.get("diagnostics") or [],
            },
            "knowledge": {
                "source": knowledge_selection.get("source"),
                "source_label_zh": self._knowledge_source_label(str(knowledge_selection.get("source") or "")),
                "selection_trace": knowledge_selection.get("selected_items") or [],
            },
            "preflight": {
                "accepted": preflight_accepted,
                "label_zh": self._accepted_label(preflight_accepted, accepted="预检通过", rejected="预检未通过"),
                "reason": preflight.get("reason"),
                "reason_label_zh": self._generation_failure_label(str(preflight.get("reason") or "")),
            },
        }

    def _ontology_generation_chain_summary_v2(
        self,
        *,
        snapshot: dict[str, Any],
        generation_status: str,
        failure_reason: Any,
        gate_passed: bool,
    ) -> dict[str, Any]:
        intent_output = self._trace_object(snapshot.get("intent"))
        intent = self._trace_object(intent_output.get("intent"))
        validator = self._trace_object(snapshot.get("validator"))
        compiler = self._trace_object(snapshot.get("compiler"))
        reason = failure_reason
        if not reason and validator.get("accepted") is False:
            failed = [
                item
                for item in validator.get("checks", [])
                if isinstance(item, dict) and item.get("accepted") is False
            ]
            reason = failed[0].get("reason") if failed else "validation_failed"
        return {
            "generation_status": {
                "value": generation_status or None,
                "label_zh": self._generation_status_label(generation_status),
            },
            "generation_mode": {
                "value": "ontology_pipeline",
                "label_zh": "本体分层生成链路",
            },
            "gate": {
                "accepted": gate_passed,
                "label_zh": "生成门禁通过" if gate_passed else "生成门禁未通过",
            },
            "failure_reason": {
                "value": reason,
                "label_zh": self._generation_failure_label(str(reason or "")),
            },
            "intent": {
                "primary_intent": intent.get("primary"),
                "secondary_intent": intent.get("secondary"),
                "source": intent.get("source"),
                "decision": intent.get("decision"),
                "decision_label_zh": self._intent_decision_label(str(intent.get("decision") or "")),
                "confidence": intent.get("confidence"),
            },
            "validation": {
                "accepted": validator.get("accepted"),
                "label_zh": self._accepted_label(validator.get("accepted"), accepted="Step 4 校验通过", rejected="Step 4 校验未通过"),
                "diagnostics": validator.get("checks") or [],
            },
            "knowledge": {
                "source": "ontology_assets",
                "source_label_zh": "本体资产",
                "selection_trace": [],
            },
            "preflight": {
                "accepted": bool(compiler.get("cypher")),
                "label_zh": "物理编排已输出" if compiler.get("cypher") else "物理编排未输出",
                "reason": reason,
                "reason_label_zh": self._generation_failure_label(str(reason or "")),
            },
        }

    def _generation_status_label(self, value: str) -> str:
        return {
            "generated": "生成成功",
            "clarification_required": "需要澄清",
            "generation_failed": "生成失败",
            "service_failed": "服务失败",
        }.get(value, "未记录")

    def _generation_mode_label(self, value: str) -> str:
        return {
            "deterministic_renderer": "确定性渲染器",
            "controlled_llm_fallback": "受控大模型兜底",
        }.get(value, "未记录")

    def _generation_failure_label(self, value: str) -> str:
        return {
            "semantic_match_rejected": "语义视图匹配未通过",
            "logical_plan_mismatch": "生成结果与逻辑查询计划不一致",
            "path_planning_failed": "图路径规划失败",
            "cypher_fallback_cannot_generate": "受控模型无法安全生成",
            "ambiguous_path_semantic": "路径语义存在歧义",
            "unbalanced_brackets": "括号未闭合",
            "no_cypher_found": "未找到 Cypher",
            "model_invocation_failed": "模型调用失败",
            "testing_agent_submission_failed": "提交 testing-agent 失败",
        }.get(value, "无" if not value else value)

    def _intent_decision_label(self, value: str) -> str:
        return {
            "accept": "已接受",
            "fallback_embedding": "转入向量召回",
            "fallback_llm": "需要大模型兜底",
            "clarify": "需要澄清",
        }.get(value, "未记录")

    def _knowledge_source_label(self, value: str) -> str:
        return {
            "rag": "RAG 知识选择",
            "file": "本地知识文件",
        }.get(value, "未记录")

    def _accepted_label(self, value: Any, *, accepted: str, rejected: str) -> str:
        if value is True:
            return accepted
        if value is False:
            return rejected
        return "未记录"

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

    def _build_stages(
        self,
        submission: dict[str, Any] | None,
        generation_failure: dict[str, Any] | None,
        ticket: dict[str, Any] | None,
    ) -> dict[str, dict[str, str]]:
        generation_status = self._generation_stage_status(submission, generation_failure)
        evaluation_status = self._evaluation_stage_status(submission, ticket)
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
        }

    def _generation_stage_status(
        self,
        submission: dict[str, Any] | None,
        generation_failure: dict[str, Any] | None,
    ) -> str:
        if generation_failure is not None:
            generation_status = str(generation_failure.get("generation_status") or "")
            if generation_status in {"clarification_required", "unsupported_query_shape"}:
                return generation_status
            return "failed"
        if submission is None:
            return "pending"
        if submission.get("generation_status") == "generated" and submission.get("generated_cypher"):
            return "passed"
        if submission.get("generation_status") in {"generation_failed", "unsupported_query_shape", "service_failed"}:
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
        for stage_key in ["query_generation", "evaluation"]:
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
        generation = stages["query_generation"]["status"]
        if generation in {"clarification_required", "unsupported_query_shape"}:
            return generation
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

    def _read_json_metadata(self, path: Path, keys: set[str]) -> dict[str, Any] | None:
        if not path.exists():
            return None
        result: dict[str, Any] = {
            "_metadata_only": True,
            "_source_path": str(path),
        }
        remaining = set(keys)
        saw_snapshot_line = False
        try:
            with path.open("r", encoding="utf-8") as file:
                line_iter = iter(file)
                for line in line_iter:
                    match = self._TOP_LEVEL_JSON_FIELD_RE.match(line)
                    if match is None:
                        continue
                    key = match.group("key")
                    raw_value = match.group("value")
                    if key == "input_prompt_snapshot":
                        saw_snapshot_line = True
                        if "_trace_profile_hint" not in result:
                            result["_trace_profile_hint"] = self._trace_profile_hint_from_snapshot_line(raw_value)
                        duration_ms = self._cga_duration_ms_from_snapshot_line(raw_value)
                        if duration_ms is not None:
                            result["_cga_duration_ms_hint"] = duration_ms
                    if key not in remaining:
                        if not remaining and saw_snapshot_line:
                            break
                        continue
                    parsed = self._parse_top_level_json_value(raw_value, line_iter)
                    if parsed is _UNPARSED_JSON_VALUE:
                        continue
                    result[key] = parsed
                    remaining.remove(key)
                    if not remaining and saw_snapshot_line:
                        break
        except OSError:
            return None
        return result

    def _trace_profile_hint_from_snapshot_line(self, raw_value: str) -> str:
        if "cga_graph_trace_v1" in raw_value:
            return "graph"
        if "cga_trace_v2" in raw_value or "trace_profile" in raw_value and "ontology" in raw_value:
            return "ontology"
        return ""

    def _parse_top_level_json_value(self, raw_value: str, line_iter: Any) -> Any:
        decoder = json.JSONDecoder()
        buffer = raw_value.strip()
        while True:
            candidate = buffer.rstrip()
            if candidate.endswith(","):
                candidate = candidate[:-1].rstrip()
            try:
                value, end_index = decoder.raw_decode(candidate)
            except json.JSONDecodeError:
                pass
            else:
                if not candidate[end_index:].strip():
                    return value
            try:
                buffer += next(line_iter)
            except StopIteration:
                return _UNPARSED_JSON_VALUE


_UNPARSED_JSON_VALUE = object()
