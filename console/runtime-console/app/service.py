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
        "clarification_required": "需要澄清",
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
        trace_schema_version = snapshot.get("schema_version") if self._is_cga_trace_v2(snapshot) else None
        failure_reason = source.get("failure_reason") or (generation_failure or {}).get("failure_reason")
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
            "clarification": source.get("clarification") or (generation_failure or {}).get("clarification"),
            "generation_status": generation_status,
            "trace_schema_version": trace_schema_version,
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

    def _is_cga_trace_v2(self, snapshot: dict[str, Any]) -> bool:
        return snapshot.get("schema_version") == "cga_trace_v2"

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
        if "candidate_generation" not in trace and isinstance(candidate_trace, list):
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

    def _llm_prompt_item_from_attempts(
        self,
        key: str,
        title_zh: str,
        raw_output_title_zh: str,
        raw_attempts: Any,
    ) -> dict[str, Any]:
        if isinstance(raw_attempts, list):
            attempts = [attempt for attempt in raw_attempts if isinstance(attempt, dict)]
        elif isinstance(raw_attempts, dict):
            attempts = [raw_attempts]
        else:
            attempts = []

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

    def _build_repair_section(
        self,
        submission: dict[str, Any] | None,
        ticket: dict[str, Any] | None,
        analysis: dict[str, Any] | None,
    ) -> dict[str, Any]:
        repair_response = self._read_repair_response(submission)
        request = (analysis or {}).get("knowledge_repair_request") or {}
        status = (analysis or {}).get("status") or (repair_response or {}).get("status")
        knowledge_agent_response = (analysis or {}).get("knowledge_agent_response")
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
        if knowledge_agent_response is None:
            knowledge_agent_response = not_repairable_response
        return {
            "issue_ticket_id": (ticket or {}).get("ticket_id") or (submission or {}).get("issue_ticket_id"),
            "analysis_id": (analysis or {}).get("analysis_id") or (repair_response or {}).get("analysis_id"),
            "status": status,
            "repair_state": self._repair_state(status=status, analysis=analysis, knowledge_agent_response=knowledge_agent_response),
            "knowledge_apply_state": self._knowledge_apply_state(
                status=status,
                analysis=analysis,
                knowledge_agent_response=knowledge_agent_response,
                request=request or not_repairable_request,
            ),
            "redispatch_state": self._redispatch_state(knowledge_agent_response),
            "non_repairable_reason": non_repairable_reason,
            "llm_prompt_markdown": self._repair_llm_prompt_markdown(analysis),
            "raw_output": (analysis or {}).get("raw_output"),
            "suggestion": request.get("suggestion"),
            "knowledge_types": request.get("knowledge_types") or [],
            "knowledge_agent_request": request or not_repairable_request,
            "knowledge_agent_response": knowledge_agent_response,
            "applied": (analysis or {}).get("applied") if analysis is not None else None,
        }

    def _repair_state(
        self,
        *,
        status: Any,
        analysis: dict[str, Any] | None,
        knowledge_agent_response: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if analysis is None:
            return self._state_item("not_recorded", "未记录", raw_status=status, message="未读取到 repair-agent 诊断记录。")
        status_text = str(status or "")
        decision = self._knowledge_agent_decision(knowledge_agent_response)
        if decision == "human_review":
            return self._state_item(
                "waiting_human_review",
                "等待人工审核",
                raw_status=status,
                message="knowledge-agent 已把修复建议转换为人工审核任务，尚不能视为知识已落库。",
            )
        if decision == "reject":
            return self._state_item(
                "rejected",
                "候选修复被拒绝",
                raw_status=status,
                message="knowledge-agent 校验拒绝了 repair-agent 候选变更。",
            )
        if status_text == "not_repairable":
            return self._state_item("not_repairable", "无需知识修复", raw_status=status, message=analysis.get("non_repairable_reason") or "repair-agent 判定该问题不是知识缺口。")
        if status_text == "repair_apply_paused":
            return self._state_item("apply_paused", "知识应用已暂停", raw_status=status, message="repair-agent 已生成建议，但知识应用当前被配置为暂停。")
        if status_text == "apply_failed":
            return self._state_item("apply_failed", "知识应用失败", raw_status=status, message="repair-agent 诊断完成，但提交 knowledge-agent 失败。")
        if status_text == "analysis_pending":
            return self._state_item("analysis_pending", "诊断中", raw_status=status, message="repair-agent 诊断尚未完成。")
        if status_text == "applied":
            applied = analysis.get("applied")
            return self._state_item(
                "applied" if applied else "suggestion_recorded",
                "知识修复已应用" if applied else "修复建议已记录",
                raw_status=status,
                message="repair-agent 原始状态为 applied；请结合 knowledge apply 与 redispatch 状态判断闭环是否完成。",
            )
        return self._state_item(status_text or "unknown", status_text or "未知状态", raw_status=status)

    def _knowledge_apply_state(
        self,
        *,
        status: Any,
        analysis: dict[str, Any] | None,
        knowledge_agent_response: dict[str, Any] | None,
        request: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if analysis is None:
            return self._state_item("not_started", "未开始", raw_status=status, message="没有可绑定的 repair analysis，无法判断知识应用状态。")
        status_text = str(status or "")
        response_status = str((knowledge_agent_response or {}).get("status") or "")
        response_code = str((knowledge_agent_response or {}).get("code") or "")
        decision = self._knowledge_agent_decision(knowledge_agent_response)
        if status_text == "not_repairable":
            return self._state_item("not_sent", "未发送", raw_status=status, message="该问题被判定为不需要 knowledge-agent 知识应用。")
        if status_text == "apply_failed":
            return self._state_item("apply_failed", "知识应用失败", raw_status=status, message="knowledge-agent 应用请求失败。")
        if status_text == "repair_apply_paused" or response_status == "paused" or response_code == "KNOWLEDGE_REPAIR_APPLY_DISABLED":
            return self._state_item("apply_paused", "知识应用已暂停", raw_status=status, message="knowledge-agent 当前不执行自动落库。")
        if decision == "human_review":
            return self._state_item(
                "waiting_human_review",
                "等待人工审核后落库",
                raw_status=status,
                message="修复建议进入人工审核；通过审核前不视为知识已落库。",
            )
        if decision == "reject":
            return self._state_item(
                "rejected",
                "候选变更被拒绝",
                raw_status=status,
                message="knowledge-agent schema-aware validation 拒绝了候选知识变更。",
            )
        if response_status == "ok" and analysis.get("applied"):
            return self._state_item("applied", "知识应用已确认", raw_status=status, message="knowledge-agent 返回 ok，且 repair analysis 标记为 applied。")
        if request:
            return self._state_item("pending", "等待 knowledge-agent 响应", raw_status=status)
        return self._state_item("not_sent", "未发送", raw_status=status)

    def _redispatch_state(self, knowledge_agent_response: dict[str, Any] | None) -> dict[str, Any]:
        response = knowledge_agent_response if isinstance(knowledge_agent_response, dict) else {}
        redispatch = response.get("redispatch") if isinstance(response.get("redispatch"), dict) else {}
        dispatch = redispatch.get("dispatch") if isinstance(redispatch.get("dispatch"), dict) else {}
        status = str(redispatch.get("status") or dispatch.get("status") or "")
        reason = str(dispatch.get("reason") or redispatch.get("reason") or "")
        if reason == "knowledge_agent_no_longer_redispatches_qa":
            return self._state_item(
                "cancelled",
                "QA 自动重派发已取消",
                raw_status=status or None,
                reason=reason,
                message="redispatch 不再由 knowledge-agent 触发；未重派发不代表知识修复失败。",
            )
        if not redispatch:
            return self._state_item("not_recorded", "未记录", message="knowledge-agent 响应中没有 redispatch 记录。")
        if status == "skipped":
            return self._state_item("skipped", "未重派发", raw_status=status, reason=reason or None)
        if status:
            return self._state_item(status, status, raw_status=status, reason=reason or None)
        return self._state_item("unknown", "未知状态", reason=reason or None)

    def _knowledge_agent_decision(self, knowledge_agent_response: dict[str, Any] | None) -> str:
        response = knowledge_agent_response if isinstance(knowledge_agent_response, dict) else {}
        agent_run = response.get("agent_run") if isinstance(response.get("agent_run"), dict) else {}
        decision = agent_run.get("decision") if isinstance(agent_run.get("decision"), dict) else {}
        return str(decision.get("action") or "")

    def _state_item(
        self,
        value: str,
        label_zh: str,
        *,
        raw_status: Any | None = None,
        reason: str | None = None,
        message: str = "",
    ) -> dict[str, Any]:
        return {
            "value": value,
            "label_zh": label_zh,
            "raw_status": raw_status,
            "reason": reason,
            "message": message,
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
        if analysis is None:
            if repair_status in {"failed", "pending"}:
                return repair_status
            if repair_status == "passed":
                return "running"
            return "not_started"
        response = (analysis or {}).get("knowledge_agent_response")
        apply_state = self._knowledge_apply_state(
            status=(analysis or {}).get("status"),
            analysis=analysis,
            knowledge_agent_response=response,
            request=(analysis or {}).get("knowledge_repair_request"),
        )
        if apply_state["value"] == "applied":
            return "passed"
        if apply_state["value"] in {"waiting_human_review", "pending", "apply_paused"}:
            return "running"
        if apply_state["value"] in {"apply_failed", "rejected"}:
            return "failed"
        if apply_state["value"] in {"not_sent", "not_started"}:
            return "not_started"
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
