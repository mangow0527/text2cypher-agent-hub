# testing-agent 架构与契约设计

testing-agent 是 Text2Cypher 闭环中的执行与评测服务。它接收标准答案和 cypher-generator-agent 生成结果，执行生成的 Cypher，产出可复现的评测事实，并在失败时把失败样本封装为问题单提交给 repair-agent。

一句话边界：

```text
testing-agent 证明“这次生成哪里错了”；
repair-agent 判断“是否属于 knowledge-agent 知识缺口，以及该修哪类 knowledge-agent 知识”。
```

## 1. 术语与核心数据结构

本章先定义文档中反复出现的业务术语和数据结构。后续流程与责任边界都以这些对象为基础。

### 1.1 QA 样本与 `id`

`id` 是一条 QA 样本在闭环中的主键。它贯穿 QA 生成、cypher-generator-agent、testing-agent 和 repair-agent。

同一个 `id` 下会出现两类输入：

- golden：标准答案，由题库或 QA 侧提供。
- submission：cypher-generator-agent 对同一问题生成的 Cypher 提交。

testing-agent 只有在同一个 `id` 同时具备 golden 和 submission 后，才会进入评测。

### 1.2 `QAGoldenRequest`

`QAGoldenRequest` 表示标准答案输入，也就是 testing-agent 的评测基准。

它包含：

| 字段 | 含义 |
| --- | --- |
| `id` | QA 样本主键，用于和 submission 配对 |
| `cypher` | 黄金 Cypher，代表该问题的标准查询写法 |
| `answer` | 黄金答案，代表黄金 Cypher 的期望结果 |
| `difficulty` | 题目难度，取值为 `L1` 到 `L8` |

### 1.3 cypher-generator-agent -> testing-agent 契约：`GeneratedCypherSubmissionRequest`

`GeneratedCypherSubmissionRequest` 表示 cypher-generator-agent 提交给 testing-agent 的一次 Cypher 生成物和生成证据。它不是 testing-agent 单方面定义的内部对象，而是 cypher-generator-agent 与 testing-agent 之间的跨服务契约，必须与 `services/cypher_generator_agent/docs/cypher-generator-agent-design.md` 中的“Step 8: 提交 testing-agent”保持一致。

这个契约的边界是：

- cypher-generator-agent 只提交生成结果和生成证据。
- testing-agent 负责接收、持久化、分配 `attempt_no`、执行和评测。
- cypher-generator-agent 不记录、不推断、不提交 `attempt_no`。
- cypher-generator-agent 不承担落盘职责。运行中心需要回放的生成证据必须随本契约提交给 testing-agent，由 testing-agent 持久化并对外查询。

它包含：

| 字段 | 含义 |
| --- | --- |
| `id` | 问题标识，用于 testing-agent 与 golden answer 对齐 |
| `question` | 原始自然语言问题，供评测和 issue ticket 使用 |
| `generation_run_id` | cypher-generator-agent 本次执行标识，供问题追踪和证据串联 |
| `generated_cypher` | cypher-generator-agent 认为可提交评测的 Cypher |
| `input_prompt_snapshot` | 最终 LLM 输入。它不参与主评测打分，主要供 repair-agent 分析 knowledge-agent 知识包、few-shot 或上下文是否诱发失败；其中 cypher-generator-agent 生成调用协议是固定系统包装，不作为 repair-agent 修复目标 |
| `last_llm_raw_output` | 最后一次生成尝试的大模型原始输出。它用于运行中心回放，不参与 testing-agent 主评测打分 |
| `generation_retry_count` | cypher-generator-agent 为得到可提交 Cypher 额外重试的次数。首次生成即通过时为 `0` |
| `generation_failure_reasons` | 本次生成过程中的历史失败原因列表，只保存固定 reason，不保存每轮完整 prompt |

`attempt_no` 不属于 cypher-generator-agent 的职责。cypher-generator-agent 是单纯的生成服务，不记录“这是第几次尝试”。testing-agent 在接收 submission 后，根据同一 `id` 的历史记录分配并维护 `attempt_no`，并在后续 attempt 存储、IssueTicket 和改进评估中使用它。

testing-agent 接收该请求后，会向 cypher-generator-agent 返回接收回执：

```json
{
  "accepted": true
}
```

### 1.3.1 cypher-generator-agent -> testing-agent 生成失败上报契约

当 cypher-generator-agent 没有得到可提交评测的 `generated_cypher` 时，也需要把生成失败事实提交给 testing-agent。这样 testing-agent 才能成为运行中心回放的统一数据源，而不是让运行中心分别读取多个服务的临时日志。

该契约独立于成功 submission，命名为 `GenerationRunFailureReport`。

字段：

| 字段 | 含义 |
| --- | --- |
| `id` | 问题标识，用于和 golden answer、运行中心任务对齐 |
| `question` | 原始自然语言问题 |
| `generation_run_id` | cypher-generator-agent 本次执行标识 |
| `input_prompt_snapshot` | 最后一次生成尝试使用的完整 LLM 输入快照 |
| `last_llm_raw_output` | 最后一次生成尝试的大模型原始输出；如果模型调用从未成功，可为空 |
| `generation_status` | `generation_failed` 或 `service_failed` |
| `failure_reason` | 固定失败原因，来自 cypher-generator-agent 的 `GenerationFailure.reason` 或 `ServiceFailure.reason` |
| `last_generation_failure_reason` | 如果最终原因是 `generation_retry_exhausted`，记录最后一次可重试生成失败原因 |
| `generation_retry_count` | 额外重试次数 |
| `generation_failure_reasons` | 历史失败原因列表，只保存 reason，不保存每轮完整 prompt |
| `parsed_cypher` | 最后一次可解析出的 Cypher；如果没有可识别 Cypher，可为空 |
| `gate_passed` | 最后一次生成门禁是否通过；生成失败场景通常为 `false` |

testing-agent 接收该报告后，必须先按 `generation_status` 区分工程失败与生成失败：

- 工程失败：`service_failed` 表示本轮没有完成一次有效生成尝试，例如 `knowledge_context_unavailable`、`model_invocation_failed`。testing-agent 只落盘为生成阶段工程失败，不构造正式评测 attempt，不进入 grammar、TuGraph execution、strict compare、semantic review 或 repair-agent。`testing_agent_submission_failed` 表示 cypher-generator-agent 到 testing-agent 的同步投递失败；该失败不应丢失，cypher-generator-agent 必须把待提交 payload 保存在本地可靠投递 outbox，并在 testing-agent 恢复后继续补投。testing-agent 自身只在补投成功送达后负责落盘。
- 生成失败：`generation_failed` 表示 LLM 已经基于当前 prompt 尝试生成，但输出没有通过 cypher-generator-agent 门禁，例如 `no_cypher_found`、`wrapped_in_markdown`、`contains_explanation`、`multiple_statements`、`unbalanced_brackets`、`unclosed_string`、`write_operation`、`unsupported_call`、`unsupported_start_clause`。testing-agent 必须把它转成一次正式失败 attempt，进入 testing 流程评分。
- `generation_retry_exhausted` 是生成失败的汇总状态，应结合 `last_generation_failure_reason` 与 `generation_failure_reasons` 展示和判断。

