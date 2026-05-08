from __future__ import annotations

import json
from dataclasses import dataclass, field
from dataclasses import asdict, is_dataclass, replace
from functools import lru_cache
from typing import Any

from .clients import CypherLLMClient, OpenAIChatCompletionCypherGenerator
from .business_slot_schema import (
    BusinessSlotSchemaConfigError,
    BusinessSlotCompletenessResult,
    BusinessSlotFiller,
    BusinessSlotFrame,
    BusinessSlotSchemaRegistry,
    get_default_business_slot_schema_registry,
)
from .cypher_renderer import CypherRenderer
from .intent_recognition import IntentRecognitionResult, get_hybrid_intent_recognizer
from .knowledge_selection import KnowledgeSelector, SelectedKnowledgeContext
from .models import PreflightCheck
from .config import get_settings
from .parser import parse_model_output
from .prompt_runtime import render_controlled_semantic_prompt, render_intent_recognition_fallback_prompt
from .schema_linking import LinkedSemantics, SchemaLinker
from .semantic_cypher_preflight import run_semantic_cypher_preflight
from .semantic_layer import get_default_semantic_layer
from .semantic_query import SemanticQueryBuilder, SemanticQuerySpec
from .semantic_validation import SemanticDiagnostic, SemanticValidationResult, SemanticValidator
from .slot_matching import SlotMatcher


def _empty_llm_prompts() -> dict[str, str | None]:
    return {
        "intent_recognition_fallback": None,
        "cypher_generation_fallback": None,
    }


@dataclass(frozen=True)
class SemanticParseResult:
    id: str | None
    question: str
    generation_run_id: str | None
    generation_mode: str | None
    intent: IntentRecognitionResult
    slots: object
    business_slots: BusinessSlotFrame | None
    slot_completeness: BusinessSlotCompletenessResult
    linked_semantics: LinkedSemantics | None
    validation: SemanticValidationResult
    semantic_query: SemanticQuerySpec | None
    generated_cypher: str | None
    preflight: object | None
    selected_knowledge: SelectedKnowledgeContext | None = None
    llm_prompts: dict[str, str | None] = field(default_factory=_empty_llm_prompts)

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "question": self.question,
            "generation_run_id": self.generation_run_id,
            "generation_mode": self.generation_mode,
            "intent": self.intent.to_dict(),
            "slots": _to_dict(self.slots),
            "business_slots": self.business_slots.to_dict() if self.business_slots is not None else None,
            "slot_completeness": self.slot_completeness.to_dict(),
            "linked_semantics": self.linked_semantics.to_dict() if self.linked_semantics is not None else None,
            "validation": self.validation.to_dict(),
            "semantic_query": self.semantic_query.to_dict() if self.semantic_query is not None else None,
            "selected_knowledge": self.selected_knowledge.to_dict() if self.selected_knowledge is not None else None,
            "generated_cypher": self.generated_cypher,
            "preflight": _to_dict(self.preflight),
            "llm_prompts": dict(self.llm_prompts or _empty_llm_prompts()),
        }


