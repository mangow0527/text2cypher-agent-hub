# cypher-generator-agent 能力基线实验设计稿

## Summary

本设计稿定义一套远端基线实验，用于回答一个问题：

在 **knowledge-agent 内容冻结**、**repair-agent 保留分析但禁止真正 apply 修复建议** 的前提下，当前 `cypher-generator-agent` 对 `L1-L8` 难度问题的稳定能力边界在哪里。

这份文档只保留两类重点细节：

1. 实验流程
2. 实验归档与证据模型

更细的执行动作、环境路径与操作提示下沉到 runbook：

[2026-04-26-cypher-generator-agent-capability-baseline-experiment-runbook.md](/Users/mangowmac/Desktop/code/NL2Cypher/console/runtime_console/docs/superpowers/specs/2026-04-26-cypher-generator-agent-capability-baseline-experiment-runbook.md)

### 2026-04-28 修订说明

本轮复盘后明确修订一条关键原则：

`qa-agent` 的作业流水线是正式样本的唯一来源。实验侧只允许配置、触发、归档和分析 `qa-agent` 作业结果，不再自行构造候选池、筛选正式实验集、拼接历史样本，或手工改写 question / gold / answer。

---

## 一、目标与边界

### 1.1 目标

本次实验希望明确：

1. 当前 `cypher-generator-agent` 的稳定能力边界
2. 从哪个 `Lx` 开始明显失稳
3. 失败首先发生在系统链路中的哪个阶段
4. 在不污染 knowledge-agent 的前提下，保留足够的 repair 诊断证据

### 1.2 非目标

本次实验不做：

- 不允许 repair-agent 真正更新 knowledge-agent
- 不以“把系统修好”为目标修改线上知识
- 不把一次偶然成功视为能力边界

### 1.3 实验结论口径

最终结论只使用三层表述：

- `可稳定应对`
- `可部分应对`
- `当前不具备稳定能力`

---

## 二、实验原则

### 2.1 固定 knowledge 基线

实验窗口内 knowledge-agent 内容不得变化。

### 2.2 保留 repair 分析，阻断 apply

保留：

- `testing-agent -> repair-agent`
- repair-agent 的分析与建议生成

阻断：

- `repair-agent -> knowledge-agent /api/knowledge/repairs/apply`

### 2.3 每个样本都必须有证据链

每个 `qa_id` 都必须能串起：

- `qa-agent` 的输入
- `cypher-generator-agent` 的生成证据
- `testing-agent` 的评测证据
- `repair-agent` 的分析与 apply 阻断证据

无法形成完整证据链的样本，不进入最终结论。

---

## 三、实验流程

### 3.1 总体阶段

实验按 4 个阶段推进：

1. 环境冻结与副作用隔离
2. 连通性与基线联调
3. `qa-agent` 作业生成、发布与按难度分析
4. 失败归因与边界确认

### 3.2 阶段一：环境冻结与副作用隔离

本阶段只做边界固定，不做能力判断。

必须完成：

- 记录环境快照
- 冻结 knowledge-agent 内容
- 阻断 repair apply
- 保存阻断验证证据

通过标准：

- 各服务健康可用
- repair 可继续分析
- apply 不会真正生效

### 3.3 阶段二：连通性与基线联调

本阶段目标是验证主链路和证据链路都可用。

验证链路：

- `qa-agent -> cypher-generator-agent`
- `cypher-generator-agent -> testing-agent`
- `testing-agent -> repair-agent`
- `repair-agent -X-> knowledge-agent apply`

通过标准：

- 每段请求有返回
- 每段 payload 可归档
- repair 可产出分析
- apply 被阻断且可证明未生效

如果该阶段失败，不进入正式实验。

### 3.4 阶段三：`qa-agent` 作业生成、发布与按难度分析

本阶段分成三部分：

1. 通过 `qa-agent` 作业接口生成、验证、去重、打包正式 QA
2. 由 `qa-agent` 内置 dispatch 逻辑发送 question 与 golden
3. 按 `L1-L8` 对已发布样本做结果归档与分析

当前默认规模：

- 目标正式样本数为 `40` 题
- 难度覆盖以 `qa-agent` 作业产物为准
- 如果必须追求每个 `Lx` 精确 `5` 题，只能通过 `qa-agent` 已支持的作业参数或发布机制实现；实验侧不得在作业外手工抽样拼装

### 3.5 阶段四：失败归因与边界确认