生成失败 attempt 的评分边界：

1. testing-agent 为本轮分配 `attempt_no`，并保存 generation evidence。
2. testing-agent 从 `parsed_cypher` 与 `last_llm_raw_output` 派生 `candidate_query_text`：优先使用非空 `parsed_cypher`；如果没有可解析 Cypher，则使用最后一次原始输出作为可回放和相似度比较文本；如果原始输出也为空，则使用空字符串。
3. `grammar.score = 0`。如果 `candidate_query_text` 非空，可以保留 parser 原始错误；如果为空或根本没有可识别 Cypher，则 `parser_error` 可以为空，`grammar.message` 必须说明失败来自 cypher-generator-agent 门禁而不是数据库执行。
4. 不进入 TuGraph execution、strict compare 或 semantic review，因此 `execution_accuracy.score = 0`，reason 为 `grammar_failed`。
5. 仍然计算 `secondary_signals.gleu` 与 `secondary_signals.jaro_winkler_similarity`，比较对象是 `candidate_query_text` 与 `gold_cypher`。这两个指标不影响 verdict，但必须作为 repair-agent 判断“是否缺少知识、是否方向接近”的证据保留。
6. 最终 `verdict = fail`，testing-agent 生成 IssueTicket，并把生成失败原因、最后原始输出、重试次数、历史失败原因和 secondary signals 一起写入问题单。

### 1.4 `EvaluationSummary`

`EvaluationSummary` 表示 testing-agent 对一次 `generated_cypher` 的评测结论。

它包含：

| 字段 | 含义 |
| --- | --- |
| `verdict` | 最终评测结论：`pass` 或 `fail` |
| `primary_metrics` | 主评测指标，当前固定包含 `grammar` 和 `execution_accuracy` |
| `secondary_signals` | 必保留的辅助证据，当前固定包含 `gleu` 和 `jaro_winkler_similarity` |

### 1.5 `IssueTicket`

`IssueTicket` 表示 testing-agent 提交给 repair-agent 的失败问题单。

它只在最终 `verdict` 不是 `pass` 时生成。

它包含：

| 字段 | 含义 |
| --- | --- |
| `ticket_id` | 问题单唯一 ID |
| `id` | QA 样本主键 |
| `difficulty` | 题目难度 |
| `question` | 原始自然语言问题 |
| `expected` | 黄金 Cypher 与黄金答案 |
| `actual` | cypher-generator-agent 生成 Cypher 或生成失败时的候选文本，与实际执行结果 |
| `evaluation` | testing-agent 生成的评测结论。来源是本次评测流程中的执行结果、规则评测结果和按流程执行的 LLM 复评结果 |
| `generation_evidence` | cypher-generator-agent 生成过程证据。来源是 cypher-generator-agent 提交的 `GeneratedCypherSubmissionRequest` 或 `GenerationRunFailureReport`，testing-agent 只负责保存、关联 attempt，并写入问题单 |

### 1.6 `GenerationEvidence`

`GenerationEvidence` 表示 cypher-generator-agent 生成过程在问题单中的证据快照。

它的来源是 cypher-generator-agent 提交给 testing-agent 的 `GeneratedCypherSubmissionRequest` 或 `GenerationRunFailureReport`。testing-agent 不重新生成这些字段，只在接收时保存，并在生成 IssueTicket 时复制到 `generation_evidence`。

它包含：

| 字段 | 含义 |
| --- | --- |
| `generation_run_id` | cypher-generator-agent 本次生成运行 ID，用于把问题单与生成链路日志、prompt snapshot 串联起来 |
| `attempt_no` | testing-agent 记录的尝试序号。它不是 cypher-generator-agent 提交的字段，而是 testing-agent 接收 submission 后按同一 `id` 的历史记录分配 |
| `input_prompt_snapshot` | prompt 快照，用于 repair-agent 分析 knowledge-agent 知识包、few-shot 和上下文是否诱发生成失败；cypher-generator-agent 固定生成协议部分不属于业务修复目标 |
| `last_llm_raw_output` | 最后一次生成尝试的大模型原始输出 |
| `generation_status` | `generated` 或 `generation_failed`；`service_failed` 只存在于 generation failure 落盘记录，不进入 IssueTicket |
| `failure_reason` | 生成失败的固定原因；成功 submission 为空 |
| `generation_retry_count` | cypher-generator-agent 额外重试次数 |
| `generation_failure_reasons` | 历史生成失败原因列表 |

### 1.7 `ImprovementAssessment`

`ImprovementAssessment` 表示 testing-agent 对多轮尝试的改进评估。

它只在同一个 `id` 下存在相邻两轮 attempt 时生成。它不是单次评测指标，也不进入 IssueTicket。

它包含：

| 字段 | 含义 |
| --- | --- |
| `qa_id` | 被比较的 QA 样本 ID |
| `current_attempt_no` | 当前轮 attempt 编号，由 testing-agent 分配 |
| `previous_attempt_no` | 被比较的上一轮 attempt 编号。首轮时为空 |
| `summary_zh` | 面向控制台展示的中文摘要 |
| `metrics` | 主评测指标与辅助信号的前后变化 |
| `highlights` | 从前后两轮 evidence 差异中提取的变化摘要 |
| `evidence` | 前后两轮评测 evidence 的截断集合，最多保留 6 条 |

## 2. 主流程

### 2.1 总览

![Testing-agent 总览流程](/Users/mangowmac/Desktop/code/NL2Cypher/services/testing_agent/docs/diagrams/testing-agent-overview-flow.svg)

### 2.2 评测主链路

testing-agent 的评测从“同一 `id` 下 golden 与可评分生成结果都到齐”开始。可评分生成结果包括成功的 `GeneratedCypherSubmissionRequest`，也包括 `generation_status = generation_failed` 的 `GenerationRunFailureReport`。`service_failed` 只落盘，不进入正式评测。

![Testing-agent 评测主链路](/Users/mangowmac/Desktop/code/NL2Cypher/services/testing_agent/docs/diagrams/testing-agent-evaluation-flow.svg)

每一步的意义：

