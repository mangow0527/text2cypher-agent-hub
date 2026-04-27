# cypher-generator-agent 能力基线实验 Runbook

## Summary

本 runbook 用于把
[2026-04-26-cypher-generator-agent-capability-baseline-experiment-design.md](/Users/mangowmac/Desktop/code/NL2Cypher/console/runtime_console/docs/superpowers/specs/2026-04-26-cypher-generator-agent-capability-baseline-experiment-design.md)
落成一套可执行操作流程。

目标是：

1. 在远端服务器上执行一轮固定 knowledge 基线实验
2. 保留完整跨服务证据链
3. 阻断 `repair-agent -> knowledge-agent apply`
4. 用 `qa-agent` 生成候选池并收敛为每级 `5` 题的正式实验集

本 runbook 面向当前已确认的远端环境：

- 主机：`39.106.229.163`
- 用户：`root`
- `nl2cypher` 目录：`/root/multi-agent/nl2cypher`
- `qa-agent` 目录：`/root/multi-agent/qa-agent`
- `knowledge-agent` 目录：`/root/multi-agent/knowledge-agent/backend`

当前线上端口：

- `8000`: `cypher-generator-agent`
- `8001`: `runtime-results-service`
- `8002`: `repair-agent`
- `8003`: `testing-agent`
- `8010`: `knowledge-agent`
- `8020`: `qa-agent`

---

## 一、实验前提

### 1.1 本轮默认参数

- 实验模式：固定 knowledge 基线
- repair 策略：保留分析，阻断 apply
- 正式实验集规模：每个 `Lx` 取 `5` 题
- 总体正式实验集规模：`40` 题
- 候选池目标规模：`80-120` 题

### 1.2 本轮不做的事

- 不修改 knowledge-agent 内容
- 不让 repair-agent 真实写回 knowledge-agent
- 不在正式实验中途换题
- 不混用不同模型、不同数据、不同样本集版本

---

## 二、远端现状基线

### 2.1 当前已确认路径

- `nl2cypher`: `/root/multi-agent/nl2cypher`
- `qa-agent`: `/root/multi-agent/qa-agent`
- `knowledge-agent`: `/root/multi-agent/knowledge-agent/backend`

### 2.2 当前已确认运行日志位置

当前正在运行的服务使用的日志路径是：

- `cypher-generator-agent`: `/tmp/cgs_8000.log`
- `runtime-results-service`: `/tmp/runtime_8001.log`
- `testing-agent`: `/tmp/testing_8003.log`
- `repair-agent`: `/tmp/repair_agent_8002.log`
- `qa-agent`: `/tmp/qa_8020.log`
- `knowledge-agent`: `/root/multi-agent/knowledge-agent/backend/backend.out`

注意：

- 仓库自带 `start.sh` 会把日志写到 `/root/multi-agent/nl2cypher/logs/`
- 但当前线上正在运行的进程不是按 `start.sh` 的日志路径落盘
- 因此本轮实验必须以“结构化归档”为主，不能只依赖服务日志文件

### 2.3 当前已确认 testing-agent 落盘能力

`testing-agent` 会自动把关键内容落到：

- `data/testing_service/goldens`
- `data/testing_service/submissions`
- `data/testing_service/submission_attempts`
- `data/testing_service/issue_tickets`

这些目录相对于 `/root/multi-agent/nl2cypher`。

### 2.4 当前已确认 repair-agent capture 能力

`repair-agent` 支持：

- `REPAIR_SERVICE_KNOWLEDGE_OPS_REPAIRS_APPLY_CAPTURE_DIR`

即使 apply 被阻断，也可以利用 capture 保留准备发往 knowledge-agent 的 payload。

---

## 三、实验目录初始化

### 3.1 建议实验 id

建议格式：

`2026-04-26-cgs-baseline-freeze-v1`

### 3.2 建议实验根目录

建议在远端建立：

`/root/multi-agent/experiment_runs/2026-04-26-cgs-baseline-freeze-v1`

### 3.3 建议目录结构

```text
/root/multi-agent/experiment_runs/2026-04-26-cgs-baseline-freeze-v1/
  manifest.json
  environment/
  samples/
  rounds/
  indexes/
  summaries/
```

### 3.4 初始化动作

建议动作：

1. 创建实验根目录
2. 创建子目录
3. 写入初始 `manifest.json`
4. 记录实验开始时间
5. 记录当前策略：
   - `knowledge_frozen=true`
   - `repair_apply_blocked=true`
   - `target_per_level=5`

