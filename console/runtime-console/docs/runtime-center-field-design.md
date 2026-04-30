# 运行中心字段展示设计

## 目标

运行中心用于回放正式 NL2Cypher 服务流程中的单题执行证据。页面展示字段应优先从正式服务落盘数据中提取，实验数据只保留实验编排、分组和离线统计需要的字段，避免重复保存 prompt、模型原始输出、评测结果和修复建议。

本设计面向运行中心单题详情页。页面结构采用纵向流水线，每个 agent 区域支持折叠展开。

## 数据源边界

运行中心主数据源是 testing-agent。cypher-generator-agent 的生成证据在提交 testing-agent 时一并进入 testing-agent 落盘；repair-agent 的修复分析由运行中心按 `analysis_id` 补读。

| 服务 | 默认目录 | 运行中心读取内容 |
| --- | --- | --- |
| testing-agent | `data/testing_service` | golden、submission、attempt、generation failure、issue ticket、evaluation、semantic review、repair response、improvement |
| repair-agent | `data/repair_service` | repair analysis，包括发给大模型的 prompt 和解析后的修复建议 |
| cypher-generator-agent | `data/cypher_generator_agent` | 仅用于补充查看 outbox 投递状态；正式展示字段不应依赖它重复读取 |

testing-agent 当前落盘子目录：

| 子目录 | 文件形态 | 含义 |
| --- | --- | --- |
| `goldens/` | `{id}.json` | 题目的标准 Cypher、标准答案和难度 |
| `submissions/` | `{id}.json` | 最新一次提交或生成失败尝试的完整状态 |
| `submission_attempts/` | `{id}__attempt_{attempt_no}.json` | 每次尝试的完整快照 |
| `generation_failures/` | `{id}__{generation_run_id}.json` | cypher-generator-agent 在未生成可提交 Cypher 时投递的失败报告 |
| `issue_tickets/` | `{ticket_id}.json` | 未通过评测后给 repair-agent 的正式问题单 |

repair-agent 当前落盘子目录：

| 子目录 | 文件形态 | 含义 |
| --- | --- | --- |
| `analyses/` | `{analysis_id}.json` | repair-agent 对 issue ticket 的分析、prompt、修复建议和知识侧响应 |

## 单题详情页结构

### 题目总览

题目总览只展示正式服务流程中的任务状态，不展示实验字段。

| 展示字段 | 字段含义 | 提取来源 | 说明 |
| --- | --- | --- | --- |
| 题目 ID | 正式 QA 样本 ID | `submissions.id`；无 submission 时可用 `generation_failures.id` | 页面主键 |
| 自然语言问题 | cypher-generator-agent 接收到的问题 | `submissions.question` 或 `generation_failures.question` | 生成失败也必须可展示 |
| 难度 | 题目难度等级 | `goldens.difficulty` | 由 testing-agent golden 落盘 |
| 当前尝试次数 | 当前展示的 attempt 编号 | `submissions.attempt_no` 或 `submission_attempts[].attempt_no` | 默认展示最新 attempt，可切换历史 attempt |
| 当前阶段 | 流水线当前停留阶段 | 根据 `submissions.state`、`generation_status`、`issue_ticket_id`、repair analysis 推导 | UI 状态字段，不需要单独落盘 |
| 最终结论 | 当前单题最终 pass/fail/pending | 根据 `evaluation.verdict` 和流水线状态推导 | 如果只有 service failed，则显示失败原因而非评测结论 |
| 更新时间 | 该题最新证据更新时间 | `updated_at`、`received_at`、repair analysis 时间中的最大值 | 用于排序和详情页抬头 |

不展示字段：`experiment_id`、`round_id`、`query_type`、`structure_family`、`artifact_dir`。这些属于实验编排或实验分析维度，不属于正式服务流程。

### cypher-generator-agent 区域

该区域展示生成阶段的输入、提示词、模型输出、解析结果和门禁结果。

