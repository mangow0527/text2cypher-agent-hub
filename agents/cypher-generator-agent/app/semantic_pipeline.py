from __future__ import annotations

import json
from dataclasses import dataclass, field
from dataclasses import asdict, is_dataclass, replace
from functools import lru_cache
from typing import Any

import yaml

from . import resource_paths
from .clients import CypherLLMClient, OpenAIChatCompletionCypherGenerator
from .cypher_renderer import CypherRenderer
from .intent_recognition import IntentRecognitionResult, get_hybrid_intent_recognizer
from .knowledge_selection import KnowledgeSelector, SelectedKnowledgeContext
from .logical_planner import LogicalQueryPlan, LogicalQueryPlanner, SchemaPathPlan
from .models import GenerationFailureReason, PreflightCheck
from .config import get_settings
from .parser import parse_model_output
from .prompt_runtime import (
    render_controlled_semantic_prompt,
    render_intent_primary_candidate_prompt,
    render_intent_primary_full_prompt,
    render_intent_secondary_candidate_prompt,
    render_intent_secondary_full_prompt,
    render_semantic_view_disambiguation_prompt,
)
from .semantic_cypher_preflight import run_semantic_cypher_preflight
from .graph_semantic_view import get_default_graph_semantic_view
from .semantic_query import SemanticQuerySpec
from .semantic_view_matching import SemanticMatchResult, SemanticViewMatcher, SemanticViewMatchingTrace


def _empty_llm_prompts() -> dict[str, str | None]:
    return {
        "cypher_generation_fallback": None,
    }


def _empty_llm_responses() -> dict[str, str | None]:
    return {
        "cypher_generation_fallback": None,
    }


@dataclass(frozen=True)
class SemanticDiagnostic:
    code: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class SemanticValidationResult:
    accepted: bool
    diagnostics: list[SemanticDiagnostic] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "accepted": self.accepted,
            "diagnostics": [diagnostic.to_dict() for diagnostic in self.diagnostics],
        }


@dataclass(frozen=True)
class SemanticParseResult:
    id: str | None
    question: str
    generation_run_id: str | None
    generation_mode: str | None
    intent: IntentRecognitionResult
    validation: SemanticValidationResult
    semantic_query: SemanticQuerySpec | None
    generated_cypher: str | None
    preflight: object | None
    semantic_view_matching: SemanticViewMatchingTrace | None = None
    logical_plan: LogicalQueryPlan | None = None
    schema_path_planning: SchemaPathPlan | None = None
    selected_knowledge: SelectedKnowledgeContext | None = None
    clarification: dict[str, object] | None = None
    llm_prompts: dict[str, str | None] = field(default_factory=_empty_llm_prompts)
    llm_responses: dict[str, str | None] = field(default_factory=_empty_llm_responses)

    def to_dict(self) -> dict[str, object]:
        generation = _generation_trace(
            generation_mode=self.generation_mode,
            generated_cypher=self.generated_cypher,
            llm_prompts=self.llm_prompts,
            llm_responses=self.llm_responses,
        )
        return {
            "schema_version": "cga_trace_v2",
            "id": self.id,
            "question": self.question,
            "generation_run_id": self.generation_run_id,
            "generation_status": _trace_generation_status(self),
            "service_context": _service_context_trace(self.semantic_view_matching is not None),
            "intent_recognition": _intent_trace(self.intent, self.llm_prompts, self.llm_responses),
            "semantic_view_matching": (
                self.semantic_view_matching.to_dict() if self.semantic_view_matching is not None else None
            ),
            "logical_query_plan": self.logical_plan.to_dict() if self.logical_plan is not None else None,
            "schema_path_planning": (
                self.schema_path_planning.to_dict() if self.schema_path_planning is not None else None
            ),
            "knowledge_selection": self.selected_knowledge.to_dict() if self.selected_knowledge is not None else None,
            "generation": generation,
            "clarification": self.clarification,
            "generation_mode": self.generation_mode,
            "intent": self.intent.to_dict(),
            "validation": self.validation.to_dict(),
            "semantic_query": self.semantic_query.to_dict() if self.semantic_query is not None else None,
            "generated_cypher": self.generated_cypher,
            "preflight": _to_dict(self.preflight),
        }


