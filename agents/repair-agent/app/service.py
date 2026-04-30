from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any, Dict, Optional, Protocol

from .models import (
    IssueTicket,
    KnowledgeRepairSuggestionRequest,
    RepairAnalysisRecord,
    RepairIssueTicketResponse,
)

from .analysis import RepairAnalyzer
from .clients import KnowledgeAgentRepairApplyClient, OpenAIChatCompletionRepairAnalyzer
from .config import Settings, get_settings
from .repository import RepairRepository, _utc_now

logger = logging.getLogger("repair_service")


def _coerce_model_payload(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def _analysis_text(value: Any, field_name: str) -> str:
    field_value = getattr(value, field_name, "")
    return field_value if isinstance(field_value, str) else ""


class KnowledgeRepairApplier(Protocol):
    async def apply(self, payload: KnowledgeRepairSuggestionRequest) -> Dict[str, object] | None:
        ...


class RepairService:
    def __init__(
        self,
        repository: RepairRepository,
        analyzer: RepairAnalyzer,
        apply_client: KnowledgeRepairApplier,
        settings: Optional[Settings] = None,
    ) -> None:
        self.repository = repository
        self.analyzer = analyzer
        self.apply_client = apply_client
        self.settings = settings

    async def create_issue_ticket_response(self, issue_ticket: IssueTicket) -> RepairIssueTicketResponse:
        existing = self.repository.get_analysis(self._analysis_id_for_ticket(issue_ticket.ticket_id))
        if existing is not None:
            return RepairIssueTicketResponse(
                status=existing.status,
                analysis_id=existing.analysis_id,
                id=existing.id,
                knowledge_repair_request=_coerce_model_payload(existing.knowledge_repair_request),
                knowledge_agent_response=existing.knowledge_agent_response,
                applied=existing.applied,
            )
        prompt_snapshot = _prompt_snapshot_from_issue_ticket(issue_ticket)
        analysis = await self.analyzer.analyze(issue_ticket, prompt_snapshot)
        if getattr(analysis, "repairable", True) is False:
            record = RepairAnalysisRecord(
                analysis_id=self._analysis_id_for_ticket(issue_ticket.ticket_id),
                ticket_id=issue_ticket.ticket_id,
                id=issue_ticket.id,
                status="not_repairable",
                prompt_snapshot=prompt_snapshot,
                system_prompt_snapshot=analysis.system_prompt_snapshot,
                user_prompt_snapshot=analysis.user_prompt_snapshot,
                raw_output=_analysis_text(analysis, "raw_output"),
                knowledge_repair_request=None,
                knowledge_agent_response=None,
                confidence=analysis.confidence,
                rationale=analysis.rationale,
                primary_knowledge_type=analysis.primary_knowledge_type,
                secondary_knowledge_types=analysis.secondary_knowledge_types,
                diagnosis_context_summary=analysis.diagnosis_context_summary,
                non_repairable_reason=getattr(analysis, "non_repairable_reason", ""),
                applied=False,
                created_at=_utc_now(),
                applied_at="",
            )
            self.repository.save_analysis(record.model_copy(deep=True))
            return RepairIssueTicketResponse(
                status=record.status,
                analysis_id=record.analysis_id,
                id=record.id,
                knowledge_repair_request=None,
                knowledge_agent_response=None,
                applied=record.applied,
            )

        request = analysis.to_request()
        record = RepairAnalysisRecord(
            analysis_id=self._analysis_id_for_ticket(issue_ticket.ticket_id),
            ticket_id=issue_ticket.ticket_id,
            id=issue_ticket.id,
            status="analysis_pending",
            prompt_snapshot=prompt_snapshot,
            system_prompt_snapshot=analysis.system_prompt_snapshot,
            user_prompt_snapshot=analysis.user_prompt_snapshot,
            raw_output=_analysis_text(analysis, "raw_output"),
            knowledge_repair_request=_coerce_model_payload(request),
            knowledge_agent_response=None,
            confidence=analysis.confidence,
            rationale=analysis.rationale,
            primary_knowledge_type=analysis.primary_knowledge_type,
            secondary_knowledge_types=analysis.secondary_knowledge_types,
            diagnosis_context_summary=analysis.diagnosis_context_summary,
            applied=False,
            created_at=_utc_now(),
            applied_at="",
        )
        self.repository.save_analysis(record.model_copy(deep=True))
        try:
            knowledge_agent_response = await self.apply_client.apply(request)
        except Exception:
            record.status = "apply_failed"
            record.knowledge_agent_response = None
            record.applied = False
            record.applied_at = ""
            self.repository.save_analysis(record.model_copy(deep=True))
            raise
        if _is_repair_apply_paused(knowledge_agent_response):
            record.status = "repair_apply_paused"
            record.knowledge_agent_response = knowledge_agent_response
            record.applied = False
            record.applied_at = ""
            self.repository.save_analysis(record.model_copy(deep=True))
            return RepairIssueTicketResponse(
                status=record.status,
                analysis_id=record.analysis_id,
                id=record.id,
                knowledge_repair_request=_coerce_model_payload(record.knowledge_repair_request),
                knowledge_agent_response=record.knowledge_agent_response,
                applied=record.applied,
            )
        record.status = "applied"
        record.knowledge_agent_response = knowledge_agent_response
        record.applied = True
        record.applied_at = _utc_now()
        self.repository.save_analysis(record.model_copy(deep=True))
        return RepairIssueTicketResponse(
            status=record.status,
            analysis_id=record.analysis_id,
            id=record.id,
            knowledge_repair_request=_coerce_model_payload(record.knowledge_repair_request),
            knowledge_agent_response=record.knowledge_agent_response,
            applied=record.applied,
        )

    def get_analysis(self, analysis_id: str) -> Optional[RepairAnalysisRecord]:
        return self.repository.get_analysis(analysis_id)

    def get_service_status(self) -> Dict[str, object]:
        settings = self.settings or get_settings()
        return {
            "storage": settings.data_dir,
            "knowledge_agent_apply_url": settings.knowledge_agent_repairs_apply_url,
            "llm_enabled": settings.llm_enabled,
            "llm_model": settings.llm_model_name,
            "llm_configured": True,
            "mode": "repair_apply",
            "diagnosis_mode": "llm",
        }

    @staticmethod
    def _analysis_id_for_ticket(ticket_id: str) -> str:
        return f"analysis-{ticket_id}"


def _prompt_snapshot_from_issue_ticket(issue_ticket: IssueTicket) -> str:
    evidence_prompt = issue_ticket.generation_evidence.input_prompt_snapshot
    return evidence_prompt if isinstance(evidence_prompt, str) else ""


def _is_repair_apply_paused(response: Dict[str, object] | None) -> bool:
    if response is None:
        return False
    return response.get("status") == "paused" and response.get("code") == "KNOWLEDGE_REPAIR_APPLY_DISABLED"


def _build_analyzer(settings: Settings) -> RepairAnalyzer:
    return RepairAnalyzer(
        diagnosis_client=OpenAIChatCompletionRepairAnalyzer(
            base_url=settings.llm_base_url or "",
            api_key=settings.llm_api_key or "",
            model=settings.llm_model_name or "",
            timeout_seconds=settings.request_timeout_seconds,
            temperature=settings.llm_temperature,
            max_retries=settings.llm_max_retries,
            retry_base_delay_seconds=settings.llm_retry_base_delay_seconds,
            max_concurrency=settings.llm_max_concurrency,
        )
    )


def build_repair_service(settings: Settings) -> RepairService:
    return RepairService(
        repository=RepairRepository(data_dir=settings.data_dir),
        analyzer=_build_analyzer(settings),
        apply_client=KnowledgeAgentRepairApplyClient(
            apply_url=settings.knowledge_agent_repairs_apply_url,
            capture_dir=settings.knowledge_agent_repairs_apply_capture_dir,
            timeout_seconds=settings.request_timeout_seconds,
            max_attempts=settings.knowledge_agent_repairs_apply_max_attempts,
        ),
        settings=settings,
    )


@lru_cache(maxsize=1)
def get_repair_service() -> RepairService:
    return build_repair_service(get_settings())