本阶段不只是统计通过率，而是输出能力边界解释。

至少要回答：

- 哪一层开始明显失稳
- 失稳主要出现在哪个阶段
- 哪些题型最先失稳
- repair 是否已经能指出清晰根因

---

## 四、`qa-agent` 作业与正式样本

### 4.1 正式样本来源

正式样本必须来自 `qa-agent` 的作业流水线。

远端 `qa-agent` 已内置以下能力：

- 接收作业请求
- 生成 QA sample
- 运行校验与 roundtrip
- 去重与发布打包
- 将 question 发送到 question endpoint
- 将 cypher / answer / difficulty 作为 golden 发送到 golden endpoint

因此，实验侧不得再维护独立的 `candidate_pool` 或 `final_sample_set` 作为正式样本来源。

### 4.2 实验侧允许做的事

实验侧只允许：

1. 设定 `qa-agent` 作业请求参数
2. 触发 `/jobs`、`/jobs/{job_id}/run` 或 `/jobs/quick-run`
3. 归档 job request、job snapshot、release rows、dispatch result、dispatch log
4. 基于 `qa-agent` 已发布的样本统计难度和 family 覆盖
5. 在覆盖不足时重新发起新的 `qa-agent` 作业，并把多次作业作为独立来源记录

### 4.3 实验侧禁止做的事

实验侧不允许：

1. 自行生成或合并候选样本池
2. 从历史样本中手工筛选正式题
3. 在 `qa-agent` 作业外改写 question、gold cypher、answer 或 difficulty
4. 用本地脚本做分层抽样并把结果冒充为 `qa-agent` 发布集
5. 先试跑再剔除失败样本
6. 把 schema / gold 不一致的历史行混入新实验

### 4.4 作业接受条件

只有满足以下条件时，某个 `qa-agent` 作业才能作为本轮实验输入：

1. job 状态达到 completed，或失败原因已完整归档
2. release / packaged rows 可追溯到同一个 job id
3. dispatch 结果已归档，至少能区分 question 与 golden 两段是否成功
4. 每个样本的 `qa_id`、question、cypher、answer、difficulty 来源一致
5. 覆盖情况已统计；若覆盖不足，作为实验限制记录，或重新发起新的 `qa-agent` 作业

如果需要多次作业补覆盖，必须保留每个 job 的边界，不得把多份作业结果在实验侧重新洗牌后当作单一发布集。

---

## 五、实验归档与证据模型

### 5.1 原则

实验归档只保存实验自身内容，不复制正式服务的执行证据。

每一轮实验都必须保留：

1. 实验编排与环境冻结信息
2. `qa-agent` 作业、release、dispatch 与覆盖统计
3. 按难度 / 实验维度生成的索引与汇总
4. repair apply 阻断配置、阻断验证和 capture 结果
5. 实验复盘所需的耗时、统计、人工备注和结论

以下内容不进入实验归档：

1. cypher-generator-agent prompt、模型原始输出、parser 后 Cypher、重试原因
2. testing-agent golden、submission、attempt、execution、evaluation、semantic review、issue ticket
3. repair-agent analysis、repair prompt、模型返回、repair suggestion
4. 上述服务证据的文件路径、服务路径或引用清单

需要查看正式服务证据时，统一通过运行中心界面查看。实验归档不承担服务证据浏览入口职责。

### 5.2 实验目录结构

建议实验目录如下：

```text
experiment_runs/
  2026-04-26-cypher-generator-agent-baseline-freeze-v1/
    manifest.json
    environment/
      health.json
      processes.json
      ports.json
      git.json
      env_snapshot.json
      repair_apply_block_config.json
      repair_apply_block_verification.json
    qa_agent/
      job_request.json
      job_snapshot.json
      job_artifacts/
      release_rows.jsonl
      dispatch_result.json
      dispatch_log.jsonl
      coverage_report.json
    rounds/
      round-001-L1/
        summary.json
        qa/
          qa-0001/
          qa-0002/
      round-002-L2/
      round-003-L3/
    indexes/
      qa_index.jsonl
      qa_index.csv
    summaries/
      experiment_summary.md
      per_difficulty_summary.csv
```

### 5.3 每个 `qa_id` 的目录结构

每个 `qa_id` 的目录只保存实验相关内容：

```text
qa/qa-0001/
  experiment_sample.json
  timing.json
  repair_apply_attempt.json
  notes.md
```

不得在每题目录中保存：