class SemanticPipeline:
    def __init__(
        self,
        *,
        semantic_view: object | None = None,
        renderer: CypherRenderer | None = None,
        llm_client: Any | None = None,
        knowledge_selector: KnowledgeSelector | None = None,
    ) -> None:
        self.semantic_view = semantic_view or get_default_graph_semantic_view()
        self.renderer = renderer or CypherRenderer()
        self.llm_client = llm_client
        self.knowledge_selector = knowledge_selector
        self.semantic_view_matcher = SemanticViewMatcher(self.semantic_view)
        self.logical_planner = LogicalQueryPlanner(self.semantic_view)

    def parse(
        self,
        *,
        id: str | None = None,
        question: str,
        generation_run_id: str | None = None,
        intent_result: IntentRecognitionResult | None = None,
    ) -> SemanticParseResult:
        context = self._build_context(
            id=id,
            question=question,
            generation_run_id=generation_run_id,
            intent_result=intent_result,
        )
        if isinstance(context, SemanticParseResult):
            return context
        cypher = self.renderer.render(context.semantic_query)
        preflight = run_semantic_cypher_preflight(cypher, semantic_query=context.semantic_query)
        return context.to_result(
            generation_mode="deterministic_renderer",
            generated_cypher=cypher,
            preflight=preflight,
        )

    async def parse_with_fallback(
        self,
        *,
        id: str | None = None,
        question: str,
        generation_run_id: str | None = None,
        intent_result: IntentRecognitionResult | None = None,
    ) -> SemanticParseResult:
        context = self._build_context(
            id=id,
            question=question,
            generation_run_id=generation_run_id,
            intent_result=intent_result,
        )
        if isinstance(context, SemanticParseResult):
            if context.intent.decision == "fallback_llm":
                return await self._fallback_intent_recognition_to_llm(base_result=context)
            if context.semantic_view_matching is not None and context.semantic_view_matching.result.needs_clarification:
                return await self._fallback_semantic_view_disambiguation_to_llm(base_result=context)
            return context
        context = context.with_selected_knowledge(await self._select_knowledge(context))
        try:
            cypher = self.renderer.render(context.semantic_query)
        except Exception as exc:
            return await self._fallback_to_llm(context=context, renderer_error=str(exc))
        preflight = run_semantic_cypher_preflight(cypher, semantic_query=context.semantic_query)
        if not preflight.accepted:
            return await self._fallback_to_llm(
                context=context,
                renderer_error=f"semantic preflight failed: {preflight.reason or 'unknown'}",
            )
        return context.to_result(
            generation_mode="deterministic_renderer",
            generated_cypher=cypher,
            preflight=preflight,
        )

    def _build_context(
        self,
        *,
        id: str | None,
        question: str,
        generation_run_id: str | None,
        intent_result: IntentRecognitionResult | None,
    ) -> "SemanticParseResult | _SemanticPipelineContext":
        intent = intent_result or get_hybrid_intent_recognizer().recognize(question)
        if intent.decision != "accept" or intent.primary_intent is None or intent.secondary_intent is None:
            return SemanticParseResult(
                id=id,
                question=question,
                generation_run_id=generation_run_id,
                generation_mode=None,
                intent=intent,
                validation=SemanticValidationResult(
                    accepted=False,
                    diagnostics=[
                        SemanticDiagnostic(
                            code="intent_not_accepted",
                            message="Intent recognition did not accept this question for deterministic semantic parsing.",
                        )
                    ],
                ),
                semantic_query=None,
                semantic_view_matching=None,
                logical_plan=None,
                schema_path_planning=None,
                generated_cypher=None,
                preflight=None,
            )

        return self._try_semantic_view_pipeline(
            id=id,
            question=question,
            generation_run_id=generation_run_id,
            intent=intent,
        )

    def _try_semantic_view_pipeline(
        self,
        *,
        id: str | None,
        question: str,
        generation_run_id: str | None,
        intent: IntentRecognitionResult,
    ) -> "SemanticParseResult | _SemanticPipelineContext":
        if intent.primary_intent not in {
            "record_retrieval_query",
            "metric_query",
            "breakdown_query",
            "ranking_query",
            "existence_query",
            "relationship_path_query",
            "set_operation_query",
        }:
            return SemanticParseResult(
                id=id,
                question=question,
                generation_run_id=generation_run_id,
                generation_mode=None,
                intent=intent,
                validation=SemanticValidationResult(
                    accepted=False,
                    diagnostics=[
                        SemanticDiagnostic(
                            code="unsupported_semantic_view_intent",
                            message="Intent is accepted but has no semantic-view planner support.",
                        )
                    ],
                ),
                semantic_query=None,
                semantic_view_matching=None,
                logical_plan=None,
                schema_path_planning=None,
                generated_cypher=None,
                preflight=None,
            )
        matching = self.semantic_view_matcher.match(question)
        if matching.result.needs_clarification:
            clarification = _clarification_payload(matching.result)
            return SemanticParseResult(
                id=id,
                question=question,
                generation_run_id=generation_run_id,
                generation_mode=None,
                intent=intent,
                validation=SemanticValidationResult(
                    accepted=False,
                    diagnostics=[
                        SemanticDiagnostic(
                            code="clarification_required",
                            message="Semantic view matching requires a user clarification.",
                        )
                    ],
                ),
                semantic_query=None,
                semantic_view_matching=matching,
                logical_plan=None,
                schema_path_planning=None,
                generated_cypher=None,
                preflight=None,
                clarification=clarification,
            )
        if not matching.result.accepted:
            return SemanticParseResult(
                id=id,
                question=question,
                generation_run_id=generation_run_id,
                generation_mode=None,
                intent=intent,
                validation=SemanticValidationResult(
                    accepted=False,
                    diagnostics=[
                        SemanticDiagnostic(
                            code="semantic_match_rejected",
                            message=matching.result.rejection_reason or "Semantic view matching did not produce an accepted candidate.",
                        )
                    ],
                ),
                semantic_query=None,
                semantic_view_matching=matching,
                logical_plan=None,
                schema_path_planning=None,
                generated_cypher=None,
                preflight=None,
            )
        planning = self.logical_planner.plan(
            question=question,
            intent_result=intent,
            semantic_match=matching.result,
            generation_run_id=generation_run_id,
        )
        return _SemanticPipelineContext(
            id=id,
            question=question,
            generation_run_id=generation_run_id,
            intent=intent,
            validation=SemanticValidationResult(accepted=True),
            semantic_query=planning.semantic_query,
            semantic_view_matching=matching,
            logical_plan=planning.logical_plan,
            schema_path_planning=planning.schema_path_plan,
        )

    async def _select_knowledge(self, context: "_SemanticPipelineContext") -> SelectedKnowledgeContext | None:
        if self.knowledge_selector is None:
            return None
        try:
            return await self.knowledge_selector.select(
                question=context.question,
                intent_result=context.intent,
                semantic_query=context.semantic_query,
            )
        except Exception as exc:
            return SelectedKnowledgeContext(
                fragments=[],
                prompt_context="",
                selection_trace=[f"knowledge selection unavailable: {type(exc).__name__}"],
                size_estimate=0,
                missing_knowledge_signals=["knowledge_selection_unavailable"],
                source="rag",
            )

    async def _fallback_to_llm(
        self,
        *,
        context: "_SemanticPipelineContext",
        renderer_error: str,
    ) -> SemanticParseResult:
        llm_client = self.llm_client or _get_default_llm_client()
        prompt = render_controlled_semantic_prompt(
            question=context.question,
            semantic_query_json=_render_fallback_plan_context(context),
            renderer_error=renderer_error,
            selected_knowledge_context=(
                context.selected_knowledge.prompt_context
                if context.selected_knowledge is not None and context.selected_knowledge.prompt_context.strip()
                else None
            ),
            extra_constraint_reason=_fallback_extra_constraint_reason(renderer_error),
        )
        llm_prompts = {
            **(context.llm_prompts or _empty_llm_prompts()),
            "cypher_generation_fallback": prompt,
        }
        raw_generation = await llm_client.generate_from_prompt(
            task_id=context.id or context.generation_run_id or "semantic-parse",
            question_text=context.question,
            llm_prompt=prompt,
        )
        raw_output = raw_generation.get("raw_output")
        llm_responses = {
            **(context.llm_responses or _empty_llm_responses()),
            "cypher_generation_fallback": raw_output if isinstance(raw_output, str) else None,
        }
        if not isinstance(raw_output, str):
            return context.to_result(
                generation_mode="controlled_llm_fallback",
                generated_cypher=None,
                preflight=PreflightCheck(accepted=False, reason="no_cypher_found"),
                llm_prompts=llm_prompts,
                llm_responses=llm_responses,
            )
        parsed = parse_model_output(raw_output)
        if parsed.reason is not None:
            return context.to_result(
                generation_mode="controlled_llm_fallback",
                generated_cypher=None,
                preflight=PreflightCheck(accepted=False, reason=parsed.reason),
                llm_prompts=llm_prompts,
                llm_responses=llm_responses,
            )
        preflight = run_semantic_cypher_preflight(parsed.parsed_cypher, semantic_query=context.semantic_query)
        return context.to_result(
            generation_mode="controlled_llm_fallback",
            generated_cypher=parsed.parsed_cypher,
            preflight=preflight,
            llm_prompts=llm_prompts,
            llm_responses=llm_responses,
        )

    async def _fallback_intent_recognition_to_llm(
        self,
        *,
        base_result: SemanticParseResult,
    ) -> SemanticParseResult:
        llm_client = self.llm_client or _get_default_llm_client()
        llm_prompts = {**(base_result.llm_prompts or _empty_llm_prompts())}
        llm_responses = {**(base_result.llm_responses or _empty_llm_responses())}
        recognizer = get_hybrid_intent_recognizer()
        embedding_diagnostic = recognizer.embedding_recognizer.diagnose(base_result.question)
        taxonomy = _intent_taxonomy_index()

        primary_candidates = _primary_candidate_cards(embedding_diagnostic)
        if primary_candidates:
            primary_prompt = render_intent_primary_candidate_prompt(
                question=base_result.question,
                candidate_cards=_render_intent_candidate_cards(primary_candidates),
            )
            primary_output = await _call_structured_llm(
                llm_client=llm_client,
                key="intent_primary_candidate",
                prompt=primary_prompt,
                result=base_result,
                llm_prompts=llm_prompts,
                llm_responses=llm_responses,
            )
            primary_allowed = {str(item["primary_intent"]) for item in primary_candidates}
            if primary_output is None:
                return _intent_llm_rejected_result(base_result, llm_prompts, llm_responses, "intent_llm_invalid_output")
            primary_decision = str(primary_output.get("decision") or "")
            if primary_decision == "clarify":
                return _intent_llm_clarification_result(base_result, llm_prompts, llm_responses, primary_output)
            if primary_decision == "accept":
                primary_intent = _accepted_primary_intent(primary_output, allowed=primary_allowed)
                if primary_intent is None:
                    return _intent_llm_rejected_result(base_result, llm_prompts, llm_responses, "intent_llm_invalid_output")
            elif primary_decision == "need_full_taxonomy":
                primary_intent = None
            else:
                return _intent_llm_rejected_result(base_result, llm_prompts, llm_responses, "intent_llm_invalid_output")
        else:
            primary_intent = None

        if primary_intent is None:
            primary_full_prompt = render_intent_primary_full_prompt(
                question=base_result.question,
                candidate_stage_summary=_intent_candidate_stage_summary(embedding_diagnostic),
            )
            primary_full_output = await _call_structured_llm(
                llm_client=llm_client,
                key="intent_primary_full",
                prompt=primary_full_prompt,
                result=base_result,
                llm_prompts=llm_prompts,
                llm_responses=llm_responses,
            )
            if primary_full_output is None:
                return _intent_llm_rejected_result(base_result, llm_prompts, llm_responses, "intent_llm_invalid_output")
            primary_decision = str(primary_full_output.get("decision") or "")
            if primary_decision == "clarify":
                return _intent_llm_clarification_result(base_result, llm_prompts, llm_responses, primary_full_output)
            if primary_decision != "accept":
                return _intent_llm_rejected_result(base_result, llm_prompts, llm_responses, "intent_llm_invalid_output")
            primary_intent = _accepted_primary_intent(
                primary_full_output,
                allowed=set(taxonomy["primary"].keys()),
            )
            if primary_intent is None:
                return _intent_llm_rejected_result(base_result, llm_prompts, llm_responses, "intent_llm_invalid_output")

        secondary_candidates = _secondary_candidate_cards(embedding_diagnostic, primary_intent)
        primary_name = str(taxonomy["primary"].get(primary_intent, {}).get("name_zh") or primary_intent)
        if secondary_candidates:
            secondary_prompt = render_intent_secondary_candidate_prompt(
                question=base_result.question,
                primary_intent=primary_intent,
                primary_intent_name=primary_name,
                candidate_cards=_render_intent_candidate_cards(secondary_candidates),
            )
            secondary_output = await _call_structured_llm(
                llm_client=llm_client,
                key="intent_secondary_candidate",
                prompt=secondary_prompt,
                result=base_result,
                llm_prompts=llm_prompts,
                llm_responses=llm_responses,
            )
            secondary_allowed = {str(item["secondary_intent"]) for item in secondary_candidates}
            if secondary_output is None:
                return _intent_llm_rejected_result(base_result, llm_prompts, llm_responses, "intent_llm_invalid_output")
            secondary_decision = str(secondary_output.get("decision") or "")
            if secondary_decision == "clarify":
                return _intent_llm_clarification_result(base_result, llm_prompts, llm_responses, secondary_output)
            if secondary_decision == "accept":
                secondary_intent = _accepted_secondary_intent(
                    secondary_output,
                    primary_intent=primary_intent,
                    allowed=secondary_allowed,
                )
                if secondary_intent is None:
                    return _intent_llm_rejected_result(base_result, llm_prompts, llm_responses, "intent_llm_invalid_output")
            elif secondary_decision == "need_full_taxonomy":
                secondary_intent = None
            else:
                return _intent_llm_rejected_result(base_result, llm_prompts, llm_responses, "intent_llm_invalid_output")
        else:
            secondary_intent = None

        if secondary_intent is None:
            secondary_full_prompt = render_intent_secondary_full_prompt(
                question=base_result.question,
                primary_intent=primary_intent,
                primary_intent_name=primary_name,
                candidate_stage_summary=_intent_candidate_stage_summary(embedding_diagnostic, primary_intent=primary_intent),
            )
            secondary_full_output = await _call_structured_llm(
                llm_client=llm_client,
                key="intent_secondary_full",
                prompt=secondary_full_prompt,
                result=base_result,
                llm_prompts=llm_prompts,
                llm_responses=llm_responses,
            )
            if secondary_full_output is None:
                return _intent_llm_rejected_result(base_result, llm_prompts, llm_responses, "intent_llm_invalid_output")
            secondary_decision = str(secondary_full_output.get("decision") or "")
            if secondary_decision == "clarify":
                return _intent_llm_clarification_result(base_result, llm_prompts, llm_responses, secondary_full_output)
            if secondary_decision != "accept":
                return _intent_llm_rejected_result(base_result, llm_prompts, llm_responses, "intent_llm_invalid_output")
            secondary_intent = _accepted_secondary_intent(
                secondary_full_output,
                primary_intent=primary_intent,
                allowed=set(taxonomy["secondary_by_primary"].get(primary_intent, {}).keys()),
            )
            if secondary_intent is None:
                return _intent_llm_rejected_result(base_result, llm_prompts, llm_responses, "intent_llm_invalid_output")

        llm_intent = IntentRecognitionResult(
            primary_intent=primary_intent,
            secondary_intent=secondary_intent,
            confidence=_max_llm_confidence(llm_responses, default=base_result.intent.confidence),
            source="llm",
            decision="accept",
        )

        context = self._build_context(
            id=base_result.id,
            question=base_result.question,
            generation_run_id=base_result.generation_run_id,
            intent_result=llm_intent,
        )
        if isinstance(context, SemanticParseResult):
            return replace(context, llm_prompts=llm_prompts, llm_responses=llm_responses)
        context = context.with_llm_prompts(llm_prompts)
        context = context.with_llm_responses(llm_responses)
        if context.semantic_view_matching is not None and context.semantic_view_matching.result.needs_clarification:
            return await self._fallback_semantic_view_disambiguation_to_llm(
                base_result=context.to_result(
                    generation_mode=None,
                    generated_cypher=None,
                    preflight=None,
                    llm_prompts=llm_prompts,
                    llm_responses=llm_responses,
                )
            )
        return await self._render_context_with_fallback(context)

    async def _fallback_semantic_view_disambiguation_to_llm(
        self,
        *,
        base_result: SemanticParseResult,
    ) -> SemanticParseResult:
        matching = base_result.semantic_view_matching
        if matching is None or not matching.result.paths:
            return base_result
        llm_client = self.llm_client or _get_default_llm_client()
        prompt = render_semantic_view_disambiguation_prompt(
            question=base_result.question,
            candidate_cards=_render_semantic_path_candidate_cards(matching.result),
        )
        raw_generation = await llm_client.generate_from_prompt(
            task_id=base_result.id or base_result.generation_run_id or "semantic-parse",
            question_text=base_result.question,
            llm_prompt=prompt,
        )
        raw_output = raw_generation.get("raw_output")
        attempt = {
            "stage": "semantic_view.disambiguation",
            "attempt_type": "limited_candidates",
            "prompt": prompt,
            "raw_output": raw_output if isinstance(raw_output, str) else None,
        }
        if not isinstance(raw_output, str):
            return _semantic_disambiguation_result(base_result, matching, attempt)
        payload = _parse_llm_json_payload(raw_output)
        if payload is None:
            return _semantic_disambiguation_result(base_result, matching, attempt)
        attempt = {
            **attempt,
            "decision": payload.get("decision"),
            "selected_path_semantic": payload.get("selected_path_semantic"),
            "reason": payload.get("reason"),
        }
        if payload.get("decision") != "accept":
            return _semantic_disambiguation_result(base_result, matching, attempt)
        selected_path = str(payload.get("selected_path_semantic") or "")
        if selected_path not in {path.path_semantic for path in matching.result.paths}:
            return _semantic_disambiguation_result(base_result, matching, attempt)
        resolved_matching = self.semantic_view_matcher.match_with_selected_path(
            base_result.question,
            selected_path_semantic=selected_path,
            llm_attempt=attempt,
        )
        if not resolved_matching.result.accepted:
            return replace(base_result, semantic_view_matching=resolved_matching)

        planning = self.logical_planner.plan(
            question=base_result.question,
            intent_result=base_result.intent,
            semantic_match=resolved_matching.result,
            generation_run_id=base_result.generation_run_id,
        )
        context = _SemanticPipelineContext(
            id=base_result.id,
            question=base_result.question,
            generation_run_id=base_result.generation_run_id,
            intent=base_result.intent,
            validation=SemanticValidationResult(accepted=True),
            semantic_query=planning.semantic_query,
            semantic_view_matching=resolved_matching,
            logical_plan=planning.logical_plan,
            schema_path_planning=planning.schema_path_plan,
            llm_prompts=base_result.llm_prompts,
            llm_responses=base_result.llm_responses,
        )
        return await self._render_context_with_fallback(context)

    async def _render_context_with_fallback(self, context: "_SemanticPipelineContext") -> SemanticParseResult:
        context = context.with_selected_knowledge(await self._select_knowledge(context))
        try:
            cypher = self.renderer.render(context.semantic_query)
        except Exception as exc:
            return await self._fallback_to_llm(context=context, renderer_error=str(exc))
        preflight = run_semantic_cypher_preflight(cypher, semantic_query=context.semantic_query)
        if not preflight.accepted:
            return await self._fallback_to_llm(
                context=context,
                renderer_error=f"semantic preflight failed: {preflight.reason or 'unknown'}",
            )
        return context.to_result(
            generation_mode="deterministic_renderer",
            generated_cypher=cypher,
            preflight=preflight,
        )


