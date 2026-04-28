from __future__ import annotations

from typing import Any

import httpx

from app.config import settings


class QARedispatchGateway:
    def __init__(self, client: httpx.Client | None = None, module_logs=None) -> None:
        self.client = client or httpx.Client(timeout=10)
        self.module_logs = module_logs

    def redispatch(self, qa_id: str) -> dict[str, Any]:
        url = f"{settings.qa_agent_base_url.rstrip('/')}/qa/{qa_id}/redispatch"
        request_body = {"qa_id": qa_id}
        if self.module_logs is not None:
            self.module_logs.append(
                module="redispatch",
                level="info",
                operation="qa_agent_redispatch_requested",
                trace_id=qa_id,
                status="started",
                request_body={"url": url, **request_body},
            )
        response = self.client.post(url, json=request_body)
        payload = response.json()
        if self.module_logs is not None:
            self.module_logs.append(
                module="redispatch",
                level="info" if response.is_success else "error",
                operation="qa_agent_redispatch_completed",
                trace_id=qa_id,
                status=payload.get("status", "error") if isinstance(payload, dict) else "error",
                request_body={"url": url, **request_body},
                response_body=payload,
                http_status=response.status_code,
            )
        response.raise_for_status()
        return payload
