from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

from services.cypher_generator_agent.app.core.errors import GenerationFailureReason


FIXTURE_DIR = Path(__file__).resolve().parent
GRAPH_MODEL_PATH = FIXTURE_DIR / "network_topology_graph_model.yaml"
VALUE_INDEX_PATH = FIXTURE_DIR / "value_index.json"
QUESTIONS_PATH = FIXTURE_DIR / "questions.yaml"
GOLDEN_QUESTIONS_PATH = FIXTURE_DIR / "golden_questions.yaml"

REQUIRED_FIXTURES = [
    GRAPH_MODEL_PATH,
    VALUE_INDEX_PATH,
    QUESTIONS_PATH,
    GOLDEN_QUESTIONS_PATH,
]

NON_SUCCESS_STATUSES = {
    "clarification_required",
    "unsupported_query_shape",
    "generation_failed",
}
EXPECTED_STATUSES = {"generated"} | NON_SUCCESS_STATUSES
GENERATION_FAILURE_REASONS = set(GenerationFailureReason.__args__)
NON_SUCCESS_REASON_GROUPS = {"failure", "clarification", "unsupported"}
REGRESSION_SCOPES = {"smoke", "full"}
EXPECTED_VERTICES = {"NetworkElement", "Tunnel", "Service", "Port"}
EXPECTED_EDGES = {
    "SERVICE_USES_TUNNEL",
    "PATH_THROUGH",
    "TUNNEL_SRC",
    "TUNNEL_DST",
    "HAS_PORT",
}
EXPECTED_PROPERTIES_BY_OWNER = {
    "NetworkElement": {"id", "name", "elem_type", "location"},
    "Tunnel": {"id", "bandwidth"},
    "Service": {"id", "quality_of_service", "service_type"},
    "Port": {"id", "status"},
    "PATH_THROUGH": {"hop_order"},
}
SUPPORTED_OR_SENTINEL_QUERY_SHAPES = {
    "vertex_lookup",
    "single_hop_traversal",
    "variable_path_traversal",
    "named_path_pattern",
    "metric_aggregate",
    "ad_hoc_aggregate",
    "top_n",
    "two_step_aggregate",
    "unsupported",
}
REQUIRED_GOLDEN_QUESTION_KEYS = {
    "id",
    "question",
    "expected_status",
    "primary_ir",
    "expected_reason_code",
    "coverage",
    "query_shape",
    "ci_smoke",
}
OPTIONAL_GOLDEN_QUESTION_KEYS = {
    "expected_dsl_ir",
    "cypher_filled_by_ir",
    "expected_dsl_fixture",
    "expected_cypher_fixture",
    "regression_scope",
    "reason",
}


def test_fixture_files_are_loadable() -> None:
    for path in REQUIRED_FIXTURES:
        assert path.exists(), f"missing fixture: {path.name}"

    model = _load_graph_model()
    value_index = _load_json(VALUE_INDEX_PATH)
    questions = _load_yaml(QUESTIONS_PATH)
    golden_questions = _load_yaml(GOLDEN_QUESTIONS_PATH)

    assert model["name"] == "network_topology"
    assert isinstance(value_index, dict)
    assert isinstance(questions["questions"], list)
    assert isinstance(golden_questions["golden_questions"], list)


def test_graph_model_declares_required_network_topology_vocabulary() -> None:
    model = _load_graph_model()

    vertices = {vertex["name"] for vertex in model["vertices"]}
    edges = {edge["name"] for edge in model["edges"]}
    metrics = {metric["name"] for metric in model["metrics"]}
    path_patterns = {pattern["name"] for pattern in model["path_patterns"]}

    assert vertices == EXPECTED_VERTICES
    assert edges == EXPECTED_EDGES
    assert metrics == {"device_count", "port_count", "service_count"}
    assert "tunnel_full_path" in path_patterns


def test_graph_model_properties_match_network_topology_vocabulary_exactly() -> None:
    properties_by_owner = _properties_by_owner(_load_graph_model())

    assert properties_by_owner == EXPECTED_PROPERTIES_BY_OWNER


def test_value_synonyms_keys_exist_in_valid_values() -> None:
    for owner_name, prop in _iter_properties(_load_graph_model()):
        value_synonyms = prop.get("value_synonyms", {})
        if not value_synonyms:
            continue

        valid_values = set(prop.get("valid_values", []))
        assert valid_values, f"{owner_name}.{prop['name']} has value_synonyms without valid_values"
        assert set(value_synonyms) <= valid_values