| 展示字段 | 字段含义 | 提取来源 | 说明 |
| --- | --- | --- | --- |
| 输入自然语言问题 | 发送给 cypher-generator-agent 的问题 | `submissions.question` 或 `generation_failures.question` | 与总览一致，区域内保留便于折叠回放 |
| 难度 | 题目难度 | `goldens.difficulty` | 服务本身不从实验字段感知难度，由 testing-agent golden 提供 |
| 生成运行 ID | 一次生成运行的唯一 ID | `generation_run_id` | 来源于 submission 或 generation failure report |
| 最终提示词 | 拉取 knowledge-agent 提示词后，由模板包装并发给大模型的完整 prompt | `input_prompt_snapshot` | UI 以 Markdown 文本展示，不展示 JSON 包装 |
| 大模型原始输出 | 最后一次调用大模型返回的原始文本 | `last_llm_raw_output` | 只保留最后一次重试的原始输出 |
| parser 后 Cypher | parser 解析后的 Cypher 候选 | 成功时 `submissions.generated_cypher`；生成失败时 `generation_failures.parsed_cypher` | 生成失败时可能为空；为空时展示为空，不从其它字段补造 |
| 门禁是否通过 | 生成结果是否通过 cypher-generator-agent 门禁 | 成功提交可推导为通过；失败报告读取 `generation_failures.gate_passed` | 失败时通常为 `false` |
| 失败原因 | 生成失败或服务失败的原因 | `failure_reason`、`last_generation_failure_reason` | `generation_failed` 仍进入 testing-agent；`service_failed` 只落盘不评测 |
| 重试次数 | cypher-generator-agent 内部生成重试次数 | `generation_retry_count` | 不记录每次重试 prompt，避免数据臃肿 |
| 历史失败原因 | 重试过程中出现过的失败原因列表 | `generation_failure_reasons` | 只保留原因，不保留每轮 prompt |
| 生成状态 | 生成阶段状态 | `generation_status` | `generated`、`generation_failed`、`service_failed` |

展示规则：

- `generated`：展示 prompt、原始输出、parser 后 Cypher、门禁通过、重试记录。
- `generation_failed`：展示 prompt、最后一次原始输出、失败原因、重试记录，并继续展示 testing-agent 的 grammar、secondary metrics 和 repair 证据。
- `service_failed`：展示 prompt、失败原因和投递状态；不展示 grammar、EX、repair，因为 testing-agent 不应执行评测。

绑定规则：

- `generated` submission 不读取同 QA id 下的历史 `generation_failures`，避免旧失败覆盖最新成功结果。
- `generation_failed` submission 只读取相同 `generation_run_id` 的 `generation_failures/{id}__{generation_run_id}.json`。精确文件不存在时，不回退到同 QA id 的其它 generation failure report。

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
| semantic check 状态 | execution accuracy 中的语义检查状态 | `execution_accuracy.semantic_check.status` | 与 semantic review artifact 互相印证 |
| GLEU | 次级指标 GLEU | `evaluation.secondary_signals.gleu.score` | Secondary Metrics 只展示指标 |
| similarity | 次级相似度指标 | `evaluation.secondary_signals.jaro_winkler_similarity.score` | Secondary Metrics 只展示指标 |
| improvement | 当前 attempt 相比上一 attempt 的变化 | `submissions.improvement_assessment` | 展示 summary、metrics、highlights |

展示规则：

- grammar 为 `0` 时，明确标注“0 = 未通过，1 = 通过”。
- EX 区域应避免“归一化 Cypher 严格比较”等表述，只描述归一化 answer/result 的严格比较。
- Secondary Metrics 区域只展示 GLEU 和 similarity 指标，不展开为主结论。
- 如果 `generation_status=service_failed`，testing-agent 区域显示“未评测”，并展示 service failure 原因。
- `improvement` 只展示 testing-agent 已落盘的 `submissions.improvement_assessment`。如果该字段不存在，运行中心显示为空，不基于历史 attempt 现场生成摘要。

### repair-agent 区域

该区域展示 repair-agent 如何基于 issue ticket 生成修复建议。

| 展示字段 | 字段含义 | 提取来源 | 说明 |
| --- | --- | --- | --- |
| 发给大模型的提示词 | repair-agent 实际用于诊断 LLM 调用的完整 prompt | `repair_analysis.system_prompt_snapshot` 与 `repair_analysis.user_prompt_snapshot` 拼成单个 Markdown 文本 | 运行中心只展示一个完整 Markdown 块，不把 system/user 拆成两个 UI 区域；不再展示 IssueTicket 中的生成 prompt evidence |
| 大模型原始返回 | repair-agent 诊断 LLM 的原始文本返回 | `repair_analysis.raw_output` | 用于核对模型真实返回，不由运行中心重新构造 |
| 发送给 knowledge-agent 的报文 | repair-agent 最终准备发送给 knowledge-agent 的请求体 | `repair_analysis.knowledge_repair_request` | 当前未真实发送时也展示 repair-agent 已构造的请求体；不从 UI 侧补造 |
| knowledge-agent 响应 | knowledge-agent apply 接口响应 | `repair_analysis.knowledge_agent_response` | 运行中心 API 字段名统一为 `knowledge_agent_response`，页面不展示旧 knowledge-ops 命名 |

