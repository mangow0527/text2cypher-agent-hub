from __future__ import annotations

import pytest

from services.cypher_generator_agent.app.intent_recognition import IntentRecognitionResult
from services.cypher_generator_agent.app.knowledge_selection import SelectedKnowledgeContext
from services.cypher_generator_agent.app.semantic_pipeline import SemanticPipeline, get_semantic_pipeline


def test_semantic_pipeline_generates_cypher_for_related_record_query() -> None:
    pipeline = get_semantic_pipeline()
    intent = IntentRecognitionResult(
        primary_intent="record_retrieval_query",
        secondary_intent="related_record_query",
        confidence=0.93,
        source="rule",
        decision="accept",
    )

    result = pipeline.parse(
        id="qa-001",
        question="查询 Gold 服务使用的隧道名称和时延",
        generation_run_id="cypher-run-001",
        intent_result=intent,
    )

    assert result.id == "qa-001"
    assert result.question == "查询 Gold 服务使用的隧道名称和时延"
    assert result.generation_run_id == "cypher-run-001"
    assert result.to_dict()["id"] == "qa-001"
    assert result.to_dict()["generation_run_id"] == "cypher-run-001"
    assert result.validation.accepted is True
    assert result.semantic_query is not None
    assert result.semantic_query.kind == "record_selection"
    assert result.generated_cypher == (
        "MATCH (s:Service)-[:SERVICE_USES_TUNNEL]->(t:Tunnel)\n"
        "WHERE s.quality_of_service = 'Gold'\n"
        "RETURN t.name AS tunnel_name, t.latency AS tunnel_latency"
    )
    assert result.preflight.accepted is True


def test_semantic_pipeline_trace_exposes_semantic_view_and_logical_plan_layers() -> None:
    pipeline = get_semantic_pipeline()
    intent = IntentRecognitionResult(
        primary_intent="record_retrieval_query",
        secondary_intent="related_record_query",
        confidence=0.93,
        source="rule",
        decision="accept",
    )

    result = pipeline.parse(
        id="qa-trace",
        question="查询金牌服务使用的隧道名称",
        generation_run_id="run-trace",
        intent_result=intent,
    )

    payload = result.to_dict()
    assert payload["schema_version"] == "cga_trace_v2"
    assert payload["semantic_view_matching"]["result"]["accepted"] is True
    assert payload["semantic_view_matching"]["result"]["entities"] == ["service", "tunnel"]
    assert payload["semantic_view_matching"]["result"]["filters"] == [
        {
            "field": "service.quality_of_service",
            "operator": "=",
            "value": "Gold",
            "evidence": "金牌",
        }
    ]
    assert payload["semantic_view_matching"]["result"]["paths"] == [
        {
            "path_semantic": "service.uses_tunnel",
            "relationships": ["service_uses_tunnel"],
            "evidence": "使用的隧道",
        }
    ]
    assert payload["semantic_view_matching"]["result"]["returns"] == [
        {"field": "tunnel.name", "evidence": "隧道名称"}
    ]
    assert payload["logical_query_plan"]["answer_shape"] == "records"
    assert [operator["op"] for operator in payload["logical_query_plan"]["operators"]] == [
        "scan",
        "traverse",
        "filter",
        "project",
    ]
    assert payload["schema_path_planning"]["selected_paths"][0]["cypher_pattern"] == (
        "(s:Service)-[:SERVICE_USES_TUNNEL]->(t:Tunnel)"
    )
    assert result.generated_cypher == (
        "MATCH (s:Service)-[:SERVICE_USES_TUNNEL]->(t:Tunnel)\n"
        "WHERE s.quality_of_service = 'Gold'\n"
        "RETURN t.name AS tunnel_name"
    )


def test_semantic_pipeline_handles_entity_detail_and_name_filter_queries() -> None:
    pipeline = get_semantic_pipeline()

    detail = pipeline.parse(
        question="查询所有服务的详细信息。",
        intent_result=IntentRecognitionResult(
            primary_intent="record_retrieval_query",
            secondary_intent="entity_detail_query",
            confidence=0.9,
            source="rule",
            decision="accept",
        ),
    )
    assert detail.generated_cypher == "MATCH (s:Service)\nRETURN s"

    projection = pipeline.parse(
        question="查询名称为 Service_002 的服务的编号、名称和带宽。",
        intent_result=IntentRecognitionResult(
            primary_intent="record_retrieval_query",
            secondary_intent="attribute_projection_query",
            confidence=0.9,
            source="rule",
            decision="accept",
        ),
    )
    assert "WHERE s.name = 'Service_002'" in projection.generated_cypher
    assert "s.id AS service_id" in projection.generated_cypher
    assert "s.name AS service_name" in projection.generated_cypher
    assert "s.bandwidth AS service_bandwidth" in projection.generated_cypher
    assert "Tunnel" not in projection.generated_cypher


