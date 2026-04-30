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
- knowledge-agent apply on success：只有 knowledge-agent apply 成功后，repair-agent 写接口才返回 `applied`。
- Analysis record idempotency by ticket：按 `ticket_id` 保存并复用分析记录。

## 1. 术语与核心数据结构

本章定义 repair-agent 文档中反复出现的业务术语和数据结构。后续流程、接口和责任边界都以这些对象为基础。

### 1.1 QA 样本与 `id`

`id` 是一条 QA 样本在闭环中的主键。它贯穿 qa-agent、cypher-generator-agent、testing-agent 和 repair-agent。

repair-agent 不生成新的 `id`，也不改变 `id` 的含义；它只消费 testing-agent 在失败问题单中传入的 `id`，并将其继续透传到正式的 `KnowledgeRepairSuggestionRequest` 中。

### 1.2 `IssueTicket`

`IssueTicket` 是 testing-agent 提交给 repair-agent 的失败问题单。它只在 testing-agent 对某次成功 submission 或可评分的 `GenerationRunFailureReport` 的最终评测结论不是 `pass` 时生成；`service_failed` 只由 testing-agent 落盘，不进入 repair-agent。

它包含：

| 字段 | 含义 |
| --- | --- |
| `ticket_id` | 问题单唯一 ID，当前格式通常为 `ticket-{id}-attempt-{attempt_no}` |
| `id` | QA 样本主键 |
| `difficulty` | 题目难度，取值为 `L1` 到 `L8` |
| `question` | 原始自然语言问题 |
| `expected` | 黄金 Cypher 与黄金答案 |
| `actual` | cypher-generator-agent 生成 Cypher 或生成失败时的候选文本，与 testing-agent 实际执行结果 |
| `evaluation` | testing-agent 生成的评测结论，只包含 `verdict`、`primary_metrics` 与 `secondary_signals` |
| `generation_evidence` | cypher-generator-agent 生成过程证据，由 testing-agent 从 `GeneratedCypherSubmissionRequest` 或 `GenerationRunFailureReport` 中复制到问题单 |

它的意义是：把一次失败事件中 repair-agent 需要的事实都固定下来。repair-agent 不再为了补齐 prompt 或生成证据回头查询 cypher-generator-agent。

### 1.3 `GenerationEvidence`

`GenerationEvidence` 表示 cypher-generator-agent 生成阶段留下的过程证据。它的来源是 cypher-generator-agent 提交给 testing-agent 的 `GeneratedCypherSubmissionRequest` 或 `GenerationRunFailureReport`。testing-agent 不重新生成这些字段，只在接收时保存，并在生成 `IssueTicket` 时复制到 `generation_evidence`。

它包含：

| 字段 | 含义 |
| --- | --- |
| `generation_run_id` | cypher-generator-agent 本次生成运行 ID，用于串联 cypher-generator-agent 日志和问题单 |
| `attempt_no` | testing-agent 记录的尝试序号 |
| `input_prompt_snapshot` | cypher-generator-agent 本轮生成实际使用的 prompt 快照 |
| `last_llm_raw_output` | 最后一次生成尝试的大模型原始输出 |
| `generation_status` | `generated` 或 `generation_failed`；`service_failed` 不进入 repair-agent |
| `failure_reason` | 生成失败的固定原因；成功 submission 为空 |
| `generation_retry_count` | cypher-generator-agent 额外重试次数 |
| `generation_failure_reasons` | 历史生成失败原因列表 |

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

它的意义是：给 LLM 一个稳定的失败现象骨架。它不是最终根因结论。

### 1.6 `prompt_evidence` 与 `relevant_prompt_fragments`

repair-agent 同时保留两类 prompt 证据：

