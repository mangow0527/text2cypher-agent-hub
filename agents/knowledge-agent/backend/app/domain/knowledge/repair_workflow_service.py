from __future__ import annotations

from typing import Any

from app.domain.knowledge.redispatch_result import skipped_redispatch_result


class RepairWorkflowService:
    def __init__(self, repair_service, qa_redispatch_gateway=None, module_logs=None, repair_agent_runtime=None) -> None:
        self.repair_service = repair_service
        self.module_logs = module_logs
        self.repair_agent_runtime = repair_agent_runtime

    def apply(self, qa_id: str, suggestion: str, knowledge_types: list[str] | None) -> dict[str, Any]:
        if self.module_logs is not None:
            self.module_logs.append(
                module="repair",
                level="info",
                operation="repair_workflow_started",
                trace_id=qa_id,
                status="started",
                request_body={
                    "qa_id": qa_id,
                    "suggestion": suggestion,
                    "knowledge_types": knowledge_types or [],
                },
            )
        if self.repair_agent_runtime is None:
            raise RuntimeError("repair_agent_runtime is required for legacy repair apply workflow")
        agent_run = self.repair_agent_runtime.create_review_run_from_legacy_apply(qa_id, suggestion, knowledge_types)
        redispatch_result = skipped_redispatch_result(qa_id)
        result = {"changes": [], "redispatch": redispatch_result, "agent_run": agent_run}
        if self.module_logs is not None:
            self.module_logs.append(
                module="repair",
                level="info",
                operation="repair_workflow_completed",
                trace_id=qa_id,
                status="success",
                response_body={
                    "change_count": 0,
                    "redispatch_status": redispatch_result["status"],
                    "agent_run_id": agent_run.run_id,
                    "agent_run_status": agent_run.status.value,
                },
            )
        return result