def test_semantic_pipeline_handles_common_service_tunnel_multihop_paths() -> None:
    pipeline = get_semantic_pipeline()
    intent = IntentRecognitionResult(
        primary_intent="record_retrieval_query",
        secondary_intent="related_record_query",
        confidence=0.9,
        source="rule",
        decision="accept",
    )

    source_ne = pipeline.parse(
        question="查询所有服务使用的隧道对应的源端网元。",
        intent_result=intent,
    )
    assert "[:TUNNEL_SRC]->(ne:NetworkElement)" in source_ne.generated_cypher

    ports = pipeline.parse(
        question="查询服务使用的隧道所经过网元上的所有端口。",
        intent_result=intent,
    )
    assert "[:PATH_THROUGH]->(ne:NetworkElement)" in ports.generated_cypher
    assert "[:HAS_PORT]->(p:Port)" in ports.generated_cypher

    vendor_breakdown = pipeline.parse(
        question="统计服务所用隧道的目的端网元厂商分布，按数量升序排列，返回前5个厂商。",
        intent_result=intent,
    )
    assert "[:TUNNEL_DST]->(ne:NetworkElement)" in vendor_breakdown.generated_cypher
    assert "ne.vendor AS network_element_vendor" in vendor_breakdown.generated_cypher
    assert "ORDER BY network_element_count ASC" in vendor_breakdown.generated_cypher


def test_semantic_pipeline_handles_two_stage_aggregate_ranking() -> None:
    pipeline = get_semantic_pipeline()
    result = pipeline.parse(
        question="统计各服务关联的目的网元总数，按首次统计值降序排列，返回前5个服务的名称、时延及两次统计结果。",
        intent_result=IntentRecognitionResult(
            primary_intent="ranking_query",
            secondary_intent="attribute_ranking_query",
            confidence=0.9,
            source="rule",
            decision="accept",
        ),
    )

    assert result.generated_cypher == (
        "MATCH (s:Service)-[:SERVICE_USES_TUNNEL]->(t:Tunnel)-[:TUNNEL_DST]->(ne:NetworkElement)\n"
        "WITH s, count(ne) AS first_total\n"
        "MATCH (s)-[:SERVICE_USES_TUNNEL]->(t:Tunnel)-[:TUNNEL_DST]->(ne:NetworkElement)\n"
        "RETURN s.name AS service_name, s.latency AS service_latency, first_total, count(ne) AS total_count\n"
        "ORDER BY first_total DESC\n"
        "LIMIT 5"
    )
    payload = result.to_dict()
    assert payload["semantic_query"]["with_stage"]["output_alias"] == "first_total"
    assert payload["logical_query_plan"]["renderer_hints"]["aggregation_shape"] == "two_stage"


def test_semantic_pipeline_returns_clarification_before_planning_for_ambiguous_view_match() -> None:
    pipeline = get_semantic_pipeline()
    intent = IntentRecognitionResult(
        primary_intent="record_retrieval_query",
        secondary_intent="related_record_query",
        confidence=0.88,
        source="rule",
        decision="accept",
    )

    result = pipeline.parse(
        id="qa-clarify",
        question="查询服务对应的网元",
        generation_run_id="run-clarify",
        intent_result=intent,
    )

    payload = result.to_dict()
    assert result.generation_mode is None
    assert result.validation.accepted is False
    assert result.validation.diagnostics[0].code == "clarification_required"
    assert payload["semantic_view_matching"]["result"]["accepted"] is False
    assert payload["semantic_view_matching"]["result"]["needs_clarification"] is True
    assert payload["logical_query_plan"] is None
    assert payload["schema_path_planning"] is None
    assert payload["clarification"]["source_stage"] == "semantic_view_matching"
    assert "源网元" in payload["clarification"]["question_zh"]


