# Cypher Generation Service 设计文档

## Summary

`Cypher Generation Service`（CGS，Cypher 生成服务）是 Text2Cypher 闭环中的生成执行服务。

它负责接收 `id + question`，向 `Knowledge Ops` 拉取当前正式提示词，调用大语言模型生成 Cypher，完成最小解析与守门，并将结构化生成结果提交给 `Testing Service`。

CGS 的职责边界非常明确：

- 它负责生成阶段执行
- 它不负责 TuGraph 执行
- 它不负责最终业务评测
- 它不负责知识修复建议

本文档以当前代码实现为准，整理 CGS 的正式职责、接口、状态语义和运行流程。

---

## 一、服务定义

### 1.1 服务名称

**Cypher Generation Service（Cypher 生成服务）**

### 1.2 核心职责

CGS 负责以下动作：

1. 接收问题生成请求
2. 记录问题与生成尝试编号
3. 向 `Knowledge Ops` 获取正式生成提示词
4. 调用 LLM 生成原始输出
5. 从原始输出中提取 Cypher
6. 执行最小守门检查
7. 保存提示词快照和生成证据
8. 向 `Testing Service` 提交评测请求

### 1.3 非目标

CGS 明确不承担以下职责：

- 不设计或编辑 Prompt
- 不执行 TuGraph 查询
- 不判断查询结果是否答对业务问题
- 不生成 `Issue Ticket`
- 不做知识修复归因
- 不直接修改知识库

---

## 二、职责边界

### 2.1 对外入口边界

CGS 的正式外部写入口是：

- `POST /api/v1/qa/questions`

请求体只保留最小输入：

- `id`
- `question`

CGS 不要求调用方提供提示词版本、模型覆写、知识标签或执行策略。

### 2.2 对 Knowledge Ops 的边界

CGS 通过以下接口向知识运营侧请求正式提示词：

- `POST /api/knowledge/rag/prompt-package`

请求体：

```json
{
  "id": "q-001",
  "question": "查询协议版本对应的隧道"
}
```

正式响应体规格为：

- 纯文本提示词字符串

边界原则：

- CGS 负责“获取并消费正式提示词”
- Knowledge Ops 负责“产出提示词内容”
- CGS 不对提示词内容做知识层编辑

### 2.2.1 Prompt 责任边界补充

在当前实现里，CGS 对 prompt 的责任是“获取、校验、留存、透传”，而不是“拼装、裁剪、解释、维护知识内容”。

CGS 当前会做的事情：

- 把 `id + question` 发送给 Knowledge Ops
- 接收返回的 prompt 原文
- 做最小 readiness 检查
- 把该 prompt 作为 `input_prompt_snapshot` 留存
- 在后续失败分析链路中对外提供该快照

CGS 当前不会做的事情：

- 不在本地拼接 schema、few-shot、system prompt、business knowledge
- 不在本地重写或补全 Knowledge Ops 返回的 prompt
- 不维护 prompt 版本、知识版本或知识片段来源
- 不根据解析结果反向修改 prompt 内容
- 不兼容历史 JSON `prompt` 包装格式

这条边界非常关键，因为它决定了：

- prompt 内容正确性归属 `Knowledge Ops`
- prompt 消费与生成执行归属 `CGS`
- prompt 相关修复建议应流向 `KRSS -> Knowledge Ops`，而不是回写到 CGS 本身

### 2.3 对 Testing Service 的边界

CGS 在生成阶段完成后，将结果提交到：

- `POST /api/v1/evaluations/submissions`

CGS 负责提交：

- 生成出的 Cypher
- 解析摘要
- 守门摘要
- 原始输出快照
- 输入提示词快照
- 生成运行标识与尝试编号

Testing Service 负责：

- TuGraph 执行
- 规则评测
- 可选 LLM 复评
- 问题票据生成
- 失败闭环分发

---

## 三、运行时组件

### 3.1 FastAPI 入口

文件：`services/query_generator_agent/app/main.py`

核心接口：

- `POST /api/v1/qa/questions`
- `GET /api/v1/questions/{id}`
- `GET /api/v1/questions/{id}/prompt`
- `POST /api/v1/internal/repair-plans`
- `GET /api/v1/generator/status`
- `GET /api/v1/tugraph/connection-test`

