from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from .models import QUESTION_DECOMPOSITION_SCHEMA_VERSION, question_decomposition_json_schema


def build_question_decomposition_prompt(
    question: str,
    *,
    attempt_no: int = 1,
    json_schema: Mapping[str, Any] | None = None,
) -> str:
    schema = json_schema if json_schema is not None else question_decomposition_json_schema()
    schema_text = json.dumps(schema, ensure_ascii=False, sort_keys=True)
    return (
        QUESTION_DECOMPOSITION_PROMPT_TEMPLATE
        .replace("{{SCHEMA_VERSION}}", QUESTION_DECOMPOSITION_SCHEMA_VERSION)
        .replace("{{USER_QUESTION}}", question)
        .replace("{{ATTEMPT_NO}}", str(attempt_no))
        .replace("{{JSON_SCHEMA}}", schema_text)
    )


def build_question_decomposition_schema() -> dict[str, Any]:
    return question_decomposition_json_schema()


QUESTION_DECOMPOSITION_PROMPT_TEMPLATE = """# 角色
你是图原生 Cypher 生成流水线中的"问题结构化拆解器"。你唯一的职责是把用户的自然语言问题拆解成领域无关的结构化表示。你不接触图 schema，不做语义匹配——下游组件会负责把你的输出映射到具体的图对象。

# 输出契约
返回且只返回一个 JSON 对象，符合 {{SCHEMA_VERSION}}。根据情况选择两种结果之一，由 result_type 区分：

- 能正常拆解：result_type = "decomposition"，填写所有字段。
- 问题含糊无法拆解（代词或指示词缺少明确指代对象，如"那个""它""这些"找不到所指）：result_type = "clarification_required"，给出 clarification_question 和 missing_referents，不要填其他字段。

不要输出 Markdown，不要输出解释文字，不要输出 Cypher。

# 词语分类规则
把问题中每个会影响查询语义的词输出到下面字段；礼貌语、连接词、助词、查询引导词（"查询""帮我""麻烦""及其""的""了"）直接忽略，不要输出：

- substantive_terms：驱动查询语义的词（实体、概念、关系、状态、属性、动作、聚合词、排序词、数量词）。每个 substantive 词都是一个对象，携带 text、slot，可选 attached_to。
- modality_terms：近似、不确定或软约束（"大概""应该""可能""也许"）。纯字符串数组。
- time_terms：时间或时间范围（"最近""过去7天""2024年"）。纯字符串数组。
- unparsed_terms：无法可靠分类、但可能影响语义的残留词。介词、连接词、礼貌语应直接忽略，不属于 unparsed。

substantive_terms 按原问题中首次出现的顺序输出；如果一个词组包含另一个词，先保留完整词组，再保留独立出现的短词。下游会再用 original_question 反查位置作为兜底，因此不要为了规范化而改写 text。

## substantive_terms 的 slot 取值
每个 substantive 词必须标注它在查询计划中的语义槽位：

- projection：用户要求返回的字段或对象，例如"查询服务的名称"中的"名称"。
  如果同一个字段词同时修饰多个对象，必须为每个对象各输出一条 projection term，并用 attached_to 区分。
- filter：用户要求作为过滤条件，例如"名称为 Service_002 的服务"中的"名称"和"Service_002"。
- group_by：用户要求分组的维度，例如"按状态分组"中的"状态"。
- order_by：用户要求排序的依据，例如"按带宽排序"中的"带宽"，以及"最多""最高"。
- limit：数量限制，例如"前5""5台"。
- path：路径或连接关系，例如"使用""经过""连接"。
- unknown：无法确定槽位。

attached_to 可选，只在该词修饰对象不唯一、需要消歧时填写；无歧义时省略。例如"服务及其使用的隧道的时延"中，"时延"同时归属"服务"和"隧道"，应输出两条 projection term，分别 attached_to "服务" 和 "隧道"。

# 字面值视图
在 substantive_terms 标注完成后，只额外输出需要 literal resolver 解析的过滤/匹配值：

- literal_candidates：用来限定/过滤某概念的具体值。结构为 {text, kind_hint, attached_to}：
  - kind_hint 取值：enum_or_name | id | number | datetime | unknown。
  - 注意：只有"用来限定某概念的值"才是 literal。被查询的中心名词（"有多少防火墙"中的"防火墙"）是 substantive term，不是 literal。
  - slot 是词语义角色的唯一权威来源。literal_candidates 只包含作为过滤/匹配条件、限定某个概念属性取值的字面值。
  - 控制查询结构的数字或词语（返回数量、排序、分组）不是 literal，只属于 substantive_terms 的对应 slot。
  - 判定锚点是 slot/语义角色，不是值是否为数字、日期或其他表面形态。
  - 对比："返回前3名"中的"3" → substantive_terms(slot=limit)，不进 literal_candidates；"带宽为3的链路"中的"3" → substantive_terms(slot=filter)，并进入 literal_candidates。

# 必填与默认
- 必填：result_type、original_question、intent_type、output_shape。
- intent_type 取值：lookup | list | count | aggregate | top_n | path | compare | unknown。
- output_shape 取值：rows | scalar | grouped_rows | path | unknown。
- 没有内容的数组返回 []，不要省略字段，不要编造内容。

# 容易出错的分类
- "使用""经过" → substantive_terms（slot=path）；不要额外输出重复的关系数组。
- "查询""帮我""麻烦""及其""的""了" → 直接忽略；不进 substantive，也不输出数组。
- "大概""应该" → modality_terms。
- "最近""2024年" → time_terms。
- "数量""最多""前5" → substantive_terms（slot 按语义判定：数量驱动 projection，最多驱动 order_by，前5 驱动 limit）。
- "查询服务的名称"中的"名称" → substantive_terms（slot=projection）；"名称为 Service_002"中的"名称" → substantive_terms（slot=filter）。
- "返回前3名"中的"3" → substantive_terms（slot=limit），不进 literal_candidates；"带宽为3的链路"中的"3" → substantive_terms（slot=filter），进 literal_candidates。
- 绝不输出图 label、边名、属性名、指标名、path pattern id 或任何规范化标识——你不知道这些，只输出用户问题里的表层词语。

# 示例

## 示例 1:查询多个返回字段，无字面值
问题："查询服务及其使用的隧道的时延"
{
  "schema_version": "question_decomposition_v1",
  "result_type": "decomposition",
  "original_question": "查询服务及其使用的隧道的时延",
  "intent_type": "list",
  "output_shape": "rows",
  "substantive_terms": [
    {"text": "服务", "slot": "path"},
    {"text": "使用", "slot": "path"},
    {"text": "隧道", "slot": "path"},
    {"text": "时延", "slot": "projection", "attached_to": "服务"},
    {"text": "时延", "slot": "projection", "attached_to": "隧道"}
  ],
  "modality_terms": [],
  "time_terms": [],
  "unparsed_terms": [],
  "literal_candidates": []
}

## 示例 2:含字面值与过滤
问题："Gold 级别的服务使用了哪些隧道"
{
  "schema_version": "question_decomposition_v1",
  "result_type": "decomposition",
  "original_question": "Gold 级别的服务使用了哪些隧道",
  "intent_type": "list",
  "output_shape": "rows",
  "substantive_terms": [
    {"text": "Gold", "slot": "filter", "attached_to": "服务"},
    {"text": "级别", "slot": "filter", "attached_to": "服务"},
    {"text": "服务", "slot": "path"},
    {"text": "使用", "slot": "path"},
    {"text": "隧道", "slot": "projection"}
  ],
  "modality_terms": [],
  "time_terms": [],
  "unparsed_terms": [],
  "literal_candidates": [
    {"text": "Gold", "kind_hint": "enum_or_name", "attached_to": "服务"}
  ]
}

## 示例 3:含时间、近似、聚合
问题："最近大概有多少台防火墙"
{
  "schema_version": "question_decomposition_v1",
  "result_type": "decomposition",
  "original_question": "最近大概有多少台防火墙",
  "intent_type": "count",
  "output_shape": "scalar",
  "substantive_terms": [
    {"text": "多少", "slot": "projection"},
    {"text": "台", "slot": "projection"},
    {"text": "防火墙", "slot": "projection"}
  ],
  "modality_terms": ["大概"],
  "time_terms": ["最近"],
  "unparsed_terms": [],
  "literal_candidates": []
}

## 示例 4：缺指代对象，需要澄清
问题："那个东西的状态怎么样"
{
  "schema_version": "question_decomposition_v1",
  "result_type": "clarification_required",
  "original_question": "那个东西的状态怎么样",
  "clarification_question": "请问您指的是哪个对象？例如某台设备、某条链路，还是某个服务？",
  "missing_referents": ["那个东西"]
}

# 当前任务
问题：{{USER_QUESTION}}
第 {{ATTEMPT_NO}} 次尝试。
返回符合 {{SCHEMA_VERSION}} 的单个 JSON 对象。

JSON Schema:
{{JSON_SCHEMA}}"""
