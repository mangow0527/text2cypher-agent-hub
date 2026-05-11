# 运行中心字段展示设计

## 目标

运行中心用于回放正式 NL2Cypher 服务流程中的单题执行证据。页面展示字段应优先从正式服务落盘数据中提取，避免在运行中心侧重复保存或现场重构语义轨迹、模型输出、评测结果和修复建议。

本设计面向运行中心单题详情页。页面结构采用纵向流水线，每个 agent 区域支持折叠展开。

## 数据源边界

运行中心主数据源是 testing-agent。cypher-generator-agent 的生成证据在提交 testing-agent 时一并进入 testing-agent 落盘，其中 `input_prompt_snapshot` 保存 CGA 分层链路证据；repair-agent 的修复分析由运行中心按 `analysis_id` 补读。

| 服务 | 默认目录 | 运行中心读取内容 |
| --- | --- | --- |
| testing-agent | `data/testing_service` | golden、submission、attempt、CGA 非成功输出报告、issue ticket、evaluation、semantic review、repair response、improvement |
| repair-agent | `data/repair_service` | repair analysis，包括发给大模型的 prompt 和解析后的修复建议 |
| cypher-generator-agent | `data/cypher_generator_agent` | 仅用于补充查看 outbox 投递状态；正式展示字段不应依赖它重复读取 |

testing-agent 当前落盘子目录：

| 子目录 | 文件形态 | 含义 |
| --- | --- | --- |
| `goldens/` | `{id}.json` | 题目的标准 Cypher、标准答案和难度 |
| `submissions/` | `{id}.json` | 最新一次可评测提交或非成功尝试的完整状态 |
| `submission_attempts/` | `{id}__attempt_{attempt_no}.json` | 每次尝试的完整快照 |
| `generation_failures/` | `{id}__{generation_run_id}.json` | cypher-generator-agent 在未生成可提交 Cypher 时投递的报告，保存 `generation_failed`、`clarification_required`、`service_failed` |
| `issue_tickets/` | `{ticket_id}.json` | 未通过评测后给 repair-agent 的正式问题单 |

repair-agent 当前落盘子目录：

| 子目录 | 文件形态 | 含义 |
| --- | --- | --- |
| `analyses/` | `{analysis_id}.json` | repair-agent 对 issue ticket 的分析、prompt、修复建议和知识侧响应 |

## 单题详情页结构

### 题目总览

题目总览只展示正式服务流程中的任务状态。

| 展示字段 | 字段含义 | 提取来源 | 说明 |
| --- | --- | --- | --- |
| 题目 ID | 正式 QA 样本 ID | `submissions.id`；无 submission 时可用 `generation_failures.id` | 页面主键 |
| 自然语言问题 | cypher-generator-agent 接收到的问题 | `submissions.question` 或 `generation_failures.question` | 非成功输出也必须可展示 |
| 难度 | 题目难度等级 | `goldens.difficulty` | 由 testing-agent golden 落盘 |
| 当前尝试次数 | 当前展示的 attempt 编号 | `submissions.attempt_no` 或 `submission_attempts[].attempt_no` | 默认展示最新 attempt，可切换过往 attempt |
| 当前阶段 | 流水线当前停留阶段 | 根据 `submissions.state`、`generation_status`、`issue_ticket_id`、repair analysis 推导 | UI 状态字段，不需要单独落盘 |
| 最终结论 | 当前单题最终 pass/fail/pending/需澄清/服务失败 | 根据 `evaluation.verdict` 和流水线状态推导 | 如果是 `clarification_required` 或 `service_failed`，显示澄清/失败原因而非评测结论 |
| 更新时间 | 该题最新证据更新时间 | `updated_at`、`received_at`、repair analysis 时间中的最大值 | 用于排序和详情页抬头 |

### cypher-generator-agent 区域

该区域展示 CGA 如何从自然语言问题走到 Cypher 或澄清/失败输出。页面首屏必须先展示最关键的对照信息，然后再按新 CGA 五层链路展开证据。