### 3.2 主编排服务

文件：`services/query_generator_agent/app/service.py`

核心对象：`QueryWorkflowService`

主入口：`ingest_question(request)`

它串起完整生成主链路：

1. 分配 `generation_run_id`
2. 计算 `attempt_no`
3. 保存问题接收状态
4. 拉取 prompt
5. 做 prompt readiness 检查
6. 调用模型
7. 解析模型输出
8. 执行最小 guardrail
9. 落盘生成结果
10. 提交给 Testing Service

### 3.3 外部客户端

文件：`services/query_generator_agent/app/clients.py`

关键客户端：

- `PromptServiceClient`
  - 负责拉取提示词
- `QwenGeneratorClient`
  - 负责统一生成调用入口
- `OpenAICompatibleCypherGenerator`
  - 当前主线 LLM 生成器
- `TestingServiceClient`
  - 负责向 Testing Service 提交评测请求

说明：

CGS 当前已经收敛为单一路径的 LLM 生成服务。`QwenGeneratorClient` 不再保留启发式生成器注入位；如果 LLM 不可用，生成阶段直接失败，而不是退回到启发式方法。

### 3.4 持久化仓库

文件：`services/query_generator_agent/app/repository.py`

`QueryGeneratorRepository` 负责保存：

- 问题记录：`questions/`
- 最新生成结果：`generation_runs/`
- 分尝试存档：`generation_attempts/`
- 修复计划回执：`repair_plan_receipts/`

---

## 四、主流程

### 4.1 生成主路径

CGS 当前主路径如下：

```text
POST /api/v1/qa/questions
  -> upsert question(status=received)
  -> fetch prompt from Knowledge Ops
  -> validate prompt readiness
  -> invoke LLM
  -> parse raw output to cypher
  -> run minimal guardrail
  -> save generation run
  -> submit evaluation payload to Testing Service
  -> return submitted_to_testing
```

### 4.2 分步流程定义

为了让设计文档能直接对应 `QueryWorkflowService.ingest_question()` 的运行时语义，CGS 的生成主流程可以拆成七个顺序步骤。

#### Step 1: Task Intake（任务接收）

输入：

- `QAQuestionRequest`

处理：

- 分配新的 `generation_run_id`
- 基于当前 `id` 计算新的 `attempt_no`
- 调用 `upsert_question(..., status="received")`

输出：

- 一个已建立上下文的生成尝试

落盘：

- `questions/{id}.json`

说明：

这一步只建立生成尝试上下文，不会生成任何 Cypher，也不会调用外部依赖。

#### Step 2: Prompt Fetch（提示词获取）

输入：

- `id`
- `question`

处理：

- 调用 `PromptServiceClient.fetch_prompt(id, question)`
- 向 `Knowledge Ops` 请求当前正式提示词

成功输出：

- `generation_prompt: str`

失败输出：

- `QueryQuestionResponse(generation_status="prompt_fetch_failed")`

失败落盘：

- `generation_runs/{id}.json`
- `generation_attempts/{id}__attempt_{n}.json`

失败语义：

- `parse_summary = "prompt_not_fetched"`
- `guardrail_summary = "prompt_fetch_failed"`
- `failure_stage = "prompt_fetch"`
- `input_prompt_snapshot = ""`

#### Step 3: Prompt Readiness Check（提示词就绪检查）

输入：

- `generation_prompt`

处理：

- 对 prompt 做最小可调用性检查
- 当前实现唯一硬性条件是：`generation_prompt.strip()` 不能为空

成功输出：

- prompt 进入可调用状态

失败输出：

- `QueryQuestionResponse(generation_status="failed")`

失败落盘：

- `generation_runs/{id}.json`
- `generation_attempts/{id}__attempt_{n}.json`

失败语义：

- `parse_summary = "prompt_empty"`
- `guardrail_summary = "prompt_not_ready"`
- `failure_stage = "prompt_readiness_check"`
- `failure_reason_summary = "Generation prompt is empty."`

补充说明：

readiness check 通过后，CGS 会额外把 `questions/{id}.json` 的状态更新为 `prompt_ready`，表示 prompt 已经可进入模型调用阶段。