def test_semantic_pipeline_clarifies_generic_service_network_element_path() -> None:
    pipeline = get_semantic_pipeline()
    intent = IntentRecognitionResult(
        primary_intent="record_retrieval_query",
        secondary_intent="related_record_query",
        confidence=0.88,
        source="rule",
        decision="accept",
    )

    result = pipeline.parse(question="查询服务有哪些网元", intent_result=intent)

    payload = result.to_dict()
    assert result.validation.accepted is False
    assert result.generated_cypher is None
    assert payload["semantic_view_matching"]["result"]["needs_clarification"] is True
    assert {item["path_semantic"] for item in payload["semantic_view_matching"]["result"]["paths"]} == {
        "service.tunnel_path",
        "service.tunnel_destination",
        "service.tunnel_source",
    }
    assert payload["clarification"]["source_stage"] == "semantic_view_matching"


def test_semantic_pipeline_does_not_generate_when_intent_is_not_accepted() -> None:
    pipeline = SemanticPipeline()
    intent = IntentRecognitionResult(
        primary_intent=None,
        secondary_intent=None,
        confidence=0.2,
        source="embedding",
        decision="fallback_llm",
    )

    result = pipeline.parse(question="帮我看看这个业务是不是正常", intent_result=intent)

    assert result.validation.accepted is False
    assert result.semantic_query is None
    assert result.generated_cypher is None
    assert result.preflight is None


@pytest.mark.asyncio
async def test_semantic_pipeline_uses_llm_as_third_stage_intent_recognizer() -> None:
    class FakeLLMClient:
        def __init__(self) -> None:
            self.calls = []

        async def generate_from_prompt(self, *, task_id: str, question_text: str, llm_prompt: str):
            raw_output = (
                '{"primary_intent":"record_retrieval_query",'
                '"secondary_intent":"related_record_query",'
                '"confidence":0.86,'
                '"decision":"accept"}'
            )
            self.calls.append(
                {
                    "task_id": task_id,
                    "question_text": question_text,
                    "llm_prompt": llm_prompt,
                    "raw_output": raw_output,
                    }
                )
            return {"raw_output": raw_output}

    llm_client = FakeLLMClient()
    pipeline = SemanticPipeline(llm_client=llm_client)
    intent = IntentRecognitionResult(
        primary_intent=None,
        secondary_intent=None,
        confidence=0.2,
        source="embedding",
        decision="fallback_llm",
    )

    result = await pipeline.parse_with_fallback(
        id="qa-fallback",
        question="查询服务使用的隧道名称",
        generation_run_id="run-fallback",
        intent_result=intent,
    )

    assert len(llm_client.calls) == 1
    assert llm_client.calls[0]["task_id"] == "qa-fallback"
    assert "第三阶段 LLM 意图识别" in llm_client.calls[0]["llm_prompt"]
    assert "只输出 JSON" in llm_client.calls[0]["llm_prompt"]
    assert "SemanticQuerySpec" not in llm_client.calls[0]["llm_prompt"]
    assert result.intent.source == "llm"
    assert result.intent.decision == "accept"
    assert result.generation_mode == "deterministic_renderer"
    assert result.validation.accepted is True
    assert result.semantic_query is not None
    assert result.generated_cypher == (
        "MATCH (s:Service)-[:SERVICE_USES_TUNNEL]->(t:Tunnel)\n"
        "RETURN t.name AS tunnel_name"
    )
    assert result.preflight.accepted is True
    payload = result.to_dict()
    intent_attempts = payload["intent_recognition"]["diagnostics"]["llm_secondary_attempts"]
    assert intent_attempts[0]["prompt"] == llm_client.calls[0]["llm_prompt"]
    assert intent_attempts[0]["raw_output"] == llm_client.calls[0]["raw_output"]
    assert payload["generation"]["cypher_fallback_llm"] is None