#### 顶部对照字段

| 展示字段 | 字段含义 | 提取来源 | 说明 |
| --- | --- | --- | --- |
| 自然语言问题 | 发送给 cypher-generator-agent 的原始问题 | `submissions.question` 或 `generation_failures.question` | 与总览一致，区域内保留便于折叠回放 |
| 标准 Cypher | QA 样本的 golden Cypher | `goldens.cypher` | 用于人工对照，不参与 CGA 生成 |
| 生成 Cypher | CGA 成功提交的 Cypher，或 `generation_failed` 时留下的候选 Cypher | 成功时 `submissions.generated_cypher`；`generation_failed` 时 `generation_failures.parsed_cypher` | 澄清时显示“需要澄清”；其它为空时显示“未生成可评测 Cypher” |
| 生成状态 | 本轮 CGA 的服务输出状态 | `generation_status` | 页面展示中文状态，同时保留原始状态码 |
| 生成运行 ID | 一次生成运行的唯一 ID | `generation_run_id` | 用于绑定同一次非成功输出报告或 outbox 投递记录 |
| 门禁结果 | CGA 最终 Cypher 是否通过生成侧门禁 | 成功提交可推导为通过；非成功报告读取 `generation_failures.gate_passed` | 门禁失败不等同于 testing-agent grammar 失败 |
| 失败原因 | CGA 生成失败、服务失败或澄清触发原因 | `failure_reason`、`clarification.reason_code`、`input_prompt_snapshot.clarification.reason_code` | 页面展示中文解释和原始 code |

生成状态展示口径：

| 原始状态 | 中文展示 | 含义 | 后续区域展示 |
| --- | --- | --- | --- |
| `generated` / `submitted_to_testing` | 已提交评测 | 生成了通过门禁的 Cypher，并已提交 testing-agent | 展示完整 CGA 链路、testing-agent 评测和 repair 证据 |
| `clarification_required` | 需要澄清 | 当前单轮问题缺少必要信息或存在业务歧义，CGA 不应继续生成 Cypher | 展示澄清问题、选项、来源层级和触发证据；不展示 testing-agent 评测 |
| `generation_failed` | 生成失败 | 链路已执行，但未形成可评测 Cypher，或生成结果未通过 CGA 门禁 | 展示失败位置、失败原因、可能的候选 Cypher；testing-agent 可记录失败尝试 |
| `service_failed` | 服务失败 | 依赖、配置、语义资产或投递失败等工程问题 | 展示服务失败快照和投递状态；不展示 grammar、EX、repair |

#### 分层链路字段