@dataclass(frozen=True)
class _SemanticPipelineContext:
    id: str | None
    question: str
    generation_run_id: str | None
    intent: IntentRecognitionResult
    validation: SemanticValidationResult
    semantic_query: SemanticQuerySpec
    semantic_view_matching: SemanticViewMatchingTrace | None = None
    logical_plan: LogicalQueryPlan | None = None
    schema_path_planning: SchemaPathPlan | None = None
    selected_knowledge: SelectedKnowledgeContext | None = None
    llm_prompts: dict[str, str | None] = field(default_factory=_empty_llm_prompts)
    llm_responses: dict[str, str | None] = field(default_factory=_empty_llm_responses)

    def with_selected_knowledge(
        self,
        selected_knowledge: SelectedKnowledgeContext | None,
    ) -> "_SemanticPipelineContext":
        return replace(self, selected_knowledge=selected_knowledge)

    def with_llm_prompts(
        self,
        llm_prompts: dict[str, str | None],
    ) -> "_SemanticPipelineContext":
        return replace(self, llm_prompts=llm_prompts)

    def with_llm_responses(
        self,
        llm_responses: dict[str, str | None],
    ) -> "_SemanticPipelineContext":
        return replace(self, llm_responses=llm_responses)

    def to_result(
        self,
        *,
        generation_mode: str,
        generated_cypher: str | None,
        preflight: object | None,
        llm_prompts: dict[str, str | None] | None = None,
        llm_responses: dict[str, str | None] | None = None,
    ) -> SemanticParseResult:
        return SemanticParseResult(
            id=self.id,
            question=self.question,
            generation_run_id=self.generation_run_id,
            generation_mode=generation_mode,
            intent=self.intent,
            validation=self.validation,
            semantic_query=self.semantic_query,
            semantic_view_matching=self.semantic_view_matching,
            logical_plan=self.logical_plan,
            schema_path_planning=self.schema_path_planning,
            selected_knowledge=self.selected_knowledge,
            generated_cypher=generated_cypher,
            preflight=preflight,
            llm_prompts=llm_prompts or self.llm_prompts or _empty_llm_prompts(),
            llm_responses=llm_responses or self.llm_responses or _empty_llm_responses(),
        )


