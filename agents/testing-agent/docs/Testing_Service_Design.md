# Testing Service 设计文档

## Summary

`Testing Service`（测试服务）是 Text2Cypher 闭环中的执行与评测中枢。

它同时接收两类输入：

- 来自 QA/题库侧的黄金答案 `QAGoldenRequest`
- 来自 CGS 的生成提交 `EvaluationSubmissionRequest`

当一条 `id` 同时具备黄金答案与生成提交后，Testing Service 会执行 TuGraph，运行规则评测，并在需要时触发可选的 LLM 复评。若最终未通过，则生成 `IssueTicket` 并投递给 KRSS；若通过，则把该次尝试标记为 `passed`。

Testing Service 的边界是：

- 它负责执行和评测
- 它负责生成失败票据
- 它不负责重新生成 Cypher
- 它不负责产出知识修复建议

---

## 一、服务定义

### 1.1 服务名称

**Testing Service（测试服务）**

### 1.2 核心职责

Testing Service 负责：

1. 接收黄金答案
2. 接收 CGS 提交的生成结果
3. 对同一 `id` 配对黄金与提交
4. 执行 TuGraph 查询
5. 做规则评测
6. 按配置执行可选 LLM 复评
7. 对失败样本生成 `IssueTicket`
8. 将问题票据投递给 KRSS
9. 保存每次尝试的执行、评测和改进评估结果

### 1.3 非目标

Testing Service 明确不承担：

- 不拉取或生成 prompt
- 不调用 LLM 生成 Cypher
- 不修改知识文件
- 不产出知识修复建议
- 不替 KRSS 做知识类型归因

---

## 二、职责边界

### 2.1 对 QA / 上游题库侧的边界

Testing Service 通过以下接口接收黄金答案：

- `POST /api/v1/qa/goldens`

黄金答案是“评测基准”，不是生成输入。

### 2.2 对 CGS 的边界

Testing Service 通过以下接口接收 CGS 的生成提交：

- `POST /api/v1/evaluations/submissions`

CGS 必须提供：

- `generated_cypher`
- `generation_run_id`
- `attempt_no`
- 生成阶段快照与摘要

Testing Service 不关心 CGS 如何生成，只消费其提交结果并负责后续执行评测。

### 2.3 对 KRSS 的边界

当评测未通过时，Testing Service 将失败样本封装成：

- `IssueTicket`

并发送到：

- `POST /api/v1/issue-tickets`

边界原则：

- Testing Service 负责生成失败证据包
- KRSS 负责知识修复归因与修复请求

---

## 三、运行时组件

### 3.1 FastAPI 入口

文件：`services/testing_agent/app/main.py`

正式接口：

- `POST /api/v1/qa/goldens`
- `POST /api/v1/evaluations/submissions`
- `GET /api/v1/evaluations/{id}`
- `GET /api/v1/issues/{ticket_id}`
- `GET /api/v1/status`

辅助接口：

- `GET /health`

### 3.2 主编排服务

文件：`services/testing_agent/app/service.py`

核心对象：`EvaluationService`

主要入口：

- `ingest_golden(request)`
- `ingest_submission(request)`

内部关键流程：

- `_evaluate_ready_pair(id)`
- `_llm_re_evaluate(...)`

### 3.3 外部与基础客户端

文件：`services/testing_agent/app/clients.py`

关键组件：

- `RepairServiceClient`
  - 向 KRSS 投递问题票据
- `LLMEvaluationClient`
  - 对规则评测结果进行语义复评
- `QueryGeneratorConsoleClient`
  - 为联调控制台提供 CGS 访问能力
- `ServiceHealthClient`
  - 读取相关服务健康状态
- `TuGraphClient`
  - 实际执行生成出来的 Cypher

### 3.4 评测引擎

规则评测逻辑位于：

- `services/testing_agent/app/evaluation.py`

它输出：

- `EvaluationSummary`
- 四个维度状态
- verdict
- symptom
- evidence

### 3.5 持久化仓库

文件：`services/testing_agent/app/repository.py`