def test_service_to_tunnel_edge_uses_canonical_name_only() -> None:
    edges = _load_graph_model()["edges"]
    edge_names = {edge["name"] for edge in edges}

    assert "USES_TUNNEL" not in edge_names
    assert "SERVICE_USES_TUNNEL" in edge_names

    for edge in edges:
        if edge["from"] == "Service" and edge["to"] == "Tunnel":
            assert edge["name"] == "SERVICE_USES_TUNNEL"


def test_edges_and_properties_reference_declared_owners() -> None:
    model = _load_graph_model()
    vertices = {vertex["name"] for vertex in model["vertices"]}
    edges = {edge["name"] for edge in model["edges"]}
    property_owners = vertices | edges

    for edge in model["edges"]:
        assert edge["from"] in vertices
        assert edge["to"] in vertices

    for owner_name, prop in _iter_properties(model):
        assert owner_name in property_owners
        assert prop["name"]
        assert prop["type"] in {"string", "int", "float", "boolean", "datetime"} or re.fullmatch(
            r"list<[^>]+>", prop["type"]
        )


def test_metric_dimensions_and_measures_reference_existing_properties() -> None:
    model = _load_graph_model()
    properties_by_owner = _properties_by_owner(model)
    edges = {edge["name"] for edge in model["edges"]}

    for metric in model["metrics"]:
        aliases = _extract_aliases(metric.get("pattern", ""))
        assert aliases, f"{metric['name']} must bind at least one alias in pattern"
        assert _extract_relationship_types(metric.get("pattern", "")) <= edges

        for dimension in metric.get("valid_dimensions", []):
            alias, property_name = dimension.split(".", 1)
            assert alias in aliases, f"{metric['name']} dimension {dimension} uses an unknown alias"
            owner = aliases[alias]
            assert property_name in properties_by_owner[owner], (
                f"{metric['name']} dimension {dimension} references missing property "
                f"{owner}.{property_name}"
            )

        for alias in _extract_count_aliases(metric["expression"]):
            assert alias in aliases, f"{metric['name']} expression references unknown alias {alias}"


def test_tunnel_full_path_pattern_uses_only_path_through_and_returns_device_and_hop() -> None:
    pattern = _path_pattern(_load_graph_model(), "tunnel_full_path")
    cypher = pattern["cypher"]
    parameters = {parameter["name"] for parameter in pattern["parameters"]}

    relationship_types = set(re.findall(r"\[[^\]]*:([A-Z][A-Z0-9_]*)[^\]]*\]", cypher))
    assert relationship_types == {"PATH_THROUGH"}
    assert parameters == {"tunnel_id"}
    assert set(re.findall(r"\$(\w+)", cypher)) == parameters
    assert "RETURN ne AS device, p.hop_order AS hop" in cypher
    assert "ORDER BY p.hop_order ASC" in cypher


def test_golden_questions_have_required_contract_fields_and_coverage() -> None:
    golden_questions = _load_yaml(GOLDEN_QUESTIONS_PATH)["golden_questions"]

    assert len(golden_questions) >= 26
    assert _unique([case["id"] for case in golden_questions])
    assert _unique([case["question"] for case in golden_questions])

    coverage = {case["coverage"] for case in golden_questions}
    assert {
        "single_hop",
        "path_pattern",
        "literal",
        "coverage_failure",
        "aggregate",
            "projection_slot",
            "unsupported_query",
            "single_shot_concede",
            "readonly_violation",
            "shape_mismatch",
    } <= coverage

    for case in golden_questions:
        assert set(case) <= REQUIRED_GOLDEN_QUESTION_KEYS | OPTIONAL_GOLDEN_QUESTION_KEYS
        assert REQUIRED_GOLDEN_QUESTION_KEYS <= set(case)
        assert case["id"]
        assert case["question"]
        assert case["expected_status"] in EXPECTED_STATUSES
        assert case["primary_ir"]
        assert case["expected_reason_code"]
        assert case["query_shape"] in SUPPORTED_OR_SENTINEL_QUERY_SHAPES
        assert isinstance(case["ci_smoke"], bool)
        regression_scope = case.get("regression_scope")
        if regression_scope is not None:
            assert regression_scope in REGRESSION_SCOPES
            assert case["ci_smoke"] is (regression_scope == "smoke")

        if case["expected_status"] == "generated":
            assert case.get("expected_dsl_ir") or case.get("cypher_filled_by_ir")
            if regression_scope is not None:
                assert case.get("expected_dsl_fixture")
                assert case.get("expected_cypher_fixture")

        if case["expected_status"] in NON_SUCCESS_STATUSES:
            assert case["reason"] in NON_SUCCESS_REASON_GROUPS

        if case["expected_status"] == "generation_failed":
            assert case["expected_reason_code"] in GENERATION_FAILURE_REASONS


