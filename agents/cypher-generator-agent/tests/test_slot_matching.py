from __future__ import annotations

from services.cypher_generator_agent.app.slot_matching import SlotMatcher


def test_slot_matcher_extracts_related_record_slots() -> None:
    slots = SlotMatcher.from_default_config().match("查询 Gold 服务使用的隧道名称和时延")

    assert [(slot.text, slot.candidate) for slot in slots.entities] == [
        ("服务", "service"),
        ("隧道", "tunnel"),
    ]
    assert [(slot.text, slot.candidate) for slot in slots.relationships] == [
        ("使用", "service_uses_tunnel")
    ]
    assert [(slot.text, slot.candidate) for slot in slots.return_fields] == [
        ("名称", "name"),
        ("时延", "latency"),
    ]
    assert [(slot.text, slot.entity, slot.property, slot.operator, slot.value) for slot in slots.filters] == [
        ("Gold 服务", "service", "quality_of_service", "=", "Gold")
    ]
    assert all(slot.confidence >= 0.9 for slot in [*slots.entities, *slots.relationships, *slots.return_fields])


def test_slot_matcher_extracts_status_filter_with_owner() -> None:
    slots = SlotMatcher.from_default_config().match("查询状态为 down 的端口信息")

    assert [(slot.text, slot.candidate) for slot in slots.entities] == [("端口", "port")]
    assert [(slot.text, slot.entity, slot.property, slot.operator, slot.value) for slot in slots.filters] == [
        ("状态为 down", "port", "status", "=", "down")
    ]


def test_slot_matcher_extracts_metric_group_order_and_limit() -> None:
    matcher = SlotMatcher.from_default_config()

    metric_slots = matcher.match("查询隧道平均时延")
    assert [(slot.text, slot.metric, slot.entity, slot.property, slot.aggregation) for slot in metric_slots.metrics] == [
        ("平均时延", "avg_tunnel_latency", "tunnel", "latency", "avg")
    ]

    group_slots = matcher.match("按厂商统计设备数量")
    assert [(slot.text, slot.candidate) for slot in group_slots.entities] == [("设备", "network_element")]
    assert [(slot.text, slot.metric, slot.entity, slot.aggregation) for slot in group_slots.metrics] == [
        ("数量", "count_network_element", "network_element", "count")
    ]
    assert [(slot.text, slot.entity, slot.property) for slot in group_slots.group_by] == [
        ("按厂商", "network_element", "vendor")
    ]

    ranking_slots = matcher.match("查询时延最高的前 5 个隧道")
    assert [(slot.text, slot.candidate) for slot in ranking_slots.entities] == [("隧道", "tunnel")]
    assert [(slot.text, slot.entity, slot.property, slot.direction) for slot in ranking_slots.order_by] == [
        ("时延最高", "tunnel", "latency", "desc")
    ]
    assert (ranking_slots.limit.text, ranking_slots.limit.value) == ("前 5 个", 5)


def test_slot_matcher_maps_type_word_to_tugraph_elem_type_property() -> None:
    slots = SlotMatcher.from_default_config().match("按类型统计隧道数量")

    assert [(slot.text, slot.entity, slot.candidate) for slot in slots.group_by] == [
        ("按类型", "tunnel", "elem_type")
    ]