| 展示层级 | 展示字段 | 提取来源 | 说明 |
| --- | --- | --- | --- |
| 服务编排层 | 请求 ID、question、generation_run_id、CGA 配置摘要 | `input_prompt_snapshot.service_context` | 配置摘要只展示与本轮生成有关的模式、模型、RAG 地址、语义视图版本 |
| 意图识别层 | 一级意图、二级意图、置信度、来源、判定结果 | `input_prompt_snapshot.intent_recognition.result` | 对应 `IntentRecognitionResult`，页面使用中文标签解释 `source` 和 `decision` |
| 意图识别层 | 规则命中摘要、向量召回 top-k、LLM 一级/二级判定尝试 | `input_prompt_snapshot.intent_recognition.diagnostics` | LLM 尝试必须展示 prompt 和 raw output；未触发时显示“本次未触发” |
| 语义视图匹配层 | 匹配到的实体、过滤条件、路径语义、返回对象、置信与歧义 | `input_prompt_snapshot.semantic_view_matching.result` | 对应 `SemanticMatchResult` |
| 语义视图匹配层 | 候选生成、语义补全、候选打分、LLM 消歧记录 | `input_prompt_snapshot.semantic_view_matching.result.trace` | `trace` 是 `SemanticMatchResult` 的一部分；只展示压缩后的候选卡片和决策证据 |
| 规划层 | LogicalQueryPlan 摘要 | `input_prompt_snapshot.logical_query_plan` | 展示答案形态、操作序列、路径引用、渲染提示和 trace 引用 |
| 图 Schema 路径规划层 | 选中的图路径、候选路径、方向、变量绑定、路径拒绝原因 | `input_prompt_snapshot.schema_path_planning` | 语义明确但路径歧义时，应能定位是否触发澄清 |
| RAG 知识选择 | 知识来源、检索 query、选中知识卡片、过滤原因 | `input_prompt_snapshot.knowledge_selection` | 只展示被选中或被拒绝的摘要，不重复保存大段知识正文 |
| 生成与提交层 | 渲染器类型、确定性渲染结果、受控 LLM 兜底结果、parser 结果 | `input_prompt_snapshot.generation` | 确定性渲染成功时不应伪造 LLM 输出 |
| 生成与提交层 | 语义、Schema、执行安全预检结果 | `input_prompt_snapshot.preflight` | 展示通过项、失败项、失败原因和关联 plan/path |
| 生成与提交层 | testing-agent 投递状态或 outbox 状态 | `input_prompt_snapshot.delivery`；必要时补读 CGA outbox | 正式回放以 testing-agent 收到的数据为准，outbox 仅用于解释“待投递” |
| 统一澄清出口 | 澄清来源层级、原因、中文问题、选项、期望回答类型 | `input_prompt_snapshot.clarification` | 来自意图识别、语义视图匹配、planner 或路径规划时均使用同一结构 |

#### LLM 调用展示

CGA 区域按触发位置组织 LLM 调用。所有 LLM 调用必须展示 prompt 与 raw output，未触发的位置显示“本次未触发”。

| 调用位置 | 展示标题 | 落盘字段 | 触发条件 |
| --- | --- | --- | --- |
| 意图识别一级分类 | 意图识别：一级分类 LLM 判定 | `intent_recognition.diagnostics.llm_primary_attempts[]` | 规则和向量召回不能稳定确定一级意图 |
| 意图识别二级分类 | 意图识别：二级分类 LLM 判定 | `intent_recognition.diagnostics.llm_secondary_attempts[]` | 一级意图已确定，但二级意图候选仍有歧义 |
| 语义视图消歧 | 语义视图匹配：受控 LLM 消歧 | `semantic_view_matching.result.trace.llm_disambiguation_attempts[]` | 候选分数接近、规则无法消歧，且仍可给出有限候选 |
| Cypher 兜底生成 | Renderer 失败后的 Cypher 兜底生成 | `generation.cypher_fallback_llm` | 确定性渲染器不覆盖或 preflight 拒绝后允许模型兜底 |

每条 LLM 调用记录至少包含：

```jsonc
{
  "call_id": "llm-intent-primary-001", // 本轮链路内唯一调用 ID，便于 trace 引用。
  "stage": "intent_recognition.primary", // 调用发生的位置。
  "model": "qwen3-vl-32b-thinking", // 实际调用模型；CGA 当前配置以该模型为准。
  "prompt_markdown": "...", // 实际发送给模型的 Markdown 提示词。
  "raw_output": "...", // 模型原始返回，不能由运行中心重构。
  "parsed_output": {}, // 服务侧解析后的结构化结果。
  "accepted": true, // 该次调用结果是否被后续链路采用。
  "rejected_reason": null // 未采用时的原因。
}
```

#### `input_prompt_snapshot` 落盘契约

`input_prompt_snapshot` 是运行中心展示 CGA 分层证据的主契约。它应保存结构化 JSON 字符串，目标结构如下：