@pytest.mark.asyncio
async def test_semantic_pipeline_rejects_cypher_text_from_intent_llm_fallback() -> None:
    class FakeLLMClient:
        async def generate_from_prompt(self, *, task_id: str, question_text: str, llm_prompt: str):
            return {"raw_output": "MATCH (s:Service) RETURN s.name AS service_name"}

    pipeline = SemanticPipeline(llm_client=FakeLLMClient())
    intent = IntentRecognitionResult(
        primary_intent=None,
        secondary_intent=None,
        confidence=0.2,
        source="embedding",
        decision="fallback_llm",
    )

    result = await pipeline.parse_with_fallback(
        id="qa-fallback-cypher-text",
        question="帮我看看这个业务是不是正常",
        generation_run_id="run-fallback-cypher-text",
        intent_result=intent,
    )

    assert result.generation_mode is None
    assert result.validation.accepted is False
    assert result.validation.diagnostics[0].code == "intent_llm_invalid_output"
    assert result.semantic_query is None
    assert result.generated_cypher is None
    assert result.preflight is None
    payload = result.to_dict()
    intent_attempts = payload["intent_recognition"]["diagnostics"]["llm_secondary_attempts"]
    assert intent_attempts[0]["prompt"]
    assert intent_attempts[0]["raw_output"] == "MATCH (s:Service) RETURN s.name AS service_name"


def test_semantic_pipeline_rejects_unmatched_semantic_view_without_slot_fallback() -> None:
    pipeline = SemanticPipeline()
    intent = IntentRecognitionResult(
        primary_intent="record_retrieval_query",
        secondary_intent="attribute_projection_query",
        confidence=0.91,
        source="embedding",
        decision="accept",
    )

    result = pipeline.parse(question="查询火星基地的能耗", intent_result=intent)

    assert result.validation.accepted is False
    assert result.semantic_query is None
    assert result.generated_cypher is None
    assert result.preflight is None
    assert result.validation.diagnostics[0].code == "semantic_match_rejected"
    assert result.semantic_view_matching is not None
    assert result.semantic_view_matching.result.rejection_reason == "no_semantic_view_candidate"
    payload = result.to_dict()
    assert payload["service_context"]["active_mode"] == "semantic_view_pipeline"
    assert "slots" not in payload
    assert "business_slots" not in payload


def test_semantic_pipeline_generates_count_metric_query() -> None:
    pipeline = SemanticPipeline()
    intent = IntentRecognitionResult(
        primary_intent="metric_query",
        secondary_intent="count_metric_query",
        confidence=0.92,
        source="rule",
        decision="accept",
    )

    result = pipeline.parse(question="统计服务数量", intent_result=intent)

    assert result.validation.accepted is True
    assert result.semantic_query is not None
    assert result.semantic_query.kind == "metric_aggregation"
    assert result.generated_cypher == "MATCH (s:Service)\nRETURN count(s) AS service_count"


def test_semantic_pipeline_generates_entity_list_with_default_projection() -> None:
    pipeline = SemanticPipeline()
    intent = IntentRecognitionResult(
        primary_intent="record_retrieval_query",
        secondary_intent="entity_list_query",
        confidence=0.92,
        source="rule",
        decision="accept",
    )

    result = pipeline.parse(question="查询所有服务", intent_result=intent)

    assert result.validation.accepted is True
    assert result.semantic_query is not None
    assert [projection.output_alias for projection in result.semantic_query.projections] == [
        "service_id",
        "service_name",
    ]
    assert result.generated_cypher == "MATCH (s:Service)\nRETURN s.id AS service_id, s.name AS service_name"
    assert result.preflight.accepted is True


def test_semantic_pipeline_runs_semantic_cypher_preflight() -> None:
    class TamperingRenderer:
        def render(self, semantic_query):
            return (
                "MATCH (s:Service)-[:UNDECLARED_EDGE]->(t:Tunnel)\n"
                "WHERE s.quality_of_service = 'Gold'\n"
                "RETURN t.name AS tunnel_name"
            )

    pipeline = SemanticPipeline(renderer=TamperingRenderer())
    intent = IntentRecognitionResult(
        primary_intent="record_retrieval_query",
        secondary_intent="related_record_query",
        confidence=0.93,
        source="rule",
        decision="accept",
    )

    result = pipeline.parse(question="查询 Gold 服务使用的隧道名称", intent_result=intent)

    assert result.preflight.accepted is False
    assert result.preflight.reason == "unauthorized_schema_reference"


