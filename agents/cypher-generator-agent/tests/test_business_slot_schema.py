from __future__ import annotations

from services.cypher_generator_agent.app.business_slot_schema import (
    BusinessSlotFiller,
    BusinessSlotFrame,
    BusinessSlotSchema,
    BusinessSlotSchemaRegistry,
    BusinessSlotValue,
    get_default_business_slot_schema_registry,
)
from services.cypher_generator_agent.app.intent_recognition import IntentRecognitionResult
from services.cypher_generator_agent.app.semantic_pipeline import SemanticPipeline
from services.cypher_generator_agent.app.slot_matching import SlotMatcher


def test_business_slot_schema_selects_schema_from_intent() -> None:
    registry = get_default_business_slot_schema_registry()
    intent = IntentRecognitionResult(
        primary_intent="record_retrieval_query",
        secondary_intent="related_record_query",
        confidence=0.93,
        source="rule",
        decision="accept",
    )

    schema = registry.select(intent)

    assert schema.schema_id == "graph_inventory.related_record"
    assert schema.scenario_id == "ops_inventory_static"
    assert [slot.name for slot in schema.required_slots] == ["query_object", "relationship_scope"]


def test_business_slot_filler_maps_low_level_slots_to_business_frame() -> None:
    registry = get_default_business_slot_schema_registry()
    intent = IntentRecognitionResult(
        primary_intent="record_retrieval_query",
        secondary_intent="related_record_query",
        confidence=0.93,
        source="rule",
        decision="accept",
    )
    schema = registry.select(intent)
    low_level_slots = SlotMatcher.from_default_config().match("查询 Gold 服务使用的隧道名称和时延")

    frame = BusinessSlotFiller().fill(schema=schema, intent=intent, low_level_slots=low_level_slots)

    assert frame.schema_id == "graph_inventory.related_record"
    assert frame.values_for("query_object") == ("service", "tunnel")
    assert frame.values_for("relationship_scope") == ("service_uses_tunnel",)
    assert frame.values_for("attribute_set") == ("name", "latency")


def test_business_slot_completeness_rejects_numeric_metric_without_metric_family() -> None:
    registry = get_default_business_slot_schema_registry()
    intent = IntentRecognitionResult(
        primary_intent="metric_query",
        secondary_intent="numeric_metric_query",
        confidence=0.93,
        source="rule",
        decision="accept",
    )
    schema = registry.select(intent)
    low_level_slots = SlotMatcher.from_default_config().match("查询隧道")
    frame = BusinessSlotFiller().fill(schema=schema, intent=intent, low_level_slots=low_level_slots)

    result = registry.validate(schema=schema, frame=frame)

    assert result.accepted is False
    assert [slot.name for slot in result.missing_slots] == ["metric_family"]
    assert result.clarification_questions == ("请指定要计算的指标，例如数量、平均时延、最大带宽。",)


def test_business_slot_required_when_depends_on_another_slot_value() -> None:
    schema = BusinessSlotSchema.from_mapping(
        {
            "schema_id": "graph_metric.trend",
            "scenario_id": "graph_time_metrics",
            "primary_intent": "trend_query",
            "secondary_intents": ["metric_trend_query"],
            "description": "Metric trend query.",
            "slots": [
                {
                    "name": "query_action",
                    "description": "Query action.",
                    "required": True,
                    "min_count": 1,
                    "depend_slots": [],
                    "priority": 100,
                    "follow_up_question": "需要什么查询动作？",
                },
                {
                    "name": "time_range",
                    "description": "Time range.",
                    "required": False,
                    "required_when": [{"slot": "query_action", "values": ["trend"]}],
                    "min_count": 1,
                    "depend_slots": ["query_action"],
                    "priority": 90,
                    "follow_up_question": "请明确趋势查询的时间范围。",
                },
            ],
        }
    )
    registry = BusinessSlotSchemaRegistry([schema])
    frame = BusinessSlotFrame(
        schema_id=schema.schema_id,
        scenario_id=schema.scenario_id,
        slots=(BusinessSlotValue(name="query_action", values=("trend",), source="intent"),),
    )

    result = registry.validate(schema=schema, frame=frame)

    assert result.accepted is False
    assert [slot.name for slot in result.missing_slots] == ["time_range"]
    assert result.clarification_questions == ("请明确趋势查询的时间范围。",)


def test_semantic_pipeline_uses_business_slot_schema_before_linking() -> None:
    pipeline = SemanticPipeline()
    intent = IntentRecognitionResult(
        primary_intent="metric_query",
        secondary_intent="numeric_metric_query",
        confidence=0.93,
        source="rule",
        decision="accept",
    )

    result = pipeline.parse(question="查询隧道", intent_result=intent)

    assert result.business_slots.schema_id == "graph_metric.scalar"
    assert result.slot_completeness.accepted is False
    assert result.slot_completeness.missing_slots[0].name == "metric_family"
    assert result.validation.accepted is False
    assert result.validation.diagnostics[0].code == "missing_required_business_slot"
    assert result.linked_semantics is None
    assert result.generated_cypher is None