@lru_cache(maxsize=1)
def get_semantic_pipeline() -> SemanticPipeline:
    return SemanticPipeline()


def _get_default_llm_client() -> CypherLLMClient:
    settings = get_settings()
    return CypherLLMClient(
        llm_generator=OpenAIChatCompletionCypherGenerator(
            base_url=settings.llm_base_url or "",
            api_key=settings.llm_api_key or "",
            model=settings.llm_model or "",
            timeout_seconds=settings.request_timeout_seconds,
            temperature=settings.llm_temperature,
        )
    )


async def _call_structured_llm(
    *,
    llm_client: CypherLLMClient,
    key: str,
    prompt: str,
    result: SemanticParseResult,
    llm_prompts: dict[str, str | None],
    llm_responses: dict[str, str | None],
) -> dict[str, Any] | None:
    llm_prompts[key] = prompt
    raw_generation = await llm_client.generate_from_prompt(
        task_id=result.id or result.generation_run_id or "semantic-parse",
        question_text=result.question,
        llm_prompt=prompt,
    )
    raw_output = raw_generation.get("raw_output")
    llm_responses[key] = raw_output if isinstance(raw_output, str) else None
    if not isinstance(raw_output, str):
        return None
    return _parse_llm_json_payload(raw_output)


def _parse_llm_json_payload(raw_output: str) -> dict[str, Any] | None:
    return _extract_json_object(raw_output)


