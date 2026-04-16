# KRSS 根因分析与超时现状说明

## 目的

这份文档用于向项目成员说明两件事：

1. 当前 KRSS 是如何做根因分析的
2. 当前 KRSS / Testing Service 调用 GLM 时为什么会超时或失败

本文以当前代码和远端 `39.106.229.163` 的实际运行现象为准。

---

## 一、当前 KRSS 的真实工作流

当前 KRSS 已经不是“把整段 prompt 扔给模型，然后让模型直接写建议”的旧模式。

现在实际分成 4 步：

### 1. 构造结构化诊断上下文

代码位置：
- [services/repair_agent/app/analysis.py](/Users/mangowmac/Desktop/code/NL2Cypher/services/repair_agent/app/analysis.py)
- `build_diagnosis_context(...)`

输入：
- `IssueTicket`
- `prompt_snapshot`

输出的上下文包含：
- `question`
- `difficulty`
- `sql_pair`
  - `expected_cypher`
  - `actual_cypher`
- `evaluation_summary`
- `failure_diff`
- `relevant_prompt_fragments`
- `recent_applied_repairs`

这里最关键的是 `failure_diff`，它会先由代码判断当前失败更像是哪几类问题：
- `ordering_problem`
- `limit_problem`
- `return_shape_problem`
- `entity_or_relation_problem`
- `execution_problem`
- `syntax_problem`

也就是说，KRSS 现在不是先让模型自己从长文本里“猜问题”，而是先把失败证据整理出来。

### 2. 让模型做根因归因

代码位置：
- [services/repair_agent/app/analysis.py](/Users/mangowmac/Desktop/code/NL2Cypher/services/repair_agent/app/analysis.py)
- `KRSSAnalyzer.analyze(...)`

KRSS 现在只允许模型在 4 类知识类型里归因：
- `cypher_syntax`
- `few_shot`
- `system_prompt`
- `business_knowledge`

模型返回的不是最终投递给知识运营的请求，而是诊断结果：
- `primary_knowledge_type`
- `secondary_knowledge_types`
- `candidate_patch_types`
- `confidence`
- `rationale`
- `need_validation`

也就是说，这一步的职责是“判断问题更像是哪一类知识缺失”，而不是“直接下发修复”。

### 3. 进行轻量验证

代码位置：
- [services/repair_agent/app/service.py](/Users/mangowmac/Desktop/code/NL2Cypher/services/repair_agent/app/service.py)
- `_lightweight_experiment_runner(...)`

如果模型认为需要验证，KRSS 会做一轮轻量验证。当前不是完整重跑实验，而是检查：
- 这个 patch type 是否能解释当前 `failure_diff`
- 它是否和最近修复重复
- 当前 prompt 中是否已经存在同类内容

验证后会得到：
- `validated_patch_types`
- `rejected_patch_types`
- `validation_reasoning`
- `validation_mode`

当前 `validation_mode` 主要是：
- `lightweight`
- `disabled`

### 4. 生成并发送最终修复建议

代码位置：
- [services/repair_agent/app/service.py](/Users/mangowmac/Desktop/code/NL2Cypher/services/repair_agent/app/service.py)
- `RepairService.create_issue_ticket_response(...)`

只有在归因和验证之后，KRSS 才会生成真正发给知识运营服务的请求。

当前严格遵守既有 contract：
- `id`
- `suggestion`
- `knowledge_types`

其中 `knowledge_types` 也只能来自前面那 4 类正式值。

---

## 二、现在可以怎样一句话解释 KRSS

可以这样向别人介绍：

“现在 KRSS 会先把失败样本整理成结构化证据，再让模型在 4 类知识类型中做根因归因，然后做一次轻量验证，最后再按知识运营服务的正式接口格式下发修复建议。”

---

## 三、当前超时问题的真实原因

结论先说：

**当前超时问题不是单一由请求体太大导致的。**

请求体大小是放大因素，但不是根因本身。

### 1. 已排除的问题

以下问题已经基本排除：

