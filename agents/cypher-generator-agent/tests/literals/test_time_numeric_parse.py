from __future__ import annotations

from pathlib import Path

import pytest

from services.cypher_generator_agent.app.literals.models import LiteralResolverRequest
from services.cypher_generator_agent.app.literals.resolver import LiteralResolver
from services.cypher_generator_agent.app.literals.value_index import StaticValueIndex
from services.cypher_generator_agent.app.semantic_model import load_graph_semantic_model


FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures"
MODEL_PATH = FIXTURE_DIR / "network_topology_graph_model.yaml"


def test_recent_seven_days_parses_as_typed_time_range() -> None:
    registry = load_graph_semantic_model(
        {
            "name": "literal_time_parse",
            "vertices": [
                {
                    "name": "Observation",
                    "id_property": "id",
                    "properties": [
                        {"name": "id", "type": "string", "required": True},
                        {"name": "observed_at", "type": "datetime"},
                    ],
                }
            ],
        }
    ).registry
    resolver = LiteralResolver(registry=registry, value_index=StaticValueIndex.empty())

    result = resolver.resolve(
        LiteralResolverRequest(
            raw_literal="最近 7 天",
            expected_vertex="Observation",
            expected_property="observed_at",
            literal_kind_hint="time",
        )
    )

    assert result.resolved is True
    assert result.match_type == "typed_parse"
    assert result.requires_user_choice is False
    assert result.normalized_value == {
        "type": "relative_time_range",
        "direction": "last",
        "amount": 7,
        "unit": "day",
    }
    assert result.evidence[0].source == "typed_parser.relative_time_range"


def test_recent_seven_days_without_spaces_parses_as_typed_time_range() -> None:
    registry = load_graph_semantic_model(
        {
            "name": "literal_time_parse_compact",
            "vertices": [
                {
                    "name": "Observation",
                    "id_property": "id",
                    "properties": [
                        {"name": "id", "type": "string", "required": True},
                        {"name": "observed_at", "type": "datetime"},
                    ],
                }
            ],
        }
    ).registry
    resolver = LiteralResolver(registry=registry, value_index=StaticValueIndex.empty())

    result = resolver.resolve(
        LiteralResolverRequest(
            raw_literal="最近7天",
            expected_vertex="Observation",
            expected_property="observed_at",
            literal_kind_hint="time",
        )
    )

    assert result.resolved is True
    assert result.match_type == "typed_parse"
    assert result.normalized_value["amount"] == 7
    assert result.normalized_value["unit"] == "day"


def test_bandwidth_capacity_literal_parses_to_mbps_float() -> None:
    registry = load_graph_semantic_model(MODEL_PATH).registry
    resolver = LiteralResolver(registry=registry, value_index=StaticValueIndex.empty())

    result = resolver.resolve(
        LiteralResolverRequest(
            raw_literal="100G",
            expected_vertex="Tunnel",
            expected_property="bandwidth",
            literal_kind_hint="numeric",
        )
    )

    assert result.resolved is True
    assert result.match_type == "typed_parse"
    assert result.normalized_value == pytest.approx(100000.0)
    assert result.evidence[0].source == "typed_parser.numeric"
