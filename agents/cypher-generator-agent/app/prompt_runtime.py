from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from . import resource_paths
from .models import GenerationFailureReason


PROMPT_TEMPLATE_VERSION = "cypher_generator_agent_prompt_v1"

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
    "unauthorized_schema_reference": "不要引用逻辑查询计划和已授权 schema path 之外的 label、edge、property。",
    "logical_plan_mismatch": "必须完整覆盖 LogicalQueryPlan 中的查询目标、过滤条件、路径引用、返回对象、排序和 limit。",
    "semantic_match_rejected": "必须满足语义视图匹配结果中的实体、过滤条件、路径语义和返回策略。",
    "path_planning_failed": "只能使用规划层已经选中的合法 schema graph path。",
    "cypher_fallback_cannot_generate": "如果无法安全生成，请输出 __CANNOT_GENERATE__。",
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
你是 cypher-generator-agent 的受控 Cypher fallback 生成模型。请根据用户问题、逻辑查询计划、已授权 schema path 和已选择知识上下文生成一条只读 Cypher 查询。

【用户问题】
{question.strip()}

【逻辑查询计划与授权路径】
{semantic_query_json.strip()}
{selected_knowledge_section}
{renderer_error_section}
【硬性约束】
- 只输出 Cypher 查询本体。
- 不要输出 Markdown、代码块、JSON、标题、解释或自然语言说明。
- 只输出一条查询。
- 查询必须是只读查询。
- 查询必须以 MATCH 或 WITH 开始。
- 不要新增逻辑查询计划和授权路径未允许的 label、edge、property。
- 必须覆盖逻辑查询计划中的查询目标、过滤条件、路径引用、返回对象、排序和 limit。
- 如果无法安全覆盖，输出 __CANNOT_GENERATE__，不要解释。
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


def render_intent_primary_candidate_prompt(
    *,
    question: str,
    candidate_cards: str,
) -> str:
    return f"""# 任务

你是 cypher-generator-agent 的意图识别模块。当前只做一级意图候选判定。

你只需要判断用户问题最终想得到什么形态的答案。

你不能做以下事情：
- 不要识别业务实体、字段、关系、路径、指标和值。
- 不要生成 Cypher。
- 不要判断二级意图。

# 用户问题

{question.strip()}

# 前置候选依据

前两阶段整理出以下一级候选。请优先使用这些候选依据判断。

{candidate_cards.strip() or "无候选依据。"}

# 判断规则

1. 如果候选依据已经足以判断一级意图，输出 `decision=accept`，并选择候选中的一级意图。
2. 如果候选依据不足以判断，但用户问题本身有明确动作和答案形态，输出 `decision=need_full_taxonomy`。
3. 如果用户问题本身缺少动作、目标或答案形态，输出 `decision=clarify`，并给出中文澄清问题。

# 输出要求

只输出 JSON，不要输出 Markdown、代码块、解释或自然语言说明。JSON 必须包含：
- `primary_intent`: 字符串或 null
- `secondary_intent`: 固定为 null
- `confidence`: 0 到 1 的数字
- `source`: 固定为 `llm`
- `decision`: 只能是 `accept`、`need_full_taxonomy` 或 `clarify`
- `reason`: 中文理由
- `clarification_question`: 仅当 `decision=clarify` 时填写中文澄清问题

不要在 `accept` 时输出候选依据之外的一级意图。""".strip()


def render_intent_primary_full_prompt(
    *,
    question: str,
    candidate_stage_summary: str,
) -> str:
    taxonomy = _load_intent_taxonomy_summary()
    return f"""# 任务

你是 cypher-generator-agent 的意图识别模块。当前只做一级意图全量兜底判定。

候选判定阶段认为前置候选依据不足，因此现在提供完整一级分类供你选择。

你不能做以下事情：
- 不要识别业务实体、字段、关系、路径、指标和值。
- 不要生成 Cypher。
- 不要判断二级意图。

# 用户问题

{question.strip()}

# 候选阶段摘要

{candidate_stage_summary.strip() or "前置候选不足。"}

# 一级意图分类

你只能从以下一级意图中选择一个。

{taxonomy["primary"]}

# 输出要求

只输出 JSON，不要输出 Markdown、代码块、解释或自然语言说明。JSON 必须包含：
- `primary_intent`: 字符串或 null
- `secondary_intent`: 固定为 null
- `confidence`: 0 到 1 的数字
- `source`: 固定为 `llm`
- `decision`: 只能是 `accept` 或 `clarify`
- `reason`: 中文理由
- `clarification_question`: 仅当 `decision=clarify` 时填写中文澄清问题

如果完整一级分类下仍无法安全判断，输出 `decision=clarify`。
不要输出上述一级意图列表之外的值。""".strip()


