# 语义视图消歧待实现事项

本文档记录语义视图匹配层中尚未实现、但需要进入后续重构计划的消歧能力。当前只记录“消除歧义”相关内容。

## 1. 当前实现状态

当前 `cypher-generator-agent` 已经接入受控 LLM 消歧，但实现范围只覆盖 `path_semantic` 路径语义消歧。

已实现能力：

- 当语义视图匹配得到多条合法路径候选时，进入受控 LLM 消歧。
- LLM prompt 只展示有限路径候选，不展示完整语义视图。
- LLM 只能返回候选中的 `selected_path_semantic`。
- 代码校验 `selected_path_semantic` 必须来自本轮候选。
- 通过校验后，使用 `match_with_selected_path()` 重新组装 `SemanticMatchResult`。

当前相关代码：

```text
services/cypher_generator_agent/app/semantic_pipeline.py
- _fallback_semantic_view_disambiguation_to_llm()
- _render_semantic_path_candidate_cards()

services/cypher_generator_agent/app/semantic_view_matching.py
- match()
- match_with_selected_path()
```

## 2. 当前缺口

当前实现不是完整的语义视图消歧，只是路径专项消歧。

尚未覆盖的歧义类型：

- 字段 owner 消歧：同名或近义字段可能属于不同实体，例如服务名称、隧道名称、网元名称。
- 字段角色消歧：同一字段可能既可作为过滤条件，也可作为返回字段。
- 指标候选消歧：自然语言中的“数量、总数、统计值”等表达可能对应不同 metric。
- 返回策略消歧：用户说“相关信息”“对应关系”“及其信息”时，可能触发不同 return policy。
- 泛化业务对象消歧：用户使用“相关资源”“对应资源”等泛化表达时，可能对应隧道、网元、端口、链路等多个候选。
- LLM 返回 `clarify` 时，目前没有完整接入模型生成的澄清问题和选项重组。

## 3. 目标形态

后续应把消歧从 `selected_path_semantic` 专项机制升级为通用候选选择机制。

建议新增统一候选结构：

```jsonc
{
  // 本轮消歧候选的唯一 ID，只在当前请求内有效。
  "candidate_id": "c_path_destination",

  // 候选类型，例如 path_semantic、field_owner、field_role、metric、return_policy、business_object。
  "candidate_type": "path_semantic",

  // 候选指向的语义视图对象 ID。
  "target_id": "service.tunnel_destination",

  // 给模型看的中文业务含义。
  "business_meaning": "服务使用隧道到达的目的网元",

  // 候选涉及的实体、字段、路径或指标摘要。
  "semantic_summary": {
    "entities": ["service", "tunnel", "network_element"],
    "relationships": ["service_uses_tunnel", "tunnel_dst"]
  },

  // 候选进入消歧的证据。
  "evidence": ["命中服务和网元", "存在多条服务到网元路径候选"],

  // 选择该候选后的后处理动作。
  "apply_action": "select_path_semantic"
}
```

LLM 输出应从路径专项字段改为通用字段：

```jsonc
{
  // accept 表示选择一个候选；clarify 表示需要用户澄清；reject 表示候选都不合适。
  "decision": "accept",

  // 选择的候选 ID，必须来自 prompt 中的 candidate_id。
  "selected_candidate_id": "c_path_destination",

  // 模型自评置信度，只用于诊断和展示。
  "confidence": 0.82,

  // 中文理由。
  "reason": "问题更像是在询问服务经隧道到达的目的网元。",

  // decision=clarify 时填写。
  "clarification_question": null
}
```

## 4. 待实现任务

### 4.1 通用候选构建

新增 `SemanticDisambiguationCandidate` 运行时结构，统一承载 path、field、metric、return policy 等候选。

需要完成：

