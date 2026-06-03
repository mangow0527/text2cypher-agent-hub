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


def test_id_shape_resolves_only_by_value_index_exact(
    resolver: LiteralResolver,
) -> None:
    result = resolver.resolve(
        LiteralResolverRequest(
            raw_literal="ne-0001",
            expected_vertex="NetworkElement",
            expected_property="id",
            literal_kind_hint="id",
        )
    )

    assert result.resolved is True
    assert result.resolved_value == "ne-0001"
    assert result.normalized_value == "ne-0001"
    assert result.match_type == "value_index_exact"
    assert result.confidence == 1.0
    assert result.requires_user_choice is False
    assert result.value_index_miss is False
    assert result.alternatives == []
    assert result.evidence[0].source == "static_value_index"
    assert result.evidence[0].matched == "ne-0001"
    assert result.evidence[0].target == "ne-0001"


def test_missing_id_shape_passes_through_without_near_id_replacement(
    resolver: LiteralResolver,
) -> None:
    result = resolver.resolve(
        LiteralResolverRequest(
            raw_literal="ne-9999",
            expected_vertex="NetworkElement",
            expected_property="id",
            literal_kind_hint="id",
        )
    )

    assert result.resolved is True
    assert result.resolved_value == "ne-9999"
    assert result.normalized_value == "ne-9999"
    assert result.match_type == "literal_passthrough"
    assert result.confidence == 0.9
    assert result.requires_user_choice is False
    assert result.value_index_miss is True
    assert result.error_code is None
    assert result.alternatives == []


def test_owner_id_property_passes_through_index_miss_without_near_id_alternatives(
    resolver: LiteralResolver,
) -> None:
    result = resolver.resolve(
        LiteralResolverRequest(
            raw_literal="ne0001",
            expected_vertex="NetworkElement",
            expected_property="id",
            literal_kind_hint=None,
        )
    )

    assert result.resolved is True
    assert result.resolved_value == "ne0001"
    assert result.match_type == "literal_passthrough"
    assert result.value_index_miss is True
    assert result.error_code is None
    assert result.alternatives == []


def test_unknown_name_value_passes_through_when_property_is_unambiguous(
) -> None:
    registry = load_graph_semantic_model(
        {
            "name": "literal_name_passthrough",
            "vertices": [
                {
                    "name": "Service",
                    "id_property": "id",
                    "properties": [
                        {"name": "id", "type": "string", "required": True},
                        {"name": "name", "type": "string", "required": True},
                    ],
                }
            ],
        }
    ).registry
    resolver = LiteralResolver(registry=registry, value_index=StaticValueIndex.empty())

    result = resolver.resolve(
        LiteralResolverRequest(
            raw_literal="Service_001",
            expected_vertex="Service",
            expected_property="name",
            literal_kind_hint="name",
        )
    )

    assert result.resolved is True
    assert result.resolved_value == "Service_001"
    assert result.normalized_value == "Service_001"
    assert result.match_type == "literal_passthrough"
    assert result.requires_user_choice is False
    assert result.value_index_miss is False
    assert result.error_code is None
    assert result.alternatives == []


def test_non_id_name_follows_fuzzy_stage_before_value_index_lookup() -> None:
    registry = load_graph_semantic_model(
        {
            "name": "literal_name_index",
            "vertices": [
                {
                    "name": "Device",
                    "id_property": "id",
                    "properties": [
                        {"name": "id", "type": "string", "required": True},
                        {"name": "name", "type": "string"},
                    ],
                }
            ],
        }
    ).registry
    value_index = StaticValueIndex.from_mapping(
        {
            "schema_version": "static_value_index_v1",
            "live_lookup": False,
            "values": {"Device": {"name": {"Alpha Device": {}}}},
        }
    )
    resolver = LiteralResolver(registry=registry, value_index=value_index)

    result = resolver.resolve(
        LiteralResolverRequest(
            raw_literal="Alpha Device",
            expected_vertex="Device",
            expected_property="name",
            literal_kind_hint="name",
        )
    )

    assert result.resolved is True
    assert result.resolved_value == "Alpha Device"
    assert result.match_type == "fuzzy_text"


def test_embedding_lookup_is_explicitly_disabled_in_v1(
    resolver: LiteralResolver,
) -> None:
    del resolver

    with pytest.raises(ValueError, match="embedding lookup disabled"):
        LiteralResolver(
            registry=load_graph_semantic_model(MODEL_PATH).registry,
            value_index=StaticValueIndex.from_path(VALUE_INDEX_PATH),
            embedding_enabled=True,
        )


def test_static_value_index_rejects_live_lookup_payload() -> None:
    with pytest.raises(ValueError, match="only accepts static value indexes"):
        StaticValueIndex.from_mapping(
            {
                "schema_version": "static_value_index_v1",
                "live_lookup": True,
                "values": {},
            }
        )