不展示字段：

- `confidence`：不作为正式契约字段、不作为选择依据、不在 UI 展示。
- `primary_knowledge_type`、`secondary_knowledge_types`：当前只作为诊断审计字段，不作为运行中心主要展示字段。
- `repair_response`：这是 testing-agent 保存的 repair-agent HTTP 响应快照，运行中心只用它定位 `analysis_id`，不作为详情 API 的展示字段透出。
- 解析后的 `suggestion`、`knowledge_types`、`applied`：这些已经包含在发给 knowledge-agent 的请求体或响应里，不再作为 repair-agent 区域的独立展示字段重复展示。

绑定规则：

- repair analysis 必须通过 `submissions.repair_response.analysis_id` 精确读取 `repair_service/analyses/{analysis_id}.json`。如果 submission 中没有合法 `analysis_id`，运行中心不得按 QA id 扫描 analyses 兜底绑定，以免把旧 attempt 的修复分析贴到当前任务。

## 生成失败与服务失败展示

生成失败和服务失败都由 cypher-generator-agent 投递 testing-agent 留存，但 UI 行为不同。

| 状态 | 含义 | testing-agent 行为 | 运行中心展示 |
| --- | --- | --- | --- |
| `generated` | 生成了通过门禁的 Cypher | 正常保存 submission 并完整评测 | 展示完整生成、评测、修复流水线 |
| `generation_failed` | 模型输出未通过生成门禁，但这是可评测的生成失败 | 保存 failure report，并创建 submission attempt；grammar 为 `0`，不执行 EX，仍计算 GLEU/similarity 并进入 repair | 展示失败原因、secondary metrics、repair 证据 |
| `service_failed` | 工程或依赖失败，例如 knowledge-agent 不可用、模型调用失败 | 只保存 failure report，不创建评测 attempt | 展示服务失败原因，不展示评测和修复 |

如果 cypher-generator-agent 投递 testing-agent 失败，内容应先进入 cypher-generator-agent outbox。投递成功后，outbox 中对应内容必须删除。运行中心正式回放以 testing-agent 收到后的数据为准；如果要展示“待投递”状态，可以单独读取 outbox，但这不属于实验字段。

## 实验数据保留范围

以下字段应留在实验落盘中，因为它们服务流程不感知，也不应该体现在运行中心主页面。实验落盘不保存正式服务证据正文，也不保存正式服务证据的路径或引用；需要查看服务证据时直接进入运行中心。

| 实验字段 | 保留原因 |
| --- | --- |
| `experiment_id`、`round_id`、批次号 | 实验编排和恢复 |
| 数据集来源、样本行号、抽样策略 | 实验复现 |
| `query_type`、`structure_family` | 实验分析维度，正式服务不感知 |
| 实验模型/参数快照 | 如果不是正式服务配置的一部分，应由实验层保留 |
| 远端 artifact 路径、同步状态 | 实验运维字段 |
| 聚合统计、分组报表、人工备注 | 实验报告和离线分析 |
| baseline 对比结果 | 实验评估字段，不属于正式单题服务证据 |

以下字段不应再由实验层重复保存：

| 字段类别 | 正式来源 |
| --- | --- |
| prompt、模型原始输出、parser 后 Cypher | testing-agent submission / generation failure |
| 重试次数、历史失败原因、门禁失败原因 | testing-agent submission / generation failure |
| golden、generated、execution、evaluation | testing-agent golden / submission / issue ticket |
| 语义评判 prompt 和返回 | testing-agent submission 的 semantic review artifact |
| repair prompt 和修复建议 | repair-agent analysis |

## 实现提示

运行中心重构时建议将单题详情页数据装配为三个来源读取：

1. `testing_data_dir/goldens/{id}.json`
2. `testing_data_dir/submissions/{id}.json`、`submission_attempts/{id}__attempt_{n}.json`、`generation_failures/{id}__*.json`、`issue_tickets/{ticket_id}.json`
3. `repair_data_dir/analyses/{analysis_id}.json`

页面层不要直接读取实验 artifact 来补齐正式服务字段。若某个展示字段只能从实验 artifact 获得，应优先判断是否应该补入正式服务契约，而不是在运行中心引入实验依赖。