- `generator_result.json`
- `testing_submission.json`
- `testing_result.json`
- `issue_ticket.json`
- `repair_analysis.json`
- `service_refs.json`
- prompt、raw output、generated Cypher、evaluation、semantic review、repair suggestion 的任何副本或路径引用

### 5.4 每类实验字段的最小要求

#### 必须完整保留的实验字段

这些字段只描述实验身份、实验分组、样本来源和实验侧控制条件：

- `qa_id`
- `experiment_id`
- `round_id`
- `qa_agent_job_id`
- `qa_agent_release_ref`
- `qa_agent_release_row_index`
- `qa_dispatch_result`
- `difficulty`
- `query_type`
- `structure_family`
- `failure_stage`
- `failure_reason`
- `repair_apply_attempted`
- `repair_apply_blocked`
- `apply_effective`
- `artifact_dir`
- `timing_status`
- `total_elapsed_ms`
- `qa_agent_elapsed_ms`
- `cypher_generator_elapsed_ms`
- `testing_agent_elapsed_ms`
- `repair_agent_elapsed_ms`
- `tugraph_execution_elapsed_ms`
- `semantic_review_elapsed_ms`

#### 默认不保留

这些字段和材料属于正式服务证据，不进入实验落盘，也不以路径引用形式保留：

- `question`
- `reference_cypher`
- `reference_answer`
- `generation_run_id`
- `generated_cypher`
- `input_prompt_snapshot`
- `generator_prompt_snapshot`
- `last_llm_raw_output`
- `generation_retry_count`
- `generation_failure_reasons`
- `submission.state`
- `execution`
- `evaluation`
- `strict_diff`
- `semantic_review`
- `issue_ticket_id`
- `analysis_id`
- `knowledge_repair_request`
- `repair_prompt_snapshot`
- `repair raw output`
- `repair suggestion`
- 完整服务日志全文

#### `experiment_sample.json`

- `qa_id`
- `experiment_id`
- `round_id`
- `qa_agent_job_id`
- `qa_agent_release_ref`
- `qa_agent_release_row_index`
- `qa_dispatch_result`
- `difficulty`
- `query_type`
- `structure_family`
- `artifact_dir`

#### `timing.json`

- `qa_id`
- `round_id`
- `timing_status`
- `qa_agent_started_at`
- `qa_agent_finished_at`
- `cypher_generator_started_at`
- `cypher_generator_finished_at`
- `testing_agent_started_at`
- `testing_agent_finished_at`
- `repair_agent_started_at`
- `repair_agent_finished_at`
- `qa_dispatch_started_at`
- `qa_dispatch_finished_at`
- `generator_started_at`
- `generator_finished_at`
- `testing_submission_received_at`
- `tugraph_execution_started_at`
- `tugraph_execution_finished_at`
- `semantic_review_started_at`
- `semantic_review_finished_at`
- `repair_started_at`
- `repair_finished_at`
- `total_elapsed_ms`
- `qa_agent_elapsed_ms`
- `cypher_generator_elapsed_ms`
- `testing_agent_elapsed_ms`
- `repair_agent_elapsed_ms`
- `generator_elapsed_ms`
- `tugraph_execution_elapsed_ms`
- `semantic_review_elapsed_ms`
- `repair_elapsed_ms`

其中：

- 时间字段允许缺失，但必须显式记录为 `null`
- 无法精确还原的阶段不允许省略，应保留 `timing_status=partial`
- 如果某阶段未触发，例如未进入 repair，则对应开始/结束与耗时字段记录为 `null`
- 服务总耗时与服务内部分段耗时必须同时记录
- `semantic_review_elapsed_ms` 只表示 `testing-agent` 内部子阶段，不代表整个 `testing-agent` 耗时

#### `repair_apply_attempt.json`

- apply capture 文件名或实验 capture 编号
- 阻断方式
- 实际结果或异常
- knowledge-agent 内容是否被修改的验证结果
- `apply_effective=false`

#### `notes.md`

- 人工复盘备注
- 实验侧异常说明
- 运行中心无法表达的实验判断

### 5.5 统一索引表

除目录化证据外，还必须维护统一索引表。

建议字段：

