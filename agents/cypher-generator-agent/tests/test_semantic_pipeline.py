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
    assert result.to_dict()["llm_prompts"]["intent_recognition_fallback"] == llm_client.calls[0]["llm_prompt"]
    assert result.to_dict()["llm_responses"]["intent_recognition_fallback"] == llm_client.calls[0]["raw_output"]
    assert result.to_dict()["llm_prompts"]["cypher_generation_fallback"] is None
    assert result.to_dict()["llm_responses"]["cypher_generation_fallback"] is None
    assert "intent_fallback_cypher_generation" not in result.to_dict()["llm_prompts"]


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
    assert result.to_dict()["llm_prompts"]["intent_recognition_fallback"]
    assert result.to_dict()["llm_responses"]["intent_recognition_fallback"] == "MATCH (s:Service) RETURN s.name AS service_name"
    assert "intent_fallback_cypher_generation" not in result.to_dict()["llm_prompts"]


def test_semantic_pipeline_rejects_accepted_intent_without_business_slot_schema() -> None:
    pipeline = SemanticPipeline()
    intent = IntentRecognitionResult(
        primary_intent="trend_query",
        secondary_intent="metric_trend_query",
        confidence=0.91,
        source="embedding",
        decision="accept",
    )

    result = pipeline.parse(question="查询最近一周链路状态变化趋势", intent_result=intent)

    assert result.validation.accepted is False
    assert result.semantic_query is None
    assert result.generated_cypher is None
    assert result.preflight is None
    assert result.validation.diagnostics[0].code == "unsupported_business_slot_schema"


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
    assert result.to_dict()["selected_knowledge"]["fragments"][0]["type"] == "verified_query"


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
    assert '"kind": "record_selection"' in llm_client.prompt
    assert "SemanticQuerySpec" in llm_client.prompt
    assert "不要新增 SemanticQuerySpec 未授权的 label、edge、property" in llm_client.prompt
    assert result.to_dict()["llm_prompts"]["cypher_generation_fallback"] == llm_client.prompt
    assert result.to_dict()["llm_responses"]["cypher_generation_fallback"] == result.generated_cypher
    assert result.to_dict()["llm_prompts"]["intent_recognition_fallback"] is None
    assert result.to_dict()["llm_responses"]["intent_recognition_fallback"] is None


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
    assert "semantic_query_mismatch" in llm_client.prompt
    assert result.to_dict()["llm_prompts"]["cypher_generation_fallback"] == llm_client.prompt


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