def render_intent_secondary_candidate_prompt(
    *,
    question: str,
    primary_intent: str,
    primary_intent_name: str,
    candidate_cards: str,
) -> str:
    return f"""# 任务

你是 cypher-generator-agent 的意图识别模块。当前只做二级意图候选判定。

一级意图已经确定为：`{primary_intent}`，中文名：{primary_intent_name}。

你只需要在这个一级意图下面判断最合适的二级意图。

你不能做以下事情：
- 不要改变一级意图。
- 不要选择其他一级意图下面的二级意图。
- 不要识别业务实体、字段、关系、路径、指标和值。
- 不要生成 Cypher。

# 用户问题

{question.strip()}

# 前置候选依据

前两阶段在当前一级意图下召回了以下二级候选。请先认真利用这些候选依据判断。

{candidate_cards.strip() or "无候选依据。"}

# 判断规则

1. 如果候选依据已经足以判断二级意图，输出 `decision=accept`，并选择候选中的二级意图。
2. 如果候选依据不足以判断，但用户问题在当前一级意图下仍有明确答案形态，输出 `decision=need_full_taxonomy`。
3. 如果当前一级意图明确，但用户表达无法区分二级答案形态，输出 `decision=clarify`，并给出中文澄清问题。

# 输出要求

只输出 JSON，不要输出 Markdown、代码块、解释或自然语言说明。JSON 必须包含：
- `primary_intent`: 固定为 `{primary_intent}`
- `secondary_intent`: 字符串或 null
- `confidence`: 0 到 1 的数字
- `source`: 固定为 `llm`
- `decision`: 只能是 `accept`、`need_full_taxonomy` 或 `clarify`
- `reason`: 中文理由
- `clarification_question`: 仅当 `decision=clarify` 时填写中文澄清问题

不要在 `accept` 时输出候选依据之外的二级意图。""".strip()


def render_intent_secondary_full_prompt(
    *,
    question: str,
    primary_intent: str,
    primary_intent_name: str,
    candidate_stage_summary: str,
) -> str:
    taxonomy = _load_intent_taxonomy_summary()
    secondary = taxonomy["secondary_by_primary"].get(primary_intent, "无")
    return f"""# 任务

你是 cypher-generator-agent 的意图识别模块。当前只做二级意图全量兜底判定。

一级意图已经确定为：`{primary_intent}`，中文名：{primary_intent_name}。

候选判定阶段认为前置二级候选依据不足，因此现在提供该一级下的完整二级分类供你选择。

你不能做以下事情：
- 不要改变一级意图。
- 不要选择其他一级意图下面的二级意图。
- 不要识别业务实体、字段、关系、路径、指标和值。
- 不要生成 Cypher。

# 用户问题

{question.strip()}

# 候选阶段摘要

{candidate_stage_summary.strip() or "前置二级候选不足。"}

# 当前一级意图下的完整二级分类

{secondary}

# 输出要求

只输出 JSON，不要输出 Markdown、代码块、解释或自然语言说明。JSON 必须包含：
- `primary_intent`: 固定为 `{primary_intent}`
- `secondary_intent`: 字符串或 null
- `confidence`: 0 到 1 的数字
- `source`: 固定为 `llm`
- `decision`: 只能是 `accept` 或 `clarify`
- `reason`: 中文理由
- `clarification_question`: 仅当 `decision=clarify` 时填写中文澄清问题

如果完整二级分类下仍无法安全判断，输出 `decision=clarify`。
不要输出当前一级意图之外的二级意图。""".strip()


def render_semantic_view_disambiguation_prompt(
    *,
    question: str,
    candidate_cards: str,
) -> str:
    return f"""# 任务

你是 cypher-generator-agent 的语义视图匹配消歧模块。当前只在有限候选中选择最符合用户问题的业务语义。

你不能做以下事情：
- 不要生成 Cypher。
- 不要创造候选列表之外的实体、字段、关系或路径。
- 不要补充用户没有表达的业务条件。

# 用户问题

{question.strip()}

# 语义候选

{candidate_cards.strip()}

# 判断规则

1. 如果用户表达已经足以选择一个候选，输出 `decision=accept`，并返回候选的 `path_semantic`。
2. 如果多个候选都合理且无法选择，输出 `decision=clarify`，并给出中文澄清问题。
3. 如果候选都不符合用户问题，输出 `decision=reject`。

# 输出要求

只输出 JSON，不要输出 Markdown、代码块、解释或自然语言说明。JSON 必须包含：
- `decision`: `accept`、`clarify` 或 `reject`
- `selected_path_semantic`: 仅当 `decision=accept` 时填写候选中的 path_semantic
- `confidence`: 0 到 1 的数字
- `reason`: 中文理由
- `clarification_question`: 仅当 `decision=clarify` 时填写中文澄清问题""".strip()


@lru_cache(maxsize=1)
def _load_intent_llm_assets() -> dict[str, str]:
    fewshot_payload = _read_yaml(resource_paths.intent_llm_fewshots_path())
    taxonomy_payload = _read_yaml(resource_paths.intent_taxonomy_path())
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


@lru_cache(maxsize=1)
def _load_intent_taxonomy_summary() -> dict[str, Any]:
    taxonomy_payload = _read_yaml(resource_paths.intent_taxonomy_path())
    primary_lines: list[str] = []
    secondary_by_primary: dict[str, str] = {}
    for primary in taxonomy_payload.get("intents", []):
        if not isinstance(primary, dict):
            continue
        primary_intent = str(primary.get("primary_intent") or "")
        if not primary_intent:
            continue
        primary_lines.append(
            f"- `{primary_intent}`：{primary.get('name_zh') or primary_intent}。{primary.get('description') or ''}"
        )
        secondary_lines = []
        for secondary in primary.get("secondary_intents", []):
            if not isinstance(secondary, dict):
                continue
            secondary_intent = str(secondary.get("secondary_intent") or "")
            if not secondary_intent:
                continue
            secondary_lines.append(
                f"- `{secondary_intent}`：{secondary.get('name_zh') or secondary_intent}。{secondary.get('description') or ''}"
            )
        secondary_by_primary[primary_intent] = "\n".join(secondary_lines) or "无"
    return {
        "primary": "\n".join(primary_lines) or "无",
        "secondary_by_primary": secondary_by_primary,
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
    if reason is None:
        return ""
    text = EXTRA_CONSTRAINT_BY_REASON[reason]
    return f"""

【额外约束】
{text}
"""