@lru_cache(maxsize=1)
def _intent_taxonomy_index() -> dict[str, dict[str, Any]]:
    payload = yaml.safe_load(resource_paths.intent_taxonomy_path().read_text(encoding="utf-8"))
    taxonomy = payload if isinstance(payload, dict) else {}
    primary: dict[str, dict[str, Any]] = {}
    secondary_by_primary: dict[str, dict[str, dict[str, Any]]] = {}
    for item in taxonomy.get("intents", []):
        if not isinstance(item, dict):
            continue
        primary_intent = str(item.get("primary_intent") or "")
        if not primary_intent:
            continue
        primary[primary_intent] = item
        secondary_by_primary[primary_intent] = {}
        for secondary in item.get("secondary_intents", []):
            if not isinstance(secondary, dict):
                continue
            secondary_intent = str(secondary.get("secondary_intent") or "")
            if secondary_intent:
                secondary_by_primary[primary_intent][secondary_intent] = secondary
    return {"primary": primary, "secondary_by_primary": secondary_by_primary}


def _primary_candidate_cards(diagnostic: Any) -> list[dict[str, object]]:
    taxonomy = _intent_taxonomy_index()
    cards: list[dict[str, object]] = []
    seen: set[str] = set()
    for candidate in getattr(diagnostic, "candidates", []) or []:
        primary_intent = getattr(candidate, "primary_intent", None)
        if not isinstance(primary_intent, str) or primary_intent in seen:
            continue
        metadata = taxonomy["primary"].get(primary_intent, {})
        cards.append(
            {
                "candidate_id": f"p{len(cards) + 1}",
                "primary_intent": primary_intent,
                "secondary_intent": getattr(candidate, "secondary_intent", None),
                "candidate_name": metadata.get("name_zh") or primary_intent,
                "definition": metadata.get("description") or "",
                "supporting_evidence": getattr(candidate, "sample_text", "") or "前置阶段召回候选",
                "risk": "只用于一级答案形态判断，业务实体和路径不在此处决定。",
            }
        )
        seen.add(primary_intent)
        if len(cards) >= 3:
            break
    return cards


