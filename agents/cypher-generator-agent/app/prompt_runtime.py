from __future__ import annotations

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
    "generation_retry_exhausted": "",
}


def render_llm_prompt(*, question: str, ko_context: str, extra_constraint_reason: GenerationFailureReason | None = None) -> str:
    extra_constraint = _render_extra_constraint(extra_constraint_reason)
    return f"""【任务说明】
你是 cypher-generator-agent 的 Cypher 生成模型调用。请根据用户问题和 knowledge-agent 上下文，生成一条只读 Cypher 查询。

【用户问题】
{question.strip()}

【knowledge-agent 上下文】
{ko_context.strip()}

【输出格式】
- 只输出 Cypher 查询本体。
- 不要输出 Markdown、代码块、JSON、标题、解释或自然语言说明。
- 只输出一条查询。
- 查询必须是只读查询。
- 查询必须以 MATCH 或 WITH 开始。

【优先级】
- 与输出形态、只读安全和提交前检查有关的要求，以本模板为准。
- 与图谱 schema、业务词汇、查询模式和示例有关的知识，以 knowledge-agent 上下文为准。
{extra_constraint}""".strip()


def _render_extra_constraint(reason: GenerationFailureReason | None) -> str:
    if reason is None or reason == "generation_retry_exhausted":
        return ""
    text = EXTRA_CONSTRAINT_BY_REASON[reason]
    return f"""

【额外约束】
{text}
"""
