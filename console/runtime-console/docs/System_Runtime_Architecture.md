# 系统运行架构（System Runtime Architecture）

## 系统概述（System Overview）

当前系统以 `测试服务（Testing Service）` 为执行与联调中枢，围绕五个正在运行的服务形成一条可验证的主链路：

- `Cypher 生成服务（Cypher Generation Service, CGS）` `8000`
- `测试服务（Testing Service）` `8001`
- `知识修复建议服务（Knowledge Repair Suggestion Service, KRSS）` `8002`
- `知识运营服务（Knowledge Ops / knowledge-agent）` `8010`
- `QA 生成器（QA Generator / qa-agent）` `8020`

其中，`Testing Service` 负责执行与评测，`KRSS` 负责知识修复建议，`Knowledge Ops` 负责提示词与知识更新，`QA Generator` 负责题目与黄金样本生成支持。

---

## 服务清单（Service Inventory）

### Cypher 生成服务（Cypher Generation Service, CGS）

- 端口（Port）：`8000`
- 主要职责（Primary Role）：
  - 接收 `id + question`
  - 向知识运营服务获取提示词包（Prompt Package）
  - 调用模型生成 Cypher
  - 保存 `提示词快照（Prompt Snapshot）`
  - 向测试服务提交 `评测提交（Evaluation Submission）`

### 测试服务（Testing Service）

- 端口（Port）：`8001`
- 主要职责（Primary Role）：
  - 接收黄金答案（Golden）
  - 执行 TuGraph
  - 做规则评测与可选 LLM 复评
  - 在失败时生成 `问题票据（Issue Ticket）`
  - 承载 `系统联调工作台（System Integration Console）`

### 知识修复建议服务（Knowledge Repair Suggestion Service, KRSS）

- 端口（Port）：`8002`
- 主要职责（Primary Role）：
  - 接收测试服务发送的问题票据
  - 向 CGS 拉取 `提示词快照（Prompt Snapshot）`
  - 进行根因分析
  - 生成 `知识修复建议（Knowledge Repair Suggestion）`
  - 向知识运营服务提交修复建议

### 知识运营服务（Knowledge Ops / knowledge-agent）

- 端口（Port）：`8010`
- 主要职责（Primary Role）：
  - 对 CGS 提供纯文本提示词字符串（Prompt Package Response）
  - 对 KRSS 接收知识修复建议并落地知识更新

### QA 生成器（QA Generator / qa-agent）

- 端口（Port）：`8020`
- 主要职责（Primary Role）：
  - 提供问题生成与黄金样本生成能力
  - 作为测试服务上游的题目与标准答案支持组件

---

## 主链路（Primary Runtime Paths）

### 成功路径（Success Path）

`问题请求（QA Question） -> CGS -> Testing Service -> 通过（Pass）`

具体过程：

1. 外部请求向 CGS 发送 `id + question`
2. CGS 向 Knowledge Ops 获取提示词包
3. CGS 生成 Cypher，并保留提示词快照
4. CGS 向 Testing Service 提交评测数据
5. Testing Service 执行 TuGraph 并完成评测
6. 如果评测通过，链路结束

### 失败闭环路径（Failure Closed Loop）

`问题请求（QA Question） -> CGS -> Testing Service -> KRSS -> Knowledge Ops`

具体过程：

1. 外部请求向 CGS 发送 `id + question`
2. CGS 向 Knowledge Ops 获取提示词包并生成 Cypher
3. Testing Service 执行评测
4. 若失败，Testing Service 生成 `问题票据（Issue Ticket）`
5. Testing Service 将问题票据发送给 KRSS
6. KRSS 向 CGS 拉取 `提示词快照（Prompt Snapshot）`
7. KRSS 生成 `知识修复建议（Knowledge Repair Suggestion）`
8. KRSS 将修复建议提交给 Knowledge Ops

---

## 关键数据对象（Key Data Objects）

### 问题请求（QA Question）

- 来源（Source）：外部调用方 / QA Generator
- 去向（Destination）：CGS
- 含义（Meaning）：一次待生成 Cypher 的问题输入，包含 `id` 与 `question`

### 提示词快照（Prompt Snapshot）

- 来源（Source）：CGS
- 去向（Destination）：KRSS
- 含义（Meaning）：CGS 在本轮生成中实际使用的提示词原文

### Prompt Package Response

- 来源（Source）：Knowledge Ops
- 去向（Destination）：CGS
- 含义（Meaning）：`POST /api/knowledge/rag/prompt-package` 的正式响应体，为纯文本提示词字符串，而不是 JSON 包装对象

### 评测提交（Evaluation Submission）

- 来源（Source）：CGS
- 去向（Destination）：Testing Service
- 含义（Meaning）：CGS 向 Testing Service 提交的生成结果与生成证据

### 问题票据（Issue Ticket）

- 来源（Source）：Testing Service
- 去向（Destination）：KRSS
- 含义（Meaning）：一条失败样本及其评测证据，用于知识根因分析

### 知识修复建议（Knowledge Repair Suggestion）

- 来源（Source）：KRSS
- 去向（Destination）：Knowledge Ops
- 含义（Meaning）：面向知识运营服务的正式知识修复请求

---

## 服务边界（Service Boundaries）

### CGS 的边界

- 负责生成（Generate）
- 不负责执行（Do Not Execute TuGraph）
- 不负责最终业务裁决（Do Not Judge Final Business Correctness）

### Testing Service 的边界

- 负责执行（Execute）
- 负责评测（Evaluate）
- 负责问题票据生成（Create Issue Tickets）
- 不负责知识修复建议生成（Do Not Produce Knowledge Repair Suggestions）

### KRSS 的边界

- 负责知识修复建议（Produce Knowledge Repair Suggestions）
- 不负责业务裁决（Do Not Judge Final Business Correctness）
- 不负责重新生成 Cypher（Do Not Regenerate Cypher）

### Knowledge Ops 的边界

- 负责提供纯文本提示词字符串（Provide Plain-Text Prompt Package Response）
- 负责知识更新（Apply Knowledge Repairs）

---

## 联调入口（Integration Entry Points）

### 系统联调工作台（System Integration Console）

- 挂载位置：`Testing Service`
- 页面路径：`/console`
- 用途：
  - 查看运行服务状态
  - 查看正式结构图与数据流
  - 发起成功路径与失败路径联调
  - 观察 `Prompt Snapshot`、`Evaluation`、`Issue Ticket` 与 `Knowledge Repair Suggestion`

### 关键接口速查（Interface Quick Reference）

- `POST /api/v1/qa/questions`
- `GET /api/v1/questions/{id}/prompt`
- `POST /api/v1/qa/goldens`
- `POST /api/v1/evaluations/submissions`
- `POST /api/v1/issue-tickets`
- `POST /api/knowledge/rag/prompt-package`
- `POST /api/knowledge/repairs/apply`

---

## 当前默认运行认知（Current Runtime Interpretation）

当前系统的运行认知应固定为：

- `CGS` 是生成入口（Generation Entry）
- `Testing Service` 是执行与评测中心（Execution and Evaluation Hub）
- `KRSS` 是知识修复建议中心（Knowledge Repair Suggestion Hub）
- `Knowledge Ops` 是提示词和知识更新的双向知识入口（Knowledge Supply and Repair Sink）
- `QA Generator` 是问题与黄金样本生成支持组件（Question and Golden Generation Support）
