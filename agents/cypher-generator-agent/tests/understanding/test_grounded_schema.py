from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from services.cypher_generator_agent.app.binding import SemanticBinder
from services.cypher_generator_agent.app.decomposition.models import QuestionDecomposition
from services.cypher_generator_agent.app.literals.models import LiteralEvidence, LiteralResolverResult
from services.cypher_generator_agent.app.retrieval.models import (
    CandidateEvidence,
    CandidateRetrievalResult,
    SemanticCandidate,
)
from services.cypher_generator_agent.app.semantic_model.loader import load_graph_semantic_model
from services.cypher_generator_agent.app.understanding import (
    GROUNDED_UNDERSTANDING_SCHEMA_VERSION,
    GroundedUnderstanding,
    GroundedUnderstandingFailure,
    GroundedUnderstandingSelector,
)


FIXTURE_PATH = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "network_topology_graph_model.yaml"
)


class FakeProviderUnavailable(RuntimeError):
    pass


class FakeGroundedLLMClient:
    provider = "fake-grounded-llm"

    def __init__(
        self,
        responses: list[Mapping[str, Any]] | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.responses = list(responses or [])
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def generate_structured(
        self,
        *,
        prompt: str,
        schema_name: str,
        schema: Mapping[str, Any],
        attempt: int,
    ) -> Mapping[str, Any]:
        self.calls.append(
            {
                "prompt": prompt,
                "schema_name": schema_name,
                "schema": schema,
                "attempt": attempt,
            }
        )
        if self.error is not None:
            raise self.error
        return self.responses.pop(0)


def test_selecting_service_uses_tunnel_yields_binder_compatible_payload() -> None:
    literal_result = _gold_literal_result()
    candidates = _gold_candidates()
    client = FakeGroundedLLMClient(
        [
            {
                "schema_version": "grounded_understanding_v1",
                "status": "grounded",
                "query_shape": "single_hop",
                "selected_bindings": [
                    _binding("source", "vertex", "Service"),
                    _binding("target", "vertex", "Tunnel"),
                    _binding("relation", "edge", "SERVICE_USES_TUNNEL", direction="forward"),
                    _binding(
                        "filter_property",
                        "property",
                        "Service.quality_of_service",
                        semantic_name="quality_of_service",
                        owner="Service",
                    ),
                ],
                "selected_literals": [literal_result.model_dump()],
                "filters": [
                    {
                        "owner": "Service",
                        "property": "quality_of_service",
                        "operator": "=",
                        "raw_literal": "Gold",
                    }
                ],
                "projection": [
                    {
                        "semantic_type": "property",
                        "owner": "Tunnel",
                        "name": "id",
                        "alias": "tunnel_id",
                    }
                ],
                "limit": 50,
                "coverage": _coverage(["Gold", "服务", "使用", "隧道"]),
                "unsupported": None,
                "confidence": 0.93,
            }
        ]
    )

    result = GroundedUnderstandingSelector(client, max_schema_retries=0).select(
        question_decomposition=_gold_decomposition(),
        candidates=candidates,
        literal_results=[literal_result],
    )

    assert isinstance(result, GroundedUnderstanding)
    assert result.schema_version == "grounded_understanding_v1"
    assert result.status == "grounded"
    assert result.coverage.substantive_terms.uncovered == []
    assert result.unsupported is None
    assert result.confidence == 0.93
    assert [call["schema_name"] for call in client.calls] == [GROUNDED_UNDERSTANDING_SCHEMA_VERSION]
    assert "vertex:Service" in client.calls[0]["prompt"]
    assert "literal_resolver_result_v1" in client.calls[0]["prompt"]
    assert "你是图原生 Cypher 生成流水线中的语义落地理解选择器" in client.calls[0]["prompt"]
    assert "只能从 top_candidates 中按 candidate_id 选择" in client.calls[0]["prompt"]
    assert "You are the Grounded Understanding selector" not in client.calls[0]["prompt"]

    binder = SemanticBinder(load_graph_semantic_model(FIXTURE_PATH).registry)
    plan = binder.bind(result.to_binder_payload(), candidates=candidates)

    assert plan.query_shape == "single_hop_traversal"
    assert [binding.name for binding in plan.vertex_bindings] == ["Service", "Tunnel"]
    assert [binding.name for binding in plan.edge_bindings] == ["SERVICE_USES_TUNNEL"]
    assert [(binding.owner, binding.name) for binding in plan.property_bindings] == [
        ("Service", "quality_of_service")
    ]
    assert plan.filters[0].value == "GOLD"
    assert plan.projection == [
        {
            "semantic_type": "property",
            "owner": "Tunnel",
            "name": "id",
            "alias": "tunnel_id",
        }
    ]


def test_compact_candidate_only_output_hydrates_to_binder_payload() -> None:
    literal_result = _gold_literal_result()
    client = FakeGroundedLLMClient(
        [
            {
                "schema_version": "grounded_understanding_v1",
                "status": "grounded",
                "query_shape": "single_hop",
                "selected_bindings": [
                    {"candidate_id": "vertex:Service"},
                    {"candidate_id": "vertex:Tunnel"},
                    {
                        "candidate_id": "edge:SERVICE_USES_TUNNEL",
                        "direction": "forward",
                    },
                    {"candidate_id": "property:Service.quality_of_service"},
                ],
                "selected_literal_ids": ["literal:0"],
                "filters": [
                    {
                        "owner": "Service",
                        "property": "quality_of_service",
                        "operator": "=",
                        "raw_literal": "Gold",
                    }
                ],
                "projection": [
                    {
                        "semantic_type": "property",
                        "owner": "Tunnel",
                        "name": "id",
                        "alias": "tunnel_id",
                    }
                ],
                "limit": 50,
            }
        ]
    )

    result = GroundedUnderstandingSelector(client, max_schema_retries=0).select(
        question_decomposition=_gold_decomposition(),
        candidates=_gold_candidates(),
        literal_results=[literal_result],
    )

    assert isinstance(result, GroundedUnderstanding)
    assert [
        {key: value for key, value in binding.model_dump().items() if value is not None}
        for binding in result.selected_bindings
    ] == [
        {
            "role": "vertex",
            "semantic_type": "vertex",
            "candidate_id": "vertex:Service",
            "semantic_id": "Service",
            "semantic_name": "Service",
        },
        {
            "role": "vertex",
            "semantic_type": "vertex",
            "candidate_id": "vertex:Tunnel",
            "semantic_id": "Tunnel",
            "semantic_name": "Tunnel",
        },
        {
            "role": "edge",
            "semantic_type": "edge",
            "candidate_id": "edge:SERVICE_USES_TUNNEL",
            "semantic_id": "SERVICE_USES_TUNNEL",
            "semantic_name": "SERVICE_USES_TUNNEL",
            "direction": "forward",
        },
        {
            "role": "property",
            "semantic_type": "property",
            "candidate_id": "property:Service.quality_of_service",
            "semantic_id": "Service.quality_of_service",
            "semantic_name": "quality_of_service",
            "owner": "Service",
        },
    ]
    assert result.selected_literals == [literal_result]

    binder = SemanticBinder(load_graph_semantic_model(FIXTURE_PATH).registry)
    plan = binder.bind(result.to_binder_payload(), candidates=_gold_candidates())

    assert plan.query_shape == "single_hop_traversal"
    assert [binding.name for binding in plan.vertex_bindings] == ["Service", "Tunnel"]
    assert [binding.name for binding in plan.edge_bindings] == ["SERVICE_USES_TUNNEL"]
    assert [(binding.owner, binding.name) for binding in plan.property_bindings] == [
        ("Service", "quality_of_service")
    ]
    assert plan.filters[0].value == "GOLD"


def test_compact_projection_vertex_binding_hydrates_to_vertex_full_projection() -> None:
    payload = _valid_minimal_payload()
    payload["selected_bindings"] = [
        {"role": "source", "candidate_id": "vertex:Service"},
        {"role": "relation", "candidate_id": "edge:SERVICE_USES_TUNNEL", "direction": "forward"},
        {"role": "projection", "candidate_id": "vertex:Tunnel"},
    ]
    payload["projection"] = []
    client = FakeGroundedLLMClient([payload])

    result = GroundedUnderstandingSelector(client, max_schema_retries=0).select(
        question_decomposition=_gold_decomposition(),
        candidates=_gold_candidates(),
        literal_results=[],
    )

    assert isinstance(result, GroundedUnderstanding)
    assert result.to_binder_payload()["projection"] == [
        {"semantic_type": "vertex_full", "name": "Tunnel", "alias": "tunnel"}
    ]


def test_legacy_string_operation_hints_hydrate_to_binder_payload() -> None:
    client = FakeGroundedLLMClient(
        [
            {
                "schema_version": "grounded_understanding_v1",
                "status": "grounded",
                "query_shape": "top_n",
                "selected_bindings": [
                    _binding("source", "vertex", "Service"),
                    _binding("relation", "edge", "SERVICE_USES_TUNNEL", direction="forward"),
                    _binding("relation", "edge", "PATH_THROUGH", direction="forward"),
                    _binding("target", "vertex", "NetworkElement"),
                    _binding(
                        "group_by_property",
                        "property",
                        "NetworkElement.location",
                        semantic_name="location",
                        owner="NetworkElement",
                    ),
                ],
                "selected_literals": [],
                "projection": ["NetworkElement.location"],
                "group_by": ["NetworkElement.location"],
                "measures": ["count(Service) as 次数"],
                "sort": ["次数 desc"],
                "assumptions": [
                    "假设 SERVICE_USES_TUNNEL 表示业务与隧道的承载关系",
                ],
                "limit": 5,
                "coverage": _coverage(["业务", "隧道", "网元", "厂商", "次数"]),
                "unsupported": None,
                "confidence": 0.8,
            }
        ]
    )

    result = GroundedUnderstandingSelector(client).select(
        question_decomposition=_gold_decomposition(),
        candidates=_aggregate_candidates(),
        literal_results=[],
    )

    assert isinstance(result, GroundedUnderstanding)
    assert result.group_by == [
        {
            "alias": "network_element_location",
            "target": "network_element",
            "property": {"owner": "NetworkElement", "name": "location"},
        }
    ]
    assert result.measures == [
        {
            "alias": "cnt",
            "function": "count",
            "target": "service",
            "property": {"owner": "Service", "name": "id"},
            "projection_terms": ["次数"],
        }
    ]
    assert result.sort == [{"source": "measure.cnt", "direction": "desc"}]
    assert result.projection == [
        {"alias": "network_element_location", "source": "group.network_element_location"}
    ]
    assert result.assumptions == [
        {
            "type": "llm_assumption",
            "message": "假设 SERVICE_USES_TUNNEL 表示业务与隧道的承载关系",
        }
    ]

    binder = SemanticBinder(load_graph_semantic_model(FIXTURE_PATH).registry)
    plan = binder.bind(result.to_binder_payload(), candidates=_aggregate_candidates())

    assert plan.query_shape == "top_n"
    assert [binding.name for binding in plan.vertex_bindings] == ["Service", "NetworkElement"]
    assert [(item["property"]["owner"], item["property"]["name"]) for item in plan.group_by] == [
        ("NetworkElement", "location")
    ]
    assert [(item["function"], item["property"]["owner"], item["property"]["name"]) for item in plan.measures] == [
        ("count", "Service", "id")
    ]


def test_compact_metric_projection_hydrates_to_measure_source() -> None:
    client = FakeGroundedLLMClient(
        [
            {
                "schema_version": "grounded_understanding_v1",
                "status": "grounded",
                "query_shape": "top_n",
                "selected_bindings": [
                    {"role": "metric", "candidate_id": "metric:device_count"},
                    {"role": "group_by", "candidate_id": "property:NetworkElement.location"},
                ],
                "projection": [
                    {
                        "semantic_type": "property",
                        "owner": "NetworkElement",
                        "name": "location",
                        "alias": "location",
                    },
                    {
                        "semantic_type": "metric",
                        "name": "device_count",
                        "alias": "网元数量",
                    },
                ],
                "group_by": [
                    {
                        "alias": "location",
                        "target": "network_element",
                        "property": {"owner": "NetworkElement", "name": "location"},
                    }
                ],
                "measures": [
                    {
                        "alias": "网元数量",
                        "function": "count",
                        "target": "network_element",
                        "property": {"owner": "NetworkElement", "name": "id"},
                    }
                ],
                "sort": [{"source": "measure.网元数量", "direction": "desc"}],
                "limit": 10,
            }
        ]
    )

    result = GroundedUnderstandingSelector(client).select(
        question_decomposition=_gold_decomposition(),
        candidates=_aggregate_candidates(),
        literal_results=[],
    )

    assert isinstance(result, GroundedUnderstanding)
    assert result.projection == [
        {"alias": "location", "source": "group.location"},
        {"alias": "网元数量", "source": "measure.网元数量"},
    ]


def test_compact_null_operation_lists_are_treated_as_empty() -> None:
    payload = _valid_minimal_payload()
    payload["sort"] = None
    client = FakeGroundedLLMClient([payload])

    result = GroundedUnderstandingSelector(client, max_schema_retries=0).select(
        question_decomposition=_gold_decomposition(),
        candidates=_gold_candidates(),
        literal_results=[],
    )

    assert isinstance(result, GroundedUnderstanding)
    assert result.sort == []


def test_schema_violation_retries_then_returns_valid_grounding() -> None:
    client = FakeGroundedLLMClient(
        [
            {"schema_version": "grounded_understanding_v1", "query_shape": "single_hop"},
            _valid_minimal_payload(),
        ]
    )

    result = GroundedUnderstandingSelector(client).select(
        question_decomposition=_gold_decomposition(),
        candidates=_gold_candidates(),
        literal_results=[],
    )

    assert isinstance(result, GroundedUnderstanding)
    assert [call["attempt"] for call in client.calls] == [1, 2]


def test_schema_violation_stops_after_initial_attempt_plus_two_retries() -> None:
    client = FakeGroundedLLMClient(
        [
            {"schema_version": "grounded_understanding_v1", "query_shape": "single_hop"},
            {"schema_version": "grounded_understanding_v1", "query_shape": "single_hop"},
            {"schema_version": "grounded_understanding_v1", "query_shape": "single_hop"},
        ]
    )

    result = GroundedUnderstandingSelector(client).select(
        question_decomposition=_gold_decomposition(),
        candidates=_gold_candidates(),
        literal_results=[],
    )

    assert isinstance(result, GroundedUnderstandingFailure)
    assert result.status == "generation_failed"
    assert result.reason == "grounded_understanding_schema_invalid"
    assert result.provider == "fake-grounded-llm"
    assert result.error_type == "ValidationError"
    assert result.attempts == 3
    assert result.retry_count == 2
    assert [call["attempt"] for call in client.calls] == [1, 2, 3]


def test_bare_vertex_projection_hydrates_to_vertex_full_when_candidate_exists() -> None:
    bare_vertex_payload = _valid_minimal_payload()
    bare_vertex_payload["projection"] = [{"semantic_type": "vertex", "name": "Service"}]
    client = FakeGroundedLLMClient(
        [
            bare_vertex_payload,
        ]
    )

    result = GroundedUnderstandingSelector(client, max_schema_retries=0).select(
        question_decomposition=_gold_decomposition(),
        candidates=_gold_candidates(),
        literal_results=[],
    )

    assert isinstance(result, GroundedUnderstanding)
    assert result.projection == [{"semantic_type": "vertex_full", "name": "Service"}]


def test_non_projection_semantic_type_is_schema_invalid() -> None:
    invalid_projection_payload = _valid_minimal_payload()
    invalid_projection_payload["projection"] = [
        {"semantic_type": "edge", "name": "SERVICE_USES_TUNNEL"}
    ]
    client = FakeGroundedLLMClient(
        [
            invalid_projection_payload,
            invalid_projection_payload,
            invalid_projection_payload,
        ]
    )

    result = GroundedUnderstandingSelector(client).select(
        question_decomposition=_gold_decomposition(),
        candidates=_gold_candidates(),
        literal_results=[],
    )

    assert isinstance(result, GroundedUnderstandingFailure)
    assert result.reason == "grounded_understanding_schema_invalid"
    assert "projection semantic_type must be property or vertex_full" in result.errors[-1].message


def test_metric_projection_without_matching_measure_source_stays_schema_invalid() -> None:
    invalid_projection_payload = _valid_minimal_payload()
    invalid_projection_payload["query_shape"] = "top_n"
    invalid_projection_payload["selected_bindings"] = [{"candidate_id": "metric:device_count"}]
    invalid_projection_payload["projection"] = [
        {"semantic_type": "metric", "name": "device_count", "alias": "设备数量"}
    ]
    invalid_projection_payload["measures"] = []
    client = FakeGroundedLLMClient([invalid_projection_payload])

    result = GroundedUnderstandingSelector(client, max_schema_retries=0).select(
        question_decomposition=_gold_decomposition(),
        candidates=_aggregate_candidates(),
        literal_results=[],
    )

    assert isinstance(result, GroundedUnderstandingFailure)
    assert result.reason == "grounded_understanding_schema_invalid"
    assert "projection semantic_type must be property or vertex_full" in result.errors[-1].message


def test_bare_vertex_projection_outside_candidates_stays_rejected() -> None:
    invalid_projection_payload = _valid_minimal_payload()
    invalid_projection_payload["projection"] = [{"semantic_type": "vertex", "name": "Protocol"}]
    client = FakeGroundedLLMClient([invalid_projection_payload])

    result = GroundedUnderstandingSelector(client, max_schema_retries=0).select(
        question_decomposition=_gold_decomposition(),
        candidates=_gold_candidates(),
        literal_results=[],
    )

    assert isinstance(result, GroundedUnderstandingFailure)
    assert result.reason == "semantic_match_rejected"
    assert "candidate_id vertex:Protocol is not present in candidate set" in result.errors[-1].message


def test_vertex_full_projection_is_explicitly_allowed() -> None:
    vertex_full_payload = _valid_minimal_payload()
    vertex_full_payload["projection"] = [
        {"semantic_type": "vertex_full", "name": "Service", "alias": "service"}
    ]
    client = FakeGroundedLLMClient([vertex_full_payload])

    result = GroundedUnderstandingSelector(client).select(
        question_decomposition=_gold_decomposition(),
        candidates=_gold_candidates(),
        literal_results=[],
    )

    assert isinstance(result, GroundedUnderstanding)
    assert result.projection == [
        {"semantic_type": "vertex_full", "name": "Service", "alias": "service"}
    ]


def test_provider_unavailable_returns_service_failed_without_deterministic_fallback() -> None:
    client = FakeGroundedLLMClient(error=FakeProviderUnavailable("provider unavailable"))

    result = GroundedUnderstandingSelector(client).select(
        question_decomposition=_gold_decomposition(),
        candidates=_gold_candidates(),
        literal_results=[],
    )

    assert isinstance(result, GroundedUnderstandingFailure)
    assert result.status == "service_failed"
    assert result.reason == "model_invocation_failed"
    assert result.provider == "fake-grounded-llm"
    assert result.error_type == "FakeProviderUnavailable"
    assert result.attempts == 1
    assert result.retry_count == 0
    assert len(client.calls) == 1


def test_unsupported_output_preserves_coverage_gap_without_binder_payload() -> None:
    client = FakeGroundedLLMClient(
        [
            {
                "schema_version": "grounded_understanding_v1",
                "status": "unsupported_query_shape",
                "query_shape": "unsupported",
                "selected_bindings": [],
                "selected_literals": [],
                "filters": [],
                "projection": [],
                "coverage": _coverage([], uncovered=["收入", "增长"]),
                "unsupported": {
                    "reason_code": "coverage_gap_unknown_metric",
                    "message": "No revenue or growth metric exists in the graph semantic model.",
                },
                "confidence": 0.88,
            }
        ]
    )

    result = GroundedUnderstandingSelector(client).select(
        question_decomposition={
            "schema_version": "question_decomposition_v1",
            "result_type": "decomposition",
            "intent_type": "compare",
            "original_question": "收入增长情况",
            "literal_candidates": [],
            "substantive_terms": [
                {"text": "收入", "slot": "unknown"},
                {"text": "增长", "slot": "unknown"},
            ],
            "modality_terms": [],
            "time_terms": [],
            "unparsed_terms": [],
            "output_shape": "unknown",
        },
        candidates=CandidateRetrievalResult(candidates=[]),
        literal_results=[],
    )

    assert isinstance(result, GroundedUnderstanding)
    assert result.status == "unsupported_query_shape"
    assert result.unsupported is not None
    assert result.unsupported.reason_code == "coverage_gap_unknown_metric"
    assert result.coverage.substantive_terms.uncovered == ["收入", "增长"]


def _valid_minimal_payload() -> dict[str, Any]:
    return {
        "schema_version": "grounded_understanding_v1",
        "status": "grounded",
        "query_shape": "lookup",
        "selected_bindings": [_binding("target", "vertex", "Service")],
        "selected_literals": [],
        "filters": [],
        "projection": [],
        "coverage": _coverage(["服务"]),
        "unsupported": None,
        "confidence": 0.9,
    }


def _gold_decomposition() -> QuestionDecomposition:
    return QuestionDecomposition(
        result_type="decomposition",
        intent_type="list",
        original_question="Gold 服务使用了哪些隧道",
        literal_candidates=[
            {"text": "Gold", "kind_hint": "enum_or_name", "attached_to": "服务"}
        ],
        substantive_terms=[
            {"text": "Gold", "slot": "filter", "attached_to": "服务"},
            {"text": "服务", "slot": "path"},
            {"text": "使用", "slot": "path"},
            {"text": "隧道", "slot": "projection"},
        ],
        modality_terms=[],
        time_terms=[],
        unparsed_terms=[],
        output_shape="rows",
    )


def _gold_literal_result() -> LiteralResolverResult:
    return LiteralResolverResult(
        raw_literal="Gold",
        resolved=True,
        resolved_value="GOLD",
        normalized_value="GOLD",
        match_type="value_synonym",
        confidence=0.98,
        expected_vertex="Service",
        expected_property="quality_of_service",
        evidence=[
            LiteralEvidence(
                source="property.value_synonyms",
                matched="Gold",
                target="GOLD",
            )
        ],
    )


def _gold_candidates() -> CandidateRetrievalResult:
    return CandidateRetrievalResult(
        candidates=[
            _candidate("vertex", "Service"),
            _candidate("vertex", "Tunnel"),
            _candidate("edge", "SERVICE_USES_TUNNEL"),
            _candidate(
                "property",
                "Service.quality_of_service",
                owner="Service",
                semantic_name="quality_of_service",
            ),
            _candidate(
                "property",
                "Tunnel.id",
                owner="Tunnel",
                semantic_name="id",
            ),
        ]
    )


def _aggregate_candidates() -> CandidateRetrievalResult:
    return CandidateRetrievalResult(
        candidates=[
            _candidate("vertex", "Service"),
            _candidate("vertex", "NetworkElement"),
            _candidate("metric", "device_count"),
            _candidate("edge", "SERVICE_USES_TUNNEL"),
            _candidate("edge", "PATH_THROUGH"),
            _candidate("property", "Service.id", owner="Service", semantic_name="id"),
            _candidate("property", "NetworkElement.id", owner="NetworkElement", semantic_name="id"),
            _candidate(
                "property",
                "NetworkElement.location",
                owner="NetworkElement",
                semantic_name="location",
            ),
        ]
    )


def _binding(
    role: str,
    semantic_type: str,
    semantic_id: str,
    *,
    semantic_name: str | None = None,
    owner: str | None = None,
    direction: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "role": role,
        "semantic_type": semantic_type,
        "candidate_id": f"{semantic_type}:{semantic_id}",
        "semantic_id": semantic_id,
        "semantic_name": semantic_name or semantic_id,
    }
    if owner is not None:
        payload["owner"] = owner
    if direction is not None:
        payload["direction"] = direction
    return payload


def _candidate(
    semantic_type: str,
    semantic_id: str,
    *,
    owner: str | None = None,
    semantic_name: str | None = None,
) -> SemanticCandidate:
    return SemanticCandidate(
        semantic_type=semantic_type,
        semantic_id=semantic_id,
        semantic_name=semantic_name or semantic_id,
        owner=owner,
        score=1.0,
        match_type="exact",
        evidence=[
            CandidateEvidence(
                term=semantic_id,
                source="test",
                matched_text=semantic_id,
            )
        ],
    )


def _coverage(covered: list[str], *, uncovered: list[str] | None = None) -> dict[str, Any]:
    covered_terms = covered
    uncovered_terms = uncovered or []
    return {
        "substantive_terms": {
            "total": len(covered_terms) + len(uncovered_terms),
            "covered": len(covered_terms),
            "uncovered": uncovered_terms,
        }
    }
