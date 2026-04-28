from __future__ import annotations

from typing import Any


class RepairWorkflowService:
    def __init__(self, repair_service, qa_redispatch_gateway, module_logs=None) -> None:
        self.repair_service = repair_service
        self.qa_redispatch_gateway = qa_redispatch_gateway
        self.module_logs = module_logs

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
        changes = self.repair_service.apply(suggestion, knowledge_types)
        redispatch_result = self.qa_redispatch_gateway.redispatch(qa_id)
        result = {"changes": changes, "redispatch": redispatch_result}
        if self.module_logs is not None:
            self.module_logs.append(
                module="repair",
                level="info",
                operation="repair_workflow_completed",
                trace_id=qa_id,
                status="success",
                response_body=result,
            )
        return result
