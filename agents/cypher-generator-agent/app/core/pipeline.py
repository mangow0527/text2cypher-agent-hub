from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
import re
from time import perf_counter
from typing import Any

from services.cypher_generator_agent.app.assembly.taxonomy import QueryShape, ShapeStatus, classify_query_shape
from services.cypher_generator_agent.app.assembly.multihop import MultihopAssembler
from services.cypher_generator_agent.app.assembly.zero_hop import ZeroHopAssembler
from services.cypher_generator_agent.app.binding import BindingValidationError, SemanticBinder
from services.cypher_generator_agent.app.compiler import CypherCompiler, CypherCompilerError
from services.cypher_generator_agent.app.core.errors import GenerationFailureReason, ServiceFailureReason
from services.cypher_generator_agent.app.core.result import GenerationOutput
from services.cypher_generator_agent.app.cypher_validation import CypherSelfValidator
from services.cypher_generator_agent.app.cypher_validation.models import CypherSelfValidationResult
from services.cypher_generator_agent.app.decomposition import QuestionDecomposer
from services.cypher_generator_agent.app.decomposition.models import (
    QuestionDecomposition,
    QuestionDecompositionClarification,
    QuestionDecompositionFailure,
)
from services.cypher_generator_agent.app.dsl.builder import RestrictedDslBuilder
from services.cypher_generator_agent.app.dsl.parser import RestrictedDslValidationError, parse_restricted_query_dsl
from services.cypher_generator_agent.app.infrastructure.config import Settings, get_settings
from services.cypher_generator_agent.app.infrastructure.llm_client import (
    OpenAICompatibleStructuredLLMClient,
    TracedStructuredLLMClient,
)
from services.cypher_generator_agent.app.literals.models import LiteralResolverRequest, LiteralResolverResult
from services.cypher_generator_agent.app.literals.resolver import LiteralResolver
from services.cypher_generator_agent.app.literals.value_index import StaticValueIndex
from services.cypher_generator_agent.app.observability.stages import StageName
from services.cypher_generator_agent.app.observability.trace import GraphTraceBuilder, inline_ref
from services.cypher_generator_agent.app.repair.controller import RepairController
from services.cypher_generator_agent.app.repair.models import RepairDecision, RepairIssue
from services.cypher_generator_agent.app.repair.notices import render_user_visible_notices
from services.cypher_generator_agent.app.retrieval.models import CandidateRetrievalResult, SemanticCandidate
from services.cypher_generator_agent.app.retrieval.retriever import CandidateRetriever
from services.cypher_generator_agent.app.retrieval.structural_reranker import StructuralReranker
from services.cypher_generator_agent.app.semantic_model.loader import load_graph_semantic_model
from services.cypher_generator_agent.app.semantic_model.registry import RegistryLookupError
from services.cypher_generator_agent.app.understanding.models import (
    GroundedUnderstanding,
    GroundedUnderstandingFailure,
)
from services.cypher_generator_agent.app.understanding.grounded_understanding import GroundedUnderstandingSelector
from services.cypher_generator_agent.app.validation.coverage import CoverageReport
from services.cypher_generator_agent.app.validation.semantic_validator import SemanticValidator
from services.cypher_generator_agent.app.validation.structural_requirements import (
    DslStructuralCoverageResult,
    StructuralRequirements,
    derive_structural_requirements,
    structural_coverage_issue,
    validate_dsl_structural_coverage,
)


_STRUCTURAL_LITERAL_SKIP_SLOTS = {"limit", "order_by", "group_by", "path", "projection"}
_PROPERTY_COUNT_MODIFIER_TERMS = {
    "属性",
    "属性值",
    "属性记录",
    "字段",
    "记录",
    "非空值",
    "字段值",
    "参数",
    "值",
    "条目",
}


def run_pipeline(
    *,
    question: str,
    qa_id: str | None = None,
    generation_run_id: str,
    _model_path: Path | None = None,
    _value_index_path: Path | None = None,
    _path_pattern_template_overrides_for_tests: Mapping[str, str] | None = None,
) -> GenerationOutput:
    question_id = qa_id or generation_run_id
    trace = GraphTraceBuilder(
        trace_id=generation_run_id,
        question_id=question_id,
        generation_run_id=generation_run_id,
        source_question=question,
    )

    settings = get_settings()
    model_path = _model_path or settings.graph_model_path
    value_index_path = _value_index_path or settings.value_index_path

    try:
        return _run_pipeline_steps(
            trace=trace,
            question=question,
            question_id=question_id,
            generation_run_id=generation_run_id,
            model_path=model_path,
            value_index_path=value_index_path,
            settings=settings,
            path_pattern_template_overrides_for_tests=_path_pattern_template_overrides_for_tests,
        )
    except Exception as exc:
        return _unexpected_failure(trace, exc)


def _run_pipeline_steps(
    *,
    trace: GraphTraceBuilder,
    question: str,
    question_id: str,
    generation_run_id: str,
    model_path: Path,
    value_index_path: Path,
    settings: Settings,
    path_pattern_template_overrides_for_tests: Mapping[str, str] | None,
) -> GenerationOutput:
    llm_client = (
        TracedStructuredLLMClient(_structured_llm_client_from_settings(settings))
        if settings.llm_enabled
        else None
    )

    load_result = _run_stage(
        trace,
        stage=StageName.GRAPH_MODEL_LOADER,
        input_payload={"model_path": str(model_path)},
        action=lambda: load_graph_semantic_model(model_path),
        output_payload=lambda result: {
            "model_name": result.registry.model.name,
            "model_checksum": result.model_checksum,
            "vertices": len(result.registry.model.vertices),
            "edges": len(result.registry.model.edges),
            "path_patterns": len(result.registry.model.path_patterns),
        },
    )
    registry = load_result.registry
    trace._semantic_model = {  # noqa: SLF001 - IR-12 wires model metadata after loading it.
        "name": registry.model.name,
        "checksum": load_result.model_checksum,
    }

    input_gate = _run_stage(
        trace,
        stage=StageName.INPUT_CLARIFICATION_GATE,
        input_payload={"question": question},
        action=lambda: _input_clarification_gate(question),
    )
    if input_gate.get("status") == "clarification_required":
        return _clarification(
            trace,
            question=str(input_gate.get("question") or "请补充澄清信息。"),
        )

    decomposition_llm_start = _llm_trace_count(llm_client)
    decomposition = _run_stage(
        trace,
        stage=StageName.QUESTION_DECOMPOSER,
        input_payload={"question": question},
        action=lambda: _decompose_question(
            question=question,
            settings=settings,
            llm_client=llm_client,
        ),
        output_payload=lambda result: _with_stage_llm_calls(
            _stage_result_payload(result),
            llm_client,
            decomposition_llm_start,
            stage="question_decomposer",
        ),
        metrics=lambda result: _llm_metrics(_llm_trace_slice(llm_client, decomposition_llm_start)),
    )
    if decomposition_output := _output_from_decomposition_outcome(trace, decomposition):
        return decomposition_output
    decomposition = _decomposition_payload(decomposition)

    retrieval_result = _run_stage(
        trace,
        stage=StageName.CANDIDATE_RETRIEVAL,
        input_payload=decomposition,
        action=lambda: CandidateRetriever(registry).retrieve(decomposition),
        output_payload=lambda result: result.model_dump(mode="json"),
        metrics=lambda result: {"candidate_count": len(result.candidates)},
    )
    retrieval_result = _run_candidate_reranker_stage(
        trace,
        retrieval_result=retrieval_result,
        structural_requirements=decomposition["structural_requirements"],
    )
    decomposition = _with_literal_requests_from_candidates(decomposition, retrieval_result, registry=registry)
    skipped_literal_candidates = list(decomposition.get("skipped_literal_candidates") or [])

    literal_results = _run_stage(
        trace,
        stage=StageName.LITERAL_RESOLVER,
        input_payload={
            "literal_requests": decomposition["literal_requests"],
            "skipped_literal_candidates": skipped_literal_candidates,
        },
        action=lambda: _resolve_literals(
            decomposition["literal_requests"],
            question=question,
            trace_id=generation_run_id,
            resolver=LiteralResolver(
                registry,
                StaticValueIndex.from_path(value_index_path),
            ),
        ),
        output_payload=lambda results: [result.model_dump(mode="json") for result in results],
        metrics=lambda results: {
            "literal_count": len(results),
            "skipped_literal_candidate_count": len(skipped_literal_candidates),
        },
    )
    unresolved_literals = [result for result in literal_results if not result.resolved]
    if unresolved_literals:
        return _handle_repair_decision(
            trace,
            decision=_run_repair_controller_stage(
                trace,
                question=question,
                selected_bindings={},
                validator_errors=[
                    _literal_unresolved_issue(result).model_dump(mode="json")
                    for result in unresolved_literals
                ],
            ),
        )

    output_clarification = _naked_object_projection_clarification_question(
        decomposition,
        candidates=list(retrieval_result.candidates),
        registry=registry,
    )
    if output_clarification is not None:
        return _clarification(trace, question=output_clarification)

    structural_requirements = StructuralRequirements.model_validate(decomposition["structural_requirements"])
    deterministic_assembly = _run_deterministic_assembler_stage(
        trace,
        decomposition=decomposition,
        retrieval_result=retrieval_result,
        literal_results=literal_results,
        registry=registry,
    )
    if deterministic_assembly.get("success") and isinstance(deterministic_assembly.get("dsl"), dict):
        return _complete_dsl_generation(
            trace,
            dsl=deterministic_assembly["dsl"],
            structural_requirements=structural_requirements,
            registry=registry,
            path_pattern_template_overrides_for_tests=path_pattern_template_overrides_for_tests,
            user_visible_notices=_decomposition_user_visible_notices(decomposition),
        )

    grounded = _run_grounded_understanding_stage(
        trace,
        decomposition=decomposition,
        retrieval_result=retrieval_result,
        literal_results=literal_results,
        settings=settings,
        llm_client=llm_client,
        registry=registry,
        attempt_no=1,
    )
    if grounded_output := _output_from_grounded_outcome(trace, grounded):
        return grounded_output
    grounded = _grounded_binder_payload(grounded)
    grounded = _enrich_binder_projection_from_decomposition(
        grounded,
        decomposition=decomposition,
        candidates=list(retrieval_result.candidates),
        registry=registry,
    )
    try:
        plan = _run_stage(
            trace,
            stage=StageName.SEMANTIC_BINDER,
            input_payload=grounded,
            action=lambda: SemanticBinder(registry).bind(grounded, candidates=retrieval_result),
            output_payload=lambda result: result.model_dump(mode="json"),
        )
    except BindingValidationError as exc:
        return _failure(trace, reason="semantic_match_rejected", message=str(exc))

    validation_result = _run_stage(
        trace,
        stage=StageName.SEMANTIC_VALIDATOR,
        input_payload={
            "binding_plan": plan.model_dump(mode="json"),
            "coverage": decomposition["coverage"],
        },
        action=lambda: SemanticValidator(registry).validate(plan, coverage=decomposition["coverage"]),
        output_payload=lambda result: result.model_dump(mode="json"),
    )
    if not validation_result.is_valid:
        return _handle_repair_decision(
            trace,
            decision=_run_repair_controller_stage(
                trace,
                question=question,
                selected_bindings=plan.model_dump(mode="json"),
                validator_errors=[issue.model_dump(mode="json") for issue in validation_result.errors],
                assumptions=validation_result.assumptions,
            ),
            fallback_details={"validation": validation_result.model_dump(mode="json")},
        )
    user_visible_notices = render_user_visible_notices(validation_result.assumptions)

    try:
        dsl = _run_stage(
            trace,
            stage=StageName.DSL_BUILDER,
            input_payload=plan.model_dump(mode="json"),
            action=lambda: RestrictedDslBuilder(registry).build(
                plan,
                source_question=question,
                query_id=question_id,
            ),
        )
    except (CypherCompilerError, RestrictedDslValidationError, ValueError) as exc:
        return _failure(trace, reason="compiler_shape_mismatch", message=str(exc))

    return _complete_dsl_generation(
        trace,
        dsl=dsl,
        structural_requirements=structural_requirements,
        registry=registry,
        path_pattern_template_overrides_for_tests=path_pattern_template_overrides_for_tests,
        user_visible_notices=user_visible_notices,
    )


def _run_stage(
    trace: GraphTraceBuilder,
    *,
    stage: StageName,
    input_payload: Any,
    action: Callable[[], Any],
    output_payload: Callable[[Any], Any] | None = None,
    metrics: Callable[[Any], dict[str, Any]] | None = None,
) -> Any:
    started = perf_counter()
    try:
        result = action()
    except Exception as exc:
        trace.add_stage(
            stage=stage,
            status="failed",
            duration_ms=_duration_ms(started),
            input_ref=inline_ref(input_payload),
            errors=[{"type": exc.__class__.__name__, "message": str(exc)}],
        )
        raise

    output = output_payload(result) if output_payload is not None else result
    trace.add_stage(
        stage=stage,
        status="success",
        duration_ms=_duration_ms(started),
        input_ref=inline_ref(input_payload),
        output_ref=inline_ref(output),
        metrics=metrics(result) if metrics is not None else {},
    )
    return result


def _stage_result_payload(result: Any) -> Any:
    if hasattr(result, "model_dump"):
        return result.model_dump(mode="json")
    return result


def _llm_trace_count(llm_client: Any | None) -> int:
    calls = getattr(llm_client, "trace_calls", None)
    return len(calls) if isinstance(calls, list) else 0


def _llm_trace_slice(llm_client: Any | None, start: int) -> list[dict[str, Any]]:
    calls = getattr(llm_client, "trace_calls", None)
    if not isinstance(calls, list):
        return []
    return [dict(call) for call in calls[start:]]


def _with_stage_llm_calls(
    payload: Any,
    llm_client: Any | None,
    start: int,
    *,
    stage: str,
) -> Any:
    calls = _llm_trace_slice(llm_client, start)
    if not calls:
        return payload
    stage_calls = [{**call, "stage": call.get("stage") or stage} for call in calls]
    if isinstance(payload, dict):
        return {**payload, "llm_calls": stage_calls}
    return {
        "value": payload,
        "llm_calls": stage_calls,
    }


def _llm_metrics(calls: list[dict[str, Any]]) -> dict[str, Any]:
    metrics: dict[str, Any] = {"llm_call_count": len(calls)}
    token_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    for call in calls:
        usage = call.get("token_usage")
        if not isinstance(usage, Mapping):
            continue
        for key in token_usage:
            token_usage[key] += int(usage.get(key, 0) or 0)
    if any(token_usage.values()):
        metrics["token_usage"] = token_usage
        metrics["token_usage_total"] = token_usage["total_tokens"] or (
            token_usage["prompt_tokens"] + token_usage["completion_tokens"]
        )
    return metrics


def _structured_llm_client_from_settings(settings: Settings) -> Any:
    if settings.llm_provider != "openai_compatible":
        raise ValueError(f"unsupported LLM provider {settings.llm_provider!r}")
    if settings.llm_base_url is None:
        raise ValueError("CYPHER_GENERATOR_AGENT_LLM_BASE_URL is required when LLM is enabled")
    if settings.llm_api_key is None:
        raise ValueError("CYPHER_GENERATOR_AGENT_LLM_API_KEY is required when LLM is enabled")
    return OpenAICompatibleStructuredLLMClient(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key.get_secret_value(),
        model=settings.llm_model,
        temperature=settings.llm_temperature,
        timeout_seconds=settings.llm_timeout_seconds,
    )


def _input_clarification_gate(question: str) -> dict[str, Any]:
    normalized = question.strip()
    if not normalized:
        return {
            "status": "clarification_required",
            "reason": "empty_question",
            "question": "请补充您想查询的对象和条件。",
        }

    for term in ("它们", "这些", "那些", "这个", "那个"):
        if term in normalized:
            if _deictic_has_explicit_referent(normalized, term):
                continue
            return {
                "status": "clarification_required",
                "reason": "missing_referent",
                "term": term,
                "question": f"请说明“{term}”指的是哪个设备、服务、隧道或端口。",
            }
    start = normalized.find("它")
    while start != -1:
        if normalized.startswith("它们", start):
            start = normalized.find("它", start + len("它们"))
            continue
        return {
            "status": "clarification_required",
            "reason": "missing_referent",
            "term": "它",
            "question": "请说明“它”指的是哪个设备、服务、隧道或端口。",
        }

    return {"status": "pass"}


def _deictic_has_explicit_referent(question: str, term: str) -> bool:
    explicit_referents = ("服务", "业务", "隧道", "网元", "端口", "节点", "设备", "链路", "路径")
    start = question.find(term)
    while start != -1:
        following = question[start + len(term) : start + len(term) + 6]
        if any(following.startswith(referent) for referent in explicit_referents):
            return True
        preceding = question[max(0, start - 8) : start]
        if any(referent in preceding for referent in explicit_referents):
            return True
        start = question.find(term, start + 1)
    return False


def _decompose_question(
    *,
    question: str,
    settings: Settings,
    llm_client: Any | None,
) -> Any:
    if llm_client is None:
        return _mock_decompose(question)
    return QuestionDecomposer(
        llm_client,
        max_schema_retries=settings.llm_max_schema_retries,
    ).decompose(question)


def _select_grounded_understanding(
    *,
    decomposition: dict[str, Any],
    retrieval_result: CandidateRetrievalResult,
    literal_results: list[LiteralResolverResult],
    settings: Settings,
    llm_client: Any | None,
    registry: Any,
) -> Any:
    if llm_client is None:
        if decomposition.get("mock_intent"):
            return _mock_understand(decomposition, literal_results)
        deterministic = _deterministic_grounding_from_slots(
            decomposition=decomposition,
            retrieval_result=retrieval_result,
            literal_results=literal_results,
            registry=registry,
        )
        if deterministic is not None:
            return _with_grounding_decision(
                deterministic,
                {
                    "grounding_source": "deterministic",
                    "deterministic_decision": "returned",
                    "fallback_mode": "llm_disabled",
                },
            )
        return _mock_understand(decomposition, literal_results)

    selected = GroundedUnderstandingSelector(
        llm_client,
        max_schema_retries=0,
    ).select(
        question_decomposition=decomposition,
        candidates=retrieval_result,
        literal_results=literal_results,
    )
    return _with_grounding_decision(
        selected,
        {
            "grounding_source": "llm",
            "deterministic_decision": "not_applicable",
            "fallback_mode": "single_shot",
        },
    )


def _structural_requirements_for_precheck(decomposition: Mapping[str, Any]) -> StructuralRequirements:
    payload = decomposition.get("structural_requirements")
    if isinstance(payload, Mapping):
        return StructuralRequirements.model_validate(payload)
    return derive_structural_requirements(decomposition)


def _with_grounding_decision(result: Any, decision: Mapping[str, Any]) -> Any:
    if isinstance(result, GroundedUnderstandingFailure):
        return result
    if isinstance(result, Mapping):
        return {**dict(result), "_grounding_decision": dict(decision)}
    if hasattr(result, "model_dump"):
        payload = result.model_dump(mode="json")
        return {**payload, "_grounding_decision": dict(decision)}
    return result


def _deterministic_grounding_from_slots(
    *,
    decomposition: Mapping[str, Any],
    retrieval_result: CandidateRetrievalResult,
    literal_results: list[LiteralResolverResult],
    registry: Any,
) -> dict[str, Any] | None:
    candidates = list(retrieval_result.candidates)
    vertex_ids = _candidate_ids(candidates, "vertex")
    edge_candidates = [candidate for candidate in candidates if candidate.semantic_type == "edge"]
    literal_payloads = [result.model_dump(mode="json") for result in literal_results]
    filters = _filters_from_literal_results(literal_results, decomposition=decomposition)
    selected_properties = _property_refs_from_filters(filters)
    coverage = decomposition.get("coverage") or _coverage(covered=_substantive_term_texts(decomposition))

    if _is_count_slot(decomposition):
        target_vertex = _count_target_vertex(decomposition, vertex_ids, literal_results)
        if target_vertex is None:
            return None
        id_property = _vertex_id_property(target_vertex, candidates)
        selected_properties = _append_unique_property_ref(
            selected_properties,
            {"owner": target_vertex, "name": id_property},
        )
        return {
            "query_shape": "ad_hoc_aggregate",
            "selected_vertices": [target_vertex],
            "selected_properties": selected_properties,
            "selected_literals": literal_payloads,
            "filters": filters,
            "group_by": [],
            "measures": [
                {
                    "alias": f"{_snake_case(target_vertex)}_count",
                    "function": "count",
                    "target": _snake_case(target_vertex),
                    "property": {"owner": target_vertex, "name": id_property},
                }
            ],
            "projection": [],
            "coverage": coverage,
        }

    connecting_edge = _best_connecting_edge(vertex_ids, edge_candidates)
    if connecting_edge is not None:
        from_vertex = str(connecting_edge.metadata["from_vertex"])
        to_vertex = str(connecting_edge.metadata["to_vertex"])
        projection_vertex = _projection_vertex_for_traversal([from_vertex, to_vertex], literal_results)
        projection = _projection_items_from_substantive_terms(
            decomposition=decomposition,
            candidates=candidates,
            registry=registry,
            selected_vertices=[from_vertex, to_vertex],
        )
        if not projection:
            projection = [_id_projection_item(projection_vertex, registry)]
        selected_properties = _append_projection_properties(selected_properties, projection)
        coverage = _coverage_with_projection_terms(decomposition, coverage, projection)
        return {
            "query_shape": "single_hop",
            "selected_vertices": [from_vertex, to_vertex],
            "selected_edges": [{"name": connecting_edge.semantic_id, "direction": "forward"}],
            "selected_properties": selected_properties,
            "selected_literals": literal_payloads,
            "filters": filters,
            "projection": projection,
            "coverage": coverage,
        }

    if len(vertex_ids) == 1:
        projection = _projection_items_from_substantive_terms(
            decomposition=decomposition,
            candidates=candidates,
            registry=registry,
            selected_vertices=[vertex_ids[0]],
        )
        if not projection:
            projection = [_id_projection_item(vertex_ids[0], registry)]
        selected_properties = _append_projection_properties(selected_properties, projection)
        coverage = _coverage_with_projection_terms(decomposition, coverage, projection)
        return {
            "query_shape": "vertex_lookup",
            "selected_vertices": [vertex_ids[0]],
            "selected_properties": selected_properties,
            "selected_literals": literal_payloads,
            "filters": filters,
            "projection": projection,
            "coverage": coverage,
        }

    return None


