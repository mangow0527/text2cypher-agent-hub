# Knowledge Repair Suggestion Service 设计文档

## Summary

`Knowledge Repair Suggestion Service`（KRSS，知识修复建议服务）是闭环中的知识诊断与知识修复请求生成服务。

它接收 `Testing Service` 产出的 `IssueTicket`，从 `Cypher Generation Service` 拉取本轮实际使用的提示词快照，结合评测证据进行根因诊断，形成结构化的 `KnowledgeRepairSuggestionRequest`，并投递给 `Knowledge Ops`。

当前实现主线是：

- **LLM-first diagnosis**
- **lightweight validation as optional extension**
- **Knowledge Ops apply on success**
- **analysis record idempotency by ticket**

它的核心边界同样清晰：

- KRSS 负责知识修复建议
- KRSS 不负责重新生成 Cypher
- KRSS 不负责最终业务裁决
- KRSS 不直接编辑知识内容本体

---

## 一、服务定义

### 1.1 服务名称

**Knowledge Repair Suggestion Service（知识修复建议服务）**

### 1.2 核心职责

KRSS 负责以下动作：

1. 接收失败样本的 `IssueTicket`
2. 使用 `ticket_id` 派生分析记录主键
3. 从 CGS 拉取 `input_prompt_snapshot`
4. 组装诊断上下文
5. 运行知识类型归因
6. 形成正式知识修复请求
7. 调用 `Knowledge Ops` apply 接口
8. 保存分析记录并返回结构化响应

### 1.3 非目标

KRSS 明确不承担：

- 不判断最终评测是否应该通过
- 不重新执行 TuGraph
- 不重新调用 CGS 生成新 Cypher
- 不直接改写 Prompt 文件或知识文件
- 不生成 QA 重写建议文档

---

## 二、职责边界

### 2.1 对 Testing Service 的边界

KRSS 的正式写入口是：

- `POST /api/v1/issue-tickets`

输入对象为共享契约 `IssueTicket`。

Testing Service 负责：

- 产出失败样本
- 提供黄金答案、实际生成结果和评测证据

KRSS 负责：

- 消费这些证据
- 归因到知识类型
- 生成正式知识修复建议

### 2.2 对 CGS 的边界

KRSS 通过以下接口回看某次生成实际使用的 prompt：

- `GET /api/v1/questions/{id}/prompt`

边界原则：

- CGS 提供“本次生成真实使用的 prompt 快照”
- KRSS 基于该快照做诊断
- KRSS 不自己猜测 prompt，也不依赖调用方透传的临时快照作为事实源

### 2.3 对 Knowledge Ops 的边界

KRSS 只调用一个正式出站接口：

- `POST /api/knowledge/repairs/apply`

出站 payload 严格收敛为：

- `id`
- `suggestion`
- `knowledge_types`

KRSS 负责“提出正式修复请求”，Knowledge Ops 负责“接收并应用知识补丁”。

---

## 三、运行时组件

### 3.1 FastAPI 入口

文件：`services/repair_agent/app/main.py`

正式接口：

- `POST /api/v1/issue-tickets`
- `GET /api/v1/krss-analyses/{analysis_id}`
- `GET /api/v1/status`

辅助接口：

- `GET /health`
- `/console`

### 3.2 主编排服务

文件：`services/repair_agent/app/service.py`

核心对象：`RepairService`

主入口：`create_issue_ticket_response(issue_ticket)`

运行步骤：

1. 根据 `ticket_id` 生成 `analysis_id = analysis-<ticket_id>`
2. 查询本地分析记录
3. 若已存在，直接返回现有结果
4. 若不存在，从 CGS 拉取 prompt snapshot
5. 调用分析器得到 `KRSSAnalysisResult`
6. 转换为 `KnowledgeRepairSuggestionRequest`
7. 调用 Knowledge Ops apply
8. 落盘 `KRSSAnalysisRecord`
9. 返回 `KRSSIssueTicketResponse`

### 3.3 分析器

文件：`services/repair_agent/app/analysis.py`

核心对象：`KRSSAnalyzer`

职责：

- 组装 `DiagnosisContext`
- 调用 `KRSSDiagnosisClient`
- 归一化知识类型与置信度
- 可选执行 lightweight validation
- 生成最终 `KRSSAnalysisResult`

当前允许的知识类型只有四类：

- `cypher_syntax`
- `few_shot`
- `system_prompt`
- `business_knowledge`

### 3.4 LLM 诊断客户端

文件：`services/repair_agent/app/clients.py`

核心对象：`OpenAICompatibleKRSSAnalyzer`

它将 ticket 摘要与诊断上下文压缩后送入 LLM，并要求返回 JSON：

- `primary_knowledge_type`
- `secondary_knowledge_types`
- `candidate_patch_types`
- `confidence`
- `suggestion`
- `rationale`
- `need_validation`

### 3.5 Knowledge Ops apply 客户端

文件：`services/repair_agent/app/clients.py`

核心对象：`KnowledgeOpsRepairApplyClient`

语义约束：

- 只要 HTTP 200 才视为 apply 成功
- transport error 与可重试非 200 响应可重试
- 4xx 直接终止
- 重试只发生在投递层，不重复执行诊断

### 3.6 分析记录仓库

文件：`services/repair_agent/app/repository.py`

默认保存位置：

- `data/repair_service/analyses/<analysis_id>.json`

它是 KRSS 实现幂等返回和历史追踪的基础。

---

## 四、主流程

### 4.1 标准主路径

