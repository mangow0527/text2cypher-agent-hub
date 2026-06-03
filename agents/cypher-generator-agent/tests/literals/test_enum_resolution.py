from __future__ import annotations

from pathlib import Path

import pytest

from services.cypher_generator_agent.app.literals.models import LiteralResolverRequest
from services.cypher_generator_agent.app.literals.resolver import LiteralResolver
from services.cypher_generator_agent.app.literals.value_index import StaticValueIndex
from services.cypher_generator_agent.app.semantic_model import load_graph_semantic_model


FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures"
MODEL_PATH = FIXTURE_DIR / "network_topology_graph_model.yaml"
VALUE_INDEX_PATH = FIXTURE_DIR / "value_index.json"


@pytest.fixture
def resolver() -> LiteralResolver:
    registry = load_graph_semantic_model(MODEL_PATH).registry
    return LiteralResolver(
        registry=registry,
        value_index=StaticValueIndex.from_path(VALUE_INDEX_PATH),
    )


def test_value_synonym_resolves_firewall_enum(
    resolver: LiteralResolver,
) -> None:
    result = resolver.resolve(
        LiteralResolverRequest(
            raw_literal="防火墙",
            expected_vertex="NetworkElement",
            expected_property="elem_type",
            literal_kind_hint="enum_or_name",
            question_context="全网有多少台防火墙",
            trace_id="q-20260527-001",
        )
    )

    assert result.resolved is True
    assert result.resolved_value == "firewall"
    assert result.normalized_value == "firewall"
    assert result.match_type == "value_synonym"
    assert result.confidence == pytest.approx(0.98)
    assert result.requires_user_choice is False
    assert result.value_index_miss is False
    assert result.alternatives == []
    assert result.evidence[0].source == "property.value_synonyms"
    assert result.evidence[0].matched == "防火墙"
    assert result.evidence[0].target == "firewall"


def test_hyphenated_enum_is_not_forced_through_id_value_index() -> None:
    registry = load_graph_semantic_model(
        {
            "name": "literal_hyphenated_enum",
            "vertices": [
                {
                    "name": "Tunnel",
                    "id_property": "id",
                    "properties": [
                        {"name": "id", "type": "string", "required": True},
                        {
                            "name": "technology",
                            "type": "string",
                            "valid_values": ["MPLS-TE", "SR-TE"],
                        },
                    ],
                }
            ],
        }
    ).registry
    resolver = LiteralResolver(registry=registry, value_index=StaticValueIndex.empty())

    result = resolver.resolve(
        LiteralResolverRequest(
            raw_literal="MPLS-TE",
            expected_vertex="Tunnel",
            expected_property="technology",
            literal_kind_hint="enum",
        )
    )

    assert result.resolved is True
    assert result.resolved_value == "MPLS-TE"
    assert result.match_type == "exact"
    assert result.value_index_miss is False


def test_ascii_enum_with_chinese_qualifier_resolves_single_contained_value(
    resolver: LiteralResolver,
) -> None:
    result = resolver.resolve(
        LiteralResolverRequest(
            raw_literal="Gold级别",
            expected_vertex="Service",
            expected_property="quality_of_service",
            literal_kind_hint="enum",
        )
    )

    assert result.resolved is True
    assert result.resolved_value == "GOLD"
    assert result.normalized_value == "GOLD"
    assert result.match_type == "exact"
    assert result.confidence == pytest.approx(0.99)
    assert result.requires_user_choice is False


def test_ascii_state_with_chinese_qualifier_resolves_single_contained_value(
    resolver: LiteralResolver,
) -> None:
    result = resolver.resolve(
        LiteralResolverRequest(
            raw_literal="down状态",
            expected_vertex="Port",
            expected_property="status",
            literal_kind_hint="enum",
        )
    )

    assert result.resolved is True
    assert result.resolved_value == "down"
    assert result.normalized_value == "down"
    assert result.match_type == "exact"
    assert result.confidence == pytest.approx(0.99)
    assert result.requires_user_choice is False