@pytest.mark.asyncio
async def test_semantic_pipeline_selects_knowledge_after_semantic_query_build() -> None:
    class FakeKnowledgeSelector:
        def __init__(self) -> None:
            self.calls = []

        async def select(self, *, question, intent_result, semantic_query):
            self.calls.append(
                {
                    "question": question,
                    "intent": intent_result.to_dict(),
                    "semantic_query": semantic_query.to_dict(),
                }
            )
            return SelectedKnowledgeContext(
                fragments=[{"id": "verified_query.0000.service_tunnel_qos_detail", "type": "verified_query"}],
                prompt_context="## verified query\nMATCH (s:Service)-[:SERVICE_USES_TUNNEL]->(t:Tunnel)",
                selection_trace=["selected verified_query.0000.service_tunnel_qos_detail via symbolic match"],
                size_estimate=88,
                missing_knowledge_signals=[],
                source="rag",
            )

    knowledge_selector = FakeKnowledgeSelector()
    pipeline = SemanticPipeline(knowledge_selector=knowledge_selector)
    intent = IntentRecognitionResult(
        primary_intent="record_retrieval_query",
        secondary_intent="related_record_query",
        confidence=0.93,
        source="rule",
        decision="accept",
    )

    result = await pipeline.parse_with_fallback(
        id="qa-001",
        question="查询 Gold 服务使用的隧道名称和时延",
        generation_run_id="cypher-run-001",
        intent_result=intent,
    )

    assert len(knowledge_selector.calls) == 1
    assert knowledge_selector.calls[0]["question"] == "查询 Gold 服务使用的隧道名称和时延"
    assert knowledge_selector.calls[0]["semantic_query"]["kind"] == "record_selection"
    assert result.selected_knowledge is not None
    assert result.selected_knowledge.source == "rag"
    assert result.to_dict()["knowledge_selection"]["fragments"][0]["type"] == "verified_query"


@pytest.mark.asyncio
async def test_semantic_pipeline_falls_back_to_controlled_llm_when_renderer_is_unsupported() -> None:
    class UnsupportedRenderer:
        def render(self, semantic_query):
            raise NotImplementedError("renderer does not support this semantic query")

    class FakeLLMClient:
        def __init__(self) -> None:
            self.prompt = ""

        async def generate_from_prompt(self, *, task_id: str, question_text: str, llm_prompt: str):
            self.prompt = llm_prompt
            return {
                "raw_output": (
                    "MATCH (s:Service)-[:SERVICE_USES_TUNNEL]->(t:Tunnel)\n"
                    "WHERE s.quality_of_service = 'Gold'\n"
                    "RETURN t.name AS tunnel_name"
                )
            }

    llm_client = FakeLLMClient()
    pipeline = SemanticPipeline(renderer=UnsupportedRenderer(), llm_client=llm_client)
    intent = IntentRecognitionResult(
        primary_intent="record_retrieval_query",
        secondary_intent="related_record_query",
        confidence=0.93,
        source="rule",
        decision="accept",
    )

    result = await pipeline.parse_with_fallback(
        id="qa-001",
        question="查询 Gold 服务使用的隧道名称",
        generation_run_id="cypher-run-001",
        intent_result=intent,
    )

    assert result.generation_mode == "controlled_llm_fallback"
    assert result.generated_cypher == (
        "MATCH (s:Service)-[:SERVICE_USES_TUNNEL]->(t:Tunnel)\n"
        "WHERE s.quality_of_service = 'Gold'\n"
        "RETURN t.name AS tunnel_name"
    )
    assert result.preflight.accepted is True
    assert "逻辑查询计划与授权路径" in llm_client.prompt
    assert "SemanticQuerySpec" not in llm_client.prompt
    assert "不要新增逻辑查询计划和授权路径未允许的 label、edge、property" in llm_client.prompt
    fallback_call = result.to_dict()["generation"]["cypher_fallback_llm"]
    assert fallback_call["prompt"] == llm_client.prompt
    assert fallback_call["raw_output"] == result.generated_cypher
    assert result.to_dict()["intent_recognition"]["diagnostics"]["llm_secondary_attempts"] == []