| 字段 | 来源 | 作用 |
| --- | --- | --- |
| `prompt_evidence` | `generation_evidence.input_prompt_snapshot` | 压缩后的完整 prompt 证据，保证中文和结构化 prompt 不会被抽空 |
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
| `repairable` | 是否应转成 knowledge-agent 知识包修复请求 |
| `non_repairable_reason` | `repairable=false` 时的非知识包缺口说明 |
| `rationale` | LLM 给出的归因理由 |
| `primary_knowledge_type` | LLM 诊断的主知识类型 |
| `secondary_knowledge_types` | 次要知识类型 |
| `diagnosis_context_summary` | 诊断上下文摘要，用于追踪与审计 |

它的意义是：把 LLM 诊断和最终知识类型选择汇总成可保存、可投递的内部结果。

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
| `knowledge_agent_response` | knowledge-agent apply 响应 |
| `confidence` | 诊断置信度 |
| `rationale` | 根因说明 |
| `primary_knowledge_type` | 主知识类型 |
| `secondary_knowledge_types` | 次要知识类型 |
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
| LLM 诊断 | `DiagnosisContext` | diagnosis JSON | 判断知识类型、置信度、建议和理由 |
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
        "reason": "not_equivalent",
        "strict_check": {
          "status": "fail",
          "message": "结果行不一致",
          "order_sensitive": false,
          "expected_row_count": 1,
          "actual_row_count": 0
        },
        "semantic_check": {
          "status": "fail",
          "message": "语义不等价",
          "raw_output": null
        }
      }
    },
    "secondary_signals": {
      "gleu": {
        "score": 0.41,
        "tokenizer": "whitespace",
        "min_n": 1,
        "max_n": 4
      },
      "jaro_winkler_similarity": {
        "score": 0.78,
        "normalization": "lightweight",
        "library": "jellyfish"
      }
    }
  },
  "generation_evidence": {
    "generation_run_id": "gen-001",
    "attempt_no": 2,
    "input_prompt_snapshot": "...",
    "last_llm_raw_output": "MATCH ...",
    "generation_status": "generated",
    "failure_reason": null,
    "generation_retry_count": 0,
    "generation_failure_reasons": []
  }
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

唯一正式来源：

```text
IssueTicket.generation_evidence.input_prompt_snapshot
```

repair-agent 只读取 `IssueTicket.generation_evidence.input_prompt_snapshot`。`generation_evidence.input_prompt_snapshot` 缺失时，请求会被输入模型拒绝；字段存在但为空字符串时，本轮 prompt evidence 为空。repair-agent 不伪造 prompt，不回查 cypher-generator-agent。

`generation_evidence` 是 testing-agent 从 cypher-generator-agent 的成功 submission 或可评分生成失败报告中保存并写入问题单的生成证据。repair-agent 使用它，是为了保证根因分析和失败评测使用同一 attempt 的事实快照。

### Step 4: 构造 DiagnosisContext

repair-agent 通过确定性代码 `build_diagnosis_context(ticket, prompt_snapshot)` 构造 `DiagnosisContext`。这个步骤不是 LLM 调用，也不会让模型重写、补全或解释字段；它只是把 `IssueTicket` 中已经固定的事实整理成结构化 JSON。

构造规则：

| DiagnosisContext 字段 | 来源或派生规则 |
| --- | --- |
| `ticket_id`、`id`、`question`、`difficulty` | 直接来自 `IssueTicket` |
| `sql_pair.expected_cypher` | `IssueTicket.expected.cypher` |
| `sql_pair.actual_cypher` | `IssueTicket.actual.generated_cypher`；成功 submission 时是生成 query，`generation_failed` 时是 testing-agent 派生的候选文本 |
| `evaluation_summary` | `IssueTicket.evaluation.verdict`、`primary_metrics`、`secondary_signals` |
| `failure_diff` | repair-agent 基于 expected/actual/evaluation 派生的失败摘要 |
| `prompt_evidence` | 从正式 prompt snapshot 压缩、去重后保留的完整 prompt 证据 |
| `generation_evidence` | `IssueTicket.generation_evidence` 的生成证据摘要 |
| `relevant_prompt_fragments` | 从 prompt snapshot 中抽取 system、business、few-shot、recent repair 片段，只作辅助证据 |
| `recent_applied_repairs` | 最近已应用修复；当前主流程默认空 |