```text
POST /api/v1/issue-tickets
  -> lookup analysis by analysis-<ticket_id>
  -> if exists: return stored response
  -> fetch prompt snapshot from CGS
  -> build diagnosis context
  -> LLM diagnosis
  -> optional lightweight validation
  -> build KnowledgeRepairSuggestionRequest
  -> POST /api/knowledge/repairs/apply
  -> save KRSSAnalysisRecord
  -> return applied response
```

### 4.2 幂等路径

KRSS 的幂等语义不是按 `id`，而是按 `ticket_id`。

也就是说，同一个失败票据只会对应一条分析记录：

- `analysis_id = analysis-<ticket_id>`

如果同一票据重复投递，KRSS 直接返回存量分析结果，不重新拉 prompt，不重新诊断，也不重复 apply。

### 4.3 诊断上下文构成

`DiagnosisContext` 主要包含：

- `question`
- `difficulty`
- `sql_pair.expected_cypher`
- `sql_pair.actual_cypher`
- `evaluation_summary`
- `failure_diff`
- `relevant_prompt_fragments`
- `recent_applied_repairs`

其中 `failure_diff` 用于提炼：

- ordering 问题
- limit 问题
- return shape 问题
- entity / relation 问题
- syntax 问题
- execution 问题

---

## 五、接口与数据契约

### 5.1 输入对象：`IssueTicket`

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

说明：

虽然 `IssueTicket` 里有 `input_prompt_snapshot` 字段，但 KRSS 当前以从 CGS 拉到的快照作为主事实来源。

### 5.2 对 CGS 的读取对象：`PromptSnapshotResponse`

```json
{
  "id": "q-001",
  "attempt_no": 1,
  "input_prompt_snapshot": "..."
}
```

### 5.3 对 Knowledge Ops 的正式出站对象：`KnowledgeRepairSuggestionRequest`

```json
{
  "id": "q-001",
  "suggestion": "Add business mapping and a matching few_shot example",
  "knowledge_types": ["business_knowledge", "few_shot"]
}
```

### 5.4 KRSS 写接口响应：`KRSSIssueTicketResponse`

```json
{
  "status": "applied",
  "analysis_id": "analysis-ticket-q-001-attempt-1",
  "id": "q-001",
  "knowledge_repair_request": {
    "id": "q-001",
    "suggestion": "Add business mapping and a matching few_shot example",
    "knowledge_types": ["business_knowledge"]
  },
  "knowledge_ops_response": {
    "status": "ok"
  },
  "applied": true
}
```

### 5.5 持久化对象：`KRSSAnalysisRecord`

记录字段包括：

- `analysis_id`
- `ticket_id`
- `id`
- `prompt_snapshot`
- `knowledge_repair_request`
- `knowledge_ops_response`
- `confidence`
- `rationale`
- `used_experiments`
- `primary_knowledge_type`
- `secondary_knowledge_types`
- `candidate_patch_types`
- `validation_mode`
- `validation_result`
- `diagnosis_context_summary`
- `created_at`
- `applied_at`

---

## 六、状态与语义

### 6.1 写接口语义

KRSS 的写接口响应状态当前收敛为：

- `status = applied`

这表示：

- 诊断已完成
- Knowledge Ops apply 已返回成功
- 分析记录已落盘

### 6.2 读接口语义

`GET /api/v1/krss-analyses/{analysis_id}` 返回的是一条稳定分析记录，而不是一次临时推理结果。

### 6.3 关键边界原则

KRSS 的 `applied` 不等于：

- 知识补丁已经验证有效
- 下次生成一定通过
- 业务问题已经被修复

它只表示 KRSS 已完成“知识修复建议生成并成功投递”。

---

## 七、持久化与可追踪性

### 7.1 分析记录主键

KRSS 使用：

- `analysis_id = analysis-<ticket_id>`

而不是直接使用 `id`。

原因是：

- `id` 表示问题或任务标识
- `ticket_id` 表示一次具体失败事件

用 `ticket_id` 才能正确承载“失败事件级”分析记录。

### 7.2 证据留存

KRSS 当前会保留：

- prompt snapshot
- 结构化修复请求
- Knowledge Ops 响应
- 诊断置信度
- 推理依据摘要
- validation 结果
- 诊断上下文摘要

这些字段构成后续 RCA、回放和外部审计的依据。

---

## 八、错误处理原则

### 8.1 CGS prompt 快照不可读

如果 KRSS 无法从 CGS 读取 prompt snapshot，当前请求直接失败，不会伪造快照继续诊断。

### 8.2 诊断失败

如果 LLM 诊断或上下文处理失败，KRSS 不会生成半结构化的假请求；当前实现应由异常向上抛出，保持失败显式可见。

### 8.3 Knowledge Ops apply 失败

如果 apply 失败：

- 可重试错误进入重试逻辑
- 非重试错误终止请求
- 不保存“已应用”的分析记录

这保证了 `KRSSIssueTicketResponse(status=applied)` 与真正的 apply 成功严格一致。

---

## 九、测试与契约守护

KRSS 需要重点守护三类契约：

### 9.1 输入票据契约

需要确保 `IssueTicket` 中以下证据面完整：

- 期望 Cypher
- 实际生成 Cypher
- 执行结果
- 评测维度
- symptom / evidence

### 9.2 CGS prompt snapshot 契约

需要确保：

- `GET /api/v1/questions/{id}/prompt` 可用
- 返回的 `input_prompt_snapshot` 是实际生成使用的快照

### 9.3 Knowledge Ops apply 契约

需要确保：

- 出站 payload 只含 `id/suggestion/knowledge_types`
- `knowledge_types` 只允许四个正式枚举值
- 只有 200 视为成功

---

## 十、一句话定义

KRSS 是一个“把失败票据转成正式知识修复请求”的服务：它消费评测证据与 prompt 快照，归因到知识类型，然后把修复建议稳定投递给 Knowledge Ops。