- 从现有 `SemanticMatchResult.paths` 派生 path 候选。
- 从字段匹配结果派生 field owner 候选。
- 从字段上下文派生 filter / return 角色候选。
- 从 metric 匹配结果派生 metric 候选。
- 从 return policy 匹配结果派生返回策略候选。
- 每个候选必须保留来源、证据和后处理动作。

### 4.2 通用 prompt 模板

将当前 `render_semantic_view_disambiguation_prompt()` 从 path 专项模板改成通用候选模板。

需要完成：

- prompt 中展示 `candidate_id`、`candidate_type`、业务含义、语义摘要和证据。
- prompt 中展示正向信号和反向信号，避免模型只凭英文 ID 猜测。
- 对“对应、相关、关联、涉及、有哪些”等泛化表达增加强澄清规则；缺少明确消歧词时不能硬选。
- 增加短示例：一个明确选择候选的示例，一个必须澄清的示例。
- 输出字段改为 `selected_candidate_id`。
- 明确禁止模型返回候选之外的对象。
- 明确模型只做选择，不生成 Cypher，不补造 schema。

### 4.3 通用输出校验

新增统一校验逻辑，替代当前只校验 `selected_path_semantic` 的逻辑。

需要完成：

- `decision` 只能是 `accept`、`clarify` 或 `reject`。
- `accept` 时 `selected_candidate_id` 必须来自本轮候选。
- 候选对应的 `target_id` 必须存在于语义视图。
- 候选引用的 label、edge、property 必须存在于真实 TuGraph schema。
- LLM 输出中不能包含 Cypher 或额外 schema 片段。

### 4.4 结果重建

新增通用候选应用逻辑，替代当前 `match_with_selected_path()` 的路径专项重建。

需要完成：

- path 候选：收敛为单一路径语义，并重新补全 returns、metrics、order_by、limit。
- field owner 候选：绑定字段 owner 后重新计算 filters 和 returns。
- field role 候选：确定字段是过滤、返回，还是两者都参与。
- metric 候选：确定指标口径和 target entity。
- return policy 候选：确定默认返回字段和上下文字段。
- 最终统一输出 `accepted=true` 的 `SemanticMatchResult`，或输出澄清/拒绝。

### 4.5 澄清反问接入

完善 LLM 返回 `clarify` 后的统一澄清出口。

需要完成：

- 将 LLM 返回的 `clarification_question` 转换为 `SemanticMatchResult.clarification_question`。
- 根据候选列表生成 `clarification_options`。
- 服务层继续输出统一 `clarification_required`。
- 不进入 planner。

### 4.6 运行中心落盘

运行中心需要能展示通用消歧候选和模型选择。

需要完成：

- `semantic_view_matching.llm_disambiguation_attempts[]` 中记录 prompt、raw output、decision、selected_candidate_id、reason。
- `candidate_trace` 中保留进入消歧的候选列表。
- 页面展示中文候选含义，不只展示英文 ID。

## 5. 测试要求

至少补充以下测试：

- 路径消歧仍然可用：源网元 / 目的网元 / 经过网元。
- 字段 owner 消歧：同一字段名在多个实体上出现时能选择正确 owner。
- 字段角色消歧：字段既可能用于过滤也可能用于返回时能正确处理。
- metric 消歧：统计表达在多个 metric 候选之间选择正确指标。
- return policy 消歧：泛化返回表达能选择正确返回策略。
- LLM 返回候选外 ID 时必须拒绝。
- LLM 返回 `clarify` 时必须输出统一澄清，不进入 planner。

## 6. 第一阶段建议范围

第一阶段不要一次性实现所有类型。建议按下面顺序推进：

1. 抽象 `SemanticDisambiguationCandidate`，先兼容现有 path 消歧。
2. 将 prompt 和输出从 `selected_path_semantic` 改为 `selected_candidate_id`。
3. 保持现有路径消歧测试全部通过。
4. 再增加 field owner 消歧。
5. 最后扩展 metric 和 return policy 消歧。