```jsonc
{
  "schema_version": "cga_trace_v2", // 运行中心解析该快照的版本。
  "question": "查询 Gold 服务使用的隧道名称和时延", // 本轮自然语言问题。
  "generation_run_id": "cypher-run-001", // 本轮生成运行 ID。
  "generation_status": "generated", // testing-agent 和运行中心看到的生成落盘状态；CGA 对调用方可返回 submitted_to_testing。
  "service_context": {
    "active_mode": "semantic_view_pipeline", // 本轮 CGA 主链路模式。
    "model": "qwen3-vl-32b-thinking", // CGA 实际生成/判定使用的模型。
    "semantic_view_version": "network_graph_semantic_view@2026-05-11", // 语义视图版本或 hash。
    "rag_source": "http://127.0.0.1:8004/api/v1/retrieve" // 本轮知识检索来源。
  },
  "intent_recognition": {
    "result": {}, // IntentRecognitionResult，完整结构见意图识别设计文档。
    "diagnostics": {
      "rule_hit": null, // 规则阶段命中摘要。
      "embedding_candidates": [], // 远端 RAG 向量召回候选摘要。
      "llm_primary_attempts": [], // 一级意图 LLM 判定调用记录。
      "llm_secondary_attempts": [] // 二级意图 LLM 判定调用记录。
    }
  },
  "semantic_view_matching": {
    "result": {} // SemanticMatchResult，完整结构见语义视图设计文档；候选生成、语义补全、候选打分和 LLM 消歧记录保存在 result.trace。
  },
  "logical_query_plan": {}, // LogicalQueryPlan，完整结构见 CGA 设计文档规划层。
  "schema_path_planning": {
    "selected_path": null, // planner 选择的 schema graph 路径。
    "candidate_paths": [], // 可选路径摘要。
    "rejected_paths": [] // 被拒绝路径及原因。
  },
  "knowledge_selection": {
    "source": "rag", // 知识来源。
    "retrieve_query": {}, // 发给 RAG 的检索请求摘要。
    "selected_items": [], // 被采用的知识卡片摘要。
    "rejected_items": [] // 被过滤掉的知识卡片摘要。
  },
  "generation": {
    "renderer": {
      "family": "deterministic", // 渲染器类型。
      "accepted": true, // 确定性渲染是否成功。
      "cypher": "MATCH ... RETURN ...", // 渲染器输出。
      "failure_reason": null // 渲染失败原因。
    },
    "cypher_fallback_llm": null, // 未触发时为 null；触发时为 LLM 调用记录。
    "parser": {
      "parsed_cypher": "MATCH ... RETURN ...", // parser 后可评测 Cypher。
      "parse_summary": "cypher_only" // parser 结果摘要。
    }
  },
  "preflight": {
    "accepted": true, // 最终提交前预检是否通过。
    "checks": [], // schema、semantic、execution-safety 等检查项。
    "reason": null // 未通过时的原因。
  },
  "clarification": null, // 未触发澄清时为 null；触发时保存统一澄清结构。
  "delivery": {
    "target": "testing-agent", // 投递目标服务。
    "status": "delivered", // delivered、outbox_pending、failed。
    "reason": null // 投递失败或 outbox 暂存原因。
  }
}
```

绑定规则：

- `generated` submission 不读取同 QA id 下的 `generation_failures`，避免非成功报告覆盖最新成功结果。
- `generation_failed` 或 `clarification_required` submission 只读取相同 `generation_run_id` 的 `generation_failures/{id}__{generation_run_id}.json`。精确文件不存在时，不回退到同 QA id 的其它非成功输出报告。

### testing-agent 区域

该区域展示标准答案、评测指标和修复依据。testing-agent 不做 Cypher 本身的归一化严格比较；EX 严格比较对象是归一化后的 answer/result。

