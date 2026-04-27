# repair-agent 架构与契约设计

repair-agent 是 Text2Cypher 闭环中的知识诊断与知识修复建议 agent。它接收 testing-agent 产出的失败问题单，基于失败样本、评测证据和生成证据判断是否存在 knowledge-agent 知识包缺口，并把结构化的知识修复建议投递给 knowledge-agent。

一句话边界：

```text
testing-agent 证明“这次生成哪里错了”；
repair-agent 判断“是否属于 knowledge-agent 知识缺口，以及该修哪类 knowledge-agent 知识”。
```

repair-agent 不负责执行 Cypher，不重新调用 cypher-generator-agent，不重新评测业务正确性，也不直接编辑 prompt 或知识文件。它只负责把一次失败事件转成可追踪、可投递的 `KnowledgeRepairSuggestionRequest`。

repair-agent 的业务修复目标只有 knowledge-agent 维护的知识包，包括业务知识、schema hints、few-shot、查询模式和 knowledge-agent 侧系统知识片段。cypher-generator-agent 固定生成协议、兜底解析、提交前 Cypher 基础检查以及 testing-agent 评测器都不属于 repair-agent 的修复对象；如果失败证据指向这些环节，repair-agent 应将其识别为非知识包缺口，不生成面向这些工程环节的修复建议。

当前实现主线：

- LLM-first diagnosis：优先由强模型基于失败证据做根因归因。
- Prompt evidence from IssueTicket：prompt 快照来自 testing-agent 的失败事件快照，而不是 repair-agent 回查 cypher-generator-agent。
- Lightweight validation：在 LLM 要求验证时，执行类型级启发式筛选。
- knowledge-agent apply on success：只有 knowledge-agent apply 成功后，repair-agent 写接口才返回 `applied`。
- Analysis record idempotency by ticket：按 `ticket_id` 保存并复用分析记录。

## 1. 术语与核心数据结构

本章定义 repair-agent 文档中反复出现的业务术语和数据结构。后续流程、接口和责任边界都以这些对象为基础。

### 1.1 QA 样本与 `id`

`id` 是一条 QA 样本在闭环中的主键。它贯穿 qa-agent、cypher-generator-agent、testing-agent 和 repair-agent。

repair-agent 不生成新的 `id`，也不改变 `id` 的含义；它只消费 testing-agent 在失败问题单中传入的 `id`，并将其继续透传到正式的 `KnowledgeRepairSuggestionRequest` 中。

### 1.2 `IssueTicket`

`IssueTicket` 是 testing-agent 提交给 repair-agent 的失败问题单。它只在 testing-agent 对某次 submission 的最终评测结论不是 `pass` 时生成。

它包含：

| 字段 | 含义 |
| --- | --- |
| `ticket_id` | 问题单唯一 ID，当前格式通常为 `ticket-{id}-attempt-{attempt_no}` |
| `id` | QA 样本主键 |
| `difficulty` | 题目难度，取值为 `L1` 到 `L8` |
| `question` | 原始自然语言问题 |
| `expected` | 黄金 Cypher 与黄金答案 |
| `actual` | cypher-generator-agent 生成 Cypher 与 testing-agent 实际执行结果 |
| `evaluation` | testing-agent 生成的评测结论，只包含 `verdict`、`primary_metrics` 与 `secondary_signals` |
| `generation_evidence` | cypher-generator-agent 生成过程证据，由 testing-agent 从 submission 中复制到问题单 |
| `input_prompt_snapshot` | prompt 快照兼容字段。正式契约优先使用 `generation_evidence.input_prompt_snapshot`；该字段仅作为兼容回退 |

它的意义是：把一次失败事件中 repair-agent 需要的事实都固定下来。repair-agent 不再为了补齐 prompt 或生成证据回头查询 cypher-generator-agent。

### 1.3 `GenerationEvidence`

`GenerationEvidence` 表示 cypher-generator-agent 生成阶段留下的过程证据。它的来源是 cypher-generator-agent 提交给 testing-agent 的 `GeneratedCypherSubmissionRequest`。testing-agent 不重新生成这些字段，只在接收 submission 时保存，并在生成 `IssueTicket` 时复制到 `generation_evidence`。

它包含：

