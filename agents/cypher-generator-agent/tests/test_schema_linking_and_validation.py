from __future__ import annotations

from services.cypher_generator_agent.app.schema_linking import SchemaLinker
from services.cypher_generator_agent.app.semantic_layer import get_default_semantic_layer
from services.cypher_generator_agent.app.semantic_validation import SemanticValidator
from services.cypher_generator_agent.app.slot_matching import SlotMatcher


def test_linker_binds_service_tunnel_question_to_graph_schema() -> None:
    semantic_layer = get_default_semantic_layer()
    slots = SlotMatcher.from_default_config().match("查询 Gold 服务使用的隧道名称和时延")

    linked = SchemaLinker(semantic_layer).link(slots)

    assert [entity.semantic_name for entity in linked.entities] == ["service", "tunnel"]
    assert [relationship.semantic_name for relationship in linked.relationships] == ["service_uses_tunnel"]
    assert [(field.owner, field.property) for field in linked.return_fields] == [
        ("tunnel", "name"),
        ("tunnel", "latency"),
    ]
    assert [(predicate.owner, predicate.property, predicate.operator, predicate.value) for predicate in linked.filters] == [
        ("service", "quality_of_service", "=", "Gold")
    ]


def test_validator_accepts_linked_service_to_tunnel_relationship() -> None:
    semantic_layer = get_default_semantic_layer()
    slots = SlotMatcher.from_default_config().match("查询 Gold 服务使用的隧道名称和时延")
    linked = SchemaLinker(semantic_layer).link(slots)

    result = SemanticValidator(semantic_layer).validate(linked)

    assert result.accepted is True
    assert result.diagnostics == []


def test_validator_rejects_unreachable_relationship() -> None:
    semantic_layer = get_default_semantic_layer()
    slots = SlotMatcher.from_default_config().match("查询服务连接的协议")
    linked = SchemaLinker(semantic_layer).link(slots)

    result = SemanticValidator(semantic_layer).validate(linked)

    assert result.accepted is False
    assert any("relationship" in diagnostic.code for diagnostic in result.diagnostics)