def _secondary_candidate_cards(diagnostic: Any, primary_intent: str) -> list[dict[str, object]]:
    taxonomy = _intent_taxonomy_index()
    secondary_metadata = taxonomy["secondary_by_primary"].get(primary_intent, {})
    cards: list[dict[str, object]] = []
    seen: set[str] = set()
    for candidate in getattr(diagnostic, "candidates", []) or []:
        if getattr(candidate, "primary_intent", None) != primary_intent:
            continue
        secondary_intent = getattr(candidate, "secondary_intent", None)
        if not isinstance(secondary_intent, str) or secondary_intent in seen:
            continue
        metadata = secondary_metadata.get(secondary_intent, {})
        cards.append(
            {
                "candidate_id": f"s{len(cards) + 1}",
                "primary_intent": primary_intent,
                "secondary_intent": secondary_intent,
                "candidate_name": metadata.get("name_zh") or secondary_intent,
                "definition": metadata.get("description") or "",
                "supporting_evidence": getattr(candidate, "sample_text", "") or "前置阶段召回候选",
                "risk": "只能在已确定的一级意图内部选择二级意图。",
            }
        )
        seen.add(secondary_intent)
        if len(cards) >= 3:
            break
    return cards


def _render_intent_candidate_cards(cards: list[dict[str, object]]) -> str:
    return "\n\n".join(
        "\n".join(
            [
                f"## 候选 {card['candidate_id']}",
                f"- 一级意图：`{card['primary_intent']}`",
                f"- 二级意图：`{card.get('secondary_intent') or '未判断'}`",
                f"- 中文名：{card['candidate_name']}",
                f"- 含义：{card['definition']}",
                f"- 支持依据：{card['supporting_evidence']}",
                f"- 易混风险：{card['risk']}",
            ]
        )
        for card in cards
    )


def _intent_candidate_stage_summary(diagnostic: Any, *, primary_intent: str | None = None) -> str:
    candidates = getattr(diagnostic, "candidates", []) or []
    if primary_intent is not None:
        candidates = [item for item in candidates if getattr(item, "primary_intent", None) == primary_intent]
    if not candidates:
        return "前置阶段没有可用候选。"
    lines = []
    for candidate in candidates[:3]:
        lines.append(
            "- "
            f"{getattr(candidate, 'primary_intent', '')}."
            f"{getattr(candidate, 'secondary_intent', '')}；"
            f"相似样本：{getattr(candidate, 'sample_text', '')}"
        )
    reason = getattr(diagnostic, "reason", None)
    if reason:
        lines.append(f"- 前置阶段未接受原因：{reason}")
    return "\n".join(lines)


def _accepted_primary_intent(payload: dict[str, Any], *, allowed: set[str]) -> str | None:
    primary_intent = payload.get("primary_intent")
    if not isinstance(primary_intent, str):
        return None
    if primary_intent not in allowed:
        return None
    return primary_intent