def _candidate_ids(candidates: list[SemanticCandidate], semantic_type: str) -> list[str]:
    ids: list[str] = []
    for candidate in candidates:
        if candidate.semantic_type == semantic_type and candidate.semantic_id not in ids:
            ids.append(candidate.semantic_id)
    return ids


def _filters_from_literal_results(
    results: list[LiteralResolverResult],
    *,
    decomposition: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    filters: list[dict[str, Any]] = []
    for result in results:
        if not result.resolved or result.expected_property is None:
            continue
        owner = result.expected_vertex or result.expected_edge
        if owner is None:
            continue
        filters.append(
            {
                "owner": owner,
                "property": result.expected_property,
                "operator": _filter_operator_for_literal(decomposition or {}, result),
                "raw_literal": result.raw_literal,
            }
        )
    return filters


def _property_refs_from_filters(filters: list[dict[str, Any]]) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    for item in filters:
        refs = _append_unique_property_ref(
            refs,
            {"owner": str(item["owner"]), "name": str(item["property"])},
        )
    return refs


def _append_unique_property_ref(
    refs: list[dict[str, str]],
    item: dict[str, str],
) -> list[dict[str, str]]:
    if item not in refs:
        refs.append(item)
    return refs


def _is_count_slot(decomposition: Mapping[str, Any]) -> bool:
    return str(decomposition.get("intent_type") or "").strip() == "count" or str(
        decomposition.get("output_shape") or ""
    ).strip() == "scalar"


def _count_target_vertex(
    decomposition: Mapping[str, Any],
    vertex_ids: list[str],
    literal_results: list[LiteralResolverResult],
) -> str | None:
    for result in literal_results:
        if result.expected_vertex in vertex_ids:
            return result.expected_vertex
    question = str(decomposition.get("original_question") or decomposition.get("question") or "")
    if "台" in question and "NetworkElement" in vertex_ids:
        return "NetworkElement"
    return vertex_ids[0] if vertex_ids else None


def _vertex_id_property(vertex_name: str, candidates: list[SemanticCandidate]) -> str:
    for candidate in candidates:
        if candidate.semantic_type == "vertex" and candidate.semantic_id == vertex_name:
            return str(candidate.metadata.get("id_property") or "id")
    return "id"


def _best_connecting_edge(
    vertex_ids: list[str],
    edges: list[SemanticCandidate],
) -> SemanticCandidate | None:
    vertices = set(vertex_ids)
    connecting = [
        edge
        for edge in edges
        if edge.metadata.get("from_vertex") in vertices and edge.metadata.get("to_vertex") in vertices
    ]
    if not connecting:
        return None
    return max(connecting, key=lambda edge: (edge.score, _edge_match_priority(edge), edge.semantic_id))


def _edge_match_priority(edge: SemanticCandidate) -> int:
    if edge.match_type == "exact":
        return 3
    if edge.match_type == "synonym":
        return 2
    if edge.match_type == "text":
        return 1
    return 0


def _projection_vertex_for_traversal(
    vertices: list[str],
    literal_results: list[LiteralResolverResult],
) -> str:
    filtered_owners = {result.expected_vertex or result.expected_edge for result in literal_results}
    for vertex in reversed(vertices):
        if vertex not in filtered_owners:
            return vertex
    return vertices[-1]


def _projection_items_from_substantive_terms(
    *,
    decomposition: Mapping[str, Any],
    candidates: list[SemanticCandidate],
    registry: Any,
    selected_vertices: list[str],
) -> list[dict[str, Any]]:
    attachment_anchors = _projection_attachment_anchor_terms(decomposition)
    endpoint_owner_aliases = _endpoint_projection_owner_aliases(
        decomposition,
        candidates=candidates,
        registry=registry,
        selected_vertices=selected_vertices,
    )
    projection_terms = [
        item
        for item in _substantive_terms_with_slot(decomposition, slot="projection")
        if _norm(str(item.get("text") or "")) not in attachment_anchors
        if not _is_endpoint_projection_anchor(
            item,
            candidates=candidates,
            registry=registry,
            selected_vertices=selected_vertices,
        )
        if not _resolve_projection_vertex_full(
            item,
            candidates=candidates,
            registry=registry,
            selected_vertices=selected_vertices,
            decomposition=decomposition,
        )
        and (
            _resolve_projection_object_owner(
                item,
                candidates=candidates,
                registry=registry,
                selected_vertices=selected_vertices,
            )
            is not None
            or _projection_slot_term_requires_property(
                item,
                candidates=candidates,
                registry=registry,
                selected_vertices=selected_vertices,
            )
        )
    ]

    items: list[dict[str, Any]] = []
    for slot_term in _substantive_terms_with_slot(decomposition, slot="projection"):
        if _norm(str(slot_term.get("text") or "")) in attachment_anchors:
            continue
        vertex_name = _resolve_projection_vertex_full(
            slot_term,
            candidates=candidates,
            registry=registry,
            selected_vertices=selected_vertices,
            decomposition=decomposition,
        )
        if vertex_name is None:
            continue
        item = {
            "semantic_type": "vertex_full",
            "name": vertex_name,
            "alias": _snake_case(vertex_name),
            "projection_terms": _projection_vertex_full_terms(slot_term, decomposition),
        }
        existing = next(
            (
                existing
                for existing in items
                if existing.get("semantic_type") == "vertex_full" and existing.get("name") == vertex_name
            ),
            None,
        )
        if existing is None:
            items.append(item)
        else:
            existing_terms = existing.setdefault("projection_terms", [])
            term = str(slot_term["text"])
            if term not in existing_terms:
                existing_terms.append(term)

    for slot_term in projection_terms:
        compound_vertex_full_owners = _compound_vertex_full_owner_names(
            str(slot_term.get("text") or ""),
            candidates=candidates,
            registry=registry,
            selected_vertices=selected_vertices,
        )
        object_owner = _resolve_projection_object_owner(
            slot_term,
            candidates=candidates,
            registry=registry,
            selected_vertices=selected_vertices,
        )
        if object_owner is not None and not (len(selected_vertices) > 2 and compound_vertex_full_owners):
            projection_term = str(slot_term["text"])
            if _object_projection_requires_vertex_full(
                slot_term,
                decomposition,
            ) or (
                _projection_object_is_path_context(slot_term, decomposition)
                and not _has_other_concrete_projection_term(
                    slot_term,
                    decomposition=decomposition,
                    candidates=candidates,
                    registry=registry,
                    selected_vertices=selected_vertices,
                )
            ):
                item = {
                    "semantic_type": "vertex_full",
                    "name": object_owner,
                    "alias": _snake_case(object_owner),
                    "projection_terms": [projection_term],
                }
                existing = next(
                    (
                        existing
                        for existing in items
                        if existing.get("semantic_type") == "vertex_full" and existing.get("name") == object_owner
                    ),
                    None,
                )
                if existing is None:
                    items.append(item)
                else:
                    existing_terms = existing.setdefault("projection_terms", [])
                    if projection_term not in existing_terms:
                        existing_terms.append(projection_term)
                continue

            item = _id_projection_item(object_owner, registry)
            item["projection_terms"] = [projection_term]
            if not any(
                existing.get("owner") == item["owner"] and existing.get("name") == item["name"]
                for existing in items
            ):
                items.append(item)
                continue
            for existing in items:
                if existing.get("owner") == item["owner"] and existing.get("name") == item["name"]:
                    existing_terms = existing.setdefault("projection_terms", [])
                    if projection_term not in existing_terms:
                        existing_terms.append(projection_term)
                    break
            continue

        source_target_override = _source_target_projection_owner_alias(
            slot_term,
            selected_vertices=selected_vertices,
        )
        if source_target_override is not None:
            owner, alias = source_target_override
            property_ref = _resolve_projection_property_for_owners(
                str(slot_term.get("text") or ""),
                owners=[owner],
                registry=registry,
                require_unique_across_owners=False,
            )
            if property_ref is None:
                continue
            owner, property_name = property_ref
            item = {
                "semantic_type": "property",
                "owner": owner,
                "name": property_name,
                "alias": alias,
                "projection_terms": [str(slot_term["text"])],
            }
            if not any(
                existing.get("owner") == owner and existing.get("name") == property_name
                for existing in items
            ):
                items.append(item)
                continue
            for existing in items:
                if existing.get("owner") == owner and existing.get("name") == property_name:
                    existing["alias"] = alias
                    existing_terms = existing.setdefault("projection_terms", [])
                    term = str(slot_term["text"])
                    if term not in existing_terms:
                        existing_terms.append(term)
                    break
            continue

        property_refs = _resolve_projection_property_refs(
            slot_term,
            candidates=candidates,
            registry=registry,
            selected_vertices=selected_vertices,
            decomposition=decomposition,
        )
        if property_refs is None:
            continue
        for owner, property_name in property_refs:
            item = {
                "semantic_type": "property",
                "owner": owner,
                "name": property_name,
                "alias": _projection_property_alias(
                    owner=owner,
                    property_name=property_name,
                    slot_term=slot_term,
                    endpoint_owner_aliases=endpoint_owner_aliases,
                    expanded_refs=property_refs,
                ),
                "projection_terms": [str(slot_term["text"])],
            }
            if not any(
                existing.get("owner") == owner and existing.get("name") == property_name
                for existing in items
            ):
                items.append(item)
                continue
            for existing in items:
                if existing.get("owner") == owner and existing.get("name") == property_name:
                    existing_terms = existing.setdefault("projection_terms", [])
                    term = str(slot_term["text"])
                    if term not in existing_terms:
                        existing_terms.append(term)
                    break
    _sort_projection_items_by_term_order(items, decomposition)
    _append_implicit_relation_source_name_projection(
        items,
        decomposition=decomposition,
        candidates=candidates,
        registry=registry,
        selected_vertices=selected_vertices,
    )
    return items


def _sort_projection_items_by_term_order(items: list[dict[str, Any]], decomposition: Mapping[str, Any]) -> None:
    order: dict[str, int] = {}
    for index, term in enumerate(_substantive_terms_with_slot(decomposition, slot="projection")):
        text = str(term.get("text") or "").strip()
        if text and text not in order:
            order[text] = index
    if not order:
        return

    def item_order(item: Mapping[str, Any]) -> int:
        terms = [
            str(term).strip()
            for term in item.get("projection_terms", [])
            if str(term).strip()
        ]
        return min((order.get(term, 10_000) for term in terms), default=10_000)

    items.sort(key=item_order)


def _append_implicit_relation_source_name_projection(
    items: list[dict[str, Any]],
    *,
    decomposition: Mapping[str, Any],
    candidates: list[SemanticCandidate],
    registry: Any,
    selected_vertices: list[str],
) -> None:
    source_owners = _implicit_relation_source_name_owners(
        decomposition=decomposition,
        candidates=candidates,
        registry=registry,
        selected_vertices=selected_vertices,
    )
    if not source_owners:
        return
    has_downstream_projection = any(
        item.get("semantic_type") == "property"
        and item.get("owner") not in source_owners
        for item in items
    )
    if not has_downstream_projection:
        return

    for owner in source_owners:
        owner_items = [
            item
            for item in items
            if item.get("semantic_type") == "property" and item.get("owner") == owner
        ]
        if _rewrite_relation_source_object_id_to_name(
            owner_items,
            owner=owner,
            candidates=candidates,
            registry=registry,
            selected_vertices=selected_vertices,
        ):
            continue
        if owner_items:
            continue
        _insert_relation_source_identity_projection(items, owner=owner, registry=registry)


def _rewrite_relation_source_object_id_to_name(
    owner_items: list[dict[str, Any]],
    *,
    owner: str,
    candidates: list[SemanticCandidate],
    registry: Any,
    selected_vertices: list[str],
) -> bool:
    if len(owner_items) != 1:
        return False
    item = owner_items[0]
    identity_property = _identity_projection_property_name(owner, registry)
    if identity_property in {None, item.get("name")}:
        return bool(identity_property == item.get("name"))
    try:
        vertex = registry.get_vertex(owner)
    except RegistryLookupError:
        return False
    if item.get("name") != vertex.id_property:
        return False
    projection_terms = [
        str(term).strip()
        for term in item.get("projection_terms", [])
        if str(term).strip()
    ]
    if not any(
        _resolve_projection_object_owner(
            {"text": term},
            candidates=candidates,
            registry=registry,
            selected_vertices=selected_vertices,
        )
        == owner
        for term in projection_terms
    ):
        return False
    item["name"] = identity_property
    item["alias"] = f"{_snake_case(owner)}_{identity_property}"
    return True


def _insert_relation_source_identity_projection(
    items: list[dict[str, Any]],
    *,
    owner: str,
    registry: Any,
) -> None:
    identity_property = _identity_projection_property_name(owner, registry)
    if identity_property is None:
        return
    source_item = {
        "semantic_type": "property",
        "owner": owner,
        "name": identity_property,
        "alias": f"{_snake_case(owner)}_{identity_property}",
        "projection_terms": ["名称"],
    }
    insert_at = next(
        (
            index
            for index, item in enumerate(items)
            if item.get("semantic_type") == "property" and item.get("owner") != owner
        ),
        len(items),
    )
    items.insert(insert_at, source_item)


def _identity_projection_property_name(owner: str, registry: Any) -> str | None:
    try:
        registry.get_property(owner, "name")
        return "name"
    except RegistryLookupError:
        pass
    try:
        vertex = registry.get_vertex(owner)
        registry.get_property(owner, vertex.id_property)
    except RegistryLookupError:
        return None
    return str(vertex.id_property)


def _implicit_relation_source_name_owners(
    *,
    decomposition: Mapping[str, Any],
    candidates: list[SemanticCandidate],
    registry: Any,
    selected_vertices: list[str],
) -> list[str]:
    if len(selected_vertices) < 2:
        return []
    question = str(decomposition.get("original_question") or decomposition.get("question") or "")
    if "及其" not in question:
        return []

    owners: list[str] = []
    start = 0
    while True:
        marker_index = question.find("及其", start)
        if marker_index < 0:
            break
        start = marker_index + len("及其")
        prefix = question[:marker_index]
        suffix = question[start:]
        prefix_owners = _projection_attached_owner_names(
            prefix,
            candidates=candidates,
            registry=registry,
            selected_vertices=selected_vertices,
        )
        if len(prefix_owners) != 1:
            continue
        owner = prefix_owners[0]
        suffix_owners = _projection_attached_owner_names(
            suffix,
            candidates=candidates,
            registry=registry,
            selected_vertices=selected_vertices,
        )
        if not any(suffix_owner != owner for suffix_owner in suffix_owners):
            continue
        if owner not in owners:
            owners.append(owner)
    return owners


def _projection_attachment_anchor_terms(decomposition: Mapping[str, Any]) -> set[str]:
    projection_terms = list(_substantive_terms_with_slot(decomposition, slot="projection"))
    term_texts = {
        _norm(str(item.get("text") or ""))
        for item in projection_terms
        if str(item.get("text") or "").strip()
    }
    anchors: set[str] = set()
    for item in projection_terms:
        attached_to = _norm(str(item.get("attached_to") or ""))
        if not attached_to:
            continue
        anchors.update(
            term
            for term in term_texts
            if len(term) >= 2 and (attached_to == term or term in attached_to)
        )
    return anchors


def _endpoint_projection_owner_aliases(
    decomposition: Mapping[str, Any],
    *,
    candidates: list[SemanticCandidate],
    registry: Any,
    selected_vertices: list[str],
) -> dict[str, str]:
    aliases: dict[str, str] = {}
    next_aliases = ["source", "target"]
    for item in _substantive_terms_with_slot(decomposition, slot="projection"):
        if not _is_endpoint_projection_anchor(
            item,
            candidates=candidates,
            registry=registry,
            selected_vertices=selected_vertices,
        ):
            continue
        owners = _projection_attached_owner_names(
            str(item.get("attached_to") or item.get("text") or ""),
            candidates=candidates,
            registry=registry,
            selected_vertices=selected_vertices,
        )
        if len(owners) != 1 or owners[0] in aliases:
            continue
        if len(aliases) >= len(next_aliases):
            return {}
        aliases[owners[0]] = next_aliases[len(aliases)]
    return aliases if len(aliases) >= 2 else {}


def _is_endpoint_projection_anchor(
    slot_term: Mapping[str, Any],
    *,
    candidates: list[SemanticCandidate],
    registry: Any,
    selected_vertices: list[str],
) -> bool:
    text = str(slot_term.get("text") or "").strip()
    normalized = _norm(text)
    if not normalized or not normalized.endswith("端"):
        return False
    owners = _projection_attached_owner_names(
        str(slot_term.get("attached_to") or text),
        candidates=candidates,
        registry=registry,
        selected_vertices=selected_vertices,
    )
    return len(owners) == 1


def _source_target_projection_owner_alias(
    slot_term: Mapping[str, Any],
    *,
    selected_vertices: list[str],
) -> tuple[str, str] | None:
    if len(selected_vertices) < 2:
        return None
    text = _norm(str(slot_term.get("text") or ""))
    if text.startswith("源") or text.startswith("source"):
        return selected_vertices[0], "source"
    if text.startswith("目标") or text.startswith("target"):
        return selected_vertices[-1], "target"
    return None


def _projection_property_alias(
    *,
    owner: str,
    property_name: str,
    slot_term: Mapping[str, Any],
    endpoint_owner_aliases: Mapping[str, str],
    expanded_refs: list[tuple[str, str]],
) -> str:
    if len(expanded_refs) > 1 and owner in endpoint_owner_aliases:
        return f"{endpoint_owner_aliases[owner]}_{_projection_alias_property_suffix(property_name)}"
    return f"{_snake_case(owner)}_{property_name}"


def _projection_alias_property_suffix(property_name: str) -> str:
    if property_name == "elem_type":
        return "type"
    return property_name


def _resolve_projection_object_owner(
    slot_term: Mapping[str, Any],
    *,
    candidates: list[SemanticCandidate],
    registry: Any,
    selected_vertices: list[str],
) -> str | None:
    text = str(slot_term.get("text") or "").strip()
    normalized_text = _norm(text)
    compact_text = _compact_surface(normalized_text)
    if not normalized_text:
        return None

    matches: list[str] = []
    for owner in selected_vertices:
        try:
            vertex = registry.get_vertex(owner)
        except RegistryLookupError:
            continue
        surfaces = [
            owner,
            vertex.name,
            *(
                item
                for item in vertex.ai_context.get("synonyms", [])
                if isinstance(item, str)
            ),
        ]
        candidate = next(
            (
                candidate
                for candidate in candidates
                if candidate.semantic_type == "vertex" and candidate.semantic_id == owner
            ),
            None,
        )
        if candidate is not None:
            surfaces.append(candidate.semantic_name)
            surfaces.extend(evidence.term for evidence in candidate.evidence)
            surfaces.extend(evidence.matched_text for evidence in candidate.evidence)
        if any(
            normalized_text == _norm(surface)
            or compact_text == _compact_surface(_norm(surface))
            for surface in surfaces
            if str(surface).strip()
        ):
            matches.append(owner)

    return matches[0] if len(matches) == 1 else None


def _resolve_projection_vertex_full(
    slot_term: Mapping[str, Any],
    *,
    candidates: list[SemanticCandidate],
    registry: Any,
    selected_vertices: list[str],
    decomposition: Mapping[str, Any] | None = None,
) -> str | None:
    text = str(slot_term.get("text") or "").strip()
    normalized_text = _norm(text)
    if not _is_vertex_full_projection_text(text):
        owner = _resolve_compound_vertex_full_owner(
            text,
            candidates=candidates,
            registry=registry,
            selected_vertices=selected_vertices,
        )
        return owner

    if normalized_text in {"信息", "节点信息"} and len(selected_vertices) > 2:
        return None

    attached_to = str(slot_term.get("attached_to") or "").strip()
    attached_owners = _projection_attached_owner_names(
        attached_to or text,
        candidates=candidates,
        registry=registry,
        selected_vertices=selected_vertices,
    )
    if not attached_owners:
        attached_owners = _projection_chained_attachment_owner_names(
            attached_to,
            decomposition=decomposition,
            candidates=candidates,
            registry=registry,
            selected_vertices=selected_vertices,
        )
    if not attached_owners and len(selected_vertices) == 1:
        attached_owners = [selected_vertices[0]]

    owners = [owner for owner in selected_vertices if owner in attached_owners]
    if len(owners) != 1:
        return None
    return owners[0]


def _resolve_compound_vertex_full_owner(
    text: str,
    *,
    candidates: list[SemanticCandidate],
    registry: Any,
    selected_vertices: list[str],
) -> str | None:
    if len(selected_vertices) > 2:
        return None
    matches = _compound_vertex_full_owner_names(
        text,
        candidates=candidates,
        registry=registry,
        selected_vertices=selected_vertices,
    )
    return matches[0] if len(matches) == 1 else None


def _compound_vertex_full_owner_names(
    text: str,
    *,
    candidates: list[SemanticCandidate],
    registry: Any,
    selected_vertices: list[str],
) -> list[str]:
    normalized_text = _norm(text)
    if not normalized_text:
        return []
    matches: list[str] = []
    for suffix in _VERTEX_FULL_COMPOUND_SUFFIX_TERMS:
        normalized_suffix = _norm(suffix)
        if not normalized_suffix or not normalized_text.endswith(normalized_suffix):
            continue
        prefix = normalized_text[: -len(normalized_suffix)]
        if not prefix:
            continue
        owners = _projection_attached_owner_names(
            prefix,
            candidates=candidates,
            registry=registry,
            selected_vertices=selected_vertices,
        )
        for owner in owners:
            if owner not in matches:
                matches.append(owner)
    return matches


def _projection_chained_attachment_owner_names(
    attached_to: str,
    *,
    decomposition: Mapping[str, Any] | None,
    candidates: list[SemanticCandidate],
    registry: Any,
    selected_vertices: list[str],
) -> list[str]:
    attached = _norm(attached_to)
    if not attached or decomposition is None:
        return []
    for item in _substantive_terms_with_slot(decomposition, slot="projection"):
        text = str(item.get("text") or "").strip()
        if _norm(text) != attached or not _is_vertex_full_projection_text(text):
            continue
        owners = _projection_attached_owner_names(
            str(item.get("attached_to") or ""),
            candidates=candidates,
            registry=registry,
            selected_vertices=selected_vertices,
        )
        if len(owners) == 1:
            return owners
    return []


def _projection_vertex_full_terms(slot_term: Mapping[str, Any], decomposition: Mapping[str, Any]) -> list[str]:
    text = str(slot_term.get("text") or "").strip()
    terms: list[str] = []
    attached = _norm(str(slot_term.get("attached_to") or ""))
    if attached:
        for item in _substantive_terms_with_slot(decomposition, slot="projection"):
            anchor_text = str(item.get("text") or "").strip()
            if _norm(anchor_text) == attached and _is_vertex_full_projection_text(anchor_text):
                terms.append(anchor_text)
                break
    if text:
        terms.append(text)
    return terms


def _is_vertex_full_projection_text(text: str) -> bool:
    normalized = _norm(text).replace(" ", "")
    return normalized in _VERTEX_FULL_PROJECTION_TERMS


def _projection_slot_term_requires_property(
    slot_term: Mapping[str, Any],
    *,
    candidates: list[SemanticCandidate],
    registry: Any,
    selected_vertices: list[str],
) -> bool:
    text = str(slot_term.get("text") or "").strip()
    if not text:
        return False
    attached_to = str(slot_term.get("attached_to") or "").strip()
    if attached_to:
        return True
    if _has_exact_projection_property_match(
        text,
        candidates=candidates,
        registry=registry,
        selected_vertices=selected_vertices,
    ):
        return True
    if _compound_vertex_full_owner_names(
        text,
        candidates=candidates,
        registry=registry,
        selected_vertices=selected_vertices,
    ):
        return True
    object_matches = _attached_vertex_names(text, candidates)
    if not object_matches:
        object_matches = _attached_vertex_names_from_registry(
            text,
            registry=registry,
            selected_vertices=selected_vertices,
        )
    if any(vertex in object_matches for vertex in selected_vertices):
        return False
    return True


def _object_projection_requires_vertex_full(
    slot_term: Mapping[str, Any],
    decomposition: Mapping[str, Any],
) -> bool:
    text = _compact_surface(_norm(str(slot_term.get("text") or "")))
    if not text:
        return False
    question = _compact_surface(
        _norm(str(decomposition.get("original_question") or decomposition.get("question") or ""))
    )
    if not question:
        return False
    return any(
        f"{text}{_compact_surface(_norm(suffix))}" in question
        for suffix in _OBJECT_INFO_PROJECTION_SUFFIXES
    )


def _naked_object_projection_clarification_question(
    decomposition: Mapping[str, Any],
    *,
    candidates: list[SemanticCandidate],
    registry: Any,
) -> str | None:
    if _is_scalar_or_aggregate_output(decomposition):
        return None

    selected_vertices = _candidate_ids(candidates, "vertex")
    if not selected_vertices:
        return None

    projection_terms = [
        item
        for item in _substantive_terms_with_slot(decomposition, slot="projection")
        if str(item.get("text") or "").strip()
    ]
    if not projection_terms:
        return None

    has_naked_object_term = False
    for slot_term in projection_terms:
        if _resolve_projection_vertex_full(
            slot_term,
            candidates=candidates,
            registry=registry,
            selected_vertices=selected_vertices,
            decomposition=decomposition,
        ):
            return None
        if _object_projection_requires_vertex_full(slot_term, decomposition):
            return None
        if _projection_slot_term_requires_property(
            slot_term,
            candidates=candidates,
            registry=registry,
            selected_vertices=selected_vertices,
        ):
            return None

        object_owner = _resolve_projection_object_owner(
            slot_term,
            candidates=candidates,
            registry=registry,
            selected_vertices=selected_vertices,
        )
        if object_owner is None:
            return None
        if _projection_object_is_path_context(slot_term, decomposition):
            continue
        has_naked_object_term = True

    if not has_naked_object_term:
        return None
    return _output_projection_clarification_text(selected_vertices)


def _projection_object_is_path_context(
    slot_term: Mapping[str, Any],
    decomposition: Mapping[str, Any],
) -> bool:
    if not _decomposition_has_path_context(decomposition):
        return False
    text = _compact_surface(_norm(str(slot_term.get("text") or "")))
    if not text:
        return False
    path_texts = {
        _compact_surface(_norm(str(item.get("text") or "")))
        for item in _substantive_terms_with_slot(decomposition, slot="path")
        if str(item.get("text") or "").strip()
    }
    if text in path_texts:
        return True
    question = _compact_surface(
        _norm(str(decomposition.get("original_question") or decomposition.get("question") or ""))
    )
    if not question:
        return True
    path_markers = ("使用", "经过", "途经", "穿过", "关联", "连接", "对应", "源", "目的", "目标")
    text_index = question.find(text)
    if text_index < 0:
        return True
    return any(
        marker in question[:text_index] or marker in question[text_index + len(text) :]
        for marker in path_markers
    )


def _has_other_concrete_projection_term(
    slot_term: Mapping[str, Any],
    *,
    decomposition: Mapping[str, Any],
    candidates: list[SemanticCandidate],
    registry: Any,
    selected_vertices: list[str],
) -> bool:
    text = str(slot_term.get("text") or "").strip()
    attached_to = str(slot_term.get("attached_to") or "").strip()
    for other in _substantive_terms_with_slot(decomposition, slot="projection"):
        other_text = str(other.get("text") or "").strip()
        other_attached_to = str(other.get("attached_to") or "").strip()
        if other_text == text and other_attached_to == attached_to:
            continue
        if _projection_slot_term_requires_property(
            other,
            candidates=candidates,
            registry=registry,
            selected_vertices=selected_vertices,
        ):
            return True
    return False


def _decomposition_has_path_context(decomposition: Mapping[str, Any]) -> bool:
    if _substantive_terms_with_slot(decomposition, slot="path"):
        return True
    structural = decomposition.get("structural_requirements")
    if isinstance(structural, Mapping):
        path_terms = structural.get("path_terms")
        if isinstance(path_terms, list) and path_terms:
            return True
        try:
            return int(structural.get("min_path_hops") or 0) > 0
        except (TypeError, ValueError):
            return False
    return False


def _is_scalar_or_aggregate_output(decomposition: Mapping[str, Any]) -> bool:
    output_shape = str(decomposition.get("output_shape") or "").strip()
    intent_type = str(decomposition.get("intent_type") or "").strip()
    structural = decomposition.get("structural_requirements")
    requires_aggregate = isinstance(structural, Mapping) and bool(structural.get("requires_aggregate"))
    raw_terms = decomposition.get("substantive_terms")
    substantive_terms = [term for term in raw_terms if isinstance(term, Mapping)] if isinstance(raw_terms, list) else []
    structural_slots = {
        str(term.get("slot") or "").strip()
        for term in substantive_terms
    }
    has_aggregate_structure = bool(structural_slots & {"group_by", "order_by", "limit"})
    has_quantity_projection = any(
        _is_quantity_projection_text(str(term.get("text") or ""))
        for term in _substantive_terms_with_slot(decomposition, slot="projection")
        if isinstance(term, Mapping)
    )
    return (
        output_shape == "scalar"
        or intent_type in {"count", "aggregate", "top_n"}
        or requires_aggregate
        or has_aggregate_structure
        or has_quantity_projection
    )


def _output_projection_clarification_text(selected_vertices: list[str]) -> str:
    owners = "、".join(_snake_case(owner) for owner in selected_vertices[:3])
    if owners:
        return f"请明确需要返回 {owners} 的哪些字段，或说明是否返回完整节点信息。"
    return "请明确需要返回哪些字段，或说明是否返回完整节点信息。"


def _resolve_projection_property(
    slot_term: Mapping[str, Any],
    *,
    candidates: list[SemanticCandidate],
    registry: Any,
    selected_vertices: list[str],
) -> tuple[str, str] | None:
    refs = _resolve_projection_property_refs(
        slot_term,
        candidates=candidates,
        registry=registry,
        selected_vertices=selected_vertices,
    )
    if refs is None or len(refs) != 1:
        return None
    return refs[0]


def _resolve_projection_property_refs(
    slot_term: Mapping[str, Any],
    *,
    candidates: list[SemanticCandidate],
    registry: Any,
    selected_vertices: list[str],
    decomposition: Mapping[str, Any] | None = None,
) -> list[tuple[str, str]] | None:
    term = str(slot_term.get("text") or "").strip()
    if not term:
        return None

    attached_to = str(slot_term.get("attached_to") or "").strip()
    attached_owners = _projection_attached_owner_names(
        attached_to,
        candidates=candidates,
        registry=registry,
        selected_vertices=selected_vertices,
    )
    if len(attached_owners) > 1:
        refs: list[tuple[str, str]] = []
        for owner in attached_owners:
            property_ref = _resolve_projection_property_for_owners(
                term,
                owners=[owner],
                registry=registry,
            )
            if property_ref is None:
                return None
            refs.append(property_ref)
        return refs

    owners = [owner for owner in selected_vertices if not attached_owners or owner in attached_owners]
    if not owners:
        owners = selected_vertices
    expanded_owners = _expanded_projection_property_owners(
        term,
        owners=owners,
        candidates=candidates,
        registry=registry,
        selected_vertices=selected_vertices,
        decomposition=decomposition,
    )
    if expanded_owners:
        refs: list[tuple[str, str]] = []
        for owner in expanded_owners:
            property_ref = _resolve_projection_property_for_owners(
                term,
                owners=[owner],
                registry=registry,
                require_unique_across_owners=False,
            )
            if property_ref is None:
                return None
            refs.append(property_ref)
        return refs
    property_ref = _resolve_projection_property_for_owners(
        term,
        owners=owners,
        registry=registry,
        require_unique_across_owners=(attached_to == "" and len(selected_vertices) > 1),
    )
    return [property_ref] if property_ref is not None else None


def _expanded_projection_property_owners(
    term: str,
    *,
    owners: list[str],
    candidates: list[SemanticCandidate],
    registry: Any,
    selected_vertices: list[str],
    decomposition: Mapping[str, Any] | None,
) -> list[str]:
    if decomposition is None or len(owners) < 2:
        return []
    endpoint_aliases = _endpoint_projection_owner_aliases(
        decomposition,
        candidates=candidates,
        registry=registry,
        selected_vertices=selected_vertices,
    )
    if endpoint_aliases:
        return [owner for owner in selected_vertices if owner in endpoint_aliases and owner in owners]
    question = str(decomposition.get("original_question") or decomposition.get("question") or "")
    if not any(marker in question for marker in ("双方", "两端", "各自", "分别")):
        return []
    resolved: list[str] = []
    for owner in owners:
        if _resolve_projection_property_for_owners(
            term,
            owners=[owner],
            registry=registry,
            require_unique_across_owners=False,
        ):
            resolved.append(owner)
    return resolved if len(resolved) >= 2 else []


def _resolve_projection_property_for_owners(
    term: str,
    *,
    owners: list[str],
    registry: Any,
    require_unique_across_owners: bool = True,
) -> tuple[str, str] | None:
    matches: list[tuple[int, int, str, str]] = []
    for owner_index, owner in enumerate(owners):
        try:
            vertex = registry.get_vertex(owner)
        except RegistryLookupError:
            continue
        for prop in vertex.properties:
            score = _property_surface_match_score(term, owner, prop)
            if score <= 0:
                continue
            matches.append((score, -owner_index, owner, prop.name))

    if not matches:
        return None
    matches.sort(reverse=True)
    best_score, _, best_owner, best_name = matches[0]
    same_score = [(owner, name) for score, _, owner, name in matches if score == best_score]
    if require_unique_across_owners and len({(owner, name) for owner, name in same_score}) > 1:
        return None
    same_owner_best = [(owner, name) for owner, name in same_score if owner == best_owner]
    if len({name for _, name in same_owner_best}) > 1:
        return None
    return best_owner, best_name


def _projection_attached_owner_names(
    attached_to: str,
    *,
    candidates: list[SemanticCandidate],
    registry: Any,
    selected_vertices: list[str],
) -> list[str]:
    attached = _norm(attached_to)
    if not attached:
        return []

    selected_index = {owner: index for index, owner in enumerate(selected_vertices)}
    matches: list[tuple[int, int, str]] = []
    seen: set[str] = set()
    for owner in selected_vertices:
        try:
            vertex = registry.get_vertex(owner)
        except RegistryLookupError:
            continue
        surfaces = [
            owner,
            vertex.name,
            *(
                item
                for item in vertex.ai_context.get("synonyms", [])
                if isinstance(item, str)
            ),
        ]
        candidate = next(
            (
                candidate
                for candidate in candidates
                if candidate.semantic_type == "vertex" and candidate.semantic_id == owner
            ),
            None,
        )
        if candidate is not None:
            surfaces.append(candidate.semantic_name)
            surfaces.extend(evidence.term for evidence in candidate.evidence)
            surfaces.extend(evidence.matched_text for evidence in candidate.evidence)
        positions = [
            position
            for surface in surfaces
            for position in [_projection_surface_position(attached, _norm(surface))]
            if position is not None
        ]
        if not positions or owner in seen:
            continue
        matches.append((min(positions), selected_index.get(owner, len(selected_vertices)), owner))
        seen.add(owner)

    if not matches:
        return []
    matches.sort()
    owners = [owner for _, _, owner in matches]
    if len(owners) == 1:
        return owners
    if _projection_attachment_is_coordinated(attached):
        return owners
    return [owners[-1]]


def _projection_surface_position(attached: str, surface: str) -> int | None:
    if not attached or not surface or len(surface) < 2:
        return None
    if attached == surface or attached.startswith(surface):
        return 0
    position = attached.find(surface)
    return position if position >= 0 else None


def _projection_attachment_is_coordinated(attached: str) -> bool:
    return any(marker in attached for marker in ("及其", "以及", "和", "与", "各自", "双方", "两端", "分别"))


def _property_surface_match_score(term: str, owner: str, prop: Any) -> int:
    normalized_term = _norm(term)
    compact_term = _compact_surface(normalized_term)
    if not normalized_term:
        return 0
    surfaces = [
        prop.name,
        f"{owner}.{prop.name}",
        *_PROPERTY_PROJECTION_TERM_SURFACES.get(prop.name, ()),
        *(
            item
            for item in prop.ai_context.get("synonyms", [])
            if isinstance(item, str)
        ),
    ]
    for surface in surfaces:
        normalized_surface = _norm(surface)
        compact_surface = _compact_surface(normalized_surface)
        if normalized_term == normalized_surface or compact_term == compact_surface:
            return 100
    for surface in surfaces:
        normalized_surface = _norm(surface)
        compact_surface = _compact_surface(normalized_surface)
        if normalized_surface and (
            normalized_term in normalized_surface or normalized_surface in normalized_term
            or compact_term in compact_surface
            or compact_surface in compact_term
        ):
            if (
                normalized_surface in _GENERIC_PROPERTY_NAME_SURFACES
                and normalized_term != normalized_surface
                and compact_term != compact_surface
            ):
                return 60
            return 70
    return 0


def _compact_surface(value: str) -> str:
    return value.replace(" ", "")


_GENERIC_PROPERTY_NAME_SURFACES = {"名称", "名字", "name", "id", "ID", "编号"}


def _has_exact_projection_property_match(
    term: str,
    *,
    candidates: list[SemanticCandidate],
    registry: Any,
    selected_vertices: list[str],
) -> bool:
    attached_owners = _attached_vertex_names(term, candidates)
    owners = [owner for owner in selected_vertices if not attached_owners or owner in attached_owners]
    if not owners:
        owners = selected_vertices
    for owner in owners:
        try:
            vertex = registry.get_vertex(owner)
        except RegistryLookupError:
            continue
        if any(_property_surface_match_score(term, owner, prop) >= 100 for prop in vertex.properties):
            return True
    return False


_PROPERTY_PROJECTION_TERM_SURFACES = {
    "id": (
        "ID",
        "id",
        "编号",
        "标识",
        "identifier",
        "服务ID",
        "服务编号",
        "隧道ID",
        "隧道编号",
        "网元ID",
        "网元编号",
        "端口ID",
        "端口编号",
        "链路ID",
        "链路编号",
        "协议ID",
        "协议编号",
        "光纤ID",
        "光纤编号",
    ),
    "name": ("名称", "名字", "服务名称", "name"),
    "elem_type": ("类型", "网元类型", "元素类型", "设备类型", "服务类型", "type"),
    "quality_of_service": ("服务质量", "服务质量等级", "质量等级", "服务等级", "等级", "等级值", "QoS"),
    "bandwidth": ("带宽",),
    "latency": ("时延", "延迟", "延迟值"),
}


_VERTEX_FULL_PROJECTION_TERMS = {
    "节点",
    "信息",
    "节点信息",
    "详细信息",
    "详情",
    "完整信息",
    "全部信息",
    "所有信息",
    "全部属性",
    "全部属性信息",
    "所有属性",
    "所有属性信息",
}

_VERTEX_FULL_COMPOUND_SUFFIX_TERMS = ("节点信息", "节点", "信息")

_OBJECT_INFO_PROJECTION_SUFFIXES = ("信息", "详情", "详细信息", "完整信息", "全部信息", "所有信息", "节点")


def _attached_vertex_names_from_registry(
    attached_to: str,
    *,
    registry: Any,
    selected_vertices: list[str],
) -> set[str]:
    attached = _norm(attached_to)
    if not attached:
        return set()
    owners: set[str] = set()
    for owner in selected_vertices:
        try:
            vertex = registry.get_vertex(owner)
        except RegistryLookupError:
            continue
        surfaces = [
            owner,
            vertex.name,
            *(
                item
                for item in vertex.ai_context.get("synonyms", [])
                if isinstance(item, str)
            ),
        ]
        if any(_vertex_surface_matches_attached_text(attached, _norm(surface)) for surface in surfaces):
            owners.add(owner)
    return owners


def _substantive_terms_with_slot(decomposition: Mapping[str, Any], *, slot: str) -> list[dict[str, Any]]:
    raw_terms = decomposition.get("substantive_terms")
    if not isinstance(raw_terms, list | tuple):
        return []
    terms: list[dict[str, Any]] = []
    for item in raw_terms:
        if not isinstance(item, Mapping):
            continue
        item_slot = str(item.get("slot") or "").strip()
        if item_slot != slot:
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        payload = {"text": text, "slot": item_slot}
        attached_to = str(item.get("attached_to") or "").strip()
        if attached_to:
            payload["attached_to"] = attached_to
        terms.append(payload)
    return terms


def _id_projection_item(vertex_name: str, registry: Any) -> dict[str, Any]:
    vertex = registry.get_vertex(vertex_name)
    return {
        "semantic_type": "property",
        "owner": vertex_name,
        "name": vertex.id_property,
        "alias": f"{_snake_case(vertex_name)}_{vertex.id_property}",
    }


def _append_projection_properties(
    selected_properties: list[dict[str, str]],
    projection: list[dict[str, Any]],
) -> list[dict[str, str]]:
    refs = list(selected_properties)
    for item in projection:
        if item.get("semantic_type") != "property":
            continue
        refs = _append_unique_property_ref(
            refs,
            {"owner": str(item["owner"]), "name": str(item["name"])},
        )
    return refs


def _coverage_with_projection_terms(
    decomposition: Mapping[str, Any],
    coverage: Any,
    projection: list[dict[str, Any]],
) -> Any:
    if _is_count_slot(decomposition):
        return coverage
    projection_terms = _required_projection_terms(decomposition)
    if not projection_terms:
        return coverage
    covered: list[str] = []
    for item in projection:
        raw_terms = item.get("projection_terms")
        if not isinstance(raw_terms, list | tuple):
            continue
        for raw_term in raw_terms:
            term = str(raw_term).strip()
            if term and term not in covered:
                covered.append(term)
    uncovered = [term for term in projection_terms if term not in covered]
    payload = dict(coverage) if isinstance(coverage, Mapping) else CoverageReport.model_validate(coverage).model_dump(mode="json")
    payload["projection_terms"] = {
        "required": projection_terms,
        "covered": covered,
        "uncovered": uncovered,
    }
    return payload


def _required_projection_terms(decomposition: Mapping[str, Any]) -> list[str]:
    return list(_structural_requirements_for_precheck(decomposition).projection_terms)


def _covered_projection_terms(projection: list[dict[str, Any]]) -> list[str]:
    covered: list[str] = []
    for item in projection:
        raw_terms = item.get("projection_terms")
        if not isinstance(raw_terms, list | tuple):
            continue
        for raw_term in raw_terms:
            term = str(raw_term).strip()
            if term and term not in covered:
                covered.append(term)
    return covered


def _required_projection_slot_terms(
    decomposition: Mapping[str, Any],
    *,
    candidates: list[SemanticCandidate],
    registry: Any,
    selected_vertices: list[str],
) -> list[dict[str, Any]]:
    terms: list[dict[str, Any]] = []
    attachment_anchors = _projection_attachment_anchor_terms(decomposition)
    for item in _substantive_terms_with_slot(decomposition, slot="projection"):
        if _norm(str(item.get("text") or "")) in attachment_anchors:
            continue
        if _is_endpoint_projection_anchor(
            item,
            candidates=candidates,
            registry=registry,
            selected_vertices=selected_vertices,
        ):
            continue
        if _resolve_projection_vertex_full(
            item,
            candidates=candidates,
            registry=registry,
            selected_vertices=selected_vertices,
            decomposition=decomposition,
        ) or _resolve_projection_object_owner(
            item,
            candidates=candidates,
            registry=registry,
            selected_vertices=selected_vertices,
        ) or _projection_slot_term_requires_property(
            item,
            candidates=candidates,
            registry=registry,
            selected_vertices=selected_vertices,
        ):
            terms.append(item)
    return terms


def _uncovered_projection_slots(
    *,
    decomposition: Mapping[str, Any],
    projection: list[dict[str, Any]],
    candidates: list[SemanticCandidate],
    registry: Any,
    selected_vertices: list[str],
) -> list[str]:
    uncovered: list[str] = []
    for slot_term in _required_projection_slot_terms(
        decomposition,
        candidates=candidates,
        registry=registry,
        selected_vertices=selected_vertices,
    ):
        label = _projection_slot_label(slot_term)
        vertex_name = _resolve_projection_vertex_full(
            slot_term,
            candidates=candidates,
            registry=registry,
            selected_vertices=selected_vertices,
            decomposition=decomposition,
        )
        if vertex_name is not None:
            if not any(
                item.get("semantic_type") == "vertex_full" and item.get("name") == vertex_name
                for item in projection
            ):
                uncovered.append(label)
            continue

        property_refs = _resolve_projection_property_refs(
            slot_term,
            candidates=candidates,
            registry=registry,
            selected_vertices=selected_vertices,
            decomposition=decomposition,
        )
        if property_refs:
            for owner, property_name in property_refs:
                if not any(
                    item.get("semantic_type") == "property"
                    and item.get("owner") == owner
                    and item.get("name") == property_name
                    for item in projection
                ):
                    uncovered.append(label)
                    break
            continue

        object_owner = _resolve_projection_object_owner(
            slot_term,
            candidates=candidates,
            registry=registry,
            selected_vertices=selected_vertices,
        )
        if object_owner is not None:
            if not any(
                (
                    item.get("semantic_type") == "property"
                    and item.get("owner") == object_owner
                )
                or (
                    item.get("semantic_type") == "vertex_full"
                    and item.get("name") == object_owner
                )
                for item in projection
            ):
                uncovered.append(label)
            continue

        uncovered.append(label)
    return uncovered


def _projection_slot_label(slot_term: Mapping[str, Any]) -> str:
    text = str(slot_term.get("text") or "").strip()
    attached_to = str(slot_term.get("attached_to") or "").strip()
    return f"{attached_to}.{text}" if attached_to else text


def _snake_case(value: str) -> str:
    text = value.replace("-", "_")
    text = "".join(f"_{char.lower()}" if char.isupper() else char for char in text)
    return text.strip("_")


def _run_grounded_understanding_stage(
    trace: GraphTraceBuilder,
    *,
    decomposition: dict[str, Any],
    retrieval_result: CandidateRetrievalResult,
    literal_results: list[LiteralResolverResult],
    settings: Settings,
    llm_client: Any | None,
    registry: Any,
    attempt_no: int,
) -> Any:
    input_payload: dict[str, Any] = {
        "decomposition": decomposition,
        "resolved_literals": [result.model_dump(mode="json") for result in literal_results],
        "attempt_no": attempt_no,
    }
    llm_start = _llm_trace_count(llm_client)
    return _run_stage(
        trace,
        stage=StageName.GROUNDED_UNDERSTANDING,
        input_payload=input_payload,
        action=lambda: _select_grounded_understanding(
            decomposition=decomposition,
            retrieval_result=retrieval_result,
            literal_results=literal_results,
            settings=settings,
            llm_client=llm_client,
            registry=registry,
        ),
        output_payload=lambda result: _with_stage_llm_calls(
            _stage_result_payload(result),
            llm_client,
            llm_start,
            stage="grounded_understanding",
        ),
        metrics=lambda result: _llm_metrics(_llm_trace_slice(llm_client, llm_start)),
    )


def _run_candidate_reranker_stage(
    trace: GraphTraceBuilder,
    *,
    retrieval_result: CandidateRetrievalResult,
    structural_requirements: Mapping[str, Any],
) -> CandidateRetrievalResult:
    rerank_result = _run_stage(
        trace,
        stage=StageName.CANDIDATE_RERANKER,
        input_payload={
            "candidate_count": len(retrieval_result.candidates),
            "structural_requirements": structural_requirements,
        },
        action=lambda: StructuralReranker().rerank(
            retrieval_result,
            structural_requirements=structural_requirements,
        ),
        output_payload=lambda result: {
            "schema_version": "candidate_structural_rerank_v1",
            "candidates": [candidate.model_dump(mode="json") for candidate in result.candidates],
            "trace": [item.model_dump(mode="json") for item in result.trace],
        },
        metrics=lambda result: {
            "candidate_count": len(result.candidates),
            "demoted_count": sum(1 for item in result.trace if item.decision == "demoted"),
        },
    )
    return CandidateRetrievalResult(candidates=rerank_result.candidates)


def _run_deterministic_assembler_stage(
    trace: GraphTraceBuilder,
    *,
    decomposition: dict[str, Any],
    retrieval_result: CandidateRetrievalResult,
    literal_results: list[LiteralResolverResult],
    registry: Any,
) -> dict[str, Any]:
    return _run_stage(
        trace,
        stage=StageName.DETERMINISTIC_ASSEMBLER,
        input_payload={
            "structural_requirements": decomposition.get("structural_requirements"),
            "candidate_count": len(retrieval_result.candidates),
            "literal_count": len(literal_results),
        },
        action=lambda: _deterministic_assembler_payload(
            decomposition=decomposition,
            retrieval_result=retrieval_result,
            literal_results=literal_results,
            registry=registry,
        ),
        output_payload=lambda result: result,
        metrics=lambda result: {
            "deterministic_hit": bool(result.get("success")),
            "fallback_reason": result.get("fallback_reason"),
        },
    )


def _deterministic_assembler_payload(
    *,
    decomposition: dict[str, Any],
    retrieval_result: CandidateRetrievalResult,
    literal_results: list[LiteralResolverResult],
    registry: Any,
) -> dict[str, Any]:
    requirements = _structural_requirements_for_precheck(decomposition)
    shape_result = classify_query_shape(requirements, decomposition)
    base = {
        "schema_version": "deterministic_assembler_result_v1",
        "shape_status": shape_result.status.value,
        "shape": shape_result.shape.value if shape_result.shape is not None else None,
        "shape_candidates": [shape.value for shape in shape_result.candidates],
    }
    if shape_result.status != ShapeStatus.RESOLVED:
        return {
            **base,
            "success": False,
            "fallback_reason": shape_result.reason or "shape_not_resolved",
        }

    if shape_result.shape in {
        QueryShape.F1_VERTEX_PROJECTION_0HOP,
        QueryShape.F2_VERTEX_FILTER_0HOP,
        QueryShape.F3_VERTEX_AGGREGATE_0HOP,
    }:
        preferred_vertex = _zero_hop_preferred_vertex(
            requirements,
            literal_results,
            candidates=retrieval_result.candidates,
        )
        assembler_requirements = _zero_hop_assembler_requirements(
            shape=shape_result.shape,
            decomposition=decomposition,
            retrieval_result=retrieval_result,
            literal_results=literal_results,
            registry=registry,
        )
        uncovered_projection_terms = assembler_requirements.get("projection_uncovered_terms")
        if uncovered_projection_terms:
            return {
                **base,
                "success": False,
                "fallback_reason": "unresolved_projection_terms",
                "uncovered_projection_terms": uncovered_projection_terms,
            }
        assembler_candidates = _zero_hop_candidates_for_assembler(
            retrieval_result.candidates,
            preferred_vertex=preferred_vertex,
        )
        assembled = ZeroHopAssembler(registry).assemble(
            shape_result.shape.value,
            assembler_candidates,
            assembler_requirements,
            literals=_zero_hop_literal_payloads(literal_results),
        )
    elif shape_result.shape in {
        QueryShape.F4_PATH_PROJECTION_MULTIHOP,
        QueryShape.F5_PATH_FILTER_MULTIHOP,
        QueryShape.F6_PATH_GROUP_TOPN,
    }:
        assembler_requirements = _multihop_assembler_requirements(
            shape=shape_result.shape,
            decomposition=decomposition,
            retrieval_result=retrieval_result,
            literal_results=literal_results,
            registry=registry,
        )
        uncovered_projection_terms = assembler_requirements.get("projection_uncovered_terms")
        if uncovered_projection_terms:
            return {
                **base,
                "success": False,
                "fallback_reason": "unresolved_projection_terms",
                "uncovered_projection_terms": uncovered_projection_terms,
            }
        assembler_candidates = _multihop_candidates_for_assembler(retrieval_result.candidates)
        assembled = MultihopAssembler(registry).assemble(
            shape_result.shape.value,
            assembler_candidates,
            assembler_requirements,
            literals=_zero_hop_literal_payloads(literal_results),
        )
    else:
        return {
            **base,
            "success": False,
            "fallback_reason": "shape_not_supported_by_current_assembler",
        }

    if not assembled.success:
        return {**base, "success": False, "fallback_reason": assembled.fallback_reason}
    return {**base, "success": True, "dsl": assembled.dsl}


def _multihop_candidates_for_assembler(candidates: list[SemanticCandidate]) -> list[SemanticCandidate]:
    vertex_ids = set(_candidate_ids(candidates, "vertex"))
    if len(vertex_ids) < 2:
        return list(candidates)

    filtered: list[SemanticCandidate] = []
    for candidate in candidates:
        if candidate.semantic_type == "vertex":
            if candidate.semantic_id in vertex_ids:
                filtered.append(candidate)
            continue
        if candidate.semantic_type == "property":
            if candidate.owner in vertex_ids:
                filtered.append(candidate)
            continue
        if candidate.semantic_type == "edge":
            endpoints = {candidate.metadata.get("from_vertex"), candidate.metadata.get("to_vertex")}
            if endpoints <= vertex_ids:
                filtered.append(candidate)
            continue
        if candidate.semantic_type == "metric":
            filtered.append(candidate)
    return filtered


def _multihop_assembler_requirements(
    *,
    shape: QueryShape,
    decomposition: Mapping[str, Any],
    retrieval_result: CandidateRetrievalResult,
    literal_results: list[LiteralResolverResult],
    registry: Any,
) -> dict[str, Any]:
    candidates = list(retrieval_result.candidates)
    vertex_ids = _candidate_ids(candidates, "vertex")
    requirements = _structural_requirements_for_precheck(decomposition).model_dump(mode="json")
    requirements["source_question"] = _path_direction_context(requirements)

    projection = _projection_items_from_substantive_terms(
        decomposition=decomposition,
        candidates=candidates,
        registry=registry,
        selected_vertices=vertex_ids,
    )
    projection_terms = _required_projection_terms(decomposition)
    if shape in {QueryShape.F4_PATH_PROJECTION_MULTIHOP, QueryShape.F5_PATH_FILTER_MULTIHOP} and projection_terms:
        uncovered_terms = _uncovered_projection_slots(
            decomposition=decomposition,
            projection=projection,
            candidates=candidates,
            registry=registry,
            selected_vertices=vertex_ids,
        )
        if uncovered_terms:
            requirements["projection_uncovered_terms"] = uncovered_terms
    if (
        not projection
        and not projection_terms
        and not _substantive_terms_with_slot(decomposition, slot="projection")
        and shape in {QueryShape.F4_PATH_PROJECTION_MULTIHOP, QueryShape.F5_PATH_FILTER_MULTIHOP}
        and vertex_ids
    ):
        projection_vertex = _projection_vertex_for_traversal(vertex_ids, literal_results)
        projection = [_id_projection_item(projection_vertex, registry)]
    requirements["projection"] = [_multihop_projection_requirement_item(item) for item in projection]

    filters = _filters_from_literal_results(literal_results, decomposition=decomposition)
    if filters:
        requirements["filters"] = [
            {
                "owner": item["owner"],
                "property": item["property"],
                "operator": item.get("operator") or "eq",
            }
            for item in filters
        ]

    if shape == QueryShape.F6_PATH_GROUP_TOPN:
        selected_vertices = vertex_ids
        group_by = _slot_property_items_from_substantive_terms(
            decomposition=decomposition,
            slot="group_by",
            candidates=candidates,
            registry=registry,
            selected_vertices=selected_vertices,
        )
        if not group_by:
            group_by = _group_by_items_from_projection_terms(
                decomposition=decomposition,
                candidates=candidates,
                registry=registry,
                selected_vertices=selected_vertices,
            )
        if group_by:
            requirements["group_by"] = group_by

        measure = _count_measure_from_projection_terms(
            decomposition=decomposition,
            candidates=candidates,
            registry=registry,
            selected_vertices=selected_vertices,
            group_by=group_by,
        )
        if measure is not None:
            requirements["aggregate"] = measure

        structural_requirements = _structural_requirements_for_precheck(decomposition)
        if structural_requirements.requires_order_by and measure is not None:
            requirements["order_by"] = [
                {
                    "source": f"measure.{measure['alias']}",
                    "direction": structural_requirements.order_direction,
                }
            ]
        if structural_requirements.requires_limit.required and structural_requirements.requires_limit.value is not None:
            requirements["limit"] = structural_requirements.requires_limit.value

    return requirements


def _multihop_projection_requirement_item(item: Mapping[str, Any]) -> dict[str, Any]:
    if item.get("semantic_type") == "vertex_full":
        payload = {
            "semantic_type": "vertex_full",
            "name": item.get("name"),
            "alias": item.get("alias"),
            "projection_terms": item.get("projection_terms", []),
        }
        return {key: value for key, value in payload.items() if value is not None}
    return {
        "owner": item["owner"],
        "property": item["name"],
        "alias": item.get("alias"),
        "projection_terms": item.get("projection_terms", []),
    }


def _slot_property_items_from_substantive_terms(
    *,
    decomposition: Mapping[str, Any],
    slot: str,
    candidates: list[SemanticCandidate],
    registry: Any,
    selected_vertices: list[str],
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for slot_term in _substantive_terms_with_slot(decomposition, slot=slot):
        property_ref = _resolve_projection_property(
            slot_term,
            candidates=candidates,
            registry=registry,
            selected_vertices=selected_vertices,
        )
        if property_ref is None:
            continue
        owner, property_name = property_ref
        if any(item["owner"] == owner and item["property"] == property_name for item in items):
            continue
        items.append(
            {
                "owner": owner,
                "property": property_name,
                "alias": f"{_snake_case(owner)}_{property_name}",
                "projection_terms": [str(slot_term["text"])],
            }
        )
    return items


def _group_by_items_from_projection_terms(
    *,
    decomposition: Mapping[str, Any],
    candidates: list[SemanticCandidate],
    registry: Any,
    selected_vertices: list[str],
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for slot_term in _substantive_terms_with_slot(decomposition, slot="projection"):
        text = str(slot_term.get("text") or "").strip()
        if not text or _is_distribution_noise_term(text) or _is_quantity_projection_text(text):
            continue
        property_refs = _resolve_projection_property_refs(
            slot_term,
            candidates=candidates,
            registry=registry,
            selected_vertices=selected_vertices,
        )
        if property_refs is None or len(property_refs) != 1:
            continue
        owner, property_name = property_refs[0]
        item = {
            "owner": owner,
            "property": property_name,
            "alias": f"{_snake_case(owner)}_{property_name}",
            "projection_terms": [text],
        }
        if item not in items:
            items.append(item)
    return items if len(items) == 1 else []


def _count_measure_from_projection_terms(
    *,
    decomposition: Mapping[str, Any],
    candidates: list[SemanticCandidate],
    registry: Any,
    selected_vertices: list[str],
    group_by: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    quantity_terms = [
        str(slot_term.get("text") or "").strip()
        for slot_term in [
            *_substantive_terms_with_slot(decomposition, slot="projection"),
            *_substantive_terms_with_slot(decomposition, slot="order_by"),
        ]
        if _is_quantity_projection_text(str(slot_term.get("text") or ""))
    ]
    owner = _count_measure_owner(
        decomposition=decomposition,
        candidates=candidates,
        registry=registry,
        selected_vertices=selected_vertices,
        group_by=group_by,
    )
    if owner is None or not quantity_terms:
        return None
    try:
        id_property = registry.get_vertex(owner).id_property
        registry.get_property(owner, id_property)
    except RegistryLookupError:
        return None
    return {
        "function": "count",
        "owner": owner,
        "property": id_property,
        "alias": f"{_snake_case(owner)}_count",
        "projection_terms": _unique_texts(quantity_terms),
    }


def _count_measure_owner(
    *,
    decomposition: Mapping[str, Any],
    candidates: list[SemanticCandidate],
    registry: Any,
    selected_vertices: list[str],
    group_by: list[dict[str, Any]] | None,
) -> str | None:
    quantity_owners: set[str] = set()
    for slot_term in [
        *_substantive_terms_with_slot(decomposition, slot="projection"),
        *_substantive_terms_with_slot(decomposition, slot="order_by"),
    ]:
        text = str(slot_term.get("text") or "").strip()
        if not _is_quantity_projection_text(text):
            continue
        quantity_owners.update(
            _owner_names_from_text_or_attachment(
                slot_term,
                registry=registry,
                selected_vertices=selected_vertices,
            )
        )
    if len(quantity_owners) == 1:
        return next(iter(quantity_owners))
    if len(quantity_owners) > 1:
        return None

    object_owners: set[str] = set()
    for slot_term in _substantive_terms_with_slot(decomposition, slot="projection"):
        text = str(slot_term.get("text") or "").strip()
        if (
            not text
            or _is_quantity_projection_text(text)
            or _is_distribution_noise_term(text)
            or _is_aggregate_modifier_projection_text(text)
        ):
            continue
        object_owner = _resolve_projection_object_owner(
            slot_term,
            candidates=candidates,
            registry=registry,
            selected_vertices=selected_vertices,
        )
        if object_owner is not None:
            object_owners.add(object_owner)
    if len(object_owners) == 1:
        return next(iter(object_owners))
    if len(object_owners) > 1:
        return None

    group_owners = {
        str(item.get("owner"))
        for item in group_by or []
        if isinstance(item, Mapping) and str(item.get("owner") or "").strip()
    }
    if len(group_owners) == 1:
        return next(iter(group_owners))
    return None


def _owner_names_from_text_or_attachment(
    slot_term: Mapping[str, Any],
    *,
    registry: Any,
    selected_vertices: list[str],
) -> set[str]:
    owners: set[str] = set()
    for text in (
        str(slot_term.get("attached_to") or "").strip(),
        str(slot_term.get("text") or "").strip(),
    ):
        owners.update(
            _attached_vertex_names_from_registry(
                text,
                registry=registry,
                selected_vertices=selected_vertices,
            )
        )
    return owners


def _is_quantity_projection_text(term: str) -> bool:
    normalized = term.strip()
    return normalized == "总" or any(marker in term for marker in ("数量", "个数", "总数", "多少", "次数", "频率", "count", "Count"))


def _is_distribution_noise_term(term: str) -> bool:
    return term.strip() in {"分布", "排行", "排名", "统计", "出现", "返回"}


def _is_aggregate_modifier_projection_text(term: str) -> bool:
    return term.strip() in {"属性值", "属性", "条目", "记录", "节点", "实例"}


def _is_property_count_modifier_text(term: str) -> bool:
    return term.strip() in _PROPERTY_COUNT_MODIFIER_TERMS


def _is_non_empty_filter_text(term: str) -> bool:
    return term.strip() in {"非空", "不为空", "不为空值", "not null", "NOT NULL"}


def _unique_texts(values: list[str]) -> list[str]:
    texts: list[str] = []
    for value in values:
        text = str(value).strip()
        if text and text not in texts:
            texts.append(text)
    return texts


def _path_direction_context(requirements: Mapping[str, Any]) -> str:
    terms = [
        str(item.get("text") or "").strip()
        for item in requirements.get("path_terms", [])
        if isinstance(item, Mapping) and str(item.get("text") or "").strip()
    ]
    return " ".join(terms) if terms else str(requirements.get("source_question") or "")


def _zero_hop_candidates_for_assembler(
    candidates: list[SemanticCandidate],
    *,
    preferred_vertex: str | None = None,
) -> list[SemanticCandidate]:
    if preferred_vertex is not None:
        filtered = [
            candidate
            for candidate in candidates
            if (
                (candidate.semantic_type == "vertex" and candidate.semantic_id == preferred_vertex)
                or (candidate.semantic_type == "property" and candidate.owner == preferred_vertex)
                or candidate.semantic_type not in {"vertex", "property"}
            )
        ]
        if not any(candidate.semantic_type == "vertex" and candidate.semantic_id == preferred_vertex for candidate in filtered):
            filtered.insert(0, _inferred_semantic_vertex_candidate(preferred_vertex))
        return filtered
    vertex_ids = _candidate_ids(candidates, "vertex")
    if len(vertex_ids) != 1:
        return list(candidates)
    vertex_name = vertex_ids[0]
    return [
        candidate
        for candidate in candidates
        if candidate.semantic_type != "property" or candidate.owner == vertex_name
    ]


def _unique_literal_expected_vertex(literal_results: list[LiteralResolverResult]) -> str | None:
    owners = {
        result.expected_vertex
        for result in literal_results
        if result.resolved and result.expected_vertex
    }
    if len(owners) != 1:
        return None
    return next(iter(owners))


def _zero_hop_preferred_vertex(
    requirements: StructuralRequirements,
    literal_results: list[LiteralResolverResult],
    *,
    candidates: list[SemanticCandidate],
) -> str | None:
    if requirements.min_path_hops > 0 or len(requirements.path_terms) > 1:
        return None
    preferred_vertex = _unique_literal_expected_vertex(literal_results)
    if preferred_vertex is None:
        return None
    candidate_vertices = {
        candidate.semantic_id
        for candidate in candidates
        if candidate.semantic_type == "vertex" and candidate.score >= 0.7
    }
    if any(vertex != preferred_vertex for vertex in candidate_vertices):
        return None
    return preferred_vertex


def _inferred_semantic_vertex_candidate(vertex_name: str) -> SemanticCandidate:
    return SemanticCandidate(
        semantic_type="vertex",
        semantic_id=vertex_name,
        semantic_name=vertex_name,
        score=1.0,
        match_type="text",
        owner=None,
        evidence=[
            {
                "term": vertex_name,
                "source": "semantic_model.literal_owner",
                "matched_text": vertex_name,
            }
        ],
        metadata={},
    )


def _zero_hop_assembler_requirements(
    *,
    shape: QueryShape,
    decomposition: Mapping[str, Any],
    retrieval_result: CandidateRetrievalResult,
    literal_results: list[LiteralResolverResult],
    registry: Any,
) -> dict[str, Any]:
    candidates = list(retrieval_result.candidates)
    vertex_ids = _candidate_ids(candidates, "vertex")
    requirements = _structural_requirements_for_precheck(decomposition)
    preferred_vertex = _zero_hop_preferred_vertex(
        requirements,
        literal_results,
        candidates=retrieval_result.candidates,
    )
    if preferred_vertex:
        selected_vertices = [preferred_vertex]
    else:
        selected_vertices = vertex_ids[:1] if len(vertex_ids) == 1 else vertex_ids
    payload: dict[str, Any] = {}
    limit_requirement = requirements.requires_limit
    if limit_requirement.required and limit_requirement.value is not None:
        payload["limit"] = {"value": limit_requirement.value}

    if shape in {QueryShape.F1_VERTEX_PROJECTION_0HOP, QueryShape.F2_VERTEX_FILTER_0HOP}:
        projection = _projection_items_from_substantive_terms(
            decomposition=decomposition,
            candidates=candidates,
            registry=registry,
            selected_vertices=selected_vertices,
        )
        payload["projection"] = projection
        uncovered_terms = _uncovered_projection_slots(
            decomposition=decomposition,
            projection=projection,
            candidates=candidates,
            registry=registry,
            selected_vertices=selected_vertices,
        )
        if uncovered_terms:
            payload["projection_uncovered_terms"] = uncovered_terms

    if shape == QueryShape.F2_VERTEX_FILTER_0HOP:
        payload["filters"] = [
            {
                "property": result.expected_property,
                "operator": _filter_operator_for_literal(decomposition, result),
            }
            for result in literal_results
            if result.resolved and result.expected_property
        ]

    if shape == QueryShape.F3_VERTEX_AGGREGATE_0HOP:
        aggregate = _zero_hop_count_aggregate_from_projection_terms(
            decomposition=decomposition,
            candidates=candidates,
            registry=registry,
            selected_vertices=selected_vertices,
        )
        payload["filters"] = _zero_hop_filters_for_assembler(
            decomposition=decomposition,
            literal_results=literal_results,
            candidates=candidates,
            registry=registry,
            selected_vertices=selected_vertices,
            aggregate=aggregate,
            skip_literal_terms=_aggregate_projection_terms(aggregate),
        )
        if aggregate is None:
            aggregate = (
                {
                    "function": "count",
                    "alias": f"{_snake_case(selected_vertices[0])}_count",
                }
                if len(selected_vertices) == 1
                else {"function": "count"}
            )
        payload["aggregate"] = aggregate

    return payload


def _zero_hop_filters_for_assembler(
    *,
    decomposition: Mapping[str, Any],
    literal_results: list[LiteralResolverResult],
    candidates: list[SemanticCandidate],
    registry: Any,
    selected_vertices: list[str],
    aggregate: Mapping[str, Any] | None = None,
    skip_literal_terms: set[str] | None = None,
) -> list[dict[str, Any]]:
    skip_literal_terms = skip_literal_terms or set()
    filters = [
        {
            "property": result.expected_property,
            "operator": _filter_operator_for_literal(decomposition, result),
        }
        for result in literal_results
        if result.resolved
        and result.expected_property
        and _norm(str(result.raw_literal)) not in skip_literal_terms
    ]
    for item in _zero_hop_non_null_filters_from_terms(
        decomposition=decomposition,
        candidates=candidates,
        registry=registry,
        selected_vertices=selected_vertices,
        aggregate=aggregate,
    ):
        if item not in filters:
            filters.append(item)
    return filters


def _aggregate_projection_terms(aggregate: Mapping[str, Any] | None) -> set[str]:
    if not isinstance(aggregate, Mapping):
        return set()
    raw_measures = aggregate.get("measures")
    specs = list(raw_measures) if isinstance(raw_measures, list | tuple) else []
    if not specs:
        specs = [aggregate]
    terms: set[str] = set()
    for spec in specs:
        if not isinstance(spec, Mapping):
            continue
        for term in spec.get("projection_terms", []) or []:
            text = str(term).strip()
            if text:
                terms.add(_norm(text))
    return terms


def _zero_hop_non_null_filters_from_terms(
    *,
    decomposition: Mapping[str, Any],
    candidates: list[SemanticCandidate],
    registry: Any,
    selected_vertices: list[str],
    aggregate: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if len(selected_vertices) != 1:
        return []
    filters: list[dict[str, Any]] = []
    filter_terms = list(_substantive_terms_with_slot(decomposition, slot="filter"))
    for index, slot_term in enumerate(filter_terms):
        text = str(slot_term.get("text") or "").strip()
        if not _is_non_empty_filter_text(text):
            continue
        attached_to = str(slot_term.get("attached_to") or "").strip()
        if not attached_to:
            attached_to = _previous_zero_hop_filter_property_term(filter_terms, index)
        if not attached_to:
            continue
        refs = _resolve_projection_property_refs(
            {"text": attached_to, "slot": "filter"},
            candidates=candidates,
            registry=registry,
            selected_vertices=selected_vertices,
        )
        if refs is None or len(refs) != 1:
            continue
        owner, property_name = refs[0]
        if owner != selected_vertices[0]:
            continue
        item = {"property": property_name, "operator": "is_not_null"}
        if item not in filters:
            filters.append(item)
    if _has_closed_owned_property_count_phrase(decomposition) and not _aggregate_has_multiple_measures(aggregate):
        aggregate_refs = _aggregate_measure_property_refs(aggregate)
        for slot_term in filter_terms:
            text = str(slot_term.get("text") or "").strip()
            if not text or _is_property_count_modifier_text(text) or _is_non_empty_filter_text(text):
                continue
            if not _closed_owned_property_count_phrase_matches(decomposition, text):
                continue
            refs = _resolve_projection_property_refs(
                slot_term,
                candidates=candidates,
                registry=registry,
                selected_vertices=selected_vertices,
            )
            if refs is None or len(refs) != 1:
                continue
            owner, property_name = refs[0]
            if owner != selected_vertices[0] or (owner, property_name) in aggregate_refs:
                continue
            item = {"property": property_name, "operator": "is_not_null"}
            if item not in filters:
                filters.append(item)
    return filters


def _previous_zero_hop_filter_property_term(
    filter_terms: list[dict[str, Any]],
    index: int,
) -> str | None:
    for term in reversed(filter_terms[:index]):
        text = str(term.get("text") or "").strip()
        if not text:
            continue
        if _is_non_empty_filter_text(text) or _is_filter_operator_term(text) or _is_property_count_modifier_text(text):
            continue
        return text
    return None


def _zero_hop_count_aggregate_from_projection_terms(
    *,
    decomposition: Mapping[str, Any],
    candidates: list[SemanticCandidate],
    registry: Any,
    selected_vertices: list[str],
) -> dict[str, Any] | None:
    property_measure = _zero_hop_count_measure_from_projection_terms(
        decomposition=decomposition,
        candidates=candidates,
        registry=registry,
        selected_vertices=selected_vertices,
    )
    if property_measure is None:
        return None
    entity_measure = _zero_hop_entity_count_measure_from_projection_terms(
        decomposition=decomposition,
        candidates=candidates,
        registry=registry,
        selected_vertices=selected_vertices,
    )
    if entity_measure is None:
        return property_measure
    entity_terms = {
        str(term).strip()
        for term in entity_measure.get("projection_terms", [])
        if str(term).strip()
    }
    property_terms = [
        str(term).strip()
        for term in property_measure.get("projection_terms", [])
        if str(term).strip()
        and str(term).strip() not in entity_terms
        and str(term).strip() not in {"节点", "实例"}
    ]
    if property_terms:
        property_measure = {**property_measure, "projection_terms": property_terms}
    return {"measures": [entity_measure, property_measure]}


def _zero_hop_entity_count_measure_from_projection_terms(
    *,
    decomposition: Mapping[str, Any],
    candidates: list[SemanticCandidate],
    registry: Any,
    selected_vertices: list[str],
) -> dict[str, Any] | None:
    if len(selected_vertices) != 1:
        return None
    owner = selected_vertices[0]
    terms: list[str] = []
    for slot_term in _substantive_terms_with_slot(decomposition, slot="projection"):
        text = str(slot_term.get("text") or "").strip()
        if not text or not _is_quantity_projection_text(text):
            continue
        if _has_closed_owned_property_count_phrase(decomposition) and "总" not in text:
            continue
        attached_to = str(slot_term.get("attached_to") or "").strip()
        if not attached_to:
            continue
        object_owner = _resolve_projection_object_owner(
            {"text": attached_to, "slot": "projection"},
            candidates=candidates,
            registry=registry,
            selected_vertices=selected_vertices,
        )
        if object_owner == owner:
            terms.append(text)
            continue
        if object_owner is not None:
            continue
        property_refs = _resolve_projection_property_refs(
            {"text": attached_to, "slot": "projection"},
            candidates=candidates,
            registry=registry,
            selected_vertices=selected_vertices,
        )
        if property_refs:
            continue
    if not terms:
        terms = _zero_hop_unattached_entity_total_terms(decomposition)
    if not terms:
        return None
    return {
        "function": "count",
        "alias": f"{_snake_case(owner)}_count",
        "projection_terms": _unique_texts(terms),
    }


def _zero_hop_unattached_entity_total_terms(decomposition: Mapping[str, Any]) -> list[str]:
    question = str(decomposition.get("original_question") or decomposition.get("question") or "")
    if not any(marker in question for marker in ("以及", "及", "和", "与")):
        return []
    terms = [
        str(slot_term.get("text") or "").strip()
        for slot_term in _substantive_terms_with_slot(decomposition, slot="projection")
        if _is_quantity_projection_text(str(slot_term.get("text") or ""))
        and "总" in str(slot_term.get("text") or "")
        and not str(slot_term.get("attached_to") or "").strip()
    ]
    return _unique_texts(terms)


def _zero_hop_count_measure_from_projection_terms(
    *,
    decomposition: Mapping[str, Any],
    candidates: list[SemanticCandidate],
    registry: Any,
    selected_vertices: list[str],
) -> dict[str, Any] | None:
    if len(selected_vertices) != 1:
        return None
    raw_quantity_terms = _zero_hop_quantity_projection_terms(decomposition)
    explicit_count_property_terms = _zero_hop_explicit_count_property_terms_from_quantity(
        decomposition=decomposition,
        candidates=candidates,
        registry=registry,
        selected_vertices=selected_vertices,
    )
    if explicit_count_property_terms:
        unique_refs = {ref for _, ref in explicit_count_property_terms}
        if len(unique_refs) != 1:
            return None
        owner, property_name = next(iter(unique_refs))
        quantity_terms = _zero_hop_quantity_projection_terms_for_property(
            decomposition,
            property_ref=(owner, property_name),
            candidates=candidates,
            registry=registry,
            selected_vertices=selected_vertices,
        ) or raw_quantity_terms
        property_terms = [text for text, _ in explicit_count_property_terms]
        modifier_terms = _zero_hop_property_count_modifier_terms_for_property(
            decomposition=decomposition,
            property_ref=(owner, property_name),
            candidates=candidates,
            registry=registry,
            selected_vertices=selected_vertices,
        )
        return {
            "function": "count",
            "owner": owner,
            "property": property_name,
            "alias": f"{_snake_case(owner)}_{property_name}_count",
            "projection_terms": _unique_texts([*property_terms, *modifier_terms, *quantity_terms]),
        }
    modifier_terms = _zero_hop_property_count_modifier_terms(decomposition)
    implicit_property_terms = _zero_hop_implicit_count_property_terms(
        decomposition=decomposition,
        candidates=candidates,
        registry=registry,
        selected_vertices=selected_vertices,
    )
    if not raw_quantity_terms and _is_count_slot(decomposition) and implicit_property_terms:
        raw_quantity_terms = ["数量"]
    if not raw_quantity_terms and _is_count_slot(decomposition) and modifier_terms and _zero_hop_non_empty_filter_terms(decomposition):
        raw_quantity_terms = ["数量"]
    if not raw_quantity_terms:
        return None
    if not modifier_terms and not implicit_property_terms:
        return None
    property_terms: list[str] = []
    property_refs: list[tuple[str, str]] = []
    for slot_term in _zero_hop_property_count_candidate_terms(decomposition):
        text = str(slot_term.get("text") or "").strip()
        if (
            not text
            or _is_quantity_projection_text(text)
            or _is_property_count_modifier_text(text)
            or _is_distribution_noise_term(text)
            or _is_non_empty_filter_text(text)
        ):
            continue
        if _resolve_projection_object_owner(
            slot_term,
            candidates=candidates,
            registry=registry,
            selected_vertices=selected_vertices,
        ) is not None:
            continue
        refs = _resolve_projection_property_refs(
            slot_term,
            candidates=candidates,
            registry=registry,
            selected_vertices=selected_vertices,
        )
        if refs is None or len(refs) != 1:
            continue
        ref = refs[0]
        if ref not in property_refs:
            property_refs.append(ref)
            property_terms.append(text)
    for text, ref in implicit_property_terms:
        if ref not in property_refs:
            property_refs.append(ref)
            property_terms.append(text)
    if len(property_refs) != 1:
        return None
    owner, property_name = property_refs[0]
    quantity_terms = _zero_hop_quantity_projection_terms_for_property(
        decomposition,
        property_ref=(owner, property_name),
        candidates=candidates,
        registry=registry,
        selected_vertices=selected_vertices,
    ) or raw_quantity_terms
    return {
        "function": "count",
        "owner": owner,
        "property": property_name,
        "alias": f"{_snake_case(owner)}_{property_name}_count",
        "projection_terms": _unique_texts([*property_terms, *modifier_terms, *quantity_terms]),
    }


def _zero_hop_explicit_count_property_terms_from_quantity(
    *,
    decomposition: Mapping[str, Any],
    candidates: list[SemanticCandidate],
    registry: Any,
    selected_vertices: list[str],
) -> list[tuple[str, tuple[str, str]]]:
    terms: list[tuple[str, tuple[str, str]]] = []
    for slot_term in _substantive_terms_with_slot(decomposition, slot="projection"):
        text = str(slot_term.get("text") or "").strip()
        if not text or not _is_quantity_projection_text(text):
            continue
        attached_to = str(slot_term.get("attached_to") or "").strip()
        if not attached_to:
            continue
        object_owner = _resolve_projection_object_owner(
            {"text": attached_to, "slot": "projection"},
            candidates=candidates,
            registry=registry,
            selected_vertices=selected_vertices,
        )
        if object_owner is not None:
            continue
        refs = _resolve_projection_property_refs(
            {"text": attached_to, "slot": "projection"},
            candidates=candidates,
            registry=registry,
            selected_vertices=selected_vertices,
        )
        if refs is None or len(refs) != 1:
            continue
        terms.append((attached_to, refs[0]))
    return terms


def _zero_hop_property_count_modifier_terms_for_property(
    *,
    decomposition: Mapping[str, Any],
    property_ref: tuple[str, str],
    candidates: list[SemanticCandidate],
    registry: Any,
    selected_vertices: list[str],
) -> list[str]:
    terms: list[str] = []
    for slot_term in [
        *_substantive_terms_with_slot(decomposition, slot="projection"),
        *_substantive_terms_with_slot(decomposition, slot="filter"),
    ]:
        text = str(slot_term.get("text") or "").strip()
        if not text or not _is_property_count_modifier_text(text):
            continue
        attached_to = str(slot_term.get("attached_to") or "").strip()
        if not attached_to:
            continue
        refs = _resolve_projection_property_refs(
            {"text": attached_to, "slot": "projection"},
            candidates=candidates,
            registry=registry,
            selected_vertices=selected_vertices,
        )
        if refs is None or len(refs) != 1 or refs[0] != property_ref:
            continue
        terms.append(text)
    return _unique_texts(terms)


def _zero_hop_implicit_count_property_terms(
    *,
    decomposition: Mapping[str, Any],
    candidates: list[SemanticCandidate],
    registry: Any,
    selected_vertices: list[str],
) -> list[tuple[str, tuple[str, str]]]:
    if not _is_count_slot(decomposition):
        return []
    matches: list[tuple[str, tuple[str, str]]] = []
    slot_terms = list(_substantive_terms_with_slot(decomposition, slot="projection"))
    if _has_closed_owned_property_count_phrase(decomposition):
        slot_terms.extend(_substantive_terms_with_slot(decomposition, slot="filter"))
    for slot_term in slot_terms:
        text = str(slot_term.get("text") or "").strip()
        if (
            not text
            or _is_quantity_projection_text(text)
            or _is_distribution_noise_term(text)
            or _is_aggregate_modifier_projection_text(text)
            or _is_property_count_modifier_text(text)
        ):
            continue
        object_owner = _resolve_projection_object_owner(
            slot_term,
            candidates=candidates,
            registry=registry,
            selected_vertices=selected_vertices,
        )
        if object_owner is not None:
            continue
        refs = _resolve_projection_property_refs(
            slot_term,
            candidates=candidates,
            registry=registry,
            selected_vertices=selected_vertices,
        )
        if refs is None or len(refs) != 1:
            continue
        matches.append((text, refs[0]))
    unique_refs = {ref for _, ref in matches}
    return matches if len(unique_refs) == 1 else []


def _has_closed_owned_property_count_phrase(decomposition: Mapping[str, Any]) -> bool:
    return any(
        _closed_owned_property_count_phrase_matches(decomposition, str(slot_term.get("text") or ""))
        for slot_term in _substantive_terms_with_slot(decomposition, slot="filter")
    )


def _closed_owned_property_count_phrase_matches(decomposition: Mapping[str, Any], text: str) -> bool:
    question = _compact_surface(
        _norm(str(decomposition.get("original_question") or decomposition.get("question") or ""))
    )
    if not question:
        return False
    normalized_text = _compact_surface(_norm(text))
    if not normalized_text or _is_property_count_modifier_text(normalized_text) or _is_non_empty_filter_text(normalized_text):
        return False
    return any(f"{prefix}{normalized_text}的" in question for prefix in ("拥有", "具有", "带有")) or any(
        f"{prefix}{normalized_text}{modifier}的" in question
        for prefix in ("拥有", "具有", "带有")
        for modifier in ("属性", "字段", "参数", "记录", "属性记录")
    )


def _aggregate_has_multiple_measures(aggregate: Mapping[str, Any] | None) -> bool:
    if not isinstance(aggregate, Mapping):
        return False
    raw_measures = aggregate.get("measures")
    return isinstance(raw_measures, list | tuple) and len(raw_measures) > 1


def _aggregate_measure_property_refs(aggregate: Mapping[str, Any] | None) -> set[tuple[str, str]]:
    if not isinstance(aggregate, Mapping):
        return set()
    raw_measures = aggregate.get("measures")
    specs = list(raw_measures) if isinstance(raw_measures, list | tuple) else [aggregate]
    refs: set[tuple[str, str]] = set()
    for spec in specs:
        if not isinstance(spec, Mapping):
            continue
        owner = str(spec.get("owner") or "").strip()
        property_name = str(spec.get("property") or "").strip()
        if owner and property_name:
            refs.add((owner, property_name))
    return refs


def _zero_hop_quantity_projection_terms_for_property(
    decomposition: Mapping[str, Any],
    *,
    property_ref: tuple[str, str],
    candidates: list[SemanticCandidate],
    registry: Any,
    selected_vertices: list[str],
) -> list[str]:
    terms: list[str] = []
    for slot_term in _substantive_terms_with_slot(decomposition, slot="projection"):
        text = str(slot_term.get("text") or "").strip()
        if not text or not _is_quantity_projection_text(text):
            continue
        attached_to = str(slot_term.get("attached_to") or "").strip()
        if not attached_to:
            terms.append(text)
            continue
        refs = _resolve_projection_property_refs(
            {"text": attached_to, "slot": "projection"},
            candidates=candidates,
            registry=registry,
            selected_vertices=selected_vertices,
        )
        if refs is None:
            continue
        if len(refs) == 1 and refs[0] == property_ref:
            terms.append(text)
    return _unique_texts(terms)


def _zero_hop_property_count_candidate_terms(decomposition: Mapping[str, Any]) -> list[dict[str, Any]]:
    terms: list[dict[str, Any]] = []
    for slot in ("projection", "filter"):
        for slot_term in _substantive_terms_with_slot(decomposition, slot=slot):
            terms.append(slot_term)
            text = str(slot_term.get("text") or "").strip()
            attached_to = str(slot_term.get("attached_to") or "").strip()
            if _is_property_count_modifier_text(text) and attached_to:
                terms.append({"text": attached_to, "slot": slot})
    return terms


def _zero_hop_non_empty_filter_terms(decomposition: Mapping[str, Any]) -> list[str]:
    return _unique_texts(
        [
            str(slot_term.get("text") or "").strip()
            for slot_term in _substantive_terms_with_slot(decomposition, slot="filter")
            if _is_non_empty_filter_text(str(slot_term.get("text") or ""))
        ]
    )


def _zero_hop_property_count_modifier_terms(decomposition: Mapping[str, Any]) -> list[str]:
    raw_terms = decomposition.get("substantive_terms")
    if not isinstance(raw_terms, list | tuple):
        return []
    return _unique_texts(
        [
            str(slot_term.get("text") or "").strip()
            for slot_term in raw_terms
            if isinstance(slot_term, Mapping)
            and str(slot_term.get("slot") or "").strip() in {"projection", "filter"}
            and _is_property_count_modifier_text(str(slot_term.get("text") or ""))
        ]
    )


def _zero_hop_quantity_projection_terms(decomposition: Mapping[str, Any]) -> list[str]:
    terms = [
        str(slot_term.get("text") or "").strip()
        for slot_term in _substantive_terms_with_slot(decomposition, slot="projection")
        if _is_quantity_projection_text(str(slot_term.get("text") or ""))
        or _is_aggregate_modifier_projection_text(str(slot_term.get("text") or ""))
        or _is_property_count_modifier_text(str(slot_term.get("text") or ""))
    ]
    return _unique_texts(terms)


def _zero_hop_literal_payloads(literal_results: list[LiteralResolverResult]) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for result in literal_results:
        if not result.resolved:
            continue
        owner = result.expected_vertex or result.expected_edge
        if owner is None or result.expected_property is None:
            continue
        values.append(
            {
                "owner": owner,
                "property": result.expected_property,
                "raw": result.raw_literal,
                "normalized": result.normalized_value if result.normalized_value is not None else result.resolved_value,
                "resolver_match_type": result.match_type,
            }
        )
    return values


def _complete_dsl_generation(
    trace: GraphTraceBuilder,
    *,
    dsl: dict[str, Any],
    structural_requirements: StructuralRequirements,
    registry: Any,
    path_pattern_template_overrides_for_tests: Mapping[str, str] | None,
    user_visible_notices: list[str] | None = None,
) -> GenerationOutput:
    try:
        ast = _run_stage(
            trace,
            stage=StageName.DSL_PARSER,
            input_payload=dsl,
            action=lambda: parse_restricted_query_dsl(dsl, registry),
            output_payload=lambda result: {
                "query_shape": result.query_shape.value,
                "operation_count": len(result.operations),
            },
        )
        structural_coverage = _run_dsl_structural_coverage_stage(
            trace,
            structural_requirements=structural_requirements,
            dsl=dsl,
        )
        if not structural_coverage.is_valid:
            return _failure(
                trace,
                reason="coverage_failure",
                message="DSL does not cover all structural requirements derived from decomposition.",
                details={
                    "structural_coverage": structural_coverage.model_dump(mode="json"),
                    "structural_requirements": structural_requirements.model_dump(mode="json"),
                },
            )
        compiler = CypherCompiler(
            registry,
            _path_pattern_template_overrides_for_tests=path_pattern_template_overrides_for_tests,
        )
        compilation = _run_stage(
            trace,
            stage=StageName.CYPHER_COMPILER,
            input_payload={"query_shape": ast.query_shape.value},
            action=lambda: compiler.compile_draft(ast),
            output_payload=lambda result: {
                "schema_version": result.schema_version,
                "cypher_template": result.cypher_template,
                "parameters": result.parameters,
                "parameter_sources": result.parameter_sources,
                "cypher_executable": result.cypher_executable,
                "cypher": result.cypher_executable,
                "expected_return_aliases": result.expected_return_aliases,
            },
        )
        validation_result = _run_cypher_self_validation_stage(
            trace,
            cypher=compilation.cypher_executable,
            expected_return_aliases=compilation.expected_return_aliases,
            validator=CypherSelfValidator(registry),
        )
        if not validation_result.valid:
            first_error = validation_result.errors[0] if validation_result.errors else None
            return _failure(
                trace,
                reason=first_error.code if first_error is not None else "target_dialect_static_error",
                message="Cypher self-validation failed.",
                details={"self_validation": validation_result.model_dump(mode="json")},
            )
    except (CypherCompilerError, RestrictedDslValidationError, ValueError) as exc:
        return _failure(trace, reason="compiler_shape_mismatch", message=str(exc))

    return _generated(trace, dsl=dsl, cypher=compilation.cypher_executable, user_visible_notices=user_visible_notices)


def _decomposition_user_visible_notices(decomposition: Mapping[str, Any]) -> list[str]:
    coverage = decomposition.get("coverage")
    if not isinstance(coverage, Mapping):
        return []
    modality_terms = coverage.get("modality_terms")
    if not isinstance(modality_terms, Mapping):
        return []
    warning_terms = modality_terms.get("warning_only")
    if not isinstance(warning_terms, list | tuple):
        return []
    return render_user_visible_notices(
        [
            {
                "type": "modality_warning",
                "term": str(term),
                "message": f"问题中的“{term}”没有被解释为查询约束。",
            }
            for term in warning_terms
            if str(term).strip()
        ]
    )


def _with_literal_requests_from_candidates(
    decomposition: dict[str, Any],
    retrieval_result: CandidateRetrievalResult,
    *,
    registry: Any | None = None,
) -> dict[str, Any]:
    if decomposition.get("literal_requests"):
        return decomposition

    literal_candidates = _literal_candidate_payloads(decomposition)
    literal_candidates, skipped_literal_candidates = _literal_candidates_allowed_by_slot(
        decomposition,
        literal_candidates,
    )
    requests = [
        request
        for request in (
            _literal_request_from_candidate(
                literal,
                retrieval_result.candidates,
                decomposition,
                registry=registry,
            )
            for literal in literal_candidates
        )
        if request is not None
    ]
    if not requests:
        requests = _literal_requests_from_value_candidates(decomposition, retrieval_result.candidates)
    if not requests and not skipped_literal_candidates:
        return decomposition

    enriched = dict(decomposition)
    enriched["literal_requests"] = requests
    enriched["skipped_literal_candidates"] = skipped_literal_candidates
    return enriched


def _literal_candidates_allowed_by_slot(
    decomposition: Mapping[str, Any],
    literal_candidates: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    slot_by_text = _slot_by_substantive_text(decomposition)
    allowed: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for literal in literal_candidates:
        raw_literal = str(literal.get("text") or "").strip()
        if not raw_literal:
            continue
        slot = slot_by_text.get(_norm(raw_literal), "unknown")
        if slot in _STRUCTURAL_LITERAL_SKIP_SLOTS:
            skipped.append({"raw": raw_literal, "slot": slot, "reason": f"slot={slot}"})
            continue
        allowed.append(literal)
    return allowed, skipped


def _slot_by_substantive_text(decomposition: Mapping[str, Any]) -> dict[str, str]:
    slots: dict[str, str] = {}
    for term in _substantive_term_payloads(decomposition.get("substantive_terms")):
        text = str(term.get("text") or "").strip()
        if not text:
            continue
        slot = str(term.get("slot") or "unknown").strip() or "unknown"
        key = _norm(text)
        if slots.get(key) in _STRUCTURAL_LITERAL_SKIP_SLOTS:
            continue
        if slot in _STRUCTURAL_LITERAL_SKIP_SLOTS or key not in slots:
            slots[key] = slot
    return slots


def _literal_candidate_payloads(decomposition: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw_candidates = decomposition.get("literal_candidate_objects") or decomposition.get("literal_candidates") or []
    if not isinstance(raw_candidates, list):
        return []
    payloads: list[dict[str, Any]] = []
    for item in raw_candidates:
        if isinstance(item, Mapping):
            text = item.get("text") or item.get("raw_literal") or item.get("value")
            if not text:
                continue
            payloads.append(
                {
                    "text": str(text),
                    "kind_hint": _normalize_literal_kind_hint(
                        item.get("kind_hint") or item.get("literal_kind_hint")
                    ),
                    "attached_to": str(item.get("attached_to") or item.get("owner") or ""),
                }
            )
        elif isinstance(item, str):
            payloads.append({"text": item, "kind_hint": "unknown", "attached_to": ""})
    return payloads


def _literal_request_from_candidate(
    literal: Mapping[str, Any],
    candidates: list[SemanticCandidate],
    decomposition: Mapping[str, Any] | None = None,
    *,
    registry: Any | None = None,
) -> dict[str, Any] | None:
    raw_literal = str(literal.get("text") or "").strip()
    if not raw_literal:
        return None

    property_hints = _literal_filter_property_hints(literal, decomposition or {})
    attached_owners = _attached_vertex_names(str(literal.get("attached_to") or ""), candidates)
    if not attached_owners:
        attached_owners = _unique_context_vertex_names(decomposition or {}, candidates)
    if not attached_owners and registry is not None:
        attached_owners = _unique_context_vertex_names_from_registry(decomposition or {}, registry)
    id_request = _id_literal_request(
        raw_literal,
        literal,
        attached_owners,
        candidates,
        property_hints=property_hints,
    )
    if id_request is not None:
        return id_request

    property_candidate = _best_literal_property_candidate(
        literal,
        candidates,
        property_hints=property_hints,
        attached_owners=attached_owners,
    )
    if property_candidate is None or property_candidate.owner is None:
        return None

    owner_kind = _candidate_owner_kind(property_candidate.owner, candidates)
    if owner_kind is None and registry is not None:
        owner_kind = _registry_owner_kind(property_candidate.owner, registry)
    if owner_kind is None:
        return None

    return {
        "raw_literal": raw_literal,
        owner_kind: property_candidate.owner,
        "expected_property": property_candidate.semantic_name,
        "literal_kind_hint": _normalize_literal_kind_hint(literal.get("kind_hint")),
    }


def _literal_requests_from_value_candidates(
    decomposition: Mapping[str, Any],
    candidates: list[SemanticCandidate],
) -> list[dict[str, Any]]:
    vertex_ids = {
        candidate.semantic_id
        for candidate in candidates
        if candidate.semantic_type == "vertex"
    }
    requests: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for candidate in candidates:
        if candidate.semantic_type != "property" or candidate.owner not in vertex_ids:
            continue
        if not candidate.metadata.get("valid_values"):
            continue
        for evidence in candidate.evidence:
            if evidence.source not in {"valid_values", "value_synonyms"}:
                continue
            raw_literal = str(evidence.term).strip()
            if not raw_literal or raw_literal not in str(
                decomposition.get("original_question") or decomposition.get("question") or ""
            ):
                continue
            if not _evidence_supports_literal_value(raw_literal, evidence.source, evidence.matched_text):
                continue
            if _is_vertex_surface_term(raw_literal, candidates):
                continue
            key = (candidate.owner or "", candidate.semantic_name, raw_literal)
            if key in seen:
                continue
            seen.add(key)
            requests.append(
                {
                    "raw_literal": raw_literal,
                    "expected_vertex": candidate.owner,
                    "expected_property": candidate.semantic_name,
                    "literal_kind_hint": "enum",
                }
            )
    return requests


def _evidence_supports_literal_value(raw_literal: str, source: str, matched_text: str) -> bool:
    if source == "value_synonyms":
        return _norm(raw_literal) == _norm(matched_text)
    if source == "valid_values":
        return _literal_value_matches(raw_literal, matched_text)
    return False


def _is_vertex_surface_term(raw_literal: str, candidates: list[SemanticCandidate]) -> bool:
    normalized = _norm(raw_literal)
    for candidate in candidates:
        if candidate.semantic_type != "vertex":
            continue
        if _norm(candidate.semantic_name) == normalized:
            return True
        for evidence in candidate.evidence:
            if _norm(evidence.term) == normalized or _norm(evidence.matched_text) == normalized:
                return True
    return False


def _id_literal_request(
    raw_literal: str,
    literal: Mapping[str, Any],
    attached_owners: set[str],
    candidates: list[SemanticCandidate],
    *,
    property_hints: list[str] | None = None,
) -> dict[str, Any] | None:
    if _normalize_literal_kind_hint(literal.get("kind_hint")) != "id" and not _looks_like_id_literal(raw_literal):
        return None
    if property_hints and _property_hints_prefer_non_id(property_hints):
        return None

    vertex_candidates = [
        candidate
        for candidate in candidates
        if candidate.semantic_type == "vertex" and candidate.semantic_id in attached_owners
    ]
    if not vertex_candidates:
        vertex_candidates = [
            candidate
            for candidate in candidates
            if candidate.semantic_type == "vertex" and _id_prefix_matches(raw_literal, candidate.semantic_id)
        ]
    if not vertex_candidates:
        return None

    vertex = max(vertex_candidates, key=lambda candidate: (candidate.score, candidate.semantic_id))
    id_property = str(vertex.metadata.get("id_property") or "id")
    return {
        "raw_literal": raw_literal,
        "expected_vertex": vertex.semantic_id,
        "expected_property": id_property,
        "literal_kind_hint": "id",
    }


def _best_literal_property_candidate(
    literal: Mapping[str, Any],
    candidates: list[SemanticCandidate],
    *,
    property_hints: list[str] | None = None,
    attached_owners: set[str] | None = None,
) -> SemanticCandidate | None:
    raw_literal = str(literal.get("text") or "")
    kind_hint = _normalize_literal_kind_hint(literal.get("kind_hint"))
    attached_to = str(literal.get("attached_to") or "")
    owner_hints = set(attached_owners or _attached_vertex_names(attached_to, candidates))
    property_candidates = [
        candidate
        for candidate in candidates
        if candidate.semantic_type == "property" and candidate.owner is not None
    ]
    if not property_candidates:
        return None
    return max(
        property_candidates,
        key=lambda candidate: _literal_property_score(
            raw_literal,
            kind_hint,
            owner_hints,
            candidate,
            property_hints=property_hints or [],
        ),
    )


def _literal_property_score(
    raw_literal: str,
    kind_hint: str,
    attached_owners: set[str],
    candidate: SemanticCandidate,
    *,
    property_hints: list[str],
) -> tuple[int, float, str]:
    score = 0
    if any(_property_hint_matches_candidate(hint, candidate) for hint in property_hints):
        score += 120
    valid_values = candidate.metadata.get("valid_values", [])
    if any(_literal_value_matches(raw_literal, value) for value in valid_values):
        score += 100
    if valid_values and kind_hint in {"enum", "enum_or_name", "unknown"}:
        score += 30
    if candidate.owner in attached_owners:
        score += 25
    if candidate.semantic_name == "id" and (kind_hint == "id" or _looks_like_id_literal(raw_literal)):
        score += 5
    if candidate.semantic_name == "id" and kind_hint != "id" and not _looks_like_id_literal(raw_literal):
        score -= 50
    return (score, candidate.score, candidate.semantic_id)


def _literal_filter_property_hints(
    literal: Mapping[str, Any],
    decomposition: Mapping[str, Any],
) -> list[str]:
    raw_literal = str(literal.get("text") or "").strip()
    literal_owner_hint = str(literal.get("attached_to") or "").strip()
    raw_key = _norm(raw_literal)
    owner_key = _norm(literal_owner_hint)
    hints: list[str] = []
    filter_terms = [
        term
        for term in _substantive_term_payloads(decomposition.get("substantive_terms"))
        if str(term.get("slot") or "").strip() == "filter"
    ]
    for index, term in enumerate(filter_terms):
        text = str(term.get("text") or "").strip()
        attached_to = str(term.get("attached_to") or "").strip()
        if raw_key and _norm(text) == raw_key:
            if attached_to and _norm(attached_to) != owner_key:
                _append_unique_text(hints, attached_to)
            elif not attached_to:
                previous_hint = _previous_filter_property_term(filter_terms, index, raw_key)
                if previous_hint:
                    _append_unique_text(hints, previous_hint)
    for term in filter_terms:
        text = str(term.get("text") or "").strip()
        attached_to = str(term.get("attached_to") or "").strip()
        if not text or _norm(text) == raw_key:
            continue
        if owner_key and _norm(attached_to) == owner_key:
            _append_unique_text(hints, text)
    return hints


def _previous_filter_property_term(
    filter_terms: list[dict[str, Any]],
    raw_index: int,
    raw_key: str,
) -> str | None:
    for term in reversed(filter_terms[:raw_index]):
        text = str(term.get("text") or "").strip()
        if not text or _norm(text) == raw_key:
            continue
        if _is_filter_operator_term(text):
            continue
        return text
    return None


def _filter_operator_for_literal(
    decomposition: Mapping[str, Any],
    result: LiteralResolverResult,
) -> str:
    operators: list[str] = []
    property_key = _norm(result.expected_property or "")
    raw_key = _norm(result.raw_literal)
    for term in _substantive_terms_with_slot(decomposition, slot="filter"):
        text = str(term.get("text") or "").strip()
        if not text:
            continue
        normalized = _norm(text)
        if normalized == property_key or normalized == raw_key:
            continue
        operator = _closed_filter_operator(text)
        if operator is None:
            continue
        if operator not in operators:
            operators.append(operator)
    if len(operators) == 1:
        return operators[0]
    if len(operators) > 1:
        return "__unsupported__"
    return "eq"


def _closed_filter_operator(text: str) -> str | None:
    return _FILTER_OPERATOR_ALIASES.get(_norm(text))


_FILTER_OPERATOR_ALIASES = {
    "为": "eq",
    "是": "eq",
    "等于": "eq",
    "等于为": "eq",
    "=": "eq",
    "==": "eq",
    "不等于": "neq",
    "不是": "neq",
    "!=": "neq",
    "<>": "neq",
    "大于": "gt",
    "超过": "gt",
    "高于": "gt",
    "多于": "gt",
    ">": "gt",
    "小于": "lt",
    "低于": "lt",
    "少于": "lt",
    "<": "lt",
    "不小于": "gte",
    "不少于": "gte",
    "至少": "gte",
    "大于等于": "gte",
    "不低于": "gte",
    ">=": "gte",
    "不大于": "lte",
    "不超过": "lte",
    "最多": "lte",
    "小于等于": "lte",
    "不高于": "lte",
    "<=": "lte",
}


def _is_filter_operator_term(text: str) -> bool:
    return _closed_filter_operator(text) is not None


def _property_hints_prefer_non_id(property_hints: list[str]) -> bool:
    return any(not _property_hint_is_id(hint) for hint in property_hints)


def _property_hint_is_id(hint: str) -> bool:
    normalized = _norm(hint).replace(" ", "")
    if normalized in {"id", "编号", "标识", "identifier"}:
        return True
    return normalized.endswith("id") or normalized.endswith("编号") or normalized.endswith("标识")


def _property_hint_matches_candidate(hint: str, candidate: SemanticCandidate) -> bool:
    normalized_hint = _norm(hint)
    if not normalized_hint:
        return False
    surfaces = [candidate.semantic_name, candidate.semantic_id]
    if candidate.owner:
        surfaces.append(f"{candidate.owner}.{candidate.semantic_name}")
    for evidence in candidate.evidence:
        surfaces.extend([evidence.term, evidence.matched_text])
    for surface in surfaces:
        normalized_surface = _norm(surface)
        if normalized_hint == normalized_surface:
            return True
        if normalized_surface and (
            normalized_hint in normalized_surface or normalized_surface in normalized_hint
        ):
            return True
    return False


def _unique_context_vertex_names(
    decomposition: Mapping[str, Any],
    candidates: list[SemanticCandidate],
) -> set[str]:
    owners: set[str] = set()
    for term in _substantive_term_payloads(decomposition.get("substantive_terms")):
        slot = str(term.get("slot") or "").strip()
        if slot not in {"path", "filter", "projection"}:
            continue
        text = str(term.get("attached_to") or term.get("text") or "").strip()
        owners.update(_attached_vertex_names(text, candidates))
    return owners if len(owners) == 1 else set()


def _unique_context_vertex_names_from_registry(
    decomposition: Mapping[str, Any],
    registry: Any,
) -> set[str]:
    selected_vertices = [vertex.name for vertex in getattr(registry.model, "vertices", [])]
    owners: set[str] = set()
    for term in _substantive_term_payloads(decomposition.get("substantive_terms")):
        slot = str(term.get("slot") or "").strip()
        if slot not in {"path", "filter", "projection"}:
            continue
        text = str(term.get("attached_to") or term.get("text") or "").strip()
        owners.update(
            _attached_vertex_names_from_registry(
                text,
                registry=registry,
                selected_vertices=selected_vertices,
            )
        )
    return owners if len(owners) == 1 else set()


def _registry_owner_kind(owner: str, registry: Any) -> str | None:
    try:
        registry.get_vertex(owner)
        return "expected_vertex"
    except RegistryLookupError:
        pass
    try:
        registry.get_edge(owner)
        return "expected_edge"
    except RegistryLookupError:
        return None


def _append_unique_text(values: list[str], value: str) -> None:
    text = value.strip()
    if text and text not in values:
        values.append(text)


def _literal_value_matches(raw_literal: str, candidate_value: Any) -> bool:
    raw = _norm(raw_literal)
    candidate = _norm(candidate_value)
    if not raw or not candidate:
        return False
    return raw == candidate or _contains_with_ascii_boundaries(raw, candidate) or _contains_with_ascii_boundaries(candidate, raw)


def _contains_with_ascii_boundaries(haystack: str, needle: str) -> bool:
    start = haystack.find(needle)
    while start != -1:
        end = start + len(needle)
        before = haystack[start - 1] if start > 0 else ""
        after = haystack[end] if end < len(haystack) else ""
        if not _is_ascii_word_char(before) and not _is_ascii_word_char(after):
            return True
        start = haystack.find(needle, start + 1)
    return False


def _is_ascii_word_char(value: str) -> bool:
    return bool(value) and (value.isascii() and (value.isalnum() or value == "_"))


def _attached_vertex_names(attached_to: str, candidates: list[SemanticCandidate]) -> set[str]:
    if not attached_to:
        return set()
    attached = _norm(attached_to)
    owners: set[str] = set()
    for candidate in candidates:
        if candidate.semantic_type != "vertex":
            continue
        if _vertex_surface_matches_attached_text(attached, _norm(candidate.semantic_name)):
            owners.add(candidate.semantic_id)
            continue
        for evidence in candidate.evidence:
            if _vertex_surface_matches_attached_text(
                attached,
                _norm(evidence.term),
            ) or _vertex_surface_matches_attached_text(
                attached,
                _norm(evidence.matched_text),
            ):
                owners.add(candidate.semantic_id)
    return owners


def _vertex_surface_matches_attached_text(attached: str, surface: str) -> bool:
    if not attached or not surface:
        return False
    return attached == surface or attached.startswith(surface)


def _candidate_owner_kind(owner: str, candidates: list[SemanticCandidate]) -> str | None:
    for candidate in candidates:
        if candidate.semantic_type == "vertex" and candidate.semantic_id == owner:
            return "expected_vertex"
        if candidate.semantic_type == "edge" and candidate.semantic_id == owner:
            return "expected_edge"
    return None


def _norm(value: Any) -> str:
    return str(value).casefold().strip().replace("_", " ").replace("-", " ")


def _normalize_literal_kind_hint(value: Any) -> str:
    normalized = str(value or "unknown").casefold().strip().replace("-", "_").replace(" ", "_")
    if normalized in {"enum", "enum_or_name", "id", "name", "time", "numeric", "unknown"}:
        return normalized
    if normalized in {"identifier", "identity", "key", "primary_key"}:
        return "id"
    if normalized in {"number", "integer", "float", "decimal", "amount", "capacity", "bandwidth", "latency"}:
        return "numeric"
    if normalized in {"date", "datetime", "timestamp", "recent", "latest"}:
        return "time"
    if normalized in {"enum_value", "status", "type", "category", "level", "tier", "qos"}:
        return "enum"
    if normalized in {"entity_name", "label", "display_name"}:
        return "name"
    return "unknown"


def _looks_like_id_literal(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z]+-[A-Za-z0-9-]+", value.strip()))


def _id_prefix_matches(raw_literal: str, semantic_id: str) -> bool:
    prefix = raw_literal.split("-", 1)[0].casefold()
    aliases = {
        "ne": {"networkelement", "network_element"},
        "tun": {"tunnel"},
        "svc": {"service"},
        "port": {"port"},
    }
    return semantic_id.casefold() in aliases.get(prefix, set())


def _duration_ms(started: float) -> int:
    return max(0, int((perf_counter() - started) * 1000))


def _run_cypher_self_validation_stage(
    trace: GraphTraceBuilder,
    *,
    cypher: str,
    expected_return_aliases: list[str],
    validator: CypherSelfValidator,
) -> CypherSelfValidationResult:
    started = perf_counter()
    result = validator.validate_generated_query(
        cypher,
        expected_return_aliases=expected_return_aliases,
    )
    trace.add_stage(
        stage=StageName.CYPHER_SELF_VALIDATION,
        status="success" if result.valid else "failed",
        duration_ms=_duration_ms(started),
        input_ref=inline_ref({"cypher": cypher, "expected_return_aliases": expected_return_aliases}),
        output_ref=inline_ref(result.model_dump(mode="json")),
        errors=[error.model_dump(mode="json") for error in result.errors],
        warnings=[warning.model_dump(mode="json") for warning in result.warnings],
    )
    return result


def _run_dsl_structural_coverage_stage(
    trace: GraphTraceBuilder,
    *,
    structural_requirements: StructuralRequirements,
    dsl: dict[str, Any],
) -> DslStructuralCoverageResult:
    started = perf_counter()
    result = validate_dsl_structural_coverage(structural_requirements, dsl)
    issue = structural_coverage_issue(result, structural_requirements) if not result.is_valid else None
    trace.add_stage(
        stage=StageName.DSL_STRUCTURAL_COVERAGE_GATE,
        status="success" if result.is_valid else "failed",
        duration_ms=_duration_ms(started),
        input_ref=inline_ref(
            {
                "structural_requirements": structural_requirements.model_dump(mode="json"),
                "dsl": dsl,
            }
        ),
        output_ref=inline_ref(
            {
                "structural_requirements": structural_requirements.model_dump(mode="json"),
                "coverage_result": result.model_dump(mode="json"),
            }
        ),
        errors=[issue] if issue is not None else [],
    )
    return result


def _run_repair_controller_stage(
    trace: GraphTraceBuilder,
    *,
    question: str,
    selected_bindings: dict[str, Any],
    validator_errors: list[dict[str, Any]] | None = None,
    cypher_validation_errors: list[dict[str, Any]] | None = None,
    assumptions: list[dict[str, Any]] | None = None,
) -> RepairDecision:
    payload = {
        "schema_version": "repair_controller_input_v1",
        "trace_id": trace._trace_id,  # noqa: SLF001 - pipeline owns the trace builder lifecycle.
        "question": question,
        "attempt_no": 1,
        "selected_bindings": selected_bindings,
        "normalized_dsl": None,
        "validator_errors": validator_errors or [],
        "cypher_validation_errors": cypher_validation_errors or [],
        "history": [],
        "assumptions": assumptions or [],
    }
    return _run_stage(
        trace,
        stage=StageName.REPAIR_CONTROLLER,
        input_payload=payload,
        action=lambda: RepairController().decide(payload),
        output_payload=lambda result: result.model_dump(mode="json"),
    )


def _output_from_decomposition_outcome(
    trace: GraphTraceBuilder,
    result: Any,
) -> GenerationOutput | None:
    if isinstance(result, QuestionDecompositionClarification):
        return _clarification(trace, question=result.clarification.question)
    if isinstance(result, QuestionDecompositionFailure):
        return _failure(trace, reason=result.reason, message=result.message)
    if isinstance(result, Mapping):
        status = result.get("status")
        if status == "clarification_required":
            clarification = result.get("clarification")
            question = _clarification_question_from_payload(clarification) or str(
                result.get("clarification_question") or "请补充澄清信息。"
            )
            return _clarification(trace, question=question)
        if status in {"generation_failed", "service_failed"}:
            return _failure(
                trace,
                reason=str(result.get("reason") or "semantic_contract_unaligned"),
                message=str(result.get("message") or result.get("reason") or status),
            )
    return None


def _decomposition_payload(result: Any) -> dict[str, Any]:
    if isinstance(result, QuestionDecomposition):
        payload = result.model_dump(mode="json")
        payload["literal_candidate_objects"] = list(payload.get("literal_candidates", []))
        payload["literal_candidates"] = [
            candidate["text"]
            for candidate in payload.get("literal_candidates", [])
            if isinstance(candidate, dict) and candidate.get("text")
        ]
        payload.setdefault("literal_requests", [])
        payload.setdefault("coverage", _coverage(covered=_substantive_term_texts(payload)))
        return _normalize_decomposition_terms(payload)
    if isinstance(result, Mapping):
        return _normalize_decomposition_terms(dict(result))
    raise TypeError(f"question decomposer returned unsupported payload: {result!r}")


def _normalize_decomposition_terms(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    for redundant_field in ("target_concepts", "relation_phrases", "stopword_terms"):
        normalized.pop(redundant_field, None)
    literal_objects = list(
        normalized.get("literal_candidate_objects") or normalized.get("literal_candidates") or []
    )
    substantive_terms = _substantive_term_payloads(normalized.get("substantive_terms"))

    for literal in literal_objects:
        if not isinstance(literal, Mapping):
            continue
        attached_to = str(literal.get("attached_to") or "").strip()
        if attached_to:
            substantive_terms = _append_unique_substantive_term(substantive_terms, attached_to)
        text = str(literal.get("text") or literal.get("raw_literal") or literal.get("value") or "").strip()
        if text and not _has_structural_substantive_slot(substantive_terms, text):
            substantive_terms = _append_unique_substantive_term(
                substantive_terms,
                text,
                slot="filter",
                attached_to=attached_to or None,
            )

    question = str(normalized.get("original_question") or normalized.get("question") or "")
    for classifier, concept in _classifier_surface_concepts(question).items():
        substantive_terms = _append_unique_substantive_term(
            substantive_terms,
            classifier,
            slot="projection",
        )
        substantive_terms = _append_unique_substantive_term(
            substantive_terms,
            concept,
            slot="projection",
        )

    normalized["substantive_terms"] = substantive_terms
    normalized.setdefault("coverage", _coverage(covered=_substantive_term_texts(normalized)))
    normalized["coverage"] = _coverage_seeded_with_projection_terms(normalized, normalized["coverage"])
    normalized["structural_requirements"] = derive_structural_requirements(normalized).model_dump(mode="json")
    return normalized


def _classifier_surface_concepts(question: str) -> dict[str, str]:
    concepts: dict[str, str] = {}
    if "台" in question:
        concepts["台"] = "设备"
    return concepts


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list | tuple):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _has_structural_substantive_slot(terms: list[dict[str, Any]], text: str) -> bool:
    target = _norm(text)
    return any(
        _norm(str(term.get("text") or "")) == target
        and str(term.get("slot") or "unknown").strip() in _STRUCTURAL_LITERAL_SKIP_SLOTS
        for term in terms
    )


def _substantive_term_payloads(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list | tuple):
        return []
    terms: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str | None]] = set()
    for item in value:
        if not isinstance(item, Mapping):
            continue
        text = str(item.get("text") or "").strip()
        slot = str(item.get("slot") or "unknown").strip() or "unknown"
        attached_to = str(item.get("attached_to") or "").strip() or None
        key = (text, slot, attached_to)
        if not text or key in seen:
            continue
        payload: dict[str, Any] = {"text": text, "slot": slot}
        if attached_to:
            payload["attached_to"] = attached_to
        terms.append(payload)
        seen.add(key)
    return terms


def _substantive_term_texts(decomposition: Mapping[str, Any]) -> list[str]:
    return [term["text"] for term in _substantive_term_payloads(decomposition.get("substantive_terms"))]


def _coverage_seeded_with_projection_terms(
    decomposition: Mapping[str, Any],
    coverage: Any,
) -> Any:
    if _is_count_slot(decomposition):
        return coverage
    projection_terms = _required_projection_terms(decomposition)
    if not projection_terms:
        return coverage
    payload = dict(coverage) if isinstance(coverage, Mapping) else CoverageReport.model_validate(coverage).model_dump(mode="json")
    payload.setdefault(
        "projection_terms",
        {"required": projection_terms, "covered": [], "uncovered": projection_terms},
    )
    return payload


def _append_unique_term(values: list[str], term: str) -> list[str]:
    if term not in values:
        values.append(term)
    return values


def _append_unique_substantive_term(
    values: list[dict[str, Any]],
    text: str,
    *,
    slot: str = "unknown",
    attached_to: str | None = None,
) -> list[dict[str, Any]]:
    if any(
        item.get("text") == text
        and str(item.get("slot") or "unknown") == slot
        and (str(item.get("attached_to") or "").strip() or None) == attached_to
        for item in values
    ):
        return values
    payload: dict[str, Any] = {"text": text, "slot": slot}
    if attached_to:
        payload["attached_to"] = attached_to
    values.append(payload)
    return values


def _output_from_grounded_outcome(
    trace: GraphTraceBuilder,
    result: Any,
) -> GenerationOutput | None:
    if isinstance(result, GroundedUnderstandingFailure):
        return _failure(trace, reason=result.reason, message=result.message)
    grounded = _coerce_grounded_understanding(result)
    if grounded is None:
        return None
    if grounded.status == "grounded":
        return None
    if grounded.status == "unsupported_query_shape":
        message = grounded.unsupported.message if grounded.unsupported is not None else "Unsupported query shape."
        return _failure(
            trace,
            reason="unsupported_query_shape",
            message=message,
            details={
                "grounded_understanding": grounded.model_dump(mode="json"),
                "reason_code": grounded.unsupported.reason_code if grounded.unsupported else "unsupported_query_shape",
            },
        )
    if grounded.status == "clarification_required":
        return _clarification(trace, question=_grounded_clarification_question(grounded))
    if grounded.status == "failed":
        return _failure(
            trace,
            reason="single_shot_fallback_failed",
            message=_grounded_failure_message(grounded),
            details={"grounded_understanding": grounded.model_dump(mode="json")},
        )
    return _failure(
        trace,
        reason="semantic_match_rejected",
        message=f"Grounded understanding returned non-grounded status {grounded.status}.",
    )


def _grounded_clarification_question(grounded: GroundedUnderstanding) -> str:
    if grounded.ambiguities:
        ambiguity = grounded.ambiguities[0]
        return f"请补充澄清信息：{ambiguity.reason}"
    return "请补充澄清信息。"


def _grounded_failure_message(grounded: GroundedUnderstanding) -> str:
    if grounded.ambiguities:
        return grounded.ambiguities[0].reason
    if grounded.unsupported is not None:
        return grounded.unsupported.message
    return "Single-shot fallback could not produce a valid DSL grounding."


def _grounded_binder_payload(result: Any) -> dict[str, Any]:
    grounded = _coerce_grounded_understanding(result)
    if grounded is not None:
        return grounded.to_binder_payload()
    if isinstance(result, Mapping):
        return dict(result)
    raise TypeError(f"grounded understanding returned unsupported payload: {result!r}")


def _enrich_binder_projection_from_decomposition(
    grounded: dict[str, Any],
    *,
    decomposition: Mapping[str, Any],
    candidates: list[SemanticCandidate],
    registry: Any,
) -> dict[str, Any]:
    if grounded.get("projection"):
        return grounded
    selected_vertices = [
        str(item.get("name") or item.get("semantic_id") or "").strip()
        for item in grounded.get("selected_vertices", [])
        if isinstance(item, Mapping) and str(item.get("name") or item.get("semantic_id") or "").strip()
    ]
    if not selected_vertices:
        return grounded
    projection: list[dict[str, Any]] = []
    for slot_term in _substantive_terms_with_slot(decomposition, slot="projection"):
        vertex_name = _resolve_projection_vertex_full(
            slot_term,
            candidates=candidates,
            registry=registry,
            selected_vertices=selected_vertices,
            decomposition=decomposition,
        )
        if vertex_name is None:
            object_owner = _resolve_projection_object_owner(
                slot_term,
                candidates=candidates,
                registry=registry,
                selected_vertices=selected_vertices,
            )
            if object_owner is None:
                continue
            if not (
                _object_projection_requires_vertex_full(slot_term, decomposition)
                or (
                    _projection_object_is_path_context(slot_term, decomposition)
                    and not _has_other_concrete_projection_term(
                        slot_term,
                        decomposition=decomposition,
                        candidates=candidates,
                        registry=registry,
                        selected_vertices=selected_vertices,
                    )
                )
            ):
                continue
            vertex_name = object_owner
        item = {
            "semantic_type": "vertex_full",
            "name": vertex_name,
            "alias": _snake_case(vertex_name),
        }
        if item not in projection:
            projection.append(item)
    if not projection:
        return grounded
    return {**grounded, "projection": projection}


def _coerce_grounded_understanding(result: Any) -> GroundedUnderstanding | None:
    if isinstance(result, GroundedUnderstanding):
        return result
    if isinstance(result, Mapping) and result.get("schema_version") == "grounded_understanding_v1":
        payload = {key: value for key, value in result.items() if not str(key).startswith("_")}
        return GroundedUnderstanding.model_validate(payload)
    return None


def _clarification_question_from_payload(payload: Any) -> str | None:
    if payload is None:
        return None
    if hasattr(payload, "question"):
        return str(payload.question)
    if isinstance(payload, Mapping):
        question = payload.get("question") or payload.get("question_zh")
        return str(question) if question else None
    return None


def _mock_decompose(question: str) -> dict[str, Any]:
    if "svc-gold-001" in question and "服务" in question and "隧道" in question:
        projection_terms = [_term("隧道", "projection")]
        if "ID" in question or "编号" in question:
            projection_terms = [_term("隧道", "path"), _term("ID", "projection", attached_to="隧道")]
        return {
            "schema_version": "question_decomposition_v1",
            "original_question": question,
            "literal_candidates": ["svc-gold-001"],
            "semantic_terms": ["Service.id", "SERVICE_USES_TUNNEL"],
            "substantive_terms": [
                _term("服务", "path"),
                _term("svc-gold-001", "filter", attached_to="服务"),
                _term("使用", "path"),
                *projection_terms,
            ],
            "literal_requests": [
                {
                    "raw_literal": "svc-gold-001",
                    "expected_vertex": "Service",
                    "expected_property": "id",
                    "literal_kind_hint": "id",
                }
            ],
            "coverage": _coverage(covered=["服务", "svc-gold-001", "使用", "隧道"]),
            "mock_intent": "service_id_tunnels",
        }

    if ("Gold" in question or "Platinum" in question) and "服务" in question and "隧道" in question:
        service_tier = "Gold" if "Gold" in question else "Platinum"
        projection_terms = [_term("隧道", "projection")]
        if "ID" in question or "编号" in question:
            projection_terms = [_term("隧道", "path"), _term("ID", "projection", attached_to="隧道")]
        elif any(term in question for term in ("信息", "详情", "详细信息", "全部信息", "完整信息")):
            projection_terms = [_term("信息", "projection", attached_to="隧道")]
        return {
            "schema_version": "question_decomposition_v1",
            "original_question": question,
            "literal_candidates": [service_tier],
            "semantic_terms": ["Service.quality_of_service"],
            "substantive_terms": [
                _term(service_tier, "filter", attached_to="服务"),
                _term("服务", "path"),
                _term("使用", "path"),
                *projection_terms,
            ],
            "literal_requests": [
                {
                    "raw_literal": service_tier,
                    "expected_vertex": "Service",
                    "expected_property": "quality_of_service",
                    "literal_kind_hint": "enum",
                }
            ],
            "coverage": _coverage(covered=[service_tier, "服务", "使用", "隧道"]),
            "mock_intent": "gold_service_tunnels",
        }

    if "tun-mpls-001" in question and "隧道" in question and "设备" in question:
        projection_term = _term("设备", "projection")
        return {
            "schema_version": "question_decomposition_v1",
            "original_question": question,
            "literal_candidates": ["tun-mpls-001"],
            "semantic_terms": ["Tunnel.id", "tunnel_full_path"],
            "substantive_terms": [
                _term("隧道", "path"),
                _term("tun-mpls-001", "filter", attached_to="隧道"),
                _term("经过", "path"),
                projection_term,
            ],
            "literal_requests": [
                {
                    "raw_literal": "tun-mpls-001",
                    "expected_vertex": "Tunnel",
                    "expected_property": "id",
                    "literal_kind_hint": "id",
                }
            ],
            "coverage": _coverage(covered=["隧道", "tun-mpls-001", "经过", "设备"]),
            "mock_intent": "tunnel_full_path",
        }

    if "ne-0001" in question and "隧道" in question and "经过" in question:
        return {
            "schema_version": "question_decomposition_v1",
            "original_question": question,
            "literal_candidates": ["ne-0001"],
            "semantic_terms": ["NetworkElement.id", "PATH_THROUGH"],
            "substantive_terms": [
                _term("隧道", "path"),
                _term("经过", "path"),
                _term("设备", "path"),
                _term("ne-0001", "filter", attached_to="设备"),
            ],
            "literal_requests": [
                {
                    "raw_literal": "ne-0001",
                    "expected_vertex": "NetworkElement",
                    "expected_property": "id",
                    "literal_kind_hint": "id",
                }
            ],
            "coverage": _coverage(covered=["隧道", "经过", "设备", "ne-0001"]),
            "mock_intent": "tunnels_through_device",
        }

    if "设备" in question and "端口" in question and ("ne-0001" in question or "ne-9999" in question):
        device_id = "ne-0001" if "ne-0001" in question else "ne-9999"
        projection_terms = [_term("端口", "projection")]
        if "ID" in question or "编号" in question:
            projection_terms.append(_term("ID", "projection", attached_to="端口"))
        return {
            "schema_version": "question_decomposition_v1",
            "original_question": question,
            "literal_candidates": [device_id],
            "semantic_terms": ["NetworkElement.id", "HAS_PORT"],
            "substantive_terms": [
                _term("设备", "path"),
                _term(device_id, "filter", attached_to="设备"),
                *projection_terms,
            ],
            "literal_requests": [
                {
                    "raw_literal": device_id,
                    "expected_vertex": "NetworkElement",
                    "expected_property": "id",
                    "literal_kind_hint": "id",
                }
            ],
            "coverage": _coverage(covered=["设备", device_id, "端口"]),
            "mock_intent": "device_ports",
        }

    if "down" in question and "端口" in question:
        projection_terms = [_term("端口", "projection")]
        if "ID" in question or "编号" in question:
            projection_terms.append(_term("ID", "projection", attached_to="端口"))
        return {
            "schema_version": "question_decomposition_v1",
            "original_question": question,
            "literal_candidates": ["down"],
            "semantic_terms": ["Port.status"],
            "substantive_terms": [
                _term("当前", "unknown"),
                _term("down", "filter", attached_to="端口"),
                *projection_terms,
            ],
            "literal_requests": [
                {
                    "raw_literal": "down",
                    "expected_vertex": "Port",
                    "expected_property": "status",
                    "literal_kind_hint": "enum",
                }
            ],
            "coverage": _coverage(covered=["当前", "down", "端口"]),
            "mock_intent": "down_ports",
        }

    if "防火墙" in question and ("多少" in question or "数量" in question):
        return {
            "schema_version": "question_decomposition_v1",
            "original_question": question,
            "intent_type": "count",
            "output_shape": "scalar",
            "literal_candidates": ["防火墙"],
            "semantic_terms": ["device_count", "NetworkElement.elem_type"],
            "substantive_terms": [
                _term("全网", "unknown"),
                _term("多少", "projection"),
                _term("防火墙", "projection"),
            ],
            "literal_requests": [
                {
                    "raw_literal": "防火墙",
                    "expected_vertex": "NetworkElement",
                    "expected_property": "elem_type",
                    "literal_kind_hint": "enum",
                }
            ],
            "coverage": _coverage(covered=["全网", "多少", "防火墙"]),
            "mock_intent": "firewall_device_count",
        }

    if "按设备类型" in question and "设备数量" in question:
        return {
            "schema_version": "question_decomposition_v1",
            "original_question": question,
            "literal_candidates": [],
            "semantic_terms": ["device_count", "NetworkElement.elem_type"],
            "substantive_terms": [
                _term("按设备类型", "group_by"),
                _term("统计", "projection"),
                _term("设备数量", "projection"),
            ],
            "literal_requests": [],
            "coverage": _coverage(covered=["按设备类型", "统计", "设备数量"]),
            "mock_intent": "device_count_by_elem_type",
        }

    if "端口最多" in question and "设备" in question:
        return {
            "schema_version": "question_decomposition_v1",
            "original_question": question,
            "literal_candidates": [],
            "semantic_terms": ["port_count", "NetworkElement.id"],
            "substantive_terms": [
                _term("端口最多", "order_by"),
                _term("5", "limit"),
                _term("设备", "projection"),
            ],
            "literal_requests": [],
            "coverage": _coverage(covered=["端口最多", "5", "设备"]),
            "mock_intent": "top_n_devices_by_port_count",
        }

    if "先按状态统计端口" in question and "最多" in question:
        return {
            "schema_version": "question_decomposition_v1",
            "original_question": question,
            "literal_candidates": [],
            "semantic_terms": ["Port.status", "Port.id"],
            "substantive_terms": [
                _term("先按状态", "group_by"),
                _term("统计端口", "projection"),
                _term("最多", "order_by"),
            ],
            "literal_requests": [],
            "coverage": _coverage(covered=["先按状态", "统计端口", "最多"]),
            "mock_intent": "two_step_port_status_count",
        }

    if "按状态" in question and "端口数量" in question:
        return {
            "schema_version": "question_decomposition_v1",
            "original_question": question,
            "literal_candidates": [],
            "semantic_terms": ["Port.status", "Port.id"],
            "substantive_terms": [
                _term("按状态", "group_by"),
                _term("统计", "projection"),
                _term("端口数量", "projection"),
            ],
            "literal_requests": [],
            "coverage": _coverage(covered=["按状态", "统计", "端口数量"]),
            "mock_intent": "port_count_by_status",
        }

    if "所有服务" in question and "元素类型" in question and "服务质量等级" in question and "时延" in question:
        return {
            "schema_version": "question_decomposition_v1",
            "result_type": "decomposition",
            "original_question": question,
            "intent_type": "list",
            "output_shape": "rows",
            "literal_candidates": [],
            "semantic_terms": ["Service"],
            "substantive_terms": [
                _term("服务", "projection"),
                _term("ID", "projection", attached_to="服务"),
                _term("名称", "projection", attached_to="服务"),
                _term("元素类型", "projection", attached_to="服务"),
                _term("服务质量等级", "projection", attached_to="服务"),
                _term("带宽", "projection", attached_to="服务"),
                _term("时延", "projection", attached_to="服务"),
            ],
            "modality_terms": [],
            "time_terms": [],
            "unparsed_terms": [],
            "literal_requests": [],
            "coverage": _coverage(covered=["服务", "ID", "名称", "元素类型", "服务质量等级", "带宽", "时延"]),
        }

    if "所有服务" in question and "隧道" in question and "返回隧道" in question and "带宽" in question:
        return {
            "schema_version": "question_decomposition_v1",
            "result_type": "decomposition",
            "original_question": question,
            "intent_type": "list",
            "output_shape": "rows",
            "literal_candidates": [],
            "semantic_terms": ["Service", "Tunnel", "SERVICE_USES_TUNNEL"],
            "substantive_terms": [
                _term("服务", "path"),
                _term("使用", "path"),
                _term("隧道", "projection"),
                _term("ID", "projection", attached_to="隧道"),
                _term("名称", "projection", attached_to="隧道"),
                _term("带宽", "projection", attached_to="隧道"),
            ],
            "modality_terms": [],
            "time_terms": [],
            "unparsed_terms": [],
            "literal_requests": [],
            "coverage": _coverage(covered=["服务", "使用", "隧道", "ID", "名称", "带宽"]),
        }

    if "服务质量等级为金牌" in question and "服务" in question and "带宽" in question:
        return {
            "schema_version": "question_decomposition_v1",
            "result_type": "decomposition",
            "original_question": question,
            "intent_type": "list",
            "output_shape": "rows",
            "literal_candidates": [
                {"text": "金牌", "kind_hint": "enum_or_name", "attached_to": "服务质量等级"}
            ],
            "semantic_terms": ["Service", "Service.quality_of_service"],
            "substantive_terms": [
                _term("服务质量等级", "filter", attached_to="服务"),
                _term("金牌", "filter", attached_to="服务质量等级"),
                _term("服务", "projection"),
                _term("ID", "projection", attached_to="服务"),
                _term("名称", "projection", attached_to="服务"),
                _term("带宽", "projection", attached_to="服务"),
            ],
            "modality_terms": [],
            "time_terms": [],
            "unparsed_terms": [],
            "literal_requests": [
                {
                    "raw_literal": "金牌",
                    "expected_vertex": "Service",
                    "expected_property": "quality_of_service",
                    "literal_kind_hint": "enum",
                }
            ],
            "coverage": _coverage(covered=["服务质量等级", "金牌", "服务", "ID", "名称", "带宽"]),
        }

    return {
        "schema_version": "question_decomposition_v1",
        "original_question": question,
        "literal_candidates": [],
        "semantic_terms": ["Service"],
        "substantive_terms": [
            _term("收入", "unknown"),
            _term("增长", "unknown"),
        ] if ("收入" in question or "增长" in question) else [_term(question, "unknown")],
        "literal_requests": [],
        "coverage": _coverage(uncovered=["收入", "增长"] if ("收入" in question or "增长" in question) else [question]),
        "mock_intent": "coverage_failure",
    }


def _term(text: str, slot: str, *, attached_to: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"text": text, "slot": slot}
    if attached_to:
        payload["attached_to"] = attached_to
    return payload


def _coverage(
    *,
    covered: list[str] | None = None,
    uncovered: list[str] | None = None,
) -> dict[str, Any]:
    covered_terms = covered or []
    uncovered_terms = uncovered or []
    return CoverageReport(
        substantive_terms={
            "total": len(covered_terms) + len(uncovered_terms),
            "covered": len(covered_terms),
            "uncovered": uncovered_terms,
        }
    ).model_dump(mode="json")


def _resolve_literals(
    literal_requests: list[dict[str, Any]],
    *,
    question: str,
    trace_id: str,
    resolver: LiteralResolver,
) -> list[LiteralResolverResult]:
    results: list[LiteralResolverResult] = []
    for payload in literal_requests:
        payload = {
            **payload,
            "literal_kind_hint": _normalize_literal_kind_hint(payload.get("literal_kind_hint")),
        }
        request = LiteralResolverRequest(
            **payload,
            question_context=question,
            trace_id=trace_id,
        )
        results.append(resolver.resolve(request))
    return results


def _mock_understand(
    decomposition: dict[str, Any],
    literal_results: list[LiteralResolverResult],
) -> dict[str, Any]:
    intent = decomposition["mock_intent"]
    literal_payloads = [result.model_dump(mode="json") for result in literal_results]
    if intent == "service_id_tunnels":
        return {
            "query_shape": "single_hop",
            "selected_vertices": ["Service", "Tunnel"],
            "selected_edges": ["SERVICE_USES_TUNNEL"],
            "selected_properties": [{"owner": "Service", "name": "id"}],
            "selected_literals": literal_payloads,
            "filters": [
                {
                    "owner": "Service",
                    "property": "id",
                    "operator": "=",
                    "raw_literal": "svc-gold-001",
                }
            ],
            "projection": [
                {"semantic_type": "property", "owner": "Tunnel", "name": "id", "alias": "tunnel_id"}
            ],
        }

    if intent == "gold_service_tunnels":
        return {
            "query_shape": "single_hop",
            "selected_vertices": ["Service", "Tunnel"],
            "selected_edges": ["SERVICE_USES_TUNNEL"],
            "selected_properties": [{"owner": "Service", "name": "quality_of_service"}],
            "selected_literals": literal_payloads,
            "filters": [
                {
                    "owner": "Service",
                    "property": "quality_of_service",
                    "operator": "=",
                    "raw_literal": "Gold",
                }
            ],
            "projection": [
                {"semantic_type": "property", "owner": "Tunnel", "name": "id", "alias": "tunnel_id"}
            ],
        }

    if intent == "tunnel_full_path":
        return {
            "query_shape": "named_path_pattern",
            "selected_vertices": ["Tunnel"],
            "selected_path_patterns": ["tunnel_full_path"],
            "selected_properties": [{"owner": "Tunnel", "name": "id"}],
            "selected_literals": literal_payloads,
            "filters": [
                {
                    "owner": "Tunnel",
                    "property": "id",
                    "operator": "=",
                    "raw_literal": "tun-mpls-001",
                }
            ],
            "projection": [
                {"alias": "device", "source": "path.device"},
                {"alias": "hop", "source": "path.hop"},
            ],
            "assumptions": [{"type": "path_pattern_selected", "name": "tunnel_full_path"}],
        }

    if intent == "tunnels_through_device":
        return {
            "query_shape": "variable_path_traversal",
            "selected_vertices": ["Tunnel", "NetworkElement"],
            "selected_edges": ["PATH_THROUGH"],
            "selected_properties": [{"owner": "NetworkElement", "name": "id"}],
            "selected_literals": literal_payloads,
            "filters": [
                {
                    "owner": "NetworkElement",
                    "property": "id",
                    "operator": "=",
                    "raw_literal": "ne-0001",
                }
            ],
            "projection": [
                {"semantic_type": "property", "owner": "Tunnel", "name": "id", "alias": "tunnel_id"}
            ],
        }

    if intent == "device_ports":
        return {
            "query_shape": "single_hop",
            "selected_vertices": ["NetworkElement", "Port"],
            "selected_edges": ["HAS_PORT"],
            "selected_properties": [{"owner": "NetworkElement", "name": "id"}],
            "selected_literals": literal_payloads,
            "filters": [
                {
                    "owner": "NetworkElement",
                    "property": "id",
                    "operator": "=",
                    "raw_literal": decomposition["literal_candidates"][0],
                }
            ],
            "projection": [
                {"semantic_type": "property", "owner": "Port", "name": "id", "alias": "port_id"}
            ],
        }

    if intent == "down_ports":
        return {
            "query_shape": "vertex_lookup",
            "selected_vertices": ["Port"],
            "selected_properties": [{"owner": "Port", "name": "status"}],
            "selected_literals": literal_payloads,
            "filters": [
                {
                    "owner": "Port",
                    "property": "status",
                    "operator": "=",
                    "raw_literal": "down",
                }
            ],
            "projection": [
                {"semantic_type": "property", "owner": "Port", "name": "id", "alias": "port_id"}
            ],
        }

    if intent == "firewall_device_count":
        return {
            "query_shape": "metric_aggregate",
            "selected_metrics": ["device_count"],
            "selected_properties": [{"owner": "NetworkElement", "name": "elem_type"}],
            "selected_literals": literal_payloads,
            "filters": [
                {
                    "owner": "NetworkElement",
                    "property": "elem_type",
                    "operator": "=",
                    "raw_literal": "防火墙",
                }
            ],
            "projection": [{"alias": "device_count", "source": "metric.device_count"}],
        }

    if intent == "device_count_by_elem_type":
        return {
            "query_shape": "metric_aggregate",
            "selected_metrics": ["device_count"],
            "selected_properties": [{"owner": "NetworkElement", "name": "elem_type"}],
            "group_by": [
                {
                    "alias": "elem_type",
                    "target": "ne",
                    "property": {"owner": "NetworkElement", "name": "elem_type"},
                }
            ],
            "projection": [
                {"alias": "elem_type", "source": "group.elem_type"},
                {"alias": "device_count", "source": "metric.device_count"},
            ],
        }

    if intent == "port_count_by_status":
        return {
            "query_shape": "ad_hoc_aggregate",
            "selected_vertices": ["Port"],
            "selected_properties": [
                {"owner": "Port", "name": "status"},
                {"owner": "Port", "name": "id"},
            ],
            "group_by": [
                {
                    "alias": "status",
                    "target": "port",
                    "property": {"owner": "Port", "name": "status"},
                }
            ],
            "measures": [
                {
                    "alias": "port_count",
                    "function": "count",
                    "target": "port",
                    "property": {"owner": "Port", "name": "id"},
                }
            ],
            "projection": [
                {"alias": "status", "source": "group.status"},
                {"alias": "port_count", "source": "measure.port_count"},
            ],
        }

    if intent == "top_n_devices_by_port_count":
        return {
            "query_shape": "top_n",
            "selected_metrics": ["port_count"],
            "selected_properties": [{"owner": "NetworkElement", "name": "id"}],
            "group_by": [
                {
                    "alias": "device",
                    "target": "ne",
                    "property": {"owner": "NetworkElement", "name": "id"},
                }
            ],
            "projection": [
                {"alias": "device", "source": "group.device"},
                {"alias": "port_count", "source": "metric.port_count"},
            ],
            "sort": [{"source": "metric.port_count", "direction": "desc"}],
            "limit": 5,
        }

    if intent == "two_step_port_status_count":
        return {
            "query_shape": "two_step_aggregate",
            "selected_vertices": ["Port"],
            "selected_properties": [
                {"owner": "Port", "name": "status"},
                {"owner": "Port", "name": "id"},
            ],
            "group_by": [
                {
                    "alias": "status",
                    "target": "port",
                    "property": {"owner": "Port", "name": "status"},
                }
            ],
            "measures": [
                {
                    "alias": "port_count",
                    "function": "count",
                    "target": "port",
                    "property": {"owner": "Port", "name": "id"},
                }
            ],
            "projection": [
                {"alias": "status", "source": "port_status_counts.status"},
                {"alias": "port_count", "source": "port_status_counts.port_count"},
            ],
            "sort": [{"source": "port_status_counts.port_count", "direction": "desc"}],
            "limit": 5,
        }

    return {
        "query_shape": "vertex_lookup",
        "selected_vertices": ["Service"],
    }


def _failure(
    trace: GraphTraceBuilder,
    *,
    reason: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> GenerationOutput:
    failure: dict[str, Any] = {
        "reason": reason,
        "message": message,
        "suggested_rewrites": [],
    }
    if details:
        failure["details"] = details
    status = _status_for_failure_reason(reason)
    _add_output_stage(
        trace,
        status=status,
        payload={"status": status, "failure": failure},
        errors=[{"code": reason, "message": message}],
    )
    return trace.finalize_failure(status=status, failure=failure)


def _clarification(
    trace: GraphTraceBuilder,
    *,
    question: str,
    user_visible_notices: list[str] | None = None,
) -> GenerationOutput:
    clarification = {"question": question}
    _add_output_stage(
        trace,
        status="clarification_required",
        payload={"status": "clarification_required", "clarification": clarification},
    )
    return trace.finalize_clarification(
        clarification=clarification,
        user_visible_notices=user_visible_notices or [],
    )


def _handle_repair_decision(
    trace: GraphTraceBuilder,
    *,
    decision: RepairDecision,
    fallback_details: dict[str, Any] | None = None,
) -> GenerationOutput:
    if decision.decision == "ask_user" and decision.clarification is not None:
        return _clarification(
            trace,
            question=decision.clarification.question or decision.clarification.question_zh or "请补充澄清信息。",
            user_visible_notices=decision.derived_user_visible_notices,
        )
    if decision.decision == "unsupported":
        return _failure(
            trace,
            reason="unsupported_query_shape",
            message=decision.reason_code,
            details=fallback_details,
        )
    if decision.decision == "generation_failed":
        return _failure(
            trace,
            reason=decision.reason_code,
            message=decision.stop_reason or decision.reason_code,
            details=fallback_details,
        )
    return _failure(
        trace,
        reason="semantic_contract_unaligned",
        message=f"Unexpected repair decision {decision.decision}",
        details={"repair_decision": decision.model_dump(mode="json"), **(fallback_details or {})},
    )


def _generated(
    trace: GraphTraceBuilder,
    *,
    dsl: dict[str, Any],
    cypher: str,
    user_visible_notices: list[str] | None = None,
) -> GenerationOutput:
    _add_output_stage(
        trace,
        status="generated",
        payload={"status": "generated", "has_dsl": True, "has_cypher": True},
    )
    return trace.finalize_generated(
        dsl=dsl,
        cypher=cypher,
        user_visible_notices=user_visible_notices or [],
    )


def _unexpected_failure(trace: GraphTraceBuilder, exc: Exception) -> GenerationOutput:
    reason = "knowledge_context_unavailable" if _last_stage_name(trace) == "graph_model_loader" else "semantic_contract_unaligned"
    return _failure(trace, reason=reason, message=str(exc))


def _add_output_stage(
    trace: GraphTraceBuilder,
    *,
    status: str,
    payload: dict[str, Any],
    errors: list[dict[str, Any]] | None = None,
) -> None:
    trace.add_stage(
        stage=StageName.OUTPUT,
        status="success" if status == "generated" else "failed",
        duration_ms=0,
        output_ref=inline_ref(payload),
        errors=errors or [],
    )


def _status_for_failure_reason(reason: str) -> str:
    if reason in set(ServiceFailureReason.__args__):
        return "service_failed"
    if reason == "unsupported_query_shape":
        return "unsupported_query_shape"
    if reason not in set(GenerationFailureReason.__args__):
        return "service_failed"
    return "generation_failed"


def _literal_unresolved_issue(result: LiteralResolverResult) -> RepairIssue:
    expected = ".".join(
        part
        for part in [result.expected_vertex or result.expected_edge, result.expected_property]
        if part
    )
    return RepairIssue(
        code="literal_unresolved",
        message=f"literal {result.raw_literal!r} could not be resolved for {expected}",
        severity="error",
        repairable=False,
        action="ask_user",
        details={
            "literal": result.raw_literal,
            "property": expected,
            "alternatives": [
                alternative.model_dump(mode="json")
                for alternative in result.alternatives
            ],
        },
    )


def _last_stage_name(trace: GraphTraceBuilder) -> str | None:
    stages = getattr(trace, "_stages", [])
    if not stages:
        return None
    return str(stages[-1].stage)