def test_hyphenated_enum_still_resolves_when_kind_hint_is_mislabeled_as_id() -> None:
    registry = load_graph_semantic_model(
        {
            "name": "literal_hyphenated_enum_mislabeled",
            "vertices": [
                {
                    "name": "Tunnel",
                    "id_property": "id",
                    "properties": [
                        {"name": "id", "type": "string", "required": True},
                        {
                            "name": "technology",
                            "type": "string",
                            "valid_values": ["MPLS-TE", "SR-TE"],
                        },
                    ],
                }
            ],
        }
    ).registry
    resolver = LiteralResolver(registry=registry, value_index=StaticValueIndex.empty())

    result = resolver.resolve(
        LiteralResolverRequest(
            raw_literal="MPLS-TE",
            expected_vertex="Tunnel",
            expected_property="technology",
            literal_kind_hint="id",
        )
    )

    assert result.resolved is True
    assert result.resolved_value == "MPLS-TE"
    assert result.match_type == "exact"
    assert result.value_index_miss is False


def test_high_risk_enum_fuzzy_match_only_returns_alternatives(
    resolver: LiteralResolver,
) -> None:
    result = resolver.resolve(
        LiteralResolverRequest(
            raw_literal="防火墙设备",
            expected_vertex="NetworkElement",
            expected_property="elem_type",
            literal_kind_hint="enum_or_name",
        )
    )

    assert result.resolved is False
    assert result.resolved_value is None
    assert result.normalized_value is None
    assert result.match_type == "unresolved"
    assert result.confidence == 0.0
    assert result.requires_user_choice is True
    assert result.value_index_miss is True
    assert len(result.alternatives) <= 3
    assert result.alternatives[0].value == "firewall"
    assert result.alternatives[0].display == "防火墙"
    assert result.alternatives[0].confidence >= 0.80


def test_indexed_enum_without_alternatives_reports_value_index_miss(
    resolver: LiteralResolver,
) -> None:
    result = resolver.resolve(
        LiteralResolverRequest(
            raw_literal="platinum",
            expected_vertex="Service",
            expected_property="quality_of_service",
            literal_kind_hint="enum_or_name",
        )
    )

    assert result.resolved is False
    assert result.error_code == "literal_value_index_miss"
    assert result.value_index_miss is True
    assert result.alternatives == []


def test_enum_alternatives_are_capped_at_three() -> None:
    registry = load_graph_semantic_model(
        {
            "name": "literal_alt_cap",
            "vertices": [
                {
                    "name": "Thing",
                    "id_property": "id",
                    "properties": [
                        {"name": "id", "type": "string", "required": True},
                        {
                            "name": "category",
                            "type": "string",
                            "valid_values": [
                                "alpha",
                                "alphabet",
                                "alphanumeric",
                                "alpine",
                                "alpaca",
                            ],
                        },
                    ],
                }
            ],
        }
    ).registry
    resolver = LiteralResolver(registry=registry, value_index=StaticValueIndex.empty())

    result = resolver.resolve(
        LiteralResolverRequest(
            raw_literal="alph",
            expected_vertex="Thing",
            expected_property="category",
            literal_kind_hint="enum_or_name",
        )
    )

    assert result.resolved is False
    assert result.requires_user_choice is True
    assert len(result.alternatives) == 3


def test_unknown_property_returns_structured_property_mismatch() -> None:
    registry = load_graph_semantic_model(MODEL_PATH).registry
    resolver = LiteralResolver(registry=registry, value_index=StaticValueIndex.from_path(VALUE_INDEX_PATH))

    result = resolver.resolve(
        LiteralResolverRequest(
            raw_literal="防火墙",
            expected_vertex="NetworkElement",
            expected_property="missing_property",
            literal_kind_hint="enum_or_name",
        )
    )

    assert result.resolved is False
    assert result.error_code == "literal_property_mismatch"
    assert result.evidence[0].source == "semantic_registry"