def _accepted_secondary_intent(
    payload: dict[str, Any],
    *,
    primary_intent: str,
    allowed: set[str],
) -> str | None:
    if payload.get("primary_intent") != primary_intent:
        return None
    secondary_intent = payload.get("secondary_intent")
    if not isinstance(secondary_intent, str):
        return None
    if secondary_intent not in allowed:
        return None
    return secondary_intent


def _max_llm_confidence(llm_responses: dict[str, str | None], *, default: float) -> float:
    confidence = default
    for key, raw_output in llm_responses.items():
        if not key.startswith("intent_") or not raw_output:
            continue
        payload = _parse_llm_json_payload(raw_output)
        if payload is None:
            continue
        try:
            confidence = max(confidence, float(payload.get("confidence", 0.0)))
        except (TypeError, ValueError):
            continue
    return max(0.0, min(1.0, confidence))


def _render_semantic_path_candidate_cards(result: SemanticMatchResult) -> str:
    lines = []
    for index, path in enumerate(result.paths, start=1):
        lines.append(
            "\n".join(
                [
                    f"## 候选 {index}",
                    f"- path_semantic：`{path.path_semantic}`",
                    f"- relationships：{', '.join(path.relationships)}",
                    f"- 命中证据：{path.evidence}",
                ]
            )
        )
    return "\n\n".join(lines)


def _semantic_disambiguation_result(
    base_result: SemanticParseResult,
    matching: SemanticViewMatchingTrace,
    attempt: dict[str, object],
) -> SemanticParseResult:
    updated_matching = replace(
        matching,
        llm_disambiguation_attempts=(*matching.llm_disambiguation_attempts, dict(attempt)),
    )
    return replace(base_result, semantic_view_matching=updated_matching)


def _parse_intent_llm_output(raw_output: str) -> IntentRecognitionResult | None:
    payload = _extract_json_object(raw_output)
    if not isinstance(payload, dict):
        return None
    decision = payload.get("decision")
    if decision not in {"accept", "clarify"}:
        return None
    primary_intent = payload.get("primary_intent")
    secondary_intent = payload.get("secondary_intent")
    if decision == "accept" and (not isinstance(primary_intent, str) or not isinstance(secondary_intent, str)):
        return None
    if primary_intent is not None and not isinstance(primary_intent, str):
        return None
    if secondary_intent is not None and not isinstance(secondary_intent, str):
        return None
    try:
        confidence = float(payload.get("confidence", 0.0))
    except (TypeError, ValueError):
        return None
    confidence = max(0.0, min(1.0, confidence))
    return IntentRecognitionResult(
        primary_intent=primary_intent,
        secondary_intent=secondary_intent,
        confidence=confidence,
        source="llm",
        decision=decision,
    )


def _extract_json_object(raw_output: str) -> dict[str, Any] | None:
    text = raw_output.strip()
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text.lower().startswith("json"):
            text = text[4:].strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            parsed = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return parsed if isinstance(parsed, dict) else None


def _intent_llm_rejected_result(
    base_result: SemanticParseResult,
    llm_prompts: dict[str, str | None],
    llm_responses: dict[str, str | None],
    code: str,
    *,
    intent: IntentRecognitionResult | None = None,
) -> SemanticParseResult:
    return replace(
        base_result,
        intent=intent or base_result.intent,
        generation_mode=None,
        validation=SemanticValidationResult(
            accepted=False,
            diagnostics=[
                SemanticDiagnostic(
                    code=code,
                    message="LLM intent recognition did not return an accepted intent for semantic parsing.",
                )
            ],
        ),
        semantic_query=None,
        generated_cypher=None,
        preflight=None,
        llm_prompts=llm_prompts,
        llm_responses=llm_responses,
    )