| 展示字段 | 字段含义 | 提取来源 | 说明 |
| --- | --- | --- | --- |
| golden Cypher | 标准 Cypher | `goldens.cypher` 或 `issue_ticket.expected.cypher` | 题目标准答案的一部分 |
| golden answer | 标准查询答案 | `goldens.answer` 或 `issue_ticket.expected.answer` | 用于 EX 严格比较 |
| actual Cypher | 本次尝试生成或失败候选 Cypher | `submissions.generated_cypher` 或 `issue_ticket.actual.generated_cypher` | 生成失败时可能是 testing-agent 派生出的候选文本 |
| 执行结果 | TuGraph 执行结果 | `submissions.execution` 或 `issue_ticket.actual.execution` | grammar 失败时可以为空 |
| grammar score | 语法门禁得分 | `evaluation.primary_metrics.grammar.score` | `0` 表示未通过，`1` 表示通过 |
| grammar 原因 | 语法失败原因或说明 | `evaluation.primary_metrics.grammar.parser_error`、`message` | 生成失败时通常 grammar 为 `0` |
| EX 得分 | 执行正确性得分 | `evaluation.primary_metrics.execution_accuracy.score` | grammar 为 `0` 时 EX 不继续执行，原因通常为 `grammar_failed` |
| EX 原因 | EX 通过或失败原因 | `evaluation.primary_metrics.execution_accuracy.reason` | 例如 `strict_equal`、`semantic_equivalent`、`grammar_failed`、`execution_failed`、`not_equivalent` |
| 严格比较结果 | 归一化答案严格比较状态 | `execution_accuracy.strict_check.status` | 比较 answer/result，不比较 Cypher 文本 |
| 严格比较差异 | 缺失行、额外行、顺序差异 | `execution_accuracy.strict_check.evidence.diff` | 可折叠展示 |
| 语义评判 prompt | 严格比较失败后发给大模型的语义评判 prompt | `submissions.semantic_review.prompt_snapshot` | 若未触发语义评判则为空 |
| 语义评判原始返回 | 语义评判大模型原始返回 | `submissions.semantic_review.raw_text` | 原始文本优先展示 |
| 语义评判结构化结果 | 语义评判解析后的 payload 和 pass/fail | `semantic_review.payload`、`normalized_judgement`、`reasoning` | UI 展示 judgement 和 reasoning |
| semantic check 状态 | execution accuracy 中的语义检查状态 | `execution_accuracy.semantic_check.status` | 与 semantic review 证据互相印证 |
| GLEU | 次级指标 GLEU | `evaluation.secondary_signals.gleu.score` | Secondary Metrics 只展示指标 |
| similarity | 次级相似度指标 | `evaluation.secondary_signals.jaro_winkler_similarity.score` | Secondary Metrics 只展示指标 |
| improvement | 当前 attempt 相比上一 attempt 的变化 | `submissions.improvement_assessment` | 展示 summary、metrics、highlights |

展示规则：

- grammar 为 `0` 时，明确标注“0 = 未通过，1 = 通过”。
- EX 区域应避免“归一化 Cypher 严格比较”等表述，只描述归一化 answer/result 的严格比较。
- Secondary Metrics 区域只展示 GLEU 和 similarity 指标，不展开为主结论。
- 如果 `generation_status=service_failed`，testing-agent 区域显示“未评测”，并展示服务失败原因。
- `improvement` 只展示 testing-agent 已落盘的 `submissions.improvement_assessment`。如果该字段不存在，运行中心显示为空，不基于历史 attempt 现场生成摘要。

### repair-agent 区域

该区域展示 repair-agent 如何基于 issue ticket 生成修复建议。

| 展示字段 | 字段含义 | 提取来源 | 说明 |
| --- | --- | --- | --- |
| 发给大模型的提示词 | repair-agent 实际用于诊断 LLM 调用的完整 prompt | `repair_analysis.system_prompt_snapshot` 与 `repair_analysis.user_prompt_snapshot` 拼成单个 Markdown 文本 | 运行中心只展示一个完整 Markdown 块，不把 system/user 拆成两个 UI 区域；不再展示 IssueTicket 中的生成 prompt evidence |
| 大模型原始返回 | repair-agent 诊断 LLM 的原始文本返回 | `repair_analysis.raw_output` | 用于核对模型真实返回，不由运行中心重新构造 |
| 发送给 knowledge-agent 的报文 | repair-agent 最终准备发送给 knowledge-agent 的请求体 | `repair_analysis.knowledge_repair_request` | 当前未真实发送时也展示 repair-agent 已构造的请求体；不从 UI 侧补造 |
| knowledge-agent 响应 | knowledge-agent apply 接口响应 | `repair_analysis.knowledge_agent_response` | 运行中心 API 字段名统一为 `knowledge_agent_response` |