#### Step 4: Model Invocation（模型调用）

输入：

- `id`
- `question`
- `generation_prompt`

处理：

- 调用 `QwenGeneratorClient.generate_from_prompt(...)`
- 当前主路径依赖 `OpenAICompatibleCypherGenerator`
- 期望返回结构中包含 `raw_output`

成功输出：

- `raw_output: str`

失败输出：

- `QueryQuestionResponse(generation_status="model_invocation_failed")`

失败落盘：

- `generation_runs/{id}.json`
- `generation_attempts/{id}__attempt_{n}.json`

失败语义：

- `parse_summary = "model_invocation_failed"`
- `guardrail_summary = "not_checked"`
- `failure_stage = "model_invocation"`
- `raw_output_snapshot = ""`

#### Step 5: Output Parsing（输出解析）

输入：

- `raw_output`

处理：

- 先去掉 Markdown fence
- 再尝试按 JSON 读取 `cypher` 字段
- 若 JSON 失败，则尝试把整段文本视为 Cypher
- 若仍失败，则扫描首个以 `MATCH` / `WITH` / `CALL` 开头的行

成功输出：

- `generated_cypher`
- `parse_summary`

失败输出：

- `QueryQuestionResponse(generation_status="output_parsing_failed")`

失败落盘：

- `generation_runs/{id}.json`
- `generation_attempts/{id}__attempt_{n}.json`

失败语义：

- `failure_stage = "output_parsing"`
- `failure_reason_summary = "Unable to parse Cypher from model output."`
- `raw_output_snapshot` 会被保留

补充说明：

当前解析摘要可能取值包括：

- `parsed_json`
- `parsed_plain_text`
- `parsed_first_query_line`
- `raw_output_empty`
- `parse_failed`

#### Step 6: Minimal Guardrail（最小守门）

输入：

- `generated_cypher`
- `parse_summary`
- `raw_output`

处理：

- 调用 `_run_minimal_guardrail(generated_cypher)`

当前 guardrail 规则只有两条：

1. Cypher 不能为空
2. 去除左侧空白后必须以 `MATCH` / `WITH` / `CALL` 开头

成功输出：

- `guardrail_summary = "accepted"`

失败输出：

- `QueryQuestionResponse(generation_status="guardrail_rejected")`

失败落盘：

- `generation_runs/{id}.json`
- `generation_attempts/{id}__attempt_{n}.json`

失败语义：

- `failure_stage = "guardrail_check"`
- `guardrail_summary` 当前可能为：
  - `empty_cypher`
  - `invalid_cypher_start`

#### Step 7: Persist And Submit（持久化并提交）

输入：

- `generated_cypher`
- `parse_summary`
- `guardrail_summary`
- `raw_output`
- `generation_prompt`

处理：

1. 先以 `generation_status="generated"` 保存一份成功生成记录
2. 构造 `EvaluationSubmissionRequest`
3. 调用 Testing Service 提交评测
4. 再构造 `generation_status="submitted_to_testing"` 的最终响应
5. 通过 `_persist_and_return(...)` 覆盖最新记录

输出：

- `QueryQuestionResponse(generation_status="submitted_to_testing")`

落盘：

- 第一次成功落盘：`generated`
- 第二次最终落盘：`submitted_to_testing`

关键说明：

这意味着当前实现里，成功路径会产生两次连续写入：

- 一次表示“生成完成”
- 一次表示“已成功提交给 Testing Service”

因此 `generation_runs/{id}.json` 的最终最新态是 `submitted_to_testing`，而 `generated` 更像提交前的中间成功状态。

### 4.3 失败分支

主流程中有四个明确失败分支：

1. `prompt_fetch_failed`
2. `failed`（prompt 为空，readiness 未通过）
3. `model_invocation_failed`
4. `output_parsing_failed`
5. `guardrail_rejected`

每个失败分支都会输出结构化 `QueryQuestionResponse`，并带上：

- `failure_stage`
- `failure_reason_summary`
- 已知范围内的上下文快照

补充说明：