---

## 四、环境快照采集

### 4.1 必采集内容

必须在实验开始前保存：

- 主机名
- 当前时间
- 当前用户
- `git branch`
- `git commit`
- `git status`
- 服务进程
- 监听端口
- `/health` 返回
- 关键环境变量快照

### 4.2 建议保存位置

- `environment/git.json`
- `environment/processes.json`
- `environment/ports.json`
- `environment/health.json`
- `environment/env_snapshot.json`

### 4.3 必记录的关键变量

至少包括：

- `CYPHER_GENERATOR_AGENT_*`
- `TESTING_SERVICE_*`
- `REPAIR_SERVICE_*`
- `TEST_AGENT_HOST`
- `TEST_AGENT_QUESTION_PORT`
- `TEST_AGENT_GOLDEN_PORT`
- 模型名 / base URL
- TuGraph 连接摘要

### 4.4 注意事项

环境快照采集完成前，不要开始任何正式实验样本运行。

---

## 五、阻断 repair apply

### 5.1 本轮目标

我们要保留：

- `testing-agent -> repair-agent`
- repair 的 repair-agent 分析
- repair suggestion 生成

我们要阻断：

- `repair-agent -> knowledge-agent /api/knowledge/repairs/apply`

### 5.2 推荐策略

推荐采用“双保险”：

1. 把 `REPAIR_SERVICE_KNOWLEDGE_OPS_REPAIRS_APPLY_CAPTURE_DIR` 指向实验目录下的 capture 目录
2. 把 `REPAIR_SERVICE_KNOWLEDGE_OPS_REPAIRS_APPLY_URL` 改为一个不可写回 knowledge 的阻断地址

推荐 capture 目录：

`/root/multi-agent/experiment_runs/2026-04-26-cgs-baseline-freeze-v1/environment/repair_apply_captures`

### 5.3 阻断策略要求

阻断后的状态必须满足：

- repair-agent 可正常运行
- repair-agent 可正常接收 issue ticket
- repair-agent 可正常产出分析
- apply payload 被 capture
- apply 不会写回到真正的 knowledge-agent

### 5.4 阻断验证

阻断后必须做一次验证，确认：

- `repair-agent /health` 正常
- service status 中 apply URL 已变更
- 触发一次 repair 流程后有 capture 文件
- knowledge-agent 内容未被修改

### 5.5 产物

保存到：

- `environment/repair_apply_block_config.json`
- `environment/repair_apply_block_verification.json`

---

## 六、最小链路联调

### 6.1 目标

正式实验前，先确认一条最小样本链路可以完整走通并留证。

### 6.2 链路

- `qa-agent -> cypher-generator-agent`
- `cypher-generator-agent -> testing-agent`
- `testing-agent -> repair-agent`
- `repair-agent -> knowledge-agent apply blocked`

### 6.3 联调标准

必须同时满足：

- 每段请求有响应
- 每段 payload 可保存
- testing 侧 submission 与 issue ticket 可落盘
- repair 侧 analysis 可落盘
- apply 被阻断且有 capture 或失败证据

### 6.4 产物

建议保存到：

`rounds/smoke-test/`

如果最小链路联调不通过，不进入候选池生成阶段。

---

## 七、候选池生成

### 7.1 当前目标

- 正式实验集：`40` 题
- 候选池：`80-120` 题

### 7.2 生成方式

使用 `qa-agent` 自动生成能力分批生成候选样本。

建议不是一次性压大批量，而是多批次生成，例如：

- batch-001
- batch-002
- batch-003

每批都单独保留原始结果。

### 7.3 每批必须保存的内容

- 触发请求参数
- 原始响应
- 生成的样本文件
- 批次号
- 时间戳

### 7.4 建议保存位置

```text
samples/
  raw_batches/
    batch-001/
    batch-002/
  candidate_pool.jsonl
  sample_set_manifest.json
```

### 7.5 候选池停止条件

只有满足以下条件时，才停止扩充候选池：

- 总体候选数达到 `80-120`
- `L1-L8` 均有覆盖
- 高难层级不再明显空洞

---

## 八、候选池清洗与正式实验集冻结

### 8.1 清洗顺序

按以下顺序处理：

1. 完整性检查
2. 去重
3. 难度覆盖统计
4. family 覆盖统计
5. 分层抽样
6. 冻结实验集版本

### 8.2 当前正式实验集目标