不展示字段：

- `confidence`：不作为正式契约字段、不作为选择依据、不在 UI 展示。
- `primary_knowledge_type`、`secondary_knowledge_types`：当前只作为诊断审计字段，不作为运行中心主要展示字段。
- `repair_response`：这是 testing-agent 保存的 repair-agent HTTP 响应快照，运行中心只用它定位 `analysis_id`，不作为详情 API 的展示字段透出。
- 解析后的 `suggestion`、`knowledge_types`、`applied`：这些已经包含在发给 knowledge-agent 的请求体或响应里，不再作为 repair-agent 区域的独立展示字段重复展示。

绑定规则：

- repair analysis 必须通过 `submissions.repair_response.analysis_id` 精确读取 `repair_service/analyses/{analysis_id}.json`。如果 submission 中没有合法 `analysis_id`，运行中心不得按 QA id 扫描 analyses 兜底绑定，以免把其它 attempt 的修复分析贴到当前任务。

## CGA 非成功输出展示

CGA 的非成功输出由 cypher-generator-agent 投递 testing-agent 留存。它们在页面上必须与“已提交评测”使用同一套状态展示组件，但后续评测行为不同。

| 状态 | 含义 | testing-agent 行为 | 运行中心展示 |
| --- | --- | --- | --- |
| `generated` / `submitted_to_testing` | 生成了通过门禁的 Cypher，并提交 testing-agent | 正常保存 submission 并完整评测 | 展示完整生成、评测、修复流水线 |
| `clarification_required` | 单轮问题需要用户补充信息后才能安全生成 | 保存澄清报告，不执行 grammar、EX、semantic review，不进入 repair | 展示澄清问题、选项、来源层级和触发证据 |
| `generation_failed` | 链路已执行，但没有形成可提交 Cypher，或 renderer/LLM 输出未通过生成门禁 | 保存非成功输出报告；如 testing-agent 创建失败 attempt，则 grammar 为 `0`，不执行 EX，可保留 secondary metrics 和 repair 证据 | 展示失败位置、失败原因、候选 Cypher、secondary metrics 和 repair 证据 |
| `service_failed` | 工程或依赖失败，例如语义资产未对齐、RAG 不可用、模型调用异常、投递异常 | 保存非成功输出报告，不创建评测 attempt | 展示服务失败原因、依赖状态和投递状态，不展示评测和修复 |

如果 cypher-generator-agent 投递 testing-agent 失败，内容应先进入 cypher-generator-agent outbox。投递成功后，outbox 中对应内容必须删除。运行中心正式回放以 testing-agent 收到后的数据为准；如果要展示“待投递”状态，可以单独读取 outbox，并明确标注该状态尚未进入 testing-agent 留存。

## 实现提示

运行中心重构时建议将单题详情页数据装配为三个来源读取：

1. `testing_data_dir/goldens/{id}.json`
2. `testing_data_dir/submissions/{id}.json`、`submission_attempts/{id}__attempt_{n}.json`、`generation_failures/{id}__*.json`、`issue_tickets/{ticket_id}.json`
3. `repair_data_dir/analyses/{analysis_id}.json`

装配 CGA 区域时先解析 `input_prompt_snapshot`：

1. 如果 `schema_version=cga_trace_v2`，按本文的分层结构生成页面字段。
2. 如果快照无法解析，只展示顶部对照字段、生成状态、失败原因和原始文本。

页面层不要从正式服务落盘之外的临时产物补齐字段。若某个展示字段无法从 testing-agent、cypher-generator-agent outbox 或 repair-agent analysis 获得，应优先补入正式服务契约。