| 字段 | 含义 |
| --- | --- |
| `generation_run_id` | cypher-generator-agent 本次生成运行 ID，用于串联 cypher-generator-agent 日志和问题单 |
| `attempt_no` | testing-agent 记录的尝试序号 |
| `input_prompt_snapshot` | cypher-generator-agent 本轮生成实际使用的 prompt 快照 |

它和 `evaluation` 的区别是：

- `generation_evidence` 描述“cypher-generator-agent 当时如何生成”。
- `evaluation` 描述“testing-agent 观察到哪里失败”。
- repair-agent 同时消费二者，用于区分 knowledge-agent 知识包缺口与非知识包问题。prompt/few-shot/图谱语义类缺口可以进入 knowledge-agent 修复建议；cypher-generator-agent 固定生成协议与 testing-agent 评测器问题属于工程缺陷，不应包装成 knowledge-agent 知识修复。

### 1.4 `DiagnosisContext`

`DiagnosisContext` 是 repair-agent 内部传给 LLM 诊断客户端的结构化上下文。它不是外部 API 契约，而是把 `IssueTicket` 和 prompt evidence 压缩成更适合根因分析的材料。

它主要包含：

| 字段 | 含义 |
| --- | --- |
| `question` | 原始自然语言问题 |
| `difficulty` | 题目难度 |
| `sql_pair` | `expected_cypher` 与 `actual_cypher` |
| `evaluation_summary` | `verdict`、`primary_metrics` 与 `secondary_signals` 的压缩摘要 |
| `failure_diff` | repair-agent 基于 `expected`、`actual` 与 `evaluation` 派生的内部失败差异摘要 |
| `prompt_evidence` | 压缩后的完整 prompt 证据 |
| `generation_evidence` | 压缩后的生成过程证据 |
| `relevant_prompt_fragments` | 从 prompt 中抽取的 system、business、few-shot、repair 片段 |
| `recent_applied_repairs` | 最近已应用修复，当前主流程默认为空 |

它的意义是：让 LLM 不需要理解完整服务调用链，也能看到根因分析所需的核心证据。

### 1.5 `failure_diff`

`failure_diff` 是 expected、actual 和 evaluation 的轻量结构化对比。它不是 testing-agent 向 repair-agent 承诺的输入字段，而是 repair-agent 在消费正式 `IssueTicket` 后内部派生出来的诊断摘要。

它包含：

| 字段 | 含义 |
| --- | --- |
| `ordering_problem` | golden 要求排序但 actual 缺失或不一致 |
| `limit_problem` | golden 要求 limit 但 actual 缺失或数量不一致 |
| `return_shape_problem` | RETURN 结构不一致 |
| `entity_or_relation_problem` | 实体或关系路径疑似不一致 |
| `execution_problem` | TuGraph 执行失败或返回错误信息 |
| `syntax_problem` | testing-agent 判定语法维度失败 |
| `missing_or_wrong_clauses` | 以上问题的摘要列表 |
| `semantic_mismatch_summary` | 基于 `evaluation.primary_metrics`、执行事实和 expected/actual 差异生成的语义不一致摘要 |

它的意义是：给 LLM 和 lightweight validation 一个稳定的失败现象骨架。它不是最终根因结论。

### 1.6 `prompt_evidence` 与 `relevant_prompt_fragments`

repair-agent 同时保留两类 prompt 证据：

| 字段 | 来源 | 作用 |
| --- | --- | --- |
| `prompt_evidence` | `generation_evidence.input_prompt_snapshot` 或兼容字段 `input_prompt_snapshot` | 压缩后的完整 prompt 证据，保证中文和结构化 prompt 不会被抽空 |
| `relevant_prompt_fragments` | 从 prompt 中按关键词抽取的片段 | 辅助判断 prompt 中是否已有 system rule、business knowledge、few-shot 或 repair 信息 |

`relevant_prompt_fragments` 只是辅助字段。repair-agent 不再只依赖英文关键词抽片段来判断知识是否缺失。

### 1.7 repair 分析结果

repair 分析结果是 repair-agent 内部分析器的输出。

它包含：