- `API key` 无效
  - 远端直接请求 `https://open.bigmodel.cn/api/paas/v4/models` 能返回 `200`
- 服务器完全无法访问智谱
  - 同样因为 `/models` 可以快速成功
- KRSS 没有真正调用模型
  - 日志里已经能看到 `llm_call_started / retry / failed`

### 2. 当前真正不稳定的是 `chat/completions`

远端最近真实现象：

#### Testing Service

在 [services/testing_agent/app/clients.py](/Users/mangowmac/Desktop/code/NL2Cypher/services/testing_agent/app/clients.py) 对应的远端日志里，可以看到：

- 多次 `429`
- 错误码 `1302`
- 提示 `您的账户已达到速率限制，请您控制请求频率`

但同一个服务也出现过成功：
- `llm_call_succeeded ... elapsed_ms=64734`

说明：
- 这个端点不是永远坏
- 但经常被限流

#### KRSS

在 [services/repair_agent/app/clients.py](/Users/mangowmac/Desktop/code/NL2Cypher/services/repair_agent/app/clients.py) 对应的远端日志里，可以看到：

- 第一轮通常先卡到 `~60000ms`
- 然后 `ReadTimeout`
- 重试后有时变成 `429`
- 有时继续超时

典型模式是：
- 第 1 次：`timeout`
- 第 2 次：`429` 或继续 `timeout`
- 第 3 次：最终失败

### 3. 为什么不能简单归因成“请求体太大”

因为新版 KRSS 请求体已经被显著压缩了。

旧版日志里更多是：
- `prompt_chars`
- `compact_prompt_chars`
- `compact_ticket_chars`

而新版结构化 KRSS 请求里已经出现：
- `context_chars`
- `compact_ticket_chars`

我在远端实际观察到一条新版日志：

- `context_chars=1611`
- `compact_ticket_chars=931`

即使在这么小的规模下，KRSS 仍然出现：
- 第一轮约 `60068ms` 超时
- 第二轮约 `121101ms` 再超时
- 第三轮最终失败

这说明：

- 请求体大，确实会更容易让问题变严重
- 但即使请求体已经压小，`chat/completions` 仍然会超时

所以“请求体太大”只能算次要原因或放大因素，不能当作根因。

### 4. 当前更准确的根因判断

当前更准确的判断是：

1. 智谱 `chat/completions` 端点存在明显波动
2. 当前账号或时段存在速率限制
3. KRSS 的任务复杂度比 Testing Service 更高，更容易在 `60s` 窗口内拿不到响应

可以总结为：

**KRSS 超时的主因是上游 GLM `chat/completions` 端点当前存在高延迟和限流；请求体大小会加重问题，但已经不是决定性主因。**

---

## 四、为什么 KRSS 比 Testing Service 更容易超时

虽然两者都在调同一个大模型端点，但它们的工作内容不同。

### Testing Service 的 LLM 调用

主要是在评测失败后做语义复评。

它的请求相对更像：
- 判断当前生成是不是与 golden 一致
- 给出评测层面的语义判断

这类任务通常上下文较短，结构更固定。

### KRSS 的 LLM 调用

主要是在做：
- 根因归因
- 候选 patch 类型判断
- 修复建议生成前置分析

即使现在已经改成结构化上下文，它仍然比 Testing Service 的评测任务更复杂，模型推理时间更长，因此更容易碰到：
- 单次 `60s` 超时
- 重试后被限流

---

## 五、当前状态一句话总结

当前 KRSS 的分析逻辑已经升级成“结构化 RCA + 轻量验证”的模式，真正的瓶颈不再是本地代码逻辑，而是上游 GLM `chat/completions` 的高延迟和限流。

---

## 六、后续优化方向

如果后续继续处理这个问题，优先级建议是：

1. 调整 KRSS 的超时与总重试预算
2. 针对 `429` 增加更稳的退避策略
3. 继续压缩 `diagnosis_context`
4. 必要时考虑将 KRSS 的分析进一步拆成更轻量的两阶段请求

其中第 3 项仍然有价值，但它不应该被误认为唯一根因。
