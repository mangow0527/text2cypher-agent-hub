from __future__ import annotations

import json
from dataclasses import dataclass, field
from dataclasses import asdict, is_dataclass, replace
from functools import lru_cache
from typing import Any

from .clients import CypherLLMClient, OpenAIChatCompletionCypherGenerator
from .cypher_renderer import CypherRenderer
from .intent_recognition import IntentRecognitionResult, get_hybrid_intent_recognizer
from .knowledge_selection import KnowledgeSelector, SelectedKnowledgeContext
from .logical_planner import LogicalQueryPlan, LogicalQueryPlanner, SchemaPathPlan
from .models import GenerationFailureReason, PreflightCheck
from .config import get_settings
from .parser import parse_model_output
from .prompt_runtime import render_controlled_semantic_prompt, render_intent_recognition_fallback_prompt
from .semantic_cypher_preflight import run_semantic_cypher_preflight
from .graph_semantic_view import get_default_graph_semantic_view
from .semantic_query import SemanticQuerySpec
from .semantic_view_matching import SemanticMatchResult, SemanticViewMatcher, SemanticViewMatchingTrace


def _empty_llm_prompts() -> dict[str, str | None]:
    return {
        "intent_recognition_fallback": None,
        "cypher_generation_fallback": None,
    }


def _empty_llm_responses() -> dict[str, str | None]:
    return {
        "intent_recognition_fallback": None,
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
        llm_responses = {
            **(base_result.llm_responses or _empty_llm_responses()),
            "intent_recognition_fallback": raw_output if isinstance(raw_output, str) else None,
        }
        if not isinstance(raw_output, str):
            return _intent_llm_rejected_result(base_result, llm_prompts, llm_responses, "intent_llm_invalid_output")
        llm_intent = _parse_intent_llm_output(raw_output)
        if llm_intent is None:
            return _intent_llm_rejected_result(base_result, llm_prompts, llm_responses, "intent_llm_invalid_output")
        if llm_intent.decision != "accept":
            return _intent_llm_rejected_result(base_result, llm_prompts, llm_responses, "intent_llm_clarify", intent=llm_intent)

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


def _intent_trace(
    intent: IntentRecognitionResult,
    llm_prompts: dict[str, str | None],
    llm_responses: dict[str, str | None],
) -> dict[str, object]:
    fallback_prompt = llm_prompts.get("intent_recognition_fallback")
    fallback_response = llm_responses.get("intent_recognition_fallback")
    return {
        "result": intent.to_dict(),
        "diagnostics": {
            "llm_primary_attempts": [],
            "llm_secondary_attempts": [
                {
                    "stage": "intent_recognition_fallback",
                    "prompt": fallback_prompt,
                    "raw_output": fallback_response,
                }
            ]
            if fallback_prompt or fallback_response
            else [],
        },
    }


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