虽然这里列出的是失败分支，但它们在当前实现里并不是“只返回、不存储”的临时错误，而是会进入统一的 `_persist_and_return(...)` 路径，形成可追踪的失败尝试记录。

### 4.4 提示词快照读取

KRSS 需要回看某次生成实际使用的 prompt，因此 CGS 暴露：

- `GET /api/v1/questions/{id}/prompt`

返回：

```json
{
  "id": "q-001",
  "attempt_no": 1,
  "input_prompt_snapshot": "..."
}
```

这条接口是 CGS 对 KRSS 的正式运行时支持接口。

### 4.5 尝试编号、重试与幂等语义

CGS 当前没有把“同一 `id` 的再次提交”视为网络层幂等重放，而是视为一次新的生成尝试。

#### 4.5.1 `attempt_no` 的生成规则

`attempt_no` 通过 `QueryGeneratorRepository.next_attempt_no(id)` 计算，规则是：

1. 读取 `questions/{id}.json` 中的 `latest_attempt_no`
2. 再检查 `generation_runs/{id}.json`
3. 再扫描 `generation_attempts/{id}__attempt_*.json`
4. 取当前已知最大值 `+1`

这意味着：

- 同一 `id` 每次重新提交，都会得到新的 `attempt_no`
- `attempt_no` 是问题内递增编号，不是全局唯一编号
- `generation_run_id` 才是单次运行的随机唯一标识

#### 4.5.2 什么算“新尝试”

以下场景在当前实现里都会被视为新尝试：

- 同一个 `id` 再次调用 `POST /api/v1/qa/questions`
- 上一次尝试已经失败后再次提交
- 上一次已经成功提交到 Testing 后再次提交同一 `id`

也就是说，CGS 当前对外没有“按 `id` 去重并直接返回旧结果”的写接口语义。

#### 4.5.3 什么不是“新尝试”

以下行为不会产生新的 `attempt_no`：

- 读取 `GET /api/v1/questions/{id}`
- 读取 `GET /api/v1/questions/{id}/prompt`
- 在同一次 `ingest_question()` 调用内部发生的阶段切换
- 成功路径中的两次连续落盘

特别说明：

成功路径虽然会先写一次 `generated`，再写一次 `submitted_to_testing`，但这两次写入都属于同一个：

- `generation_run_id`
- `attempt_no`

它们不是两次独立尝试。

#### 4.5.4 问题主键冲突语义

CGS 对 `questions/{id}.json` 的约束是：

- 相同 `id` 可以重复提交
- 但如果同一个 `id` 绑定了不同的 `question` 文本，则会报冲突

也就是说：

- `id + same question` -> 允许，生成新 attempt
- `id + different question` -> 拒绝，抛出 `Question conflict for id=<id>`

这是当前实现对“问题标识稳定性”的核心守护规则。

#### 4.5.5 对外部重试的含义

因为 CGS 当前把重复提交视为新 attempt，所以调用方如果做请求重试，需要特别注意：

- 如果同一请求被真正重复发送到服务端，CGS 会生成新的 `attempt_no`
- CGS 本身不区分“用户主动重试”和“网络层重复投递”

因此，若未来需要更强的幂等写接口语义，需要单独引入请求幂等键或显式重试令牌；这不是当前实现的一部分。

---

## 五、接口与数据契约

### 5.1 输入对象：`QAQuestionRequest`

共享模型位置：`contracts/models.py`

```json
{
  "id": "q-001",
  "question": "查询协议版本对应的隧道"
}
```

### 5.2 输出对象：`QueryQuestionResponse`

```json
{
  "id": "q-001",
  "generation_run_id": "9b8d...",
  "attempt_no": 1,
  "generation_status": "submitted_to_testing",
  "generated_cypher": "MATCH ...",
  "parse_summary": "parsed_json_field",
  "guardrail_summary": "basic_guardrail_passed",
  "raw_output_snapshot": "...",
  "failure_stage": null,
  "failure_reason_summary": null,
  "input_prompt_snapshot": "..."
}
```

### 5.3 向 Testing Service 的提交对象：`EvaluationSubmissionRequest`

