from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any, Dict, Optional, Protocol

from .models import (
    IssueTicket,
    KnowledgeRepairSuggestionRequest,
    KnowledgeType,
    RepairAnalysisRecord,
    RepairIssueTicketResponse,
)

from .analysis import DiagnosisContext, RepairAnalyzer
from .clients import KnowledgeOpsRepairApplyClient, OpenAICompatibleRepairAnalyzer
from .config import Settings, get_settings
from .repository import RepairRepository, _utc_now

logger = logging.getLogger("repair_service")


def _coerce_model_payload(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


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
                analysis_id=existing.analysis_id,
                id=existing.id,
                knowledge_repair_request=_coerce_model_payload(existing.knowledge_repair_request),
                knowledge_ops_response=existing.knowledge_ops_response,
                applied=existing.applied,
            )
        prompt_snapshot = _prompt_snapshot_from_issue_ticket(issue_ticket)
        analysis = await self.analyzer.analyze(issue_ticket, prompt_snapshot)
        request = analysis.to_request()
        knowledge_ops_response = await self.apply_client.apply(request)

        record = RepairAnalysisRecord(
            analysis_id=self._analysis_id_for_ticket(issue_ticket.ticket_id),
            ticket_id=issue_ticket.ticket_id,
            id=issue_ticket.id,
            prompt_snapshot=prompt_snapshot,
            knowledge_repair_request=_coerce_model_payload(request),
            knowledge_ops_response=knowledge_ops_response,
            confidence=analysis.confidence,
            rationale=analysis.rationale,
            used_experiments=analysis.used_experiments,
            primary_knowledge_type=analysis.primary_knowledge_type,
            secondary_knowledge_types=analysis.secondary_knowledge_types,
            candidate_patch_types=analysis.candidate_patch_types,
            validation_mode=analysis.validation_mode,
            validation_result=analysis.validation_result,
            diagnosis_context_summary=analysis.diagnosis_context_summary,
            created_at=_utc_now(),
            applied_at=_utc_now(),
        )
        self.repository.save_analysis(record)
        return RepairIssueTicketResponse(
            analysis_id=record.analysis_id,
            id=record.id,
            knowledge_repair_request=_coerce_model_payload(record.knowledge_repair_request),
            knowledge_ops_response=record.knowledge_ops_response,
            applied=record.applied,
        )

    def get_analysis(self, analysis_id: str) -> Optional[RepairAnalysisRecord]:
        return self.repository.get_analysis(analysis_id)

    def get_service_status(self) -> Dict[str, object]:
        settings = self.settings or get_settings()
        return {
            "storage": settings.data_dir,
            "knowledge_agent_apply_url": settings.knowledge_ops_repairs_apply_url,
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
    if isinstance(evidence_prompt, str) and evidence_prompt:
        return evidence_prompt
    fallback_prompt = getattr(issue_ticket, "input_prompt_snapshot", "")
    return fallback_prompt if isinstance(fallback_prompt, str) else ""


def _build_analyzer(settings: Settings) -> RepairAnalyzer:
    return RepairAnalyzer(
        diagnosis_client=OpenAICompatibleRepairAnalyzer(
            base_url=settings.llm_base_url or "",
            api_key=settings.llm_api_key or "",
            model=settings.llm_model_name or "",
            timeout_seconds=settings.request_timeout_seconds,
            temperature=settings.llm_temperature,
            max_retries=settings.llm_max_retries,
            retry_base_delay_seconds=settings.llm_retry_base_delay_seconds,
            max_concurrency=settings.llm_max_concurrency,
        ),
        experiment_runner=_lightweight_experiment_runner,
    )


async def _lightweight_experiment_runner(
    ticket: IssueTicket,
    context: DiagnosisContext,
    patch_type: KnowledgeType,
    diagnosis: Dict[str, Any],
) -> Dict[str, Any]:
    del diagnosis
    failure_diff = context.get("failure_diff", {})
    relevant_fragments = context.get("relevant_prompt_fragments", {})
    recent_repairs = context.get("recent_applied_repairs", [])

    explanatory_power = _patch_type_matches_failure_diff(patch_type, failure_diff)
    duplicate_repair = any(repair.get("knowledge_type") == patch_type for repair in recent_repairs if isinstance(repair, dict))
    fragment_conflict = _fragment_already_covers_patch(patch_type, relevant_fragments)
    improved = explanatory_power and not duplicate_repair and not fragment_conflict
    confidence = 0.86 if improved else 0.25
    return {
        "improved": improved,
        "confidence": confidence,
        "reason": _build_validation_reason(patch_type, explanatory_power, duplicate_repair, fragment_conflict, ticket.id),
    }


def _patch_type_matches_failure_diff(patch_type: KnowledgeType, failure_diff: Dict[str, Any]) -> bool:
    if patch_type == "cypher_syntax":
        return bool(failure_diff.get("syntax_problem") or failure_diff.get("execution_problem"))
    if patch_type == "few_shot":
        return bool(
            failure_diff.get("entity_or_relation_problem")
            or failure_diff.get("return_shape_problem")
            or failure_diff.get("ordering_problem")
            or failure_diff.get("limit_problem")
        )
    if patch_type == "business_knowledge":
        return bool(failure_diff.get("entity_or_relation_problem"))
    if patch_type == "system_prompt":
        return bool(failure_diff.get("return_shape_problem") or failure_diff.get("ordering_problem") or failure_diff.get("limit_problem"))
    return False


def _fragment_already_covers_patch(patch_type: KnowledgeType, relevant_fragments: Dict[str, Any]) -> bool:
    fragment_map = {
        "cypher_syntax": "system_rules_fragment",
        "few_shot": "few_shot_fragment",
        "system_prompt": "system_rules_fragment",
        "business_knowledge": "business_knowledge_fragment",
    }
    fragment = relevant_fragments.get(fragment_map.get(patch_type, ""), "")
    return isinstance(fragment, str) and bool(fragment.strip())


def _build_validation_reason(
    patch_type: KnowledgeType,
    explanatory_power: bool,
    duplicate_repair: bool,
    fragment_conflict: bool,
    qa_id: str,
) -> str:
    if duplicate_repair:
        return f"{patch_type} already appears in recent repairs for {qa_id}"
    if fragment_conflict:
        return f"{patch_type} is already represented in the current prompt fragments"
    if explanatory_power:
        return f"{patch_type} best explains the current failure diff"
    return f"{patch_type} does not explain the current failure diff"


def build_repair_service(settings: Settings) -> RepairService:
    return RepairService(
        repository=RepairRepository(data_dir=settings.data_dir),
        analyzer=_build_analyzer(settings),
        apply_client=KnowledgeOpsRepairApplyClient(
            apply_url=settings.knowledge_ops_repairs_apply_url,
            capture_dir=settings.knowledge_ops_repairs_apply_capture_dir,
            timeout_seconds=settings.request_timeout_seconds,
        ),
        settings=settings,
    )


@lru_cache(maxsize=1)
def get_repair_service() -> RepairService:
    return build_repair_service(get_settings())
