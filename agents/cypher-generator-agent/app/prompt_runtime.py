from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from .models import GenerationFailureReason


PROMPT_TEMPLATE_VERSION = "cypher_generator_agent_prompt_v1"
_CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"

EXTRA_CONSTRAINT_BY_REASON: dict[GenerationFailureReason, str] = {
    "empty_output": "必须输出一条完整的只读 Cypher。",
    "no_cypher_found": "只输出 Cypher 查询本体。",
    "wrapped_in_markdown": "不要使用 Markdown 或代码块包装查询。",
    "wrapped_in_json": "不要使用 JSON 包装查询。",
    "contains_explanation": "不要输出解释、标题或自然语言说明。",
    "multiple_statements": "只输出一条 Cypher 查询。",
    "unbalanced_brackets": "确保圆括号、方括号和花括号完整闭合。",
    "unclosed_string": "确保字符串引号完整闭合。",
    "write_operation": "只生成只读查询。",
    "unsupported_call": "不要使用未允许的 CALL procedure。",
    "unsupported_start_clause": "使用 MATCH 或 WITH 作为查询起始子句。",
    "unauthorized_schema_reference": "不要引用 SemanticQuerySpec 未授权的 label、edge、property。",
    "semantic_query_mismatch": "必须完整覆盖 SemanticQuerySpec 中的实体、关系、过滤、投影、维度、指标、排序、limit 和输出别名。",
    "semantic_parse_rejected": "必须先满足意图、业务槽位和语义层约束，再生成 Cypher。",
    "generation_retry_exhausted": "",
}


def render_controlled_semantic_prompt(
    *,
    question: str,
    semantic_query_json: str,
    renderer_error: str | None = None,
    selected_knowledge_context: str | None = None,
    extra_constraint_reason: GenerationFailureReason | None = None,
) -> str:
    renderer_error_section = ""
    if renderer_error:
        renderer_error_section = f"""

【确定性 Renderer 未覆盖原因】
{renderer_error.strip()}
"""
    selected_knowledge_section = ""
    if selected_knowledge_context and selected_knowledge_context.strip():
        selected_knowledge_section = f"""

【已选择知识上下文】
{selected_knowledge_context.strip()}
"""
    extra_constraint = _render_extra_constraint(extra_constraint_reason)
    return f"""【任务说明】
你是 cypher-generator-agent 的受控 Cypher fallback 生成模型。请根据用户问题、SemanticQuerySpec 和已选择知识上下文生成一条只读 Cypher 查询。

【用户问题】
{question.strip()}

【SemanticQuerySpec】
{semantic_query_json.strip()}
{selected_knowledge_section}
{renderer_error_section}
【硬性约束】
- 只输出 Cypher 查询本体。
- 不要输出 Markdown、代码块、JSON、标题、解释或自然语言说明。
- 只输出一条查询。
- 查询必须是只读查询。
- 查询必须以 MATCH 或 WITH 开始。
- 不要新增 SemanticQuerySpec 未授权的 label、edge、property。
- 必须覆盖 SemanticQuerySpec 中的 entity、relationship、filter、projection、dimension、metric、order_by、limit、output_alias。
- 如果无法覆盖，仍然只输出最接近 SemanticQuerySpec 的只读 Cypher，不要解释。
{extra_constraint}""".strip()


def render_intent_recognition_fallback_prompt(
    *,
    question: str,
    fallback_reason: str | None = None,
) -> str:
    assets = _load_intent_llm_assets()
    fallback_reason_section = ""
    if fallback_reason and fallback_reason.strip():
        fallback_reason_section = f"""

【前置阶段未接受原因】
{fallback_reason.strip()}
"""
    return f"""【任务说明】
你是 cypher-generator-agent 的第三阶段 LLM 意图识别模型。规则和 embedding 阶段未能稳定接受当前问题，请只判断自然语言问题的查询意图，不要生成 Cypher。

【用户问题】
{question.strip()}
{fallback_reason_section}
【输出 JSON 契约】
只输出 JSON，不要输出 Markdown、代码块、解释或自然语言说明。JSON 必须包含：
- primary_intent: string 或 null
- secondary_intent: string 或 null
- confidence: 0 到 1 之间的数字
- decision: "accept" 或 "clarify"

当 decision="accept" 时，primary_intent 和 secondary_intent 必须来自下面的意图分类表。
当问题无法稳定归类或需要用户补充条件时，decision="clarify"，primary_intent 和 secondary_intent 使用 null。

【全局分类原则】
{assets["principles"]}

【易混边界】
{assets["boundaries"]}

【合法意图分类表】
{assets["taxonomy"]}

【Few-shot 示例】
{assets["fewshots"]}""".strip()


@lru_cache(maxsize=1)
def _load_intent_llm_assets() -> dict[str, str]:
    fewshot_payload = _read_yaml(_CONFIG_DIR / "intent_llm_fewshots.yaml")
    taxonomy_payload = _read_yaml(_CONFIG_DIR / "intent_taxonomy.yaml")
    principles = "\n".join(f"- {item}" for item in fewshot_payload.get("global_decision_principles", []))
    boundaries = "\n".join(_format_boundary(item) for item in fewshot_payload.get("confusable_boundaries", []))
    fewshots = "\n".join(_format_fewshot(item) for item in fewshot_payload.get("few_shot_reasoning_examples", [])[:12])
    taxonomy = json.dumps(taxonomy_payload.get("intents", taxonomy_payload), ensure_ascii=False, indent=2)
    return {
        "principles": principles or "无",
        "boundaries": boundaries or "无",
        "fewshots": fewshots or "无",
        "taxonomy": taxonomy,
    }


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _format_boundary(item: Any) -> str:
    if not isinstance(item, dict):
        return ""
    examples = item.get("examples") if isinstance(item.get("examples"), list) else []
    example_text = "；".join(
        f"{example.get('question')} => {example.get('correct')}"
        for example in examples
        if isinstance(example, dict)
    )
    return (
        f"- {item.get('boundary_id')}: {item.get('left')} vs {item.get('right')}；"
        f"规则：{item.get('decision_rule')}；示例：{example_text}"
    )


def _format_fewshot(item: Any) -> str:
    if not isinstance(item, dict):
        return ""
    return (
        f"- 问题：{item.get('question')}\n"
        f"  输出：primary_intent={item.get('primary_intent')}, "
        f"secondary_intent={item.get('secondary_intent')}, decision=accept\n"
        f"  理由：{item.get('reason')}"
    )


def _render_extra_constraint(reason: GenerationFailureReason | None) -> str:
    if reason is None or reason == "generation_retry_exhausted":
        return ""
    text = EXTRA_CONSTRAINT_BY_REASON[reason]
    return f"""

【额外约束】
{text}
"""
