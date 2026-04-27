# cypher-generator-agent 能力基线实验设计稿

## Summary

本设计稿定义一套远端基线实验，用于回答一个问题：

在 **knowledge-agent 内容冻结**、**repair-agent 保留分析但禁止真正 apply 修复建议** 的前提下，当前 `cypher-generator-agent` 对 `L1-L8` 难度问题的稳定能力边界在哪里。

这份文档只保留两类重点细节：

1. 实验流程
2. 实验归档与证据模型

更细的执行动作、环境路径与操作提示下沉到 runbook：

[2026-04-26-cypher-generator-agent-capability-baseline-experiment-runbook.md](/Users/mangowmac/Desktop/code/NL2Cypher/console/runtime_console/docs/superpowers/specs/2026-04-26-cypher-generator-agent-capability-baseline-experiment-runbook.md)

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
3. 候选池收敛与按难度主实验
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

### 3.4 阶段三：候选池收敛与按难度主实验

本阶段分成两部分：

1. 候选池生成、清洗、去重、分层抽样
2. 按 `L1-L8` 分轮执行正式实验

当前默认规模：

- 每个 `Lx` 取 `5` 题
- 正式实验集共 `40` 题
- 候选池目标为正式实验集的 `2-3` 倍，即 `80-120` 题

### 3.5 阶段四：失败归因与边界确认

本阶段不只是统计通过率，而是输出能力边界解释。

至少要回答：

- 哪一层开始明显失稳
- 失稳主要出现在哪个阶段
- 哪些题型最先失稳
- repair 是否已经能指出清晰根因

---

## 四、候选池与正式实验集

### 4.1 候选池使用原则

允许使用 `qa-agent` 自动生成候选池，但不允许按“稳定性”预筛样本。

原因是：

- 本次实验要测真实能力边界
- 如果预先剔除疑似失败样本，会把真实失稳模式洗掉

因此，`qa-agent` 的职责是：

- 扩充候选池
- 补齐不同难度和题型覆盖
- 不替实验提前过滤潜在失败样本

### 4.2 允许的筛选

正式实验前只允许三类筛选：

1. 完整性筛选
2. 重复样本清理
3. 代表性分层抽样

不允许依据“更像 demo”“更容易成功”“更稳定”来取舍样本。

### 4.3 候选池收敛流程

建议流程如下：

1. 用 `qa-agent` 批量生成候选池
2. 做完整性检查
3. 做重复样本清理
4. 统计难度和 family 覆盖
5. 按 `difficulty -> query_type -> structure_family -> 关键语义特征` 分层抽样
6. 冻结正式实验集版本

### 4.4 停机条件

只有满足以下条件时，候选池才能冻结成正式实验集：

1. 每个 `Lx` 达到目标样本数
2. 每个 `Lx` 有基本可接受的 family 覆盖
3. 所有样本完成完整性检查
4. 重复样本已折叠
5. 正式实验集版本文件已生成

---

## 五、实验归档与证据模型

### 5.1 原则

实验归档是本次方案的核心部分。

每一轮实验都必须保留：

1. 原始业务载荷
2. 服务间调用元数据
3. 关键日志摘录
4. repair apply 阻断证据

### 5.2 实验目录结构

建议实验目录如下：

```text
experiment_runs/
  2026-04-26-cgs-baseline-freeze-v1/
    manifest.json
    environment/
      health.json
      processes.json
      ports.json
      git.json
      env_snapshot.json
      repair_apply_block_config.json
      repair_apply_block_verification.json
    samples/
      sample_set_manifest.json
      sample_set.jsonl
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

每个 `qa_id` 必须有独立目录：

```text
qa/qa-0001/
  input.json
  qa_dispatch.json
  cgs_request.json
  cgs_result.json
  testing_submission.json
  testing_result.json
  issue_ticket.json
  repair_analysis.json
  repair_apply_attempt.json
  logs.json
```

### 5.4 每类证据的最小要求

#### `input.json`

- `qa_id`
- `question`
- `difficulty`
- `query_type`
- `structure_family`
- `reference_cypher`
- `reference_answer`

#### `qa_dispatch.json`

- qa-agent 发往 generator 的 payload
- qa-agent 发往 testing 的 golden payload
- 时间戳
- URL
- 状态码

#### `cgs_result.json`

- `generation_run_id`
- `generated_cypher`
- `parse_summary`
- `preflight_check`
- `raw_output_snapshot`
- `input_prompt_snapshot`
- 耗时
- 成功 / 失败标记

#### `testing_result.json`

- `pass/fail`
- `failure_stage`
- `failure_reason`
- `issue_ticket_id`
- TuGraph 执行结果摘要

#### `issue_ticket.json`

- 完整 issue ticket

#### `repair_analysis.json`

- repair 输入
- repair-agent 分析结果
- repair suggestion / repair plan

#### `repair_apply_attempt.json`

- apply payload
- apply URL
- 阻断方式
- 实际结果或异常
- `apply_effective=false`

#### `logs.json`

- qa-agent 日志摘录
- cypher-generator-agent 日志摘录
- testing-agent 日志摘录
- repair-agent 日志摘录
- 时间戳对齐信息

### 5.5 统一索引表

除目录化证据外，还必须维护统一索引表。

建议字段：

- `experiment_id`
- `round_id`
- `qa_id`
- `difficulty`
- `query_type`
- `structure_family`
- `question`
- `generated_cypher`
- `pass_fail`
- `failure_stage`
- `failure_reason`
- `issue_ticket_id`
- `repair_generated`
- `repair_apply_attempted`
- `repair_apply_blocked`
- `artifact_dir`

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
- `issue_ticket_created`
- `repair_analysis_failed`
- `repair_apply_blocked`

每个 `qa_id` 只记录一个“首次失败阶段”用于统计。

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