默认保存内容：

- `goldens/`
- `submissions/`
- `submission_attempts/`
- `issue_tickets/`

其中既保存最新记录，也保存按尝试编号分档的历史快照。

---

## 四、主流程

### 4.1 双入口配对机制

Testing Service 支持黄金与提交乱序到达。

路径一：

```text
POST /api/v1/qa/goldens
  -> save golden
  -> if no submission: return received_golden_only
  -> else evaluate ready pair
```

路径二：

```text
POST /api/v1/evaluations/submissions
  -> save submission
  -> if no golden: return waiting_for_golden
  -> else evaluate ready pair
```

### 4.2 评测主路径

一旦同一 `id` 的黄金与提交都就绪，就进入：

```text
evaluate_ready_pair(id)
  -> read golden + submission
  -> execute generated_cypher on TuGraph
  -> save execution snapshot
  -> run rule-based evaluation
  -> optionally run LLM re-evaluation
  -> if pass: mark passed
  -> else create IssueTicket and submit to KRSS
```

### 4.3 失败闭环路径

未通过时，Testing Service 会：

1. 构造 `IssueTicket`
2. 为当前尝试标记 `repair_pending`
3. 调用 KRSS
4. 若调用成功，标记 `issue_ticket_created`
5. 保存 `krss_response`

若投递 KRSS 失败，则标记：

- `repair_submission_failed`

---

## 五、接口与数据契约

### 5.1 黄金输入：`QAGoldenRequest`

```json
{
  "id": "q-001",
  "cypher": "MATCH ...",
  "answer": [],
  "difficulty": "L3"
}
```

### 5.2 生成提交：`EvaluationSubmissionRequest`

```json
{
  "id": "q-001",
  "question": "查询协议版本对应的隧道",
  "generation_run_id": "run-001",
  "attempt_no": 1,
  "generated_cypher": "MATCH ...",
  "parse_summary": "parsed_json_field",
  "guardrail_summary": "basic_guardrail_passed",
  "raw_output_snapshot": "...",
  "input_prompt_snapshot": "..."
}
```

### 5.3 评测返回：`EvaluationSubmissionResponse`

```json
{
  "id": "q-001",
  "status": "issue_ticket_created",
  "issue_ticket_id": "ticket-q-001-attempt-1",
  "verdict": "partial_fail"
}
```

对于黄金接口，如果提交尚未到达，则返回：

```json
{
  "id": "q-001",
  "status": "received_golden_only"
}
```

### 5.4 失败票据：`IssueTicket`

```json
{
  "ticket_id": "ticket-q-001-attempt-1",
  "id": "q-001",
  "difficulty": "L3",
  "question": "查询协议版本对应的隧道",
  "expected": {
    "cypher": "MATCH ...",
    "answer": []
  },
  "actual": {
    "generated_cypher": "MATCH ...",
    "execution": {
      "success": true,
      "rows": [],
      "row_count": 0,
      "error_message": null,
      "elapsed_ms": 12
    }
  },
  "evaluation": {
    "verdict": "partial_fail",
    "dimensions": {
      "syntax_validity": "pass",
      "schema_alignment": "pass",
      "result_correctness": "fail",
      "question_alignment": "fail"
    },
    "symptom": "Wrong tunnel returned",
    "evidence": ["result does not match expected tunnel"]
  },
  "input_prompt_snapshot": "..."
}
```

---

## 六、状态语义

Testing Service 使用 `EvaluationState`：

- `received_golden_only`
- `received_submission_only`
- `waiting_for_golden`
- `ready_to_evaluate`
- `repair_pending`
- `repair_submission_failed`
- `issue_ticket_created`
- `passed`

### 6.1 主要状态含义

说明：

- `received_submission_only` 当前仍在共享枚举中保留
- 但现行外部主路径在“submission 先到、golden 未到”时返回的是 `waiting_for_golden`

- `received_golden_only`
  - 已收到黄金，等待 CGS 提交
- `waiting_for_golden`
  - 已收到 CGS 提交，等待黄金