| 步骤 | 输入 | 输出 | 意义 |
| --- | --- | --- | --- |
| 配对 | `QAGoldenRequest`、`GeneratedCypherSubmissionRequest` 或可评分 `GenerationRunFailureReport` | ready pair | 确保评测同时拥有标准答案和生成结果；工程失败只落盘，不配对评测 |
| grammar check | `generated_cypher` 或生成失败派生出的 `candidate_query_text` | grammar result | 先用 parser 或生成失败原因判定 query 是否是合法 Cypher/GQL 句子 |
| 执行 | `generated_cypher` | actual execution result | 在 grammar 通过后，用真实执行事实替代纯文本猜测 |
| strict compare | question、golden、actual_answer | strict_check、evidence | 生成可复现、可解释的规则类结果比较 |
| semantic check | strict compare 失败样本与语义材料 | semantic_check | 处理 strict compare 可能误判的语义等价情况 |
| 改进评估 | 当前轮与上一轮 `EvaluationSummary` | `ImprovementAssessment` | 比较修复后生成结果是否相对上一轮变好，不引入新的 LLM 判断 |
| 失败票据 | 最终 `verdict=fail` 的评测结果 | `IssueTicket` | 把失败事实交给 repair-agent 做根因分析 |

## 3. 分步数据流

本章按 testing-agent 的运行顺序描述每一步输入、输出和意义。这里的 Step 与 cypher-generator-agent 文档中的 Step 结构保持一致，但 testing-agent 的 Step 从 golden/submission 配对开始。

### Step 1: 接收 golden

接口：

```text
POST /api/v1/qa/goldens
```