| 字段 | 含义 |
| --- | --- |
| `id` | QA 样本主键 |
| `suggestion` | 面向 knowledge-agent 的修复建议文本 |
| `knowledge_types` | 本次建议涉及的知识类型 |
| `confidence` | 诊断置信度，范围 0 到 1 |
| `rationale` | LLM 给出的归因理由 |
| `used_experiments` | 是否执行 lightweight validation |
| `primary_knowledge_type` | LLM 诊断的主知识类型 |
| `secondary_knowledge_types` | 次要知识类型 |
| `candidate_patch_types` | LLM 建议验证的候选类型 |
| `validation_mode` | `disabled` 或 `lightweight` |
| `validation_result` | 验证通过/拒绝的类型与理由 |
| `diagnosis_context_summary` | 诊断上下文摘要，用于追踪与审计 |

它的意义是：把 LLM 诊断、可选验证和最终知识类型选择汇总成可保存、可投递的内部结果。

### 1.8 `KnowledgeRepairSuggestionRequest`

`KnowledgeRepairSuggestionRequest` 是 repair-agent 投递给 knowledge-agent 的正式出站契约。

它严格只有三个字段：

| 字段 | 含义 |
| --- | --- |
| `id` | QA 样本主键 |
| `suggestion` | 给 knowledge-agent 的修复建议 |
| `knowledge_types` | 建议修复的知识类型 |

允许的知识类型只有：

```text
cypher_syntax
few_shot
system_prompt
business_knowledge
```

它的意义是：把 repair-agent 内部诊断结果收敛成 knowledge-agent 可以应用的最小修复请求。

### 1.9 repair 分析记录

repair 分析记录是 repair-agent 落盘保存的稳定分析记录。

它包含：

| 字段 | 含义 |
| --- | --- |
| `analysis_id` | 分析记录主键，当前为 `analysis-{ticket_id}` |
| `ticket_id` | testing-agent 失败问题单 ID |
| `id` | QA 样本主键 |
| `prompt_snapshot` | repair-agent 实际用于诊断的 prompt 快照，来源于 IssueTicket |
| `knowledge_repair_request` | 已投递给 knowledge-agent 的修复请求 |
| `knowledge_ops_response` | knowledge-agent apply 响应 |
| `confidence` | 诊断置信度 |
| `rationale` | 根因说明 |
| `used_experiments` | 是否执行 lightweight validation |
| `primary_knowledge_type` | 主知识类型 |
| `secondary_knowledge_types` | 次要知识类型 |
| `candidate_patch_types` | 候选修复类型 |
| `validation_mode` | 验证模式 |
| `validation_result` | 验证结果 |
| `diagnosis_context_summary` | 诊断上下文摘要 |
| `created_at` | 记录创建时间 |
| `applied_at` | knowledge-agent apply 成功时间 |

它的意义是：为幂等返回、审计、回放和控制台展示提供稳定事实。

## 2. 主流程

### 2.1 总览

repair-agent 的工作起点是 testing-agent 投递的失败 `IssueTicket`。它不重新执行测试，也不重新生成 Cypher，而是在失败事实已经固定的前提下，判断这次失败是否属于 knowledge-agent 知识包缺口，并把结果收敛成正式修复请求。

### 2.2 根因分析主链路

```text
IssueTicket
  -> 按 ticket_id 查询 repair 分析记录
  -> 已存在：直接返回历史 applied 响应
  -> 不存在：从 IssueTicket 提取 prompt snapshot
  -> 构造 DiagnosisContext
  -> LLM-first diagnosis
  -> 可选 lightweight validation
  -> 生成 repair 分析结果
  -> 转换 KnowledgeRepairSuggestionRequest
  -> knowledge-agent apply
  -> 保存 repair 分析记录
  -> 返回 repair-agent 写接口响应
```

每一步的意义：

| 步骤 | 输入 | 输出 | 意义 |
| --- | --- | --- | --- |
| 幂等查询 | `ticket_id` | 已有记录或空 | 避免同一失败事件重复诊断和重复 apply |
| 提取 prompt | `IssueTicket.generation_evidence` | prompt snapshot | 使用 testing-agent 固化的失败事件快照作为事实源 |
| 构造上下文 | `IssueTicket` 与 prompt | `DiagnosisContext` | 将失败事实整理为 LLM 可消费材料 |
| LLM 诊断 | `DiagnosisContext` | diagnosis JSON | 判断知识类型、置信度、建议和是否需要验证 |
| 轻量验证 | candidate patch types | selected knowledge types | 对候选知识类型做启发式筛选 |
| 生成请求 | repair 分析结果 | `KnowledgeRepairSuggestionRequest` | 收敛为 knowledge-agent 出站契约 |
| apply | 修复请求 | knowledge-agent 响应 | 投递正式修复建议 |
| 落盘 | 全链路结果 | repair 分析记录 | 支撑审计、回放和幂等 |