@pytest.mark.asyncio
async def test_semantic_pipeline_falls_back_to_controlled_llm_when_renderer_preflight_fails() -> None:
    class MismatchingRenderer:
        def render(self, semantic_query):
            return (
                "MATCH (s:Service)-[:SERVICE_USES_TUNNEL]->(t:Tunnel)\n"
                "WHERE s.quality_of_service = 'Gold'\n"
                "RETURN t.id AS tunnel_id"
            )

    class FakeLLMClient:
        def __init__(self) -> None:
            self.prompt = ""

        async def generate_from_prompt(self, *, task_id: str, question_text: str, llm_prompt: str):
            self.prompt = llm_prompt
            return {
                "raw_output": (
                    "MATCH (s:Service)-[:SERVICE_USES_TUNNEL]->(t:Tunnel)\n"
                    "WHERE s.quality_of_service = 'Gold'\n"
                    "RETURN t.name AS tunnel_name"
                )
            }

    llm_client = FakeLLMClient()
    pipeline = SemanticPipeline(renderer=MismatchingRenderer(), llm_client=llm_client)
    intent = IntentRecognitionResult(
        primary_intent="record_retrieval_query",
        secondary_intent="related_record_query",
        confidence=0.93,
        source="rule",
        decision="accept",
    )

    result = await pipeline.parse_with_fallback(
        id="qa-preflight-fallback",
        question="查询 Gold 服务使用的隧道名称",
        generation_run_id="cypher-run-preflight-fallback",
        intent_result=intent,
    )

    assert result.generation_mode == "controlled_llm_fallback"
    assert result.generated_cypher.endswith("RETURN t.name AS tunnel_name")
    assert result.preflight.accepted is True
    assert "semantic preflight failed" in llm_client.prompt
    assert "logical_plan_mismatch" in llm_client.prompt
    assert result.to_dict()["generation"]["cypher_fallback_llm"]["prompt"] == llm_client.prompt


@pytest.mark.asyncio
async def test_semantic_pipeline_controlled_llm_prompt_includes_selected_knowledge() -> None:
    class UnsupportedRenderer:
        def render(self, semantic_query):
            raise NotImplementedError("renderer does not support this semantic query")

    class FakeKnowledgeSelector:
        async def select(self, *, question, intent_result, semantic_query):
            return SelectedKnowledgeContext(
                fragments=[{"id": "business_rule.0002.service_terms", "type": "business_rule"}],
                prompt_context="## business_rule\nGold 服务质量等级映射到 Service.quality_of_service = 'Gold'",
                selection_trace=["selected business_rule.0002.service_terms via symbolic match"],
                size_estimate=73,
                missing_knowledge_signals=[],
                source="rag",
            )

    class FakeLLMClient:
        def __init__(self) -> None:
            self.prompt = ""

        async def generate_from_prompt(self, *, task_id: str, question_text: str, llm_prompt: str):
            self.prompt = llm_prompt
            return {
                "raw_output": (
                    "MATCH (s:Service)-[:SERVICE_USES_TUNNEL]->(t:Tunnel)\n"
                    "WHERE s.quality_of_service = 'Gold'\n"
                    "RETURN t.name AS tunnel_name"
                )
            }

    llm_client = FakeLLMClient()
    pipeline = SemanticPipeline(
        renderer=UnsupportedRenderer(),
        llm_client=llm_client,
        knowledge_selector=FakeKnowledgeSelector(),
    )
    intent = IntentRecognitionResult(
        primary_intent="record_retrieval_query",
        secondary_intent="related_record_query",
        confidence=0.93,
        source="rule",
        decision="accept",
    )

    result = await pipeline.parse_with_fallback(question="查询 Gold 服务使用的隧道名称", intent_result=intent)

    assert result.generation_mode == "controlled_llm_fallback"
    assert "【已选择知识上下文】" in llm_client.prompt
    assert "Gold 服务质量等级映射到 Service.quality_of_service = 'Gold'" in llm_client.prompt


@pytest.mark.asyncio
async def test_semantic_pipeline_fallback_reports_parser_failure() -> None:
    class UnsupportedRenderer:
        def render(self, semantic_query):
            raise NotImplementedError("renderer does not support this semantic query")

    class FakeLLMClient:
        async def generate_from_prompt(self, *, task_id: str, question_text: str, llm_prompt: str):
            return {"raw_output": "```cypher\nMATCH (s:Service) RETURN s.name\n```"}

    pipeline = SemanticPipeline(renderer=UnsupportedRenderer(), llm_client=FakeLLMClient())
    intent = IntentRecognitionResult(
        primary_intent="record_retrieval_query",
        secondary_intent="entity_list_query",
        confidence=0.93,
        source="rule",
        decision="accept",
    )

    result = await pipeline.parse_with_fallback(question="查询所有服务", intent_result=intent)

    assert result.generation_mode == "controlled_llm_fallback"
    assert result.generated_cypher is None
    assert result.preflight.accepted is False
    assert result.preflight.reason == "wrapped_in_markdown"