```json
{
  "id": "q-001",
  "question": "查询协议版本对应的隧道",
  "generation_run_id": "9b8d...",
  "attempt_no": 1,
  "generated_cypher": "MATCH ...",
  "parse_summary": "parsed_json_field",
  "guardrail_summary": "basic_guardrail_passed",
  "raw_output_snapshot": "...",
  "input_prompt_snapshot": "..."
}
```

### 5.4 修复计划接收回执

CGS 仍保留：

- `POST /api/v1/internal/repair-plans`

当前行为是接收并存储 `RepairPlan` 回执，返回：

```json
{
  "status": "accepted",
  "plan_id": "plan-001",
  "id": "q-001"
}
```

它属于保留的系统接口，不是当前主闭环主路径上的关键节点。

---

## 六、状态语义

### 6.1 生成状态集合

CGS 使用 `GenerationProcessingStatus`：

- `received`
- `prompt_fetch_failed`
- `prompt_ready`
- `generated`
- `model_invocation_failed`
- `output_parsing_failed`
- `guardrail_rejected`
- `submitted_to_testing`
- `failed`

### 6.2 状态含义

- `received`
  - 已接收问题，请求已入库
- `prompt_ready`
  - 已获取到非空 prompt，可进入模型调用
- `generated`
  - 已完成解析与守门，结果已落盘
- `submitted_to_testing`
  - 已把结果提交给 Testing Service
- `failed`
  - 非特化失败，当前实现主要用于 prompt readiness 失败

### 6.3 关键边界原则

`submitted_to_testing` 不等于：

- 业务通过
- 查询正确
- 最终成功

它只表示 CGS 自己的生成阶段已完成，并已把结果交给 Testing Service。

---

## 七、持久化与可追踪性

### 7.1 问题级追踪

CGS 以 `id` 作为问题主键，记录：

- 问题原文
- 最新状态
- 最新尝试编号

### 7.2 尝试级追踪

每次生成都会分配：

- `generation_run_id`
- `attempt_no`

并保存到：

- 最新记录：`generation_runs/{id}.json`
- 尝试存档：`generation_attempts/{id}__attempt_{n}.json`

### 7.3 证据留存

CGS 当前显式保留：

- `input_prompt_snapshot`
- `raw_output_snapshot`
- `parse_summary`
- `guardrail_summary`
- `failure_stage`
- `failure_reason_summary`

这些字段是后续排障、评测解释、KRSS 回溯分析的最小证据面。

---

## 八、错误处理与守门原则

### 8.1 Prompt 获取失败

如果 Knowledge Ops 调用失败，CGS 返回 `prompt_fetch_failed`，并记录失败摘要。

### 8.2 Prompt 空值

如果获取到的 prompt 去空白后为空串，CGS 返回：

- `generation_status = failed`
- `failure_stage = prompt_readiness_check`

### 8.3 模型调用失败

模型接口异常会返回：

- `generation_status = model_invocation_failed`

### 8.4 解析失败

如果无法从模型原始输出中提取 Cypher，会返回：

- `generation_status = output_parsing_failed`

### 8.5 最小守门失败

如果生成出的 Cypher 未通过最小 guardrail，会返回：

- `generation_status = guardrail_rejected`

当前 guardrail 只承担最小格式与安全守门，不承担业务正确性裁决。

---

## 九、测试与契约守护

CGS 的设计稳定性重点在三类契约：

### 9.1 上游提示词契约

需要守护：

- `POST /api/knowledge/rag/prompt-package` 的请求体形状
- 响应体必须是纯文本提示词字符串
- 返回 JSON 结构时应视为契约违规并立即失败

### 9.2 下游评测提交契约

需要守护：

- `EvaluationSubmissionRequest` 字段完整性
- `attempt_no` 与 `generation_run_id` 的传递一致性
- `input_prompt_snapshot` 的准确留存

### 9.3 自身状态机契约

需要守护：

- 不把评测成功状态混入生成状态
- 每次尝试都能稳定落盘
- `GET /api/v1/questions/{id}/prompt` 始终返回最后一次实际使用的 prompt 快照

---

## 十、一句话定义

CGS 是一个“只负责生成阶段执行与证据留存”的服务：它把自然语言问题转成可评测的 Cypher 产物，再把结果交给 Testing Service，而不是自己决定业务是否成功。