def test_questions_and_golden_questions_are_aligned() -> None:
    questions_document = _load_yaml(QUESTIONS_PATH)
    golden_document = _load_yaml(GOLDEN_QUESTIONS_PATH)
    questions = questions_document["questions"]
    golden_questions = golden_document["golden_questions"]

    assert questions_document["question_set_version"] == golden_document["golden_set_version"]
    assert _unique([case["id"] for case in questions])
    assert _unique([case["question"] for case in questions])
    questions_by_id = {case["id"]: case for case in questions}
    golden_questions_by_id = {case["id"]: case for case in golden_questions}

    assert set(questions_by_id) == set(golden_questions_by_id)
    for question_id, case in questions_by_id.items():
        golden_case = golden_questions_by_id[question_id]
        assert case["question"] == golden_case["question"]
        assert case["coverage"] == golden_case["coverage"]


def test_value_index_is_static_and_does_not_contain_unknown_future_id() -> None:
    value_index = _load_json(VALUE_INDEX_PATH)
    properties_by_owner = _properties_by_owner(_load_graph_model())

    assert value_index["source"] == "static_fixture"
    assert value_index["live_lookup"] is False
    assert set(value_index["values"]) == EXPECTED_VERTICES
    for owner, property_index in value_index["values"].items():
        assert set(property_index) <= properties_by_owner[owner]
        for property_name, values in property_index.items():
            assert values
            for value_payload in values.values():
                assert set(value_payload) <= properties_by_owner[owner] - {property_name}

    assert "ne-0001" in value_index["values"]["NetworkElement"]["id"]
    assert "tun-mpls-001" in value_index["values"]["Tunnel"]["id"]
    assert "svc-gold-001" in value_index["values"]["Service"]["id"]
    assert "ne-9999" not in value_index["values"]["NetworkElement"]["id"]


def _load_yaml(path: Path) -> Any:
    with path.open(encoding="utf-8") as file:
        return yaml.safe_load(file)


def _load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as file:
        return json.load(file)


def _load_graph_model() -> dict[str, Any]:
    document = _load_yaml(GRAPH_MODEL_PATH)
    semantic_models = document["semantic_model"]
    assert len(semantic_models) == 1
    return semantic_models[0]


def _iter_properties(model: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    properties: list[tuple[str, dict[str, Any]]] = []
    for vertex in model["vertices"]:
        properties.extend((vertex["name"], prop) for prop in vertex.get("properties", []))
    for edge in model["edges"]:
        properties.extend((edge["name"], prop) for prop in edge.get("properties", []))
    return properties


def _properties_by_owner(model: dict[str, Any]) -> dict[str, set[str]]:
    return {
        owner_name: {prop["name"] for prop in props}
        for owner_name, props in _group_properties_by_owner(model).items()
    }


def _group_properties_by_owner(model: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for owner_name, prop in _iter_properties(model):
        grouped.setdefault(owner_name, []).append(prop)
    return grouped


def _extract_aliases(pattern: str) -> dict[str, str]:
    return {
        alias: label
        for alias, label in re.findall(r"\((\w+):([A-Z][A-Za-z0-9]*)\)", pattern)
    }


def _extract_count_aliases(expression: str) -> set[str]:
    return set(re.findall(r"count\((\w+)\)", expression))


def _extract_relationship_types(pattern: str) -> set[str]:
    return set(re.findall(r"\[[^\]]*:([A-Z][A-Z0-9_]*)[^\]]*\]", pattern))


def _path_pattern(model: dict[str, Any], name: str) -> dict[str, Any]:
    return next(pattern for pattern in model["path_patterns"] if pattern["name"] == name)


def _unique(values: list[str]) -> bool:
    return len(values) == len(set(values))