@pytest.mark.asyncio
async def test_semantic_pipeline_fallback_reports_semantic_preflight_failure() -> None:
    class UnsupportedRenderer:
        def render(self, semantic_query):
            raise NotImplementedError("renderer does not support this semantic query")

    class FakeLLMClient:
        async def generate_from_prompt(self, *, task_id: str, question_text: str, llm_prompt: str):
            return {"raw_output": "MATCH (x:Secret) RETURN x.name AS service_name"}

    pipeline = SemanticPipeline(renderer=UnsupportedRenderer(), llm_client=FakeLLMClient())
    intent = IntentRecognitionResult(
        primary_intent="record_retrieval_query",
        secondary_intent="entity_list_query",
        confidence=0.93,
        source="rule",
        decision="accept",
    )

    result = await pipeline.parse_with_fallback(question="查询所有服务", intent_result=intent)

    assert result.generation_mode == "controlled_llm_fallback"
    assert result.generated_cypher == "MATCH (x:Secret) RETURN x.name AS service_name"
    assert result.preflight.accepted is False
    assert result.preflight.reason == "unauthorized_schema_reference"


def test_semantic_pipeline_generates_numeric_metric_query() -> None:
    pipeline = SemanticPipeline()
    intent = IntentRecognitionResult(
        primary_intent="metric_query",
        secondary_intent="numeric_metric_query",
        confidence=0.92,
        source="rule",
        decision="accept",
    )

    result = pipeline.parse(question="查询隧道平均时延", intent_result=intent)

    assert result.validation.accepted is True
    assert result.generated_cypher == "MATCH (t:Tunnel)\nRETURN avg(t.latency) AS avg_tunnel_latency"


def test_semantic_pipeline_generates_single_dimension_breakdown_query() -> None:
    pipeline = SemanticPipeline()
    intent = IntentRecognitionResult(
        primary_intent="breakdown_query",
        secondary_intent="single_dimension_breakdown_query",
        confidence=0.92,
        source="rule",
        decision="accept",
    )

    result = pipeline.parse(question="按厂商统计设备数量", intent_result=intent)

    assert result.validation.accepted is True
    payload = result.to_dict()
    assert payload["semantic_view_matching"]["result"]["metrics"] == [
        {"metric_id": "network_element_count", "evidence": "设备数量"}
    ]
    assert result.generated_cypher == (
        "MATCH (ne:NetworkElement)\n"
        "RETURN ne.vendor AS network_element_vendor, count(ne) AS network_element_count\n"
        "ORDER BY network_element_count DESC"
    )


def test_semantic_pipeline_generates_elem_type_breakdown_query() -> None:
    pipeline = SemanticPipeline()
    intent = IntentRecognitionResult(
        primary_intent="breakdown_query",
        secondary_intent="single_dimension_breakdown_query",
        confidence=0.92,
        source="rule",
        decision="accept",
    )

    result = pipeline.parse(question="按类型统计隧道数量", intent_result=intent)

    assert result.validation.accepted is True
    assert result.generated_cypher == (
        "MATCH (t:Tunnel)\n"
        "RETURN t.elem_type AS tunnel_type, count(t) AS tunnel_count\n"
        "ORDER BY tunnel_count DESC"
    )


def test_semantic_pipeline_generates_topn_ranking_query() -> None:
    pipeline = SemanticPipeline()
    intent = IntentRecognitionResult(
        primary_intent="ranking_query",
        secondary_intent="attribute_ranking_query",
        confidence=0.92,
        source="rule",
        decision="accept",
    )

    result = pipeline.parse(question="查询时延最高的前 5 个隧道", intent_result=intent)

    assert result.validation.accepted is True
    assert result.generated_cypher == (
        "MATCH (t:Tunnel)\n"
        "RETURN t.name AS tunnel_name, t.latency AS tunnel_latency\n"
        "ORDER BY t.latency DESC\n"
        "LIMIT 5"
    )


def test_semantic_pipeline_generates_relationship_existence_query() -> None:
    pipeline = SemanticPipeline()
    intent = IntentRecognitionResult(
        primary_intent="existence_query",
        secondary_intent="relationship_existence_query",
        confidence=0.92,
        source="rule",
        decision="accept",
    )

    result = pipeline.parse(question="服务是否使用隧道", intent_result=intent)

    assert result.validation.accepted is True
    assert result.generated_cypher == (
        "MATCH (s:Service)-[:SERVICE_USES_TUNNEL]->(t:Tunnel)\n"
        "RETURN count(*) > 0 AS exists"
    )