- `ready_to_evaluate`
  - 黄金与提交都已具备，可进入评测
- `repair_pending`
  - 已生成问题票据，准备向 KRSS 投递
- `repair_submission_failed`
  - 问题票据已生成，但 KRSS 投递失败
- `issue_ticket_created`
  - 问题票据已成功提交给 KRSS
- `passed`
  - 本次尝试评测通过

### 6.2 verdict 与 state 的区别

`EvaluationState` 关注“处理流程走到哪一步”，而 `verdict` 关注“业务评测结论是什么”。

例如：

- `status = issue_ticket_created`
- `verdict = partial_fail`

这两者并不冲突，分别表示流程状态和评测结论。

---

## 七、评测逻辑

### 7.1 规则评测

Testing Service 通过 `evaluate_submission(...)` 产出四维判断：

- `syntax_validity`
- `schema_alignment`
- `result_correctness`
- `question_alignment`

再归并为：

- `pass`
- `partial_fail`
- `fail`

### 7.2 可选 LLM 复评

当规则评测未通过且 `llm_client` 可用时，Testing Service 可执行语义复评。

LLM 只允许修正两个维度：

- `result_correctness`
- `question_alignment`

如果 LLM 将失败维度翻转为 `pass`，Testing Service 会重新计算最终 verdict。

### 7.3 关键边界原则

Testing Service 的 LLM 复评是“评测补充”，不是重新生成，也不是知识修复建议。

---

## 八、持久化与可追踪性

### 8.1 黄金存储

黄金记录保存在：

- `goldens/{id}.json`

若同一 `id` 重复提交但内容不同，仓库会报冲突错误。

### 8.2 提交与尝试存储

生成提交保存在：

- 最新记录：`submissions/{id}.json`
- 分尝试记录：`submission_attempts/{id}__attempt_{n}.json`

这使 Testing Service 能同时看到“当前最新尝试”和“历史尝试序列”。

### 8.3 票据存储

`IssueTicket` 保存在：

- `issue_tickets/{ticket_id}.json`

并与 submission 记录互相关联。

### 8.4 改进评估

Testing Service 还会为每次尝试生成 `ImprovementAssessment`，用于比较当前尝试相对上一尝试是否：

- improved
- regressed
- unchanged
- not_comparable

这属于测试与回归分析能力，不改变主闭环边界。

---

## 九、错误处理原则

### 9.1 配对前错误

- 黄金冲突会拒绝写入
- 同一 `generation_run_id + attempt_no` 的重复 submission 会拒绝写入

### 9.2 TuGraph 执行失败

执行失败不会中断评测流程，而会进入 `ActualAnswer.execution`，再由评测逻辑把它转成评测证据。

### 9.3 KRSS 投递失败

如果 KRSS 接口调用失败：

- 当前尝试状态标记为 `repair_submission_failed`
- 异常向上抛出

这样可以明确区分：

- “样本评测失败”
- “问题票据已经生成”
- “但失败闭环尚未成功送达 KRSS”

---

## 十、测试与契约守护

Testing Service 的稳定性重点在四类契约：

### 10.1 CGS 提交契约

需要守护：

- `EvaluationSubmissionRequest` 字段完整
- `attempt_no` 语义正确
- prompt / raw output 快照被完整透传

### 10.2 QA 黄金契约

需要守护：

- `QAGoldenRequest` 的唯一性
- 黄金答案与难度的稳定保存

### 10.3 KRSS 票据契约

需要守护：

- `IssueTicket` 的结构稳定
- `ticket_id` 与尝试编号对应关系稳定
- 失败证据足够驱动 KRSS 做知识归因

### 10.4 状态机契约

需要守护：

- 配对前后状态切换正确
- 通过样本不能进入 KRSS
- KRSS 投递失败与评测失败要分开表达

---

## 十一、一句话定义

Testing Service 是闭环中的“执行与评测中枢”：它把 CGS 的生成结果和黄金答案配对执行、做出评测结论，并把失败样本标准化为 `IssueTicket` 发送给 KRSS。