- 每个 `Lx` 选 `5` 题
- 总计 `40` 题

### 8.3 当前抽样优先级

按以下优先级补覆盖：

1. `difficulty`
2. `query_type`
3. `structure_family`
4. 关键语义特征

### 8.4 必须禁止的做法

不允许：

- 依据“是否容易成功”筛样本
- 先试跑再把失败样本排除出正式实验集
- 用“看起来更像 demo”的题替换难样本

### 8.5 正式实验集落盘

建议保存：

- `samples/final_sample_set.jsonl`
- `samples/final_sample_set_manifest.json`
- `samples/coverage_report.json`

一旦正式实验集冻结，不在同一实验 id 中修改。

---

## 九、分轮执行正式实验

### 9.1 轮次结构

建议按难度分轮：

- `round-001-L1`
- `round-002-L2`
- `round-003-L3`
- `round-004-L4`
- `round-005-L5`
- `round-006-L6`
- `round-007-L7`
- `round-008-L8`

### 9.2 每轮固定动作

每一轮都按以下顺序：

1. 记录轮次开始时环境快照
2. 提交该难度的 `5` 题
3. 归档每题证据目录
4. 更新统一索引
5. 生成本轮 `summary.json`

### 9.3 每轮结束必须输出

- 总题数
- 通过数
- 失败数
- 通过率
- issue ticket 数
- repair 建议数
- apply 阻断次数
- `failure_stage` 分布

---

## 十、证据归档与索引

### 10.1 每题证据目录

每个 `qa_id` 建议保存：

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

### 10.2 可直接利用的现有数据源

#### `testing-agent`

可直接从：

- `/root/multi-agent/nl2cypher/data/testing_service/goldens`
- `/root/multi-agent/nl2cypher/data/testing_service/submissions`
- `/root/multi-agent/nl2cypher/data/testing_service/submission_attempts`
- `/root/multi-agent/nl2cypher/data/testing_service/issue_tickets`

提取：

- golden payload
- submission payload
- attempt 级 submission
- issue ticket

#### `repair-agent`

可直接从：

- `data/repair_service`
- `REPAIR_SERVICE_KNOWLEDGE_OPS_REPAIRS_APPLY_CAPTURE_DIR`

提取：

- analysis record
- knowledge repair request
- apply capture

#### 服务日志

仅用于补充时间线，不作为唯一事实来源：

- `/tmp/cgs_8000.log`
- `/tmp/testing_8003.log`
- `/tmp/repair_agent_8002.log`
- `/tmp/qa_8020.log`
- `/root/multi-agent/knowledge-agent/backend/backend.out`

### 10.3 统一索引表

建议维护：

- `indexes/qa_index.jsonl`
- `indexes/qa_index.csv`

字段建议：

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

---

## 十一、首个失稳层级复盘

### 11.1 目标

不是只统计通过率，而是找出第一个明显失稳的 `Lx` 并解释原因。

### 11.2 复盘动作

- 比较 `L1-L8` 的通过率
- 识别首个显著下降层级
- 回放该层级全部 `5` 题
- 与上一层级做对照
- 汇总主要失败阶段
- 汇总主要失败模式

### 11.3 输出

- `summaries/per_difficulty_summary.csv`
- `summaries/failure_boundary_analysis.md`
- `summaries/experiment_summary.md`

---

## 十二、实验结束检查

实验结束前必须检查：

1. 所有轮次是否都有 `summary.json`
2. 所有 `qa_id` 是否都有证据目录
3. 索引表是否完整
4. apply capture 是否存在
5. 最终总结是否能明确回答能力边界问题

### 12.1 必须能回答的问题

最终至少要能明确回答：

- 当前稳定能力边界在哪一层
- 首个明显失稳层级是哪一层
- 失败主要发生在哪个阶段
- repair 是否已经能看出根因
- 本轮结论是否建立在固定 knowledge 基线上

---

## 十三、文档间关系

本 runbook 是执行型补充文档。

建议与下列文档配套使用：

- 设计稿：
  [2026-04-26-cypher-generator-agent-capability-baseline-experiment-design.md](/Users/mangowmac/Desktop/code/NL2Cypher/console/runtime_console/docs/superpowers/specs/2026-04-26-cypher-generator-agent-capability-baseline-experiment-design.md)

如果本 runbook 继续下钻，下一步可以单独再拆成：

1. 远端 repair apply 阻断手册
2. `qa-agent` 候选池生成手册
3. 证据归档与汇总脚本使用手册