## 3. 分步数据流

本章按 repair-agent 的运行顺序描述每一步输入、输出和意义，章节组织方式与 testing-agent 和 cypher-generator-agent 文档保持一致。

### Step 1: 接收失败问题单

接口：

```text
POST /api/v1/issue-tickets
```

请求体结构：

```json
{
  "ticket_id": "ticket-q-001-attempt-2",
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
    "verdict": "fail",
    "primary_metrics": {
      "grammar": {
        "score": 1,
        "parser_error": null,
        "message": null
      },
      "execution_accuracy": {
        "score": 0,
        "reason": "strict_mismatch"
      }
    },
    "secondary_signals": {
      "gleu": {
        "score": 0.41
      },
      "jaro_winkler_similarity": {
        "score": 0.78
      }
    }
  },
  "generation_evidence": {
    "generation_run_id": "gen-001",
    "attempt_no": 2,
    "input_prompt_snapshot": "..."
  },
  "input_prompt_snapshot": "..."
}
```

请求体字段定义与 [1.2 IssueTicket](#12-issueticket) 和 [1.3 GenerationEvidence](#13-generationevidence) 一致。

### Step 2: 幂等检查

repair-agent 根据 `ticket_id` 派生：

```text
analysis_id = analysis-{ticket_id}
```

如果该记录已存在，repair-agent 直接返回存量结果，不重新诊断，也不重复投递 knowledge-agent。

### Step 3: 读取 prompt snapshot

repair-agent 当前不向 cypher-generator-agent 拉取 prompt snapshot。

来源优先级：

```text
IssueTicket.generation_evidence.input_prompt_snapshot
  -> IssueTicket.input_prompt_snapshot
```

`generation_evidence` 是 testing-agent 从 cypher-generator-agent submission 中保存并写入问题单的生成证据。repair-agent 使用它，是为了保证根因分析和失败评测使用同一 attempt 的事实快照。

### Step 4: 构造 DiagnosisContext

repair-agent 通过 `build_diagnosis_context(ticket, prompt_snapshot)` 构造 `DiagnosisContext`。

输入：

```text
IssueTicket
prompt_snapshot
recent_applied_repairs（当前主流程默认空）
```

输出：

```json
{
  "question": "...",
  "difficulty": "L3",
  "sql_pair": {
    "expected_cypher": "MATCH ...",
    "actual_cypher": "MATCH ..."
  },
  "evaluation_summary": {
    "verdict": "fail",
    "primary_metrics": {
      "grammar": {
        "score": 1,
        "parser_error": null,
        "message": null
      },
      "execution_accuracy": {
        "score": 0,
        "reason": "strict_mismatch"
      }
    },
    "secondary_signals": {
      "gleu": {
        "score": 0.41
      },
      "jaro_winkler_similarity": {
        "score": 0.78
      }
    }
  },
  "failure_diff": {
    "ordering_problem": false,
    "limit_problem": false,
    "return_shape_problem": true,
    "entity_or_relation_problem": true,
    "execution_problem": false,
    "syntax_problem": false,
    "missing_or_wrong_clauses": ["relation_path"],
    "semantic_mismatch_summary": "strict_check failed while grammar passed"
  },
  "prompt_evidence": "压缩后的完整 prompt",
  "generation_evidence": {
    "generation_run_id": "gen-001",
    "attempt_no": 2,
    "input_prompt_snapshot": "压缩后的 prompt"
  },
  "relevant_prompt_fragments": {
    "system_rules_fragment": "...",
    "business_knowledge_fragment": "...",
    "few_shot_fragment": "...",
    "recent_repair_fragment": "..."
  },
  "recent_applied_repairs": []
}
```

### Step 5: LLM-first diagnosis

repair-agent 将 `DiagnosisContext` 压缩后送入 LLM 诊断客户端。

LLM 必须返回 JSON：

| 字段 | 含义 |
| --- | --- |
| `primary_knowledge_type` | 主知识类型 |
| `secondary_knowledge_types` | 次要知识类型 |
| `candidate_patch_types` | 需要验证的候选类型 |
| `confidence` | 置信度 |
| `suggestion` | 修复建议 |
| `rationale` | 诊断理由 |
| `need_validation` | 是否需要 lightweight validation |

如果 LLM 返回旧字段 `knowledge_types`，repair-agent 会兼容地将第一个类型视为 primary，其余类型视为 secondary。

### Step 6: knowledge type 归一化

repair-agent 不直接信任 LLM 输出的知识类型。

归一化规则：

- `primary_knowledge_type` 必须属于四个正式类型，否则回退为 `system_prompt`。
- `secondary_knowledge_types` 和 `candidate_patch_types` 中的非法类型会被丢弃。
- 重复类型会被去重。
- `confidence` 会被限制在 0 到 1 之间，非法或非有限值回退为 0。

这一步的意义是：保证出站到 knowledge-agent 的 `knowledge_types` 不会被 LLM 幻觉污染。

### Step 7: lightweight validation

lightweight validation 是可选步骤。

触发条件：

```text
LLM 返回 need_validation = true
且 candidate_patch_types 非空
且 repair 分析器配置了 experiment_runner
```

当前默认服务配置会注入 `_lightweight_experiment_runner`，因此 repair-agent 具备执行 lightweight validation 的能力。

当前触发逻辑不由 `min_confidence_for_direct_return` 阈值决定。该配置仍存在于分析器构造参数中，但主流程实际只看 LLM 返回的 `need_validation`、候选类型和 `experiment_runner` 是否存在。

但当前 lightweight validation 不是重新生成或真实对照实验。它不做：

- 不重新调用 cypher-generator-agent。
- 不重新执行 TuGraph。
- 不向 knowledge-agent 拉补丁包。
- 不验证知识修复后是否通过评测。

它当前做的是类型级启发式筛选：

| 判断项 | 含义 |
| --- | --- |
| `explanatory_power` | 候选知识类型是否能解释 `failure_diff` |
| `duplicate_repair` | 最近修复记录中是否已有同类修复 |
| `fragment_conflict` | 当前 prompt fragments 是否已经覆盖该类型 |

判定规则：

```text
improved = explanatory_power
           and not duplicate_repair
           and not fragment_conflict
```

如果有候选类型通过验证，repair-agent 优先选择验证指标最高的类型；如果没有候选类型通过验证，则回退到 LLM 的 primary knowledge type。

### Step 8: 生成知识修复建议

repair 分析结果会生成正式出站请求：

```json
{
  "id": "q-001",
  "suggestion": "Add a few-shot example that covers Service -> Tunnel traversal.",
  "knowledge_types": ["few_shot"]
}
```

### Step 9: knowledge-agent apply

接口：

```text
POST /api/knowledge/repairs/apply
```

出站 payload：

```json
{
  "id": "q-001",
  "suggestion": "...",
  "knowledge_types": ["business_knowledge", "few_shot"]
}
```

成功语义：

```text
只有 HTTP 200 视为 apply 成功。
```

transport error 与可重试非 200 响应会重试；4xx 直接终止。

### Step 10: 保存分析记录并返回响应

knowledge-agent apply 成功后，repair-agent 保存 repair 分析记录。

默认路径：

```text
data/repair_service/analyses/<analysis_id>.json
```

写接口响应示例：

```json
{
  "status": "applied",
  "analysis_id": "analysis-ticket-q-001-attempt-2",
  "id": "q-001",
  "knowledge_repair_request": {
    "id": "q-001",
    "suggestion": "...",
    "knowledge_types": ["few_shot"]
  },
  "knowledge_ops_response": {
    "status": "ok"
  },
  "applied": true
}
```

只有保存记录后，repair-agent 写接口才返回 `status = applied`。

## 4. 对外接口

### 4.1 提交失败问题单

`POST /api/v1/issue-tickets`

请求体为 `IssueTicket`，字段定义见 [1.2 IssueTicket](#12-issueticket) 与 [1.3 GenerationEvidence](#13-generationevidence)。

成功响应示例：

```json
{
  "status": "applied",
  "analysis_id": "analysis-ticket-q-001-attempt-2",
  "id": "q-001",
  "knowledge_repair_request": {
    "id": "q-001",
    "suggestion": "...",
    "knowledge_types": ["few_shot"]
  },
  "knowledge_ops_response": {
    "status": "ok"
  },
  "applied": true
}
```

### 4.2 查询分析记录

`GET /api/v1/analyses/{analysis_id}`

返回指定 repair 分析记录。该接口用于回放根因分析结果和排查诊断输入，不用于生成新的修复建议。

### 4.3 健康与服务状态

`GET /health`

用于服务存活检查。

```json
{
  "status": "ok",
  "service": "repair-agent"
}
```

`GET /api/v1/status`

用于排查 repair-agent 当前运行配置、存储路径、knowledge-agent 连接配置和 LLM 诊断配置。它不承担运行中心的跨服务聚合职责。

```json
{
  "storage": "data/repair_service",
  "knowledge_agent_apply_url": "...",
  "llm_enabled": true,
  "llm_model": "...",
  "mode": "repair_apply",
  "diagnosis_mode": "llm"
}
```

## 5. 运行结果模型与诊断契约

### 5.1 正式输出结构

repair-agent 对外有两类正式输出：

- 写接口成功响应：表示一次失败问题单已经完成根因分析、knowledge-agent apply 与分析记录落盘。
- repair 分析记录：表示一次具体失败事件的稳定诊断事实，可用于回放、审计和排查。

写接口成功响应结构如下：

```json
{
  "status": "applied",
  "analysis_id": "analysis-ticket-q-001-attempt-2",
  "id": "q-001",
  "knowledge_repair_request": {
    "id": "q-001",
    "suggestion": "...",
    "knowledge_types": ["few_shot"]
  },
  "knowledge_ops_response": {
    "status": "ok"
  },
  "applied": true
}
```

repair 分析记录在此基础上额外保留 `prompt_snapshot`、`confidence`、`rationale`、`validation_result` 和 `diagnosis_context_summary` 等追溯字段。

### 5.2 repair-agent 写接口状态

repair-agent 写接口当前只返回一种成功状态：

```text
status = applied
```

它表示：

- repair-agent 已完成根因诊断。
- repair-agent 已生成知识修复请求。
- knowledge-agent apply 返回成功。
- repair-agent 已保存分析记录。

它不表示：

- 知识补丁已经被验证有效。
- 下一轮 cypher-generator-agent 生成一定通过。
- 业务问题已经最终解决。

### 5.3 validation 结果

`validation_mode` 只有两种：

| 取值 | 含义 |
| --- | --- |
| `disabled` | 未执行 lightweight validation |
| `lightweight` | 执行了类型级启发式筛选 |

`used_experiments = true` 表示执行了 lightweight validation。这里的 experiments 是历史命名，不代表真实重生成或真实对照实验。

### 5.4 knowledge type 语义

| 类型 | 含义 |
| --- | --- |
| `cypher_syntax` | knowledge-agent 知识包中的 Cypher 写法提示、只读查询约束或查询模式知识不清晰；不包含 cypher-generator-agent 固定输出协议和提交前基础检查实现 |
| `few_shot` | 示例覆盖不足，模型缺少相似问题的正确模式 |
| `system_prompt` | knowledge-agent 知识包中的系统级查询约束、术语解释或生成注意事项不清晰；不包含 cypher-generator-agent 固定生成协议、输出契约、兜底解析或提交前 Cypher 基础检查 |
| `business_knowledge` | 业务术语、实体关系、领域映射或图谱语义知识不足 |

这些类型用于指导 knowledge-agent 选择修复位置，不等同于最终业务根因裁决。

### 5.5 IssueTicket 输入契约

需要确保问题单包含：

- `ticket_id`
- `id`
- `question`
- `expected.cypher`
- `actual.generated_cypher`
- `actual.execution`
- `evaluation.verdict`
- `evaluation.primary_metrics`
- `evaluation.secondary_signals`
- `generation_evidence.input_prompt_snapshot`

### 5.6 prompt evidence 契约

需要确保：

- repair-agent 不从 cypher-generator-agent 拉 prompt snapshot。
- repair-agent 优先使用 `IssueTicket.generation_evidence.input_prompt_snapshot`。
- 中文或结构化 prompt 能进入 `prompt_evidence`。
- `relevant_prompt_fragments` 只是辅助字段，不能作为唯一 prompt 证据。

### 5.7 LLM diagnosis 契约

需要确保：

- LLM 返回 JSON object。
- 允许的知识类型只有四个正式类型。
- 非法类型会被清洗或回退。
- `need_validation` 缺失时兼容旧字段 `need_experiments`。

### 5.8 knowledge-agent apply 契约

需要确保：

- 出站 payload 只含 `id`、`suggestion`、`knowledge_types`。
- `knowledge_types` 只允许正式类型。
- 只有 HTTP 200 视为 apply 成功。
- apply 成功前不保存 applied 记录。

## 6. 运行状态、持久化与错误处理

### 6.1 运行状态

repair-agent 使用写接口成功状态与分析记录是否落盘来表达一次处理流程的最终结果。

| 状态 | 含义 |
| --- | --- |
| `analysis_pending` | 已收到 `IssueTicket`，正在执行根因分析，尚未形成正式成功结果 |
| `apply_failed` | 已完成诊断，但 knowledge-agent apply 失败，本次请求不保存 applied 记录 |
| `applied` | 已完成诊断、knowledge-agent apply 成功，且 repair 分析记录已落盘 |

其中，`applied` 是当前对外稳定可见的正式成功状态；其余状态用于解释 repair-agent 的运行过程和失败语义。

### 6.2 持久化与追溯

repair-agent 使用：

```text
analysis_id = analysis-{ticket_id}
```

原因：

- `id` 表示 QA 样本。
- `ticket_id` 表示一次具体失败事件。
- 同一个 `id` 可以有多轮 attempt，每一轮失败都应有独立分析记录。

默认数据目录：

```text
data/repair_service/analyses/
```

repair-agent 会在 repair 分析记录中保留：

- prompt snapshot。
- 知识修复请求。
- knowledge-agent 响应。
- 诊断置信度。
- LLM rationale。
- lightweight validation 结果。
- 诊断上下文摘要。

这些字段构成后续 RCA、回放、控制台展示和外部审计的依据。

### 6.3 prompt snapshot 留存

repair 分析记录中的 `prompt_snapshot` 保存的是 repair-agent 实际用于诊断的 prompt。

当前来源：

```text
IssueTicket.generation_evidence.input_prompt_snapshot
  -> IssueTicket.input_prompt_snapshot
```

它不是 repair-agent 从 cypher-generator-agent 在线查询得到的。

### 6.4 输入证据缺失

如果 `generation_evidence` 不存在，repair-agent 会回退到兼容字段 `input_prompt_snapshot`。

如果 prompt 为空，repair-agent 不会伪造 prompt；诊断上下文中的 prompt evidence 为空，LLM 仍基于其他失败证据进行诊断。后续可以把“缺失 prompt evidence”升级为显式输入契约错误。

### 6.5 诊断失败

如果 LLM 诊断、JSON 解析或上下文处理失败，repair-agent 不生成半结构化假请求，异常向上抛出。

### 6.6 knowledge-agent apply 失败

如果 apply 失败：

- transport error 可重试。
- 5xx、202、204 等非 200 响应按可重试路径处理。
- 4xx 直接终止。
- 不保存“已应用”的分析记录。

这保证了 repair-agent 写接口响应中的 `status=applied` 与真正的 apply 成功严格一致。

### 6.7 幂等记录已存在

如果 `analysis-{ticket_id}` 已存在，repair-agent 直接返回历史记录。

当前实现是读后判断，不是强并发原子 claim。并发重复提交同一 ticket 时，仍可能出现重复 apply 风险。后续如果进入生产并发场景，应将 repository 扩展为原子占位或锁机制。

## 7. 结论

repair-agent 是一个“把 testing-agent 的失败问题单转成正式 knowledge-agent 知识包修复请求”的服务：它消费失败样本、评测证据和生成证据，判断失败是否属于 knowledge-agent 知识缺口，形成最小修复建议，并在 knowledge-agent apply 成功后保存可追踪的分析记录。cypher-generator-agent 固定生成协议、兜底解析、提交前 Cypher 基础检查和 testing-agent 评测器问题属于工程缺陷，不进入 repair-agent -> knowledge-agent 的业务知识修复闭环。