字段处理细节：

1. `sql_pair`：直接复制 golden Cypher 和 actual query/candidate text，不做语法修复、不做归一化改写。
2. `evaluation_summary`：直接序列化 testing-agent 的 `EvaluationSummary`，保留 `grammar`、`execution_accuracy`、`gleu`、`jaro_winkler_similarity` 等评测事实。
3. `failure_diff`：由 repair-agent 用规则派生，只描述症状：
   - `ordering_problem`：golden 有 `ORDER BY` 但 actual 没有。
   - `limit_problem`：golden 有 `LIMIT`，actual 缺失或 limit 数值不同。
   - `return_shape_problem`：两边都有 `RETURN`，但 return clause 结构不同。
   - `syntax_problem`：testing-agent 的 grammar score 为 0 或存在 parser error。
   - `execution_problem`：TuGraph execution 失败或有错误信息。
   - `entity_or_relation_problem`：execution accuracy 为 0，且 strict/semantic 失败、相似度较低，或问题/golden/actual 显示实体关系路径相关。
4. `prompt_evidence`：从 `generation_evidence.input_prompt_snapshot` 构造，按行去重，跳过空行和 `appendix:` 行；超过 1200 字符时保留头尾，中间插入 `...[prompt truncated]...`。
5. `generation_evidence`：复制 testing-agent 的 generation evidence，并把其中的 `input_prompt_snapshot` 替换为最多 600 字符的压缩 prompt evidence，避免在诊断上下文中重复塞入完整 prompt。
6. `relevant_prompt_fragments`：用简单关键词从 prompt snapshot 抽取辅助片段：
   - 包含 `system` 的首个片段进入 `system_rules_fragment`。
   - 包含 `business` 的首个片段进入 `business_knowledge_fragment`。
   - 包含 `few-shot` 或 `few_shot` 的片段进入 `few_shot_fragment`。
   - 包含 `repair` 的片段进入 `recent_repair_fragment`。
   这些片段只帮助模型定位 prompt 中已有材料，不能替代完整 `prompt_evidence`。

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
        "reason": "not_equivalent",
        "strict_check": {
          "status": "fail",
          "message": "结果行不一致",
          "order_sensitive": false,
          "expected_row_count": 1,
          "actual_row_count": 0
        },
        "semantic_check": {
          "status": "fail",
          "message": "语义不等价",
          "raw_output": null
        }
      }
    },
    "secondary_signals": {
      "gleu": {
        "score": 0.41,
        "tokenizer": "whitespace",
        "min_n": 1,
        "max_n": 4
      },
      "jaro_winkler_similarity": {
        "score": 0.78,
        "normalization": "lightweight",
        "library": "jellyfish"
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
    "input_prompt_snapshot": "压缩后的 prompt",
    "last_llm_raw_output": "MATCH ...",
    "generation_status": "generated",
    "failure_reason": null,
    "generation_retry_count": 0,
    "generation_failure_reasons": []
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

`sql_pair.actual_cypher` 来自 `IssueTicket.actual.generated_cypher`。对成功 submission，它是本次生成的 query；对 `generation_failed`，它是 testing-agent 从 `parsed_cypher` 或 `last_llm_raw_output` 派生出的候选文本。

### Step 5: LLM-first diagnosis

repair-agent 将 `IssueTicketSummary`、压缩后的 `DiagnosisContext` 和字段解释说明用固定模板组装成 LLM user message。提示词组装在 `prompting.py` 中通过直接模板拼接完成，不经过 LLM 预处理。

System message 固定模板：

```text
你是 Text2Cypher 系统中的知识修复诊断器。
你的任务不是修 query，而是判断 knowledge-agent 应该补哪类知识。
你只能使用这些知识类型：cypher_syntax, few_shot, system_prompt, business_knowledge。
只返回 JSON，不要返回 Markdown、解释性正文或额外字段。
JSON 必须包含这些字段：repairable, non_repairable_reason, primary_knowledge_type, secondary_knowledge_types, confidence, suggestion, rationale。
```

User message 固定模板：

```text
IssueTicketSummary: {compact_issue_ticket_json}
DiagnosisContext: {compact_diagnosis_context_json}
诊断顺序：
1. 先看 DiagnosisContext.generation_evidence：如果 generation_status=generation_failed，优先根据 failure_reason、generation_failure_reasons、generation_retry_count、last_llm_raw_output 判断失败发生在输出协议、Cypher 语法还是知识缺失。
2. 再看 evaluation.primary_metrics.grammar：如果 grammar.score=0 或 parser_error 非空，优先考虑 cypher_syntax。
3. 再看 IssueTicketSummary.actual.execution：如果 execution.success=false 或 error_message 非空，结合错误判断是 cypher_syntax 还是 business_knowledge。
4. 再看 execution_accuracy.strict_check 和 semantic_check：如果语法和执行都成功但 strict/semantic 失败，说明 query 语义不等价；比较 expected.cypher 与 actual.generated_cypher，找缺失的 label、relation、path、filter、return shape。
5. 最后看 DiagnosisContext.prompt_evidence：判断 expected 需要的 schema/path/business term/输出约束在 prompt snapshot 里是否存在，以及 actual 是否正确使用了这些知识。
字段说明：
- IssueTicketSummary.expected.cypher 是标准答案 query。
- IssueTicketSummary.actual.generated_cypher 是本次提交的 query；如果 generation_failed，则是 testing-agent 派生出的候选文本。
- evaluation.primary_metrics.grammar 是语法检查结果；parser_error 是语法失败的直接证据。
- IssueTicketSummary.actual.execution 是实际执行结果；error_message 是执行失败的直接证据。
- execution_accuracy.strict_check 是结果集严格比较；semantic_check 是语义等价判断。
- secondary_signals.gleu 和 jaro_winkler_similarity 只能作为相似度辅助信号，不能单独决定根因。
- DiagnosisContext.generation_evidence 记录生成过程证据，用来判断失败发生在生成、解析、重试还是评估阶段。
- DiagnosisContext.failure_diff 是 repair-agent 用确定性代码生成的失败症状摘要，不等同于最终根因。
- DiagnosisContext.prompt_evidence 是 cypher-generator-agent 当次使用的 prompt snapshot，已压缩用于诊断。
- DiagnosisContext.relevant_prompt_fragments 是辅助提取片段；不能脱离 prompt_evidence 单独作为判断依据。
- 你只诊断 knowledge-agent 知识缺口，不诊断 cypher-generator-agent 固定协议、parser、preflight 或 testing-agent evaluator 的 bug。
知识类型选择规则：
- cypher_syntax：Cypher 语法、括号、非法 clause、写操作、unsupported call、unsupported start clause 等问题。
- few_shot：prompt_evidence 中已经有相关规则或业务知识，但缺少相似示例，导致模型没有把规则迁移到当前问题。
- system_prompt：输出格式、只输出 Cypher、禁止解释、禁止 Markdown、固定协议、边界约束不清。
- business_knowledge：schema、label、relation、path、filter、业务术语映射、实体关系知识缺失或错误。
判断 prompt_evidence 的规则：
- 如果 expected.cypher 需要的 schema/path/business term 在 prompt_evidence 中不存在，倾向 business_knowledge。
- 如果 prompt_evidence 中已有必要规则，但 actual.generated_cypher 没有正确套用，倾向 few_shot。
- 如果失败主要来自输出包装、解释文本、Markdown、非 JSON/非 Cypher 边界不清，倾向 system_prompt。
- 如果失败主要来自 Cypher 结构本身不合法，倾向 cypher_syntax。
输出 JSON schema：{"repairable": boolean, "non_repairable_reason": string, "primary_knowledge_type": string, "secondary_knowledge_types": string[], "confidence": number, "suggestion": string, "rationale": string}。
如果 repairable=false，non_repairable_reason 必须说明为什么这不是 knowledge-agent 知识包缺口。
primary_knowledge_type 和 secondary_knowledge_types 只能使用这些值：cypher_syntax, few_shot, system_prompt, business_knowledge。
```

其中：

- `compact_issue_ticket_json` 只保留 ticket id、QA id、difficulty、question、expected cypher、actual query/candidate text、execution 摘要和 evaluation 摘要。
- `compact_diagnosis_context_json` 保留 `sql_pair`、`evaluation_summary`、`failure_diff`、压缩后的 `prompt_evidence`、去掉完整 `input_prompt_snapshot` 的 `generation_evidence`、`relevant_prompt_fragments` 和 `recent_applied_repairs`。
- prompt snapshot 会先按行去重；过长时保留头尾，中间用 `...[prompt truncated]...` 标记。
- 如果 `relevant_prompt_fragments` 已经包含某些行，`prompt_evidence` 会过滤重复行，避免同一知识片段在 user prompt 中重复出现。

`IssueTicketSummary` 的构造规则：

| 字段 | 来源 | 处理 |
| --- | --- | --- |
| `ticket_id`、`id`、`difficulty`、`question` | `IssueTicket` | 原样放入 |
| `expected.cypher` | `IssueTicket.expected.cypher` | 原样放入，不附带 golden answer 全量数据 |
| `actual.generated_cypher` | `IssueTicket.actual.generated_cypher` | 原样放入 |
| `actual.execution.success`、`row_count`、`elapsed_ms` | `IssueTicket.actual.execution` | 原样放入 |
| `actual.execution.error_message` | `IssueTicket.actual.execution.error_message` | 最多保留 240 字符 |
| `evaluation` | `IssueTicket.evaluation` | 序列化 `verdict`、`primary_metrics`、`secondary_signals` |

LLM 请求必须启用 JSON object 响应格式；如果底层模型接口支持，使用 `response_format = {"type": "json_object"}`。LLM 必须返回 JSON object：

| 字段 | 含义 |
| --- | --- |
| `repairable` | 是否属于 knowledge-agent 知识包缺口 |
| `non_repairable_reason` | 非知识包缺口原因；可修复时为空字符串 |
| `primary_knowledge_type` | 主知识类型 |
| `secondary_knowledge_types` | 次要知识类型 |
| `confidence` | 置信度 |
| `suggestion` | 修复建议 |
| `rationale` | 诊断理由 |

当前 LLM 客户端要求以上字段全部存在；缺字段视为诊断响应不符合契约并抛错。

### Step 6: knowledge type 归一化

repair-agent 不直接信任 LLM 输出的知识类型。

归一化规则：

- `primary_knowledge_type` 必须属于四个正式类型，否则默认置为 `system_prompt`。
- `secondary_knowledge_types` 中的非法类型会被丢弃。
- 重复类型会被去重。
- `confidence` 会被限制在 0 到 1 之间，非法或非有限值默认置为 0。

这一步的意义是：保证出站到 knowledge-agent 的 `knowledge_types` 不会被 LLM 幻觉污染。

### Step 7: 生成知识修复建议

repair 分析结果会生成正式出站请求：

```json
{
  "id": "q-001",
  "suggestion": "Add a few-shot example that covers Service -> Tunnel traversal.",
  "knowledge_types": ["few_shot"]
}
```

### Step 8: knowledge-agent apply

接口：

```text
POST /api/knowledge/repairs/apply
```

出站 payload：

```json
{
  "id": "q-001",
  "suggestion": "...",
  "knowledge_types": ["business_knowledge"]
}
```

出站 `knowledge_types` 只包含 LLM 诊断得到的 `primary_knowledge_type`。`secondary_knowledge_types` 只保存在分析记录中，用于审计和人工回看，不进入当前 knowledge-agent apply 请求。

成功语义：

```text
只有 HTTP 200 视为 apply 成功。
```

transport error 与可重试非 200 响应会重试；4xx 直接终止。

### Step 9: 保存分析记录并返回响应

repair-agent 在生成可修复诊断后先保存 `analysis_pending` 分析记录，再调用 knowledge-agent apply。apply 失败时更新为 `apply_failed`；apply 成功后更新为 `applied` 并返回写接口响应。

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
  "knowledge_agent_response": {
    "status": "ok"
  },
  "applied": true
}
```

只有分析记录更新为 `applied` 后，repair-agent 写接口才返回 `status = applied`。

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
  "knowledge_agent_response": {
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
  "llm_configured": true,
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
  "knowledge_agent_response": {
    "status": "ok"
  },
  "applied": true
}
```

repair 分析记录在此基础上额外保留 `prompt_snapshot`、`confidence`、`rationale`、LLM prompt snapshots 和 `diagnosis_context_summary` 等追溯字段。

### 5.2 repair-agent 写接口状态

repair-agent 写接口和分析记录当前使用这些状态：

| 状态 | 含义 |
| --- | --- |
| `analysis_pending` | 已保存待 apply 的中间记录；主要用于内部追踪 |
| `apply_failed` | 已完成诊断，但 knowledge-agent apply 失败 |
| `applied` | 已完成诊断、knowledge-agent apply 成功，且 repair 分析记录已落盘 |
| `not_repairable` | 已完成诊断，但失败原因不属于 knowledge-agent 知识包缺口 |
| `repair_apply_paused` | 已完成诊断，但 knowledge-agent 明确返回 `KNOWLEDGE_REPAIR_APPLY_DISABLED`，本次请求不写入知识包且不向上游抛 500 |

`status = applied` 表示：

- repair-agent 已完成根因诊断。
- repair-agent 已生成知识修复请求。
- knowledge-agent apply 返回成功。
- repair-agent 已保存分析记录。

它不表示：

- 知识补丁已经被验证有效。
- 下一轮 cypher-generator-agent 生成一定通过。
- 业务问题已经最终解决。

### 5.3 knowledge type 语义

| 类型 | 含义 |
| --- | --- |
| `cypher_syntax` | knowledge-agent 知识包中的 Cypher 写法提示、只读查询约束或查询模式知识不清晰；不包含 cypher-generator-agent 固定输出协议和提交前基础检查实现 |
| `few_shot` | 示例覆盖不足，模型缺少相似问题的正确模式 |
| `system_prompt` | knowledge-agent 知识包中的系统级查询约束、术语解释或生成注意事项不清晰；不包含 cypher-generator-agent 固定生成协议、输出契约、兜底解析或提交前 Cypher 基础检查 |
| `business_knowledge` | 业务术语、实体关系、领域映射或图谱语义知识不足 |

这些类型用于指导 knowledge-agent 选择修复位置，不等同于最终业务根因裁决。

### 5.4 IssueTicket 输入契约

需要确保问题单包含：

- `ticket_id`
- `id`
- `question`
- `expected.cypher`
- `actual.generated_cypher`，成功 submission 时为生成 query，`generation_failed` 时为 testing-agent 派生的候选文本
- `actual.execution`，可为 `null`；grammar 失败或 `generation_failed` 路径不会执行 TuGraph
- `evaluation.verdict`
- `evaluation.primary_metrics`
- `evaluation.secondary_signals`
- `generation_evidence.input_prompt_snapshot`

### 5.5 prompt evidence 契约

需要确保：

- repair-agent 不从 cypher-generator-agent 拉 prompt snapshot。
- repair-agent 只使用 `IssueTicket.generation_evidence.input_prompt_snapshot`。
- 中文或结构化 prompt 能进入 `prompt_evidence`。
- `relevant_prompt_fragments` 只是辅助字段，不能作为唯一 prompt 证据。

### 5.6 LLM diagnosis 契约

需要确保：

- LLM 返回 JSON object。
- JSON object 必须包含 `repairable`、`non_repairable_reason`、`primary_knowledge_type`、`secondary_knowledge_types`、`confidence`、`suggestion`、`rationale`。
- 允许的知识类型只有四个正式类型。
- 非法类型会被清洗或默认置为正式类型。
- LLM 返回字段不包含实验、验证或重跑控制字段。

### 5.7 knowledge-agent apply 契约

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
| `not_repairable` | 已完成诊断，但失败原因不属于 knowledge-agent 知识缺口，不执行 apply |
| `repair_apply_paused` | 已完成诊断，但 knowledge-agent apply 当前被关闭；保存分析与暂停响应，`applied=false` |

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
- LLM system prompt 和 user prompt 快照。
- 诊断上下文摘要。

这些字段构成后续 RCA、回放、控制台展示和外部审计的依据。

### 6.3 prompt snapshot 留存

repair 分析记录中的 `prompt_snapshot` 保存的是 repair-agent 实际用于诊断的 prompt。

当前来源：

```text
IssueTicket.generation_evidence.input_prompt_snapshot
```

它不是 repair-agent 从 cypher-generator-agent 在线查询得到的。

### 6.4 输入证据缺失

`generation_evidence.input_prompt_snapshot` 是 `IssueTicket` 的必填字段；字段缺失时会被输入模型拒绝。字段存在但为空字符串时，repair-agent 不会伪造 prompt；诊断上下文中的 prompt evidence 为空，LLM 仍基于其他失败证据进行诊断。

### 6.5 诊断失败

如果 LLM 诊断、JSON 解析或上下文处理失败，repair-agent 不生成半结构化假请求，异常向上抛出。

### 6.6 knowledge-agent apply 失败

如果 apply 失败：

- transport error 可重试。
- 5xx、202、204 等非 200 响应按可重试路径处理。
- 4xx 直接终止。
- 不保存“已应用”的分析记录。
- 已保存的分析记录更新为 `status=apply_failed`、`applied=false`，且不写入 `knowledge_agent_response`。

这保证了 repair-agent 写接口响应中的 `status=applied` 与真正的 apply 成功严格一致。

### 6.7 幂等记录已存在

如果 `analysis-{ticket_id}` 已存在，repair-agent 直接返回历史记录。

当前实现是读后判断，不是强并发原子 claim。并发重复提交同一 ticket 时，仍可能出现重复 apply 风险。后续如果进入生产并发场景，应将 repository 扩展为原子占位或锁机制。

### 6.8 配置边界

repair-agent 的配置只表达自身运行、knowledge-agent apply 出站和自身 LLM 诊断能力。

当前保留的设置：

```text
app_name
host
port
data_dir
knowledge_agent_repairs_apply_url
knowledge_agent_repairs_apply_capture_dir
knowledge_agent_repairs_apply_max_attempts
request_timeout_seconds
llm_enabled
llm_provider
llm_base_url
llm_api_key
llm_model_name
llm_temperature
llm_max_retries
llm_retry_base_delay_seconds
llm_max_concurrency
```

repair-agent 不暴露其它服务的运行配置。它不在线调用 cypher-generator-agent、不执行 TuGraph、不配置 generator-agent 的 LLM，也不向其它服务的 feedback URL 投递结果；这些事实必须来自 `IssueTicket` 或由对应服务自己负责。

## 7. 结论

repair-agent 是一个“判断 testing-agent 失败问题单是否应转成 knowledge-agent 知识包修复请求”的服务：它消费失败样本、评测证据和生成证据，判断失败是否属于 knowledge-agent 知识缺口；可修复时形成最小修复建议，并在 knowledge-agent apply 成功后保存可追踪的分析记录；不可修复时保存 `not_repairable` 诊断记录且不执行 apply。cypher-generator-agent 固定生成协议、兜底解析、提交前 Cypher 基础检查和 testing-agent 评测器问题属于工程缺陷，不进入 repair-agent -> knowledge-agent 的业务知识修复闭环。