class SemanticPipeline:
    def __init__(
        self,
        *,
        semantic_layer: object | None = None,
        slot_matcher: SlotMatcher | None = None,
        linker: SchemaLinker | None = None,
        business_slot_schema_registry: BusinessSlotSchemaRegistry | None = None,
        business_slot_filler: BusinessSlotFiller | None = None,
        validator: SemanticValidator | None = None,
        semantic_query_builder: SemanticQueryBuilder | None = None,
        renderer: CypherRenderer | None = None,
        llm_client: Any | None = None,
        knowledge_selector: KnowledgeSelector | None = None,
    ) -> None:
        self.semantic_layer = semantic_layer or get_default_semantic_layer()
        self.slot_matcher = slot_matcher or SlotMatcher.from_default_config()
        self.linker = linker or SchemaLinker(self.semantic_layer)
        self.business_slot_schema_registry = business_slot_schema_registry or get_default_business_slot_schema_registry()
        self.business_slot_filler = business_slot_filler or BusinessSlotFiller()
        self.validator = validator or SemanticValidator(self.semantic_layer)
        self.semantic_query_builder = semantic_query_builder or SemanticQueryBuilder()
        self.renderer = renderer or CypherRenderer()
        self.llm_client = llm_client
        self.knowledge_selector = knowledge_selector

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
            return context
        context = context.with_selected_knowledge(await self._select_knowledge(context))
        try:
            cypher = self.renderer.render(context.semantic_query)
        except Exception as exc:
            return await self._fallback_to_llm(context=context, renderer_error=str(exc))
        preflight = run_semantic_cypher_preflight(cypher, semantic_query=context.semantic_query)
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
        empty_slots = self.slot_matcher.empty_result() if hasattr(self.slot_matcher, "empty_result") else None
        if intent.decision != "accept" or intent.primary_intent is None or intent.secondary_intent is None:
            return SemanticParseResult(
                id=id,
                question=question,
                generation_run_id=generation_run_id,
                generation_mode=None,
                intent=intent,
                slots=empty_slots,
                business_slots=None,
                slot_completeness=BusinessSlotCompletenessResult.not_applicable(),
                linked_semantics=None,
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
                generated_cypher=None,
                preflight=None,
            )

        slots = self.slot_matcher.match(question)
        try:
            business_slot_schema = self.business_slot_schema_registry.select(intent)
        except BusinessSlotSchemaConfigError as exc:
            return SemanticParseResult(
                id=id,
                question=question,
                generation_run_id=generation_run_id,
                generation_mode=None,
                intent=intent,
                slots=slots,
                business_slots=None,
                slot_completeness=BusinessSlotCompletenessResult.not_applicable(),
                linked_semantics=None,
                validation=SemanticValidationResult(
                    accepted=False,
                    diagnostics=[
                        SemanticDiagnostic(
                            code="unsupported_business_slot_schema",
                            message=str(exc),
                        )
                    ],
                ),
                semantic_query=None,
                generated_cypher=None,
                preflight=None,
            )
        business_slots = self.business_slot_filler.fill(
            schema=business_slot_schema,
            intent=intent,
            low_level_slots=slots,
        )
        slot_completeness = self.business_slot_schema_registry.validate(
            schema=business_slot_schema,
            frame=business_slots,
        )
        if not slot_completeness.accepted:
            return SemanticParseResult(
                id=id,
                question=question,
                generation_run_id=generation_run_id,
                generation_mode=None,
                intent=intent,
                slots=slots,
                business_slots=business_slots,
                slot_completeness=slot_completeness,
                linked_semantics=None,
                validation=SemanticValidationResult(
                    accepted=False,
                    diagnostics=[
                        SemanticDiagnostic(
                            code="missing_required_business_slot",
                            message="Required business slots are missing for this intent before semantic linking.",
                        )
                    ],
                ),
                semantic_query=None,
                generated_cypher=None,
                preflight=None,
            )
        linked = self.linker.link(slots)
        validation = self.validator.validate(linked)
        if not validation.accepted:
            return SemanticParseResult(
                id=id,
                question=question,
                generation_run_id=generation_run_id,
                generation_mode=None,
                intent=intent,
                slots=slots,
                business_slots=business_slots,
                slot_completeness=slot_completeness,
                linked_semantics=linked,
                validation=validation,
                semantic_query=None,
                generated_cypher=None,
                preflight=None,
            )

        semantic_query = self.semantic_query_builder.build(
            intent_result=intent,
            linked_semantics=linked,
            business_slots=business_slots,
        )
        return _SemanticPipelineContext(
            id=id,
            question=question,
            generation_run_id=generation_run_id,
            intent=intent,
            slots=slots,
            business_slots=business_slots,
            slot_completeness=slot_completeness,
            linked_semantics=linked,
            validation=validation,
            semantic_query=semantic_query,
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
            semantic_query_json=context.semantic_query.to_json(),
            renderer_error=renderer_error,
            selected_knowledge_context=(
                context.selected_knowledge.prompt_context
                if context.selected_knowledge is not None and context.selected_knowledge.prompt_context.strip()
                else None
            ),
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
        if not isinstance(raw_output, str):
            return context.to_result(
                generation_mode="controlled_llm_fallback",
                generated_cypher=None,
                preflight=PreflightCheck(accepted=False, reason="no_cypher_found"),
                llm_prompts=llm_prompts,
            )
        parsed = parse_model_output(raw_output)
        if parsed.reason is not None:
            return context.to_result(
                generation_mode="controlled_llm_fallback",
                generated_cypher=None,
                preflight=PreflightCheck(accepted=False, reason=parsed.reason),
                llm_prompts=llm_prompts,
            )
        preflight = run_semantic_cypher_preflight(parsed.parsed_cypher, semantic_query=context.semantic_query)
        return context.to_result(
            generation_mode="controlled_llm_fallback",
            generated_cypher=parsed.parsed_cypher,
            preflight=preflight,
            llm_prompts=llm_prompts,
        )

    async def _fallback_intent_recognition_to_llm(
        self,
        *,
        base_result: SemanticParseResult,
    ) -> SemanticParseResult:
        llm_client = self.llm_client or _get_default_llm_client()
        prompt = render_intent_recognition_fallback_prompt(
            question=base_result.question,
            fallback_reason=json.dumps(base_result.intent.to_dict(), ensure_ascii=False, indent=2),
        )
        llm_prompts = {
            **(base_result.llm_prompts or _empty_llm_prompts()),
            "intent_recognition_fallback": prompt,
        }
        raw_generation = await llm_client.generate_from_prompt(
            task_id=base_result.id or base_result.generation_run_id or "semantic-parse",
            question_text=base_result.question,
            llm_prompt=prompt,
        )
        raw_output = raw_generation.get("raw_output")
        if not isinstance(raw_output, str):
            return _intent_llm_rejected_result(base_result, llm_prompts, "intent_llm_invalid_output")
        llm_intent = _parse_intent_llm_output(raw_output)
        if llm_intent is None:
            return _intent_llm_rejected_result(base_result, llm_prompts, "intent_llm_invalid_output")
        if llm_intent.decision != "accept":
            return _intent_llm_rejected_result(base_result, llm_prompts, "intent_llm_clarify", intent=llm_intent)

        context = self._build_context(
            id=base_result.id,
            question=base_result.question,
            generation_run_id=base_result.generation_run_id,
            intent_result=llm_intent,
        )
        if isinstance(context, SemanticParseResult):
            return replace(context, llm_prompts=llm_prompts)
        context = context.with_llm_prompts(llm_prompts)
        context = context.with_selected_knowledge(await self._select_knowledge(context))
        try:
            cypher = self.renderer.render(context.semantic_query)
        except Exception as exc:
            return await self._fallback_to_llm(context=context, renderer_error=str(exc))
        preflight = run_semantic_cypher_preflight(cypher, semantic_query=context.semantic_query)
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
    slots: object
    business_slots: BusinessSlotFrame | None
    slot_completeness: BusinessSlotCompletenessResult
    linked_semantics: LinkedSemantics | None
    validation: SemanticValidationResult
    semantic_query: SemanticQuerySpec
    selected_knowledge: SelectedKnowledgeContext | None = None
    llm_prompts: dict[str, str | None] = field(default_factory=_empty_llm_prompts)

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

    def to_result(
        self,
        *,
        generation_mode: str,
        generated_cypher: str | None,
        preflight: object | None,
        llm_prompts: dict[str, str | None] | None = None,
    ) -> SemanticParseResult:
        return SemanticParseResult(
            id=self.id,
            question=self.question,
            generation_run_id=self.generation_run_id,
            generation_mode=generation_mode,
            intent=self.intent,
            slots=self.slots,
            business_slots=self.business_slots,
            slot_completeness=self.slot_completeness,
            linked_semantics=self.linked_semantics,
            validation=self.validation,
            semantic_query=self.semantic_query,
            selected_knowledge=self.selected_knowledge,
            generated_cypher=generated_cypher,
            preflight=preflight,
            llm_prompts=llm_prompts or self.llm_prompts or _empty_llm_prompts(),
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
    )


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