- `experiment_id`
- `round_id`
- `qa_id`
- `qa_agent_job_id`
- `qa_agent_release_ref`
- `qa_dispatch_result`
- `difficulty`
- `query_type`
- `structure_family`
- `pass_fail`
- `failure_stage`
- `failure_reason`
- `repair_triggered`
- `repair_apply_attempted`
- `repair_apply_blocked`
- `artifact_dir`
- `timing_status`
- `total_elapsed_ms`
- `qa_agent_elapsed_ms`
- `cypher_generator_elapsed_ms`
- `testing_agent_elapsed_ms`
- `repair_agent_elapsed_ms`
- `generator_elapsed_ms`
- `tugraph_execution_elapsed_ms`
- `semantic_review_elapsed_ms`
- `repair_elapsed_ms`

### 5.6 轮次汇总

每个难度轮次都必须输出 `summary.json`，至少包含：

- 总题数
- 通过数
- 失败数
- 通过率
- issue ticket 数
- repair 建议数
- apply 阻断次数
- `failure_stage` 分布
- `timing_coverage_rate`
- `total_elapsed_ms_p50`
- `total_elapsed_ms_p95`
- `qa_agent_elapsed_ms_p50`
- `qa_agent_elapsed_ms_p95`
- `cypher_generator_elapsed_ms_p50`
- `cypher_generator_elapsed_ms_p95`
- `testing_agent_elapsed_ms_p50`
- `testing_agent_elapsed_ms_p95`
- `repair_agent_elapsed_ms_p50`
- `repair_agent_elapsed_ms_p95`
- `generator_elapsed_ms_p50`
- `generator_elapsed_ms_p95`
- `tugraph_execution_elapsed_ms_p50`
- `tugraph_execution_elapsed_ms_p95`
- `semantic_review_elapsed_ms_p50`
- `semantic_review_elapsed_ms_p95`
- `repair_elapsed_ms_p50`
- `repair_elapsed_ms_p95`

### 5.7 耗时记录要求

本轮实验除证据链外，还必须具备最小耗时观测能力。

目的不是做精细性能压测，而是保证实验结束后能够回答：

1. 总体慢在哪一段
2. 某个 `Lx` 是否因为某个服务或服务内子阶段显著变慢
3. 慢是集中在 `qa-agent`、`cypher-generator-agent`、`testing-agent`、`repair-agent` 中的哪一个
4. 如果慢点在服务内部，是否进一步集中在 generator、TuGraph、semantic review 或 repair 子阶段

记录原则：

- 优先使用服务已有时间戳和持久化记录还原
- 无法精确获取时，允许使用请求发出/响应返回时间近似
- 所有耗时字段统一以 `ms` 记录
- 所有时间统一使用带时区的 ISO 8601 字符串
- 每个 `qa_id` 必须输出 `timing.json`
- 每个轮次 `summary.json` 必须输出耗时分位统计
- 所有服务都先记录总耗时，再按需要记录内部关键子阶段耗时

---

## 六、失败阶段与结论

### 6.1 `failure_stage`

建议统一使用：

- `passed`
- `generator_request_failed`
- `generator_output_invalid`
- `generator_preflight_failed`
- `testing_submission_failed`
- `tugraph_execution_failed`
- `result_mismatch`
- `semantic_review_invalid`
- `issue_ticket_created`
- `repair_analysis_failed`
- `repair_apply_blocked`

每个 `qa_id` 只记录一个“首次失败阶段”用于统计。

其中：

- `semantic_review_invalid` 表示 TuGraph 执行已成功，但 semantic review 未返回可接受的 `pass/fail + reasoning`
- 这类样本不应自动进入 repair 统计

### 6.2 核心指标

最终至少统计：

1. 每个 `Lx` 的通过率
2. 每个 `Lx` 的首次失败阶段分布
3. 每个 `Lx` 内不同 family 的成功率
4. 各难度的 repair 触发比例

### 6.3 验收标准

实验结束时，至少要能明确回答：

- 当前稳定能力边界在哪一层
- 首个明显失稳层级是哪一层
- 失败主要出现在哪个阶段
- repair 是否已经能给出清晰根因
- 本轮结论是否建立在固定 knowledge 基线上

---

## 七、文档关系

本设计稿只负责：

- 定义实验边界
- 定义实验流程
- 定义归档与证据要求

执行层细节、远端路径、服务现状、操作顺序见 runbook：

[2026-04-26-cypher-generator-agent-capability-baseline-experiment-runbook.md](/Users/mangowmac/Desktop/code/NL2Cypher/console/runtime_console/docs/superpowers/specs/2026-04-26-cypher-generator-agent-capability-baseline-experiment-runbook.md)