请求体结构与字段含义见 [1.2 QAGoldenRequest](#12-qagoldenrequest)。

如果 submission 尚未到达，则返回 `received_golden_only`；如果 submission 已到达，则直接进入评测，并返回评测处理结果。

### Step 2: 接收 submission

接口：

```text
POST /api/v1/evaluations/submissions
```

请求体结构与字段含义见 [1.3 GeneratedCypherSubmissionRequest](#13-cypher-generator-agent---testing-agent-契约generatedcyphersubmissionrequest)。

testing-agent 接收 submission 后立即返回接收回执；字段定义见 [1.3 GeneratedCypherSubmissionRequest](#13-cypher-generator-agent---testing-agent-契约generatedcyphersubmissionrequest)。如果同一 `id` 的 golden 已经存在，testing-agent 会在回执返回后，于内部链路继续执行评测，而不是让 cypher-generator-agent 同步等待完整评测结束。

生成失败上报也在这一阶段接收：

```text
POST /api/v1/evaluations/generation-failures
```

请求体结构与字段含义见 [1.3.1 cypher-generator-agent -> testing-agent 生成失败上报契约](#131-cypher-generator-agent---testing-agent-生成失败上报契约)。

接收规则：

1. 如果 `generation_status = service_failed`，testing-agent 只保存生成阶段工程失败，不分配正式 `attempt_no`，不进入评测主链路。
2. 如果 `generation_status = generation_failed`，testing-agent 必须保存 generation evidence，并在同一 `id` 的 golden 已存在时进入评测主链路；如果 golden 尚未到达，则等待 golden 到达后再评测。
3. 对 generation failed，testing-agent 会为本轮分配 `attempt_no`，并派生 `candidate_query_text` 供 grammar、GLEU 和 similarity 使用。

### Step 3: Grammar Check

输入：

```text
generated_cypher 或 candidate_query_text
```

处理规则：

1. 对成功 submission，testing-agent 使用 `antlr4-cypher` 作为 grammar parser，对 `generated_cypher` 做确定性的语法解析。
2. 对 generation failed，testing-agent 先派生 `candidate_query_text`：
   - `parsed_cypher` 非空时使用 `parsed_cypher`
   - 否则使用非空 `last_llm_raw_output`
   - 两者都为空时使用空字符串
3. generation failed 的 `grammar.score` 固定为 `0`。如果 `candidate_query_text` 非空，testing-agent 可以尝试 parser 并保留 parser 原始报错；如果为空或 `failure_reason` 已明确表示 `empty_output` / `no_cypher_found`，`parser_error` 可以为空，但 `grammar.message` 必须说明生成阶段未产出可提交 Cypher。
4. testing-agent 统一通过内部 `grammar_check()` adapter 调用 parser。
5. `grammar_check()` 的输入输出契约固定如下：

```json
{
  "success": true,
  "parser_error": null
}
```

或：

```json
{
  "success": false,
  "parser_error": "Unexpected token RETURN near line 1, column 18."
}
```

6. `grammar_check()` 的判断规则如下：
   - 只有当 query 被 parser 完整解析成功，且未产生语法错误时，`success = true`；
   - 只要 parser 无法完整解析整条 query，就判定 `success = false`；
   - grammar 判定不得依赖数据库执行副作用，也不得使用 LLM 参与。
7. 如果 parser 成功，则：
   - `grammar.score = 1`
   - `grammar.parser_error = null`
   - `grammar.message = null`
8. 如果 parser 失败，则：
   - `grammar.score = 0`
   - `grammar.parser_error` 保留 parser 原始报错
   - 触发 grammar explanation step，生成 `grammar.message`
9. grammar explanation step 只在 `grammar.score == 0` 且存在可解释的 parser_error 时调用；grammar 通过时不调用。generation failed 且没有可解析文本时，不强行调用 grammar explanation LLM，而是使用生成失败原因生成 `grammar.message`。

grammar explanation step 的输入输出约束：

- 输入固定为：
  - `generated_cypher` 或 `candidate_query_text`
  - `parser_error`
- 输出固定为：
  - `message`

grammar explanation step 的具体过程：

1. testing-agent 将 `generated_cypher` 与 `parser_error` 组织成固定 prompt，请 LLM 只解释语法错误，不参与 grammar 判定。
2. LLM 输出必须是 JSON，且只能包含一个字段：

### Step 6: Semantic Check

semantic check 只在以下前提同时满足时触发：

- grammar 已通过
- generated_cypher 已真实执行
- strict compare 判定未通过

它的职责不是重新跑主评测，而是在 strict compare 已失败的前提下，判断 `actual_answer` 是否仍满足 `question`。

#### 6.1 semantic review 的最小返回契约

semantic review 的最小业务结果只有两个字段：

| 字段 | 含义 |
| --- | --- |
| `judgement` | 必填，枚举值只能是 `pass` 或 `fail` |
| `reasoning` | 必填，中文解释，说明为何作出该判断 |

只有 LLM 返回内容严格满足上表结构时，semantic check 才算“成功完成”。

为了尽可能减少输出漂移，semantic review 的提示词必须明确写死：

- `judgement` 只能输出 `"pass"` 或 `"fail"`
- 不允许输出 `valid`、`invalid`、`correct`、`incorrect`、`equivalent`、`not_equivalent` 等其他同义值
- 如果模型倾向于使用其他英文词，也必须先映射为 `pass/fail` 再输出

#### 6.2 semantic review 原始返回保留

为了后续排查和规则改进，testing-agent 必须保留 semantic review 的原始返回。最少应保留：

- 原始响应字符串
- 去掉 code fence 后的文本
- JSON 解析后的 payload（如果能解析）
- `qa_id`
- `attempt_no`
- 模型名
- request id / x-request-id（如果有）
- 时间戳

这份原始返回不直接参与业务结论判定，但必须可用于后续人工回看和规则改进。

#### 6.3 semantic review 返回不合规时的处理规则

如果 semantic review 返回满足以下任一情况，则判定为“semantic review 不合规”：

- 无法解析为 JSON
- 缺少 `judgement`
- `judgement` 无法稳定归一为 `pass/fail`
- 缺少有效 `reasoning`

出现这种情况时，testing-agent 不得直接把该样本判定为 `fail`，也不得继续生成 issue ticket 或进入 repair-agent。

应执行以下规则：

1. 保留 semantic review 原始返回。
2. 将当前 attempt 标记为显式的评测阻塞状态。
3. 对外暴露“本轮执行已完成，但 semantic review 返回不合规”的事实。
4. 停止后续 repair 流程。

推荐状态名：

- `semantic_review_invalid`

如果运行中心需要更抽象的汇总语义，也可以把它归为：

- `evaluation_blocked`

但 testing-agent 自身存储层应保留更具体的 `semantic_review_invalid`。

#### 6.4 为什么不直接判 fail

semantic review 不合规表示“评测器自身没有稳定产出语义结论”，而不是“generated_cypher 已被证明错误”。

因此它属于：

- 评测器错误

而不是：

- 业务样本失败

把它直接记为 `fail` 会污染：

- 失败率统计
- repair 触发率
- 实验中的能力边界结论

#### 6.5 对实验与运行中心的影响

当出现 `semantic_review_invalid` 时：

- 运行中心应能看到 TuGraph 执行已成功
- strict compare 已失败
- semantic review 返回不合规
- 原始 semantic review 返回已保留

这样后续可以按真实原始返回做补漏，而不是重新猜测模型当时输出了什么。

```json
{
  "message": "..."
}
```

3. testing-agent 解析 LLM 输出：
   - 如果成功解析 JSON，且 `message` 为非空字符串，则写入 `grammar.message`；
   - 否则视为该步骤失败。
4. explanation step 调用失败、超时、返回非 JSON 或缺少 `message` 时，testing-agent 应将本次评测视为服务异常，不能改写为 `parser_error` 或继续输出正式评测结果。

中文提示词约束：

- 提示词必须明确该步骤只负责解释语法错误，不参与 grammar 判定；
- 提示词输入必须至少包含 `generated_cypher` 与 `parser_error`；
- 提示词必须要求模型忠实于 parser 原始报错，不扩展到 schema、业务语义或修复建议；
- 提示词必须要求模型输出 JSON，且只能包含 `message` 一个字段。

调用边界：

- 该步骤只负责解释“哪里语法不合法、为什么 parser 无法接受”。
- 该步骤不参与 grammar 判定，`grammar.score` 仍然只由 parser 决定。
- 该步骤不提供修复建议，不扩展到 schema、业务语义或 query correctness 判断。

输出结构与字段含义见 [5.3 grammar 指标](#53-grammar-指标)。

### Step 4: 执行 generated Cypher

输入：

```text
generated_cypher
TuGraph connection settings
grammar.score
```

这一阶段产生真实执行结果，至少包括：

- `success`
- `rows`
- `row_count`
- `error_message`
- `elapsed_ms`

执行事实结构如下：

```json
{
  "success": true,
  "rows": [...],
  "row_count": 3,
  "error_message": null,
  "elapsed_ms": 12
}
```

字段含义：

- `success`
  - 表示数据库是否成功执行 `generated_cypher`
  - 取值为 `true` 或 `false`
- `rows`
  - 成功执行时返回的完整结果集
  - 失败时为 `null`
- `row_count`
  - 实际返回结果的顶层行数
  - 成功执行时等于 `rows` 的行数
  - 失败时为 `null`
- `error_message`
  - 执行失败时的原始错误信息
  - 成功执行时为 `null`
- `elapsed_ms`
  - 本次执行耗时，单位毫秒
  - 仅作为执行事实保留，不参与 `verdict` 汇总

采集规则：

1. 如果 `grammar.score == 0`，该步骤直接跳过，不产生执行事实。
2. 如果 grammar 通过，testing-agent 执行 `generated_cypher`，并记录本次执行是否成功。
3. 执行成功时：
   - 记录完整 `rows`
   - 记录 `row_count`
   - `error_message = null`
4. 执行失败时：
   - `rows = null`
   - `row_count = null`
   - 记录原始 `error_message`
   - 本轮 submission 状态更新为 `tugraph_execution_failed`，不继续 strict compare、semantic review 或 IssueTicket 投递。
5. 无论成功还是失败，都应记录 `elapsed_ms`，用于后续运行分析与问题排查。

消费位置：

- `success` 与 `error_message`
  - 用于决定 `strict_check.status` 是否为 `not_run`
  - 用于决定 `execution_accuracy.reason` 是否为 `execution_failed`
- `rows` 与 `row_count`
  - 用于 Step 5 的 strict compare
  - 用于 `strict_check.evidence.actual_answer`
  - 用于 `IssueTicket.actual.execution`
- `elapsed_ms`
  - 用于执行链路观测与排障
  - 当前不进入正式评测指标

这些执行事实用于运行排查。当前实现中 TuGraph 执行失败是 testing-agent 自身执行阶段失败，会保存为 `tugraph_execution_failed` 状态，不构造正式 `EvaluationSummary` 或 `IssueTicket`。

### Step 5: 结果规范化与 strict compare

输入：

```text
question
expected_cypher
expected_answer
actual execution result
```

处理规则：

1. 该步骤仅在 Step 4 成功返回 `actual_answer` 后执行。
2. testing-agent 对 `golden_answer` 和 `actual_answer` 做结果规范化，并进行 strict compare。
3. strict compare 失败时，生成完整 `evidence`，包含：
   - `golden_answer`
   - `actual_answer`
   - `diff`

结果规范化对象：

- `golden_answer`
- `actual_answer`

testing-agent 在 strict compare 前，先把这两个结果集规范化到可比较的统一表示。这里的规范化对象是执行结果，不是 query 文本；它与 `secondary_signals` 中用于计算 GLEU / Jaro-Winkler 的 query text normalization 不是一回事。

结果规范化规则：

1. 顶层结果统一视为 row list。
2. 每一行统一视为结构化 row object，不保留数据库驱动私有包装。
3. 数值类型按比较规则做轻量统一：
   - `1` 与 `1.0` 视为等价；
   - 不把字符串 `"1"` 与数值 `1` 视为等价。
4. `null`、布尔值、字符串、数组、对象都保留原有语义结构，不做文本拼接式比较。
5. 如果字段值本身是合法 JSON 字符串，不自动提升为对象；只有在比较策略明确要求时才做额外解析。当前设计默认按结果原始结构比较。
6. 图实体（如 node / relationship / path）如果由数据库驱动返回复杂对象，应先转成稳定、可序列化的结构化表示，再进入 compare。
7. 规范化的目标是消除驱动返回形式和轻量类型噪声，而不是改写结果语义。

图实体的稳定表示约定如下：

1. node 统一规范化为：

```json
{
  "__type__": "node",
  "labels": ["LabelA", "LabelB"],
  "properties": {
    "k1": "v1",
    "k2": 2
  }
}
```

约束：
- `labels` 按稳定顺序排序；
- `properties` 的 key 按稳定顺序排列；
- 不依赖驱动对象的内存地址、对象类型名或非业务字段；
- 默认不把数据库内部 id 作为比较字段，除非题目显式要求返回该 id。

2. relationship 统一规范化为：

```json
{
  "__type__": "relationship",
  "rel_type": "CONNECTED_TO",
  "properties": {
    "distance": 12
  }
}
```

约束：
- `rel_type` 保留关系类型；
- `properties` 的 key 按稳定顺序排列；
- 默认不比较起点/终点的内部数据库 id，避免把执行环境细节误当成结果语义。

3. path 统一规范化为：

```json
{
  "__type__": "path",
  "nodes": [
    { "...normalized node..." },
    { "...normalized node..." }
  ],
  "relationships": [
    { "...normalized relationship..." }
  ]
}
```

约束：
- `path` 中 `nodes` 与 `relationships` 保留原始路径顺序；
- path 内部节点和关系分别先按本节规则完成规范化；
- path 的比较同时要求长度一致与每一段内容一致。

4. 如果数据库驱动返回的是已展平的普通对象，而不是专门的图实体对象，则按普通 row object 处理，不额外提升为 node / relationship / path。

strict compare 规则：

1. 如果 `order_sensitive = false`，则按无序结果集比较：
   - 忽略行顺序；
   - 保留重复行数量；
   - 比较 `golden_answer` 与 `actual_answer` 的规范化后 multiset 是否一致。
2. 如果 `order_sensitive = true`，则按有序结果集比较：
   - 行内容必须一致；
   - 行顺序也必须一致。
3. `order_sensitive` 的判断依据如下：
   - `gold_cypher` 包含 `ORDER BY` 时，默认视为顺序敏感；
   - 或题目元数据显式要求顺序敏感；
   - 否则默认不要求顺序。
4. strict compare 通过时：
   - `strict_check.status = pass`
   - `strict_check.evidence = null`
5. strict compare 失败时：
   - `strict_check.status = fail`
   - 生成完整 `evidence`
   - 在 `diff` 中给出：
     - `missing_rows`
     - `unexpected_rows`
     - `order_mismatch`

`diff` 的生成逻辑：

1. `missing_rows`
   - 表示规范化后的 `golden_answer` 中存在、但 `actual_answer` 中缺失的结果行。
2. `unexpected_rows`
   - 表示规范化后的 `actual_answer` 中存在、但 `golden_answer` 中没有的结果行。
3. `order_mismatch`
   - 只在 `order_sensitive = true` 时有意义；
   - 如果结果内容一致但顺序不一致，则记为 `true`；
   - 否则记为 `false`；
   - 如果本次比较本来就不要求顺序，则记为 `null`。
4. 在无序比较下，`missing_rows` 与 `unexpected_rows` 反映的是内容集合差异；
5. 在有序比较下，如果内容相同但顺序不同，则应优先通过 `order_mismatch` 表达，而不是把顺序差异误写成内容缺失。

`strict_check.message` 的生成规则：

- 如果 strict compare 通过，则 `strict_check.message = null`。
- 如果 strict compare 失败，则由 testing-agent 规则层生成简洁解释文本，例如：
  - 行顺序不一致
  - 结果行数不一致
  - 返回字段或字段值不一致
- 该字段不使用 LLM 生成。

`strict_check` 的正式结构与 `evidence` 约束见 [5.4.2 strict_check](#542-strict_check) 和 [5.4.3 strict_check.evidence](#543-strict_checkevidence)。

### Step 6: semantic review

执行条件：

```text
grammar.score == 1
strict_check.status == fail
```

输入材料：

- `question`
- `gold_cypher`
- `gold_answer`
- `generated_cypher`
- `actual_answer`
- `strict_check.message`
- `strict_check.evidence.diff`

输出约束：

- `semantic_check.status`
- `semantic_check.message`
- `semantic_check.raw_output`

semantic review 的具体过程：

1. testing-agent 将 `question`、`gold_cypher`、`gold_answer`、`generated_cypher`、`actual_answer` 以及 strict compare 的失败材料组织成固定 prompt，请 LLM 判断 `actual_answer` 是否仍然满足用户问题。
2. LLM 输出必须是 JSON，且至少包含：

```json
{
  "judgement": "pass",
  "reasoning": "..."
}
```

其中：

- `judgement` 只能是 `"pass"` 或 `"fail"`
- 不允许输出第三状态
- 不允许输出 `valid`、`invalid`、`correct`、`incorrect` 等同义词
- 如果无法判断，也必须在 `pass/fail` 二选一中输出最接近的业务结论

3. testing-agent 解析 LLM 输出：
   - 如果成功解析 JSON，且 `judgement` 为 `pass` 或 `fail`，则映射到 `semantic_check.status`；
   - 如果 `reasoning` 为非空字符串，则写入 `semantic_check.message`；
   - 原始 JSON 保留为 `semantic_check.raw_output`。
4. semantic review 调用失败、超时、返回非 JSON，或缺少合法 `judgement` 时，testing-agent 应将本次评测视为服务异常，而不是继续输出降级后的正式评测结果。

中文提示词约束：

- 提示词必须明确该步骤只在 strict compare 已失败的前提下，判断 `actual_answer` 是否仍满足 `question`；
- 提示词输入必须至少包含 `question`、`gold_cypher`、`gold_answer`、`generated_cypher`、`actual_answer`、`strict_check.message` 和 `strict_diff`；
- 提示词必须要求模型只判断语义满足性，不提供修复建议，不讨论模型行为、prompt 设计或 schema 问题；
- 提示词必须要求模型输出 JSON，且至少包含 `judgement` 与 `reasoning` 两个字段；
- 提示词必须明确 `judgement` 的合法枚举值只能是 `pass` 和 `fail`，并显式禁止输出其他判定词。

调用边界：

- semantic review 只在 strict compare 失败后执行，不在 grammar 失败或执行失败时执行。
- semantic review 只影响 `execution_accuracy.score` 的最终汇总，不产生独立于正式评测之外的新 verdict。
- 如果该步骤满足触发条件，则 semantic reviewer 必须可用；否则 testing-agent 不能继续完成本次正式评测。

`semantic_check` 的正式结构、输入输出约束和边界见 [5.4.4 semantic_check](#544-semantic_check)。

### Step 7: 组装 EvaluationSummary 并投递 IssueTicket

在这一步，testing-agent 会完成三件事：

1. 汇总 `grammar`、`execution_accuracy`
2. 计算 `secondary_signals`，即 `GLEU` 与 `Jaro-Winkler Similarity`
3. 组装最终 `EvaluationSummary`

`secondary_signals` 的计算不依赖 grammar 通过。即使 generation failed 或 grammar failed，testing-agent 也必须基于当前 attempt 的 query 文本计算 GLEU 与 Jaro-Winkler similarity：

- 成功 submission 使用 `generated_cypher`
- generation failed 使用 `candidate_query_text`

这样 repair-agent 即使面对“无法执行”的失败，也能看到生成结果与 golden Cypher 的表面接近程度，辅助判断是否可能缺少知识、schema 映射错误或只是格式门禁失败。

最终 `EvaluationSummary` 结构见第 5 章。顶层 `verdict` 规则为：

```text
if grammar.score == 0:
    verdict = fail
elif execution_accuracy.score == 1:
    verdict = pass
else:
    verdict = fail
```

如果最终 `verdict` 为 `fail`，testing-agent 生成 `IssueTicket`，保存到本地问题单存储，并投递给 repair-agent。

### Step 8: 生成 ImprovementAssessment

`ImprovementAssessment` 只在当前 `attempt_no` 存在上一轮时生成，也就是只有当同一 `id` 下已经存在 `attempt_no - 1` 的评测结果时，testing-agent 才执行这一步。

输入：

```text
current EvaluationSummary
previous EvaluationSummary
current attempt_no
previous attempt_no
```

处理规则：

1. 不引入新的 LLM 判断，也不重新执行 semantic review。
2. 只比较前后两轮已经产出的正式评测结果。
3. 比较项固定为：
   - `grammar.score`
   - `execution_accuracy.score`
   - `secondary_signals.gleu.score`
   - `secondary_signals.jaro_winkler_similarity.score`
4. 判定规则固定为：
   - `grammar.score`：`0 -> 1` 记为 `improved`，`1 -> 0` 记为 `regressed`，相同为 `unchanged`
   - `execution_accuracy.score`：`0 -> 1` 记为 `improved`，`1 -> 0` 记为 `regressed`，相同为 `unchanged`
   - `gleu.score`：数值升高为 `improved`，降低为 `regressed`，不变为 `unchanged`
   - `jaro_winkler_similarity.score`：数值升高为 `improved`，降低为 `regressed`，不变为 `unchanged`
5. 如果缺少上一轮结果，则该步骤跳过，不生成 `ImprovementAssessment`。

正式结构见 [1.7 ImprovementAssessment](#17-improvementassessment)。这一步的目标只是回答“这一轮是否比上一轮更好”，而不是发明一套独立于正式评测之外的新标准。

## 4. 对外接口

本章说明 testing-agent 对外暴露的正式接口。主流程和分步数据流先定义服务如何工作；本章只保留目标架构下仍属于 testing-agent 职责边界的接口。

### 4.1 提交 golden

`POST /api/v1/qa/goldens`

请求体为 `QAGoldenRequest`，字段定义见 [1.2 QAGoldenRequest](#12-qagoldenrequest)。

如果同一 `id` 的 submission 尚未到达，则返回 `received_golden_only`；如果 submission 已经到达，testing-agent 会继续进入评测流程，并返回评测处理结果。

### 4.2 提交 generated Cypher

`POST /api/v1/evaluations/submissions`

请求体为 `GeneratedCypherSubmissionRequest`，字段定义见 [1.3 cypher-generator-agent -> testing-agent 契约](#13-cypher-generator-agent---testing-agent-契约generatedcyphersubmissionrequest)。

响应字段定义与 [1.3 cypher-generator-agent -> testing-agent 契约](#13-cypher-generator-agent---testing-agent-契约generatedcyphersubmissionrequest) 中的接收回执一致。它是 cypher-generator-agent 的提交确认，不是 qa-agent 的业务响应。

如果 golden 已经到达，testing-agent 会继续进入评测流程。通过样本最终进入 `passed` 状态；失败样本最终进入 `issue_ticket_created` 状态并关联 `issue_ticket_id`。

### 4.3 查询评测状态

`GET /api/v1/evaluations/{id}`

返回该 `id` 下 testing-agent 已保存的 golden、submission、执行结果、问题单和 repair-agent 响应摘要。它是 testing-agent 的评测状态查询接口，不承担运行中心的跨服务聚合职责。

### 4.4 查询问题单

`GET /api/v1/issues/{ticket_id}`

返回指定 `IssueTicket`。该接口用于回放失败事件和排查 repair-agent 输入，不用于生成新的修复建议。

### 4.5 健康与服务状态

`GET /health`

用于服务存活检查。

```json
{
  "status": "ok",
  "service": "testing-agent"
}
```

`GET /api/v1/status`

用于排查 testing-agent 当前运行配置、存储路径、repair-agent 连接配置和 LLM 复评配置。它不返回跨服务运行中心视图，也不暴露 `/api/v1/runtime/*` 能力。

## 5. 评测结果模型与诊断契约

本章集中说明 testing-agent 的正式输出结构、字段生成规则，以及向 repair-agent 传递失败事实时使用的问题单契约。

### 5.1 正式输出结构

testing-agent 对外只输出一个最终结论 `verdict`，不再区分 `strict_verdict`、`final_status` 或 `partial_fail`。

正式结果结构如下：

```json
{
  "verdict": "pass",
  "primary_metrics": {
    "grammar": {
      "score": 1,
      "parser_error": null,
      "message": null
    },
    "execution_accuracy": {
      "score": 1,
      "reason": "semantic_equivalent",
      "strict_check": {
        "status": "fail",
        "message": "The actual result does not strictly match the golden answer because the output field names differ.",
        "order_sensitive": false,
        "expected_row_count": 1,
        "actual_row_count": 1,
        "evidence": {
          "golden_answer": [
            {
              "count": 5
            }
          ],
          "actual_answer": [
            {
              "total": 5
            }
          ],
          "diff": {
            "missing_rows": [
              {
                "count": 5
              }
            ],
            "unexpected_rows": [
              {
                "total": 5
              }
            ],
            "order_mismatch": false
          }
        }
      },
      "semantic_check": {
        "status": "pass",
        "message": "虽然字段名不同，但结果仍然回答了用户提出的计数问题。",
        "raw_output": {
          "judgement": "pass",
          "reasoning": "The result uses a different output field name but preserves the requested count."
        }
      }
    }
  },
  "secondary_signals": {
    "gleu": {
      "score": 0.77,
      "tokenizer": "cypher_tokenizer_v1",
      "min_n": 1,
      "max_n": 4
    },
    "jaro_winkler_similarity": {
      "score": 0.89,
      "normalization": "query_text_lightweight_v1",
      "library": "rapidfuzz"
    }
  }
}
```

字段分层含义：

| 字段 | 含义 |
| --- | --- |
| `verdict` | testing-agent 的最终通过/失败结论 |
| `primary_metrics.grammar` | parser 驱动的 grammar 判定结果 |
| `primary_metrics.execution_accuracy` | 最终 EX 指标，内部包含 strict_check 与 semantic_check |
| `secondary_signals` | 必须保留的辅助证据，不参与 `verdict` 汇总 |

### 5.2 verdict 汇总规则

顶层 `verdict` 只依赖 `grammar.score` 与 `execution_accuracy.score`：

```text
if grammar.score == 0:
    verdict = fail
elif execution_accuracy.score == 1:
    verdict = pass
else:
    verdict = fail
```

其中：

- `grammar.score` 负责回答“这是不是一条合法 Cypher/GQL 句子”
- `execution_accuracy.score` 负责回答“这条 query 的执行结果最终是否满足用户问题”

### 5.3 grammar 指标

`grammar` 的正式结构如下：

```json
{
  "score": 0,
  "parser_error": "Unexpected token RETURN near line 1, column 18.",
  "message": "RETURN 子句出现在不合法的位置，当前查询在完成 MATCH 模式前就提前结束了。"
}
```

字段含义：

| 字段 | 来源 | 含义 |
| --- | --- | --- |
| `score` | grammar check function / parser | `0/1`，是否通过 grammar 检查 |
| `parser_error` | parser 原始输出 | grammar 失败时的原始报错 |
| `message` | grammar explanation step | 更适合外部和 repair-agent 消费的解释文本 |

生成时机、explanation step 的输入输出约束和调用边界见 [Step 3: Grammar Check](#step-3-grammar-check)。

### 5.4 execution_accuracy 指标

`execution_accuracy` 是 testing-agent 的最终 EX 指标。它不再等同于“纯 strict compare 结果”，而是把 semantic review 纳入 EX 的计算过程。

正式结构：

```json
{
  "score": 1,
  "reason": "semantic_equivalent",
  "strict_check": { ... },
  "semantic_check": { ... }
}
```

字段含义：

| 字段 | 来源 | 含义 |
| --- | --- | --- |
| `score` | strict_check + semantic_check 汇总 | `0/1`，最终 EX |
| `reason` | 汇总逻辑 | 最终 EX 的原因 |
| `strict_check` | 规则类比较逻辑 | strict compare 的结果与证据 |
| `semantic_check` | LLM semantic review | strict compare 失败后的语义校验结果 |

#### 5.4.1 execution_accuracy.score 汇总规则

```text
if grammar.score == 0:
    execution_accuracy.score = 0
    reason = grammar_failed
elif strict_check.status == pass:
    execution_accuracy.score = 1
    reason = strict_equal
elif semantic_check.status == pass:
    execution_accuracy.score = 1
    reason = semantic_equivalent
elif strict_check.status == not_run:
    execution_accuracy.score = 0
    reason = execution_failed
else:
    execution_accuracy.score = 0
    reason = not_equivalent
```

`reason` 允许的取值：

| 取值 | 含义 |
| --- | --- |
| `strict_equal` | strict compare 直接通过，因此 EX=1 |
| `semantic_equivalent` | strict compare 未通过，但 semantic review 通过，因此 EX=1 |
| `grammar_failed` | grammar 未通过，因此 EX=0 |
| `execution_failed` | query 执行失败，因此 EX=0 |
| `not_equivalent` | strict compare 与 semantic review 都未通过，因此 EX=0 |

#### 5.4.2 strict_check

`strict_check` 是规则类结果比较模块，不使用 LLM。正式结构：

```json
{
  "status": "fail",
  "message": "The actual result does not strictly match the golden answer because the output field names differ.",
  "order_sensitive": false,
  "expected_row_count": 1,
  "actual_row_count": 1,
  "evidence": {
    "golden_answer": [...],
    "actual_answer": [...],
    "diff": {
      "missing_rows": [...],
      "unexpected_rows": [...],
      "order_mismatch": false
    }
  }
}
```

字段含义：

| 字段 | 含义 |
| --- | --- |
| `status` | `pass` / `fail` / `not_run` |
| `message` | strict compare 的简洁解释，来源于 testing-agent 规则层 |
| `order_sensitive` | 本次结果比较是否要求顺序一致 |
| `expected_row_count` | `golden_answer` 的顶层行数 |
| `actual_row_count` | `actual_answer` 的顶层行数 |
| `evidence` | strict compare 失败时的直接证据 |

strict compare 的生成流程、结果规范化原则和 `strict_check.message` 的来源见 [Step 5: 结果规范化与 strict compare](#step-5-结果规范化与-strict-compare)。

#### 5.4.3 strict_check.evidence

`evidence` 只保留 strict compare 的直接比较证据：

```json
{
  "golden_answer": [...],
  "actual_answer": [...],
  "diff": {
    "missing_rows": [...],
    "unexpected_rows": [...],
    "order_mismatch": false
  }
}
```

约束：

1. `golden_answer` 必须保留完整内容，不能只给 preview。
2. `actual_answer` 必须保留完整内容，因为 testing-agent 是系统里唯一真正执行 generated_cypher 的地方。
3. `diff` 只包含：
   - `missing_rows`
   - `unexpected_rows`
   - `order_mismatch`
4. 不在 `evidence` 中放修复建议、comparison policy 快照、parser 报错或 LLM 解释。

#### 5.4.4 semantic_check

`semantic_check` 只在 strict compare 失败后执行。正式结构：

```json
{
  "status": "pass",
  "message": "虽然字段名不同，但结果仍然回答了用户提出的计数问题。",
  "raw_output": {
    "judgement": "pass",
    "reasoning": "The result uses a different output field name but preserves the requested count."
  }
}
```

字段含义：

| 字段 | 含义 |
| --- | --- |
| `status` | `pass` / `fail` / `not_run`；其中 `not_run` 只表示本次评测流程没有进入 semantic review，例如 grammar 失败、执行失败或 strict compare 已通过 |
| `message` | semantic review 的对外解释文本 |
| `raw_output` | LLM 的结构化原始输出 |

执行条件、输入输出、提示词约束和调用边界见 [Step 6: semantic review](#step-6-semantic-review)。

### 5.5 secondary_signals

`secondary_signals` 是必须保留并输出的辅助证据，但不参与 `verdict` 汇总。它们在 grammar failed 和 generation failed 场景下也要计算，因为这些指标是 repair-agent 判断失败是否可能来自知识缺失、schema 选择偏差或输出格式问题的重要证据。

正式结构：

```json
{
  "gleu": {
    "score": 0.77,
    "tokenizer": "cypher_tokenizer_v1",
    "min_n": 1,
    "max_n": 4
  },
  "jaro_winkler_similarity": {
    "score": 0.89,
    "normalization": "query_text_lightweight_v1",
    "library": "rapidfuzz"
  }
}
```

#### 5.5.1 GLEU

`gleu` 比较的是当前 attempt 的 query 文本与 `gold_cypher` 的 token-level 相似度。成功 submission 使用 `generated_cypher`；generation failed 使用 testing-agent 派生出的 `candidate_query_text`。

输出字段：

| 字段 | 含义 |
| --- | --- |
| `score` | `0.0 ~ 1.0` 的 GLEU 分数 |
| `tokenizer` | 当前使用的 tokenizer 名称 |
| `min_n` | 最小 n-gram 阶数 |
| `max_n` | 最大 n-gram 阶数 |

#### 5.5.2 Jaro-Winkler Similarity

`jaro_winkler_similarity` 比较的是当前 attempt 的 query 文本与 `gold_cypher` 的字符表面相似度。成功 submission 使用 `generated_cypher`；generation failed 使用 testing-agent 派生出的 `candidate_query_text`。

输出字段：

| 字段 | 含义 |
| --- | --- |
| `score` | `0.0 ~ 1.0` 的 Jaro-Winkler 分数 |
| `normalization` | 当前 query 文本轻量规范化策略名 |
| `library` | 当前使用的相似度实现库名 |

`secondary_signals` 的计算时机和生成方式见 [Step 7: 组装 EvaluationSummary 并投递 IssueTicket](#step-7-组装-evaluationsummary-并投递-issueticket)。

### 5.6 IssueTicket 契约

当 `verdict = fail` 时，testing-agent 向 repair-agent 投递 `IssueTicket`。

字段结构：

| 字段 | 内容 | 说明 |
| --- | --- | --- |
| `ticket_id` | 问题单 ID | testing-agent 生成的唯一标识 |
| `id` | QA 样本主键 | 与 golden、submission、attempt 对齐 |
| `difficulty` | 题目难度 | 直接来自 golden |
| `question` | 原始自然语言问题 | 供 repair-agent 理解用户意图 |
| `expected` | 黄金 Cypher 与黄金答案 | 评测基准 |
| `actual.generated_cypher` | 本次生成的 query | 直接来自 submission |
| `actual.execution` | Step 4 的执行事实 | 区分 grammar 失败、执行失败和结果不一致 |
| `evaluation` | 第 5 章正式输出结构 | 只包含 `verdict`、`primary_metrics` 与 `secondary_signals` |
| `generation_evidence` | 第 1.6 节定义的生成侧证据 | 供 repair-agent 做链路归因 |

## 6. 运行状态、持久化与错误处理

### 6.1 运行状态

testing-agent 使用 `EvaluationState` 表达流程状态：

| 状态 | 含义 |
| --- | --- |
| `received_golden_only` | 已收到 golden，等待 submission |
| `received_submission_only` | 已收到 submission，等待 golden |
| `ready_to_evaluate` | golden 与 submission 已具备，可评测 |
| `tugraph_execution_failed` | TuGraph 执行阶段失败，testing-agent 保存执行错误并停止本轮正式评测 |
| `semantic_review_invalid` | strict compare 失败后触发 semantic review，但 LLM 返回不合规，testing-agent 保存原始返回并停止本轮正式评测 |
| `repair_pending` | 已生成 issue ticket，准备投递 repair-agent |
| `repair_submission_failed` | ticket 已生成，但 repair-agent 投递失败 |
| `issue_ticket_created` | ticket 已成功提交 repair-agent |
| `passed` | 本次尝试评测通过 |

`EvaluationState` 说明流程走到哪里；`verdict` 说明评测结论是什么。

### 6.2 持久化与追溯

默认数据目录：

```text
data/testing_service/
```

存储结构：

| 路径 | 内容 |
| --- | --- |
| `goldens/{id}.json` | 黄金答案 |
| `submissions/{id}.json` | 最新 submission |
| `submission_attempts/{id}__attempt_{n}.json` | 按尝试编号保存的 submission |
| `generation_failures/{id}__{generation_run_id}.json` | 生成阶段失败报告；`service_failed` 的正式落点在这里 |
| `issue_tickets/{ticket_id}.json` | 问题单 |

当前系统没有正式 artifact ref 协议，因此设计文档不应虚构 `testing://` 或 `cypher-generator-agent://` 引用。

第一版追溯依赖真实主键：

```text
id
attempt_no
generation_run_id
ticket_id
```

其中 `attempt_no` 是 testing-agent 维护和归档的尝试序号。cypher-generator-agent 不记录尝试次数，也不向 testing-agent 声明尝试次数；testing-agent 是 attempt 历史记录的唯一权威来源。

如果未来需要跨服务拉取完整 rows、完整 metrics 或完整 prompt，应新增正式 API，而不是暴露内部文件路径。

### 6.3 输入冲突

- 同一 `id` 的 golden 如果内容不同，拒绝写入。
- 同一 `id + generation_run_id` 的 submission 如果内容不同，拒绝写入；`attempt_no` 由 testing-agent 在保存时分配。

### 6.4 执行失败

TuGraph 执行失败会中断本轮正式评测。testing-agent 保存执行事实，并将 submission 状态更新为：

```text
tugraph_execution_failed
```

该状态不生成 `EvaluationSummary`，不投递 `IssueTicket`，也不进入 repair-agent。

### 6.5 repair-agent 投递失败

如果 repair-agent 不可用：

```text
IssueTicket 已保存
submission 标记 repair_submission_failed
异常向上抛出
```

这一区分了：

```text
评测失败
问题单已生成
问题单投递失败
```

后续可将 repair-agent 投递失败转为可重试状态，而不是直接表现为 API 500。

## 7. 结论

testing-agent 的核心设计应该保持克制：

```text
它负责执行、评测、证据整理和失败现象诊断；
它不负责根因归因和修复策略生成。
```

清晰的 testing-agent -> repair-agent 契约，是后续闭环稳定性的关键。
这个契约只承载失败事实与 knowledge-agent 修复所需证据，不承载生成协议、解析器或评测器实现细节。