def _intent_llm_clarification_result(
    base_result: SemanticParseResult,
    llm_prompts: dict[str, str | None],
    llm_responses: dict[str, str | None],
    payload: dict[str, Any],
) -> SemanticParseResult:
    try:
        confidence = float(payload.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    clarification_question = str(payload.get("clarification_question") or "请补充你希望查询的答案形态。")
    return replace(
        base_result,
        intent=IntentRecognitionResult(
            primary_intent=payload.get("primary_intent") if isinstance(payload.get("primary_intent"), str) else None,
            secondary_intent=payload.get("secondary_intent") if isinstance(payload.get("secondary_intent"), str) else None,
            confidence=max(0.0, min(1.0, confidence)),
            source="llm",
            decision="clarify",
        ),
        generation_mode=None,
        validation=SemanticValidationResult(
            accepted=False,
            diagnostics=[
                SemanticDiagnostic(
                    code="clarification_required",
                    message="LLM intent recognition requires a user clarification.",
                )
            ],
        ),
        semantic_query=None,
        generated_cypher=None,
        preflight=None,
        clarification={
            "source_stage": "intent_recognition",
            "reason_code": "intent_ambiguity",
            "question_zh": clarification_question,
            "expected_answer_type": "free_text",
            "options": [],
        },
        llm_prompts=llm_prompts,
        llm_responses=llm_responses,
    )


def _intent_trace(
    intent: IntentRecognitionResult,
    llm_prompts: dict[str, str | None],
    llm_responses: dict[str, str | None],
) -> dict[str, object]:
    return {
        "result": intent.to_dict(),
        "diagnostics": {
            "llm_primary_attempts": _intent_llm_attempts("intent_primary", llm_prompts, llm_responses),
            "llm_secondary_attempts": _intent_llm_attempts("intent_secondary", llm_prompts, llm_responses),
        },
    }


def _intent_llm_attempts(
    prefix: str,
    llm_prompts: dict[str, str | None],
    llm_responses: dict[str, str | None],
) -> list[dict[str, object]]:
    ordered_keys = [
        f"{prefix}_candidate",
        f"{prefix}_full",
    ]
    attempts: list[dict[str, object]] = []
    for key in ordered_keys:
        prompt = llm_prompts.get(key)
        raw_output = llm_responses.get(key)
        if not prompt and not raw_output:
            continue
        payload = _parse_llm_json_payload(raw_output) if raw_output else None
        attempts.append(
            {
                "stage": key,
                "attempt_type": "candidate_first" if key.endswith("_candidate") else "full_taxonomy_fallback",
                "prompt": prompt,
                "raw_output": raw_output,
                "decision": payload.get("decision") if payload else None,
            }
        )
    return attempts


def _trace_generation_status(result: SemanticParseResult) -> str:
    if result.clarification:
        return "clarification_required"
    if result.generated_cypher:
        return "generated"
    return "generation_failed"


def _service_context_trace(uses_semantic_view: bool) -> dict[str, object]:
    settings = get_settings()
    rag_endpoint = f"{settings.rag_service_url.rstrip('/')}/api/v1/retrieve"
    return {
        "active_mode": "semantic_view_pipeline",
        "model": settings.llm_model,
        "knowledge_context_source": settings.knowledge_context_source,
        "semantic_view_version": "network_graph_semantic_view.yaml",
        "rag_source": rag_endpoint if settings.knowledge_context_source == "rag" else settings.knowledge_docs_dir,
    }


def _generation_trace(
    *,
    generation_mode: str | None,
    generated_cypher: str | None,
    llm_prompts: dict[str, str | None],
    llm_responses: dict[str, str | None],
) -> dict[str, object]:
    fallback_prompt = llm_prompts.get("cypher_generation_fallback")
    fallback_response = llm_responses.get("cypher_generation_fallback")
    fallback_triggered = bool(fallback_prompt or fallback_response)
    return {
        "renderer": {
            "family": generation_mode,
            "accepted": bool(generated_cypher) and not fallback_triggered,
            "cypher": generated_cypher if generation_mode == "deterministic_renderer" else None,
            "generated_cypher": generated_cypher if generation_mode == "deterministic_renderer" else None,
            "failure_reason": None if generated_cypher else "no_cypher_found",
        },
        "cypher_fallback_llm": (
            {
                "stage": "generation.cypher_fallback",
                "prompt": fallback_prompt,
                "raw_output": fallback_response,
                "accepted": bool(generated_cypher),
            }
            if fallback_triggered
            else None
        ),
        "parser": {
            "parsed_cypher": generated_cypher,
            "parse_summary": "cypher_only" if generated_cypher else None,
        },
    }


def _render_fallback_plan_context(context: "_SemanticPipelineContext") -> str:
    if context.logical_plan is None:
        return context.semantic_query.to_json()
    lines: list[str] = []
    logical_plan = context.logical_plan
    lines.append(f"答案形态：{logical_plan.answer_shape}")
    entity_lines = [
        f"- {entity.name} 使用变量 {entity.alias}，对应点标签 {entity.label}。"
        for entity in context.semantic_query.entities
    ]
    if entity_lines:
        lines.append("\n实体：")
        lines.extend(entity_lines)
    path_items = (
        context.schema_path_planning.selected_paths
        if context.schema_path_planning is not None
        else ()
    )
    if path_items:
        lines.append("\n已选路径：")
        for path in path_items:
            lines.append(f"- {path.get('cypher_pattern')}")
    if context.semantic_query.filters:
        lines.append("\n过滤：")
        for item in context.semantic_query.filters:
            lines.append(f"- {item.left} {item.operator} {json.dumps(item.value, ensure_ascii=False)}。")
    return_items = [*context.semantic_query.projections, *context.semantic_query.dimensions]
    if context.semantic_query.metrics:
        metric_lines = [f"- {item.expression} AS {item.output_alias}。" for item in context.semantic_query.metrics]
    else:
        metric_lines = []
    if return_items or metric_lines:
        lines.append("\n返回字段：")
        lines.extend(f"- {item.expression} AS {item.output_alias}。" for item in return_items)
        lines.extend(metric_lines)
    if context.semantic_query.order_by:
        lines.append("\n排序：")
        lines.extend(f"- {item.expression} {item.direction}。" for item in context.semantic_query.order_by)
    lines.append(f"\n数量限制：{context.semantic_query.limit if context.semantic_query.limit is not None else '无'}")
    return "\n".join(lines)


def _fallback_extra_constraint_reason(renderer_error: str) -> GenerationFailureReason | None:
    prefix = "semantic preflight failed:"
    if prefix not in renderer_error:
        return None
    reason = renderer_error.split(prefix, 1)[1].strip()
    if reason == "semantic_query_mismatch":
        return "logical_plan_mismatch"
    if reason in set(GenerationFailureReason.__args__):
        return reason  # type: ignore[return-value]
    return "logical_plan_mismatch"


def _clarification_payload(match_result: SemanticMatchResult) -> dict[str, object]:
    return {
        "source_stage": "semantic_view_matching",
        "reason_code": match_result.clarification_type or "semantic_ambiguity",
        "question_zh": match_result.clarification_question or "请补充问题中的业务语义。",
        "expected_answer_type": "single_choice" if match_result.clarification_options else "free_text",
        "options": [
            {"id": str(option.get("value", "")), "label": str(option.get("label", ""))}
            for option in match_result.clarification_options
        ],
    }


def _to_dict(value: object | None) -> object | None:
    if value is None:
        return None
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump()
    if is_dataclass(value):
        return asdict(value)
    return value
