# cypher-generator-agent 能力基线实验 Runbook

## Summary

本 runbook 用于把
[2026-04-26-cypher-generator-agent-capability-baseline-experiment-design.md](/Users/mangowmac/Desktop/code/NL2Cypher/console/runtime_console/docs/superpowers/specs/2026-04-26-cypher-generator-agent-capability-baseline-experiment-design.md)
落成一套可执行操作流程。

目标是：

1. 在远端服务器上执行一轮固定 knowledge 基线实验
2. 保留完整跨服务证据链
3. 阻断 `repair-agent -> knowledge-agent apply`
4. 用 `qa-agent` 内置作业流水线生成、校验、发布并发送正式 QA

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
- 正式样本目标规模：`40` 题
- 样本来源：`qa-agent` job / release / dispatch 产物
- 难度覆盖：优先争取 `L1-L8` 均衡；若 `qa-agent` 当前接口不能精确约束每级数量，则按实际发布分布统计并在结论中说明

### 1.2 本轮不做的事

- 不修改 knowledge-agent 内容
- 不让 repair-agent 真实写回 knowledge-agent
- 不在正式实验中途换题
- 不混用不同模型、不同数据、不同样本集版本
- 不在实验侧构造候选池、手工筛选正式集，或拼接历史 QA 行

---

## 二、远端现状基线

### 2.1 当前已确认路径

- `nl2cypher`: `/root/multi-agent/nl2cypher`
- `qa-agent`: `/root/multi-agent/qa-agent`
- `knowledge-agent`: `/root/multi-agent/knowledge-agent/backend`

### 2.2 当前已确认运行日志位置

当前正在运行的服务使用的日志路径是：

- `cypher-generator-agent`: 以当前进程实际 stdout/stderr 重定向路径为准
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

- `REPAIR_SERVICE_KNOWLEDGE_AGENT_REPAIRS_APPLY_CAPTURE_DIR`

即使 apply 被阻断，也可以利用 capture 保留准备发往 knowledge-agent 的 payload。

---

## 三、实验目录初始化

### 3.1 建议实验 id

建议格式：

`2026-04-26-cypher-generator-agent-baseline-freeze-v1`

### 3.2 建议实验根目录

建议在远端建立：

`/root/multi-agent/experiment_runs/2026-04-26-cypher-generator-agent-baseline-freeze-v1`

### 3.3 建议目录结构

```text
/root/multi-agent/experiment_runs/2026-04-26-cypher-generator-agent-baseline-freeze-v1/
  manifest.json
  environment/
  qa_agent/
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
   - `qa_agent_owns_sample_generation=true`
   - `target_qa_count=40`

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

1. 把 `REPAIR_SERVICE_KNOWLEDGE_AGENT_REPAIRS_APPLY_CAPTURE_DIR` 指向实验目录下的 capture 目录
2. 把 `REPAIR_SERVICE_KNOWLEDGE_AGENT_REPAIRS_APPLY_URL` 改为一个不可写回 knowledge 的阻断地址

推荐 capture 目录：

`/root/multi-agent/experiment_runs/2026-04-26-cypher-generator-agent-baseline-freeze-v1/environment/repair_apply_captures`

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

### 6.4 semantic review 联调补充

在真实 TuGraph 恢复后，最小链路联调还必须额外确认：

- semantic review 正常返回 `pass/fail + reasoning`

如果 semantic review 返回不合规，则本轮不进入正式实验，而应先保留以下证据：

- semantic review 原始返回
- semantic review 解析结果
- request id / model / qa_id / attempt_no

并将该样本记为：

- `failure_stage=semantic_review_invalid`

此时不得把样本直接计入普通业务失败，也不得继续触发 repair。

### 6.5 产物

建议保存到：

`rounds/smoke-test/`

如果最小链路联调不通过，不进入正式 `qa-agent` 作业阶段。

---

## 七、`qa-agent` 作业生成与内置 dispatch

### 7.1 当前目标

- 目标正式 QA：`40` 题
- 正式来源：`qa-agent` job 输出、release rows 与 dispatch result
- 约束：实验侧不得构造 `candidate_pool`、不得生成 `final_sample_set`、不得改写 QA 行

### 7.2 远端已确认的 `qa-agent` 逻辑

远端 `qa-agent` 提供以下入口：

- `POST /jobs`
- `POST /jobs/quick-run`
- `POST /jobs/{job_id}/run`
- `POST /jobs/{job_id}/dispatch`
- `POST /qa/{qa_id}/redispatch`

作业模型中 `output_config.target_qa_count` 当前限制为 `1-50`，因此本轮 `40` 题目标应作为一次 `qa-agent` 作业请求参数传入。

dispatch 逻辑由 `qa-agent` 内置完成：

1. 先向 question endpoint 发送 `id` 与 `question`
2. 再向 golden endpoint 发送同一 `id` 的 `cypher`、`answer` 与 `difficulty`
3. 记录 question 与 golden 两段各自的状态

这意味着 question 与 gold 必须来自同一个 `QASample` / release row，不允许实验侧拆开重组。

### 7.3 推荐触发方式

优先使用“创建 job，再 run”的两步方式，便于归档 job id 和中间状态。

示例：

```bash
EXP_ROOT=/root/multi-agent/experiment_runs/2026-04-26-cypher-generator-agent-baseline-freeze-v1
mkdir -p "$EXP_ROOT/qa_agent"

cat > "$EXP_ROOT/qa_agent/job_request.json" <<'JSON'
{
  "mode": "online",
  "validation_config": {
    "require_runtime_validation": true,
    "allow_empty_results": true,
    "roundtrip_required": true
  },
  "output_config": {
    "target_qa_count": 40
  }
}
JSON

curl -sS http://127.0.0.1:8020/jobs \
  -H 'Content-Type: application/json' \
  -d @"$EXP_ROOT/qa_agent/job_request.json" \
  | tee "$EXP_ROOT/qa_agent/job_create_response.json"
```

从 `job_create_response.json` 中取出 `job_id` 后执行：

```bash
JOB_ID=<job_id>

curl -sS -X POST "http://127.0.0.1:8020/jobs/$JOB_ID/run" \
  | tee "$EXP_ROOT/qa_agent/job_run_response.json"

curl -sS "http://127.0.0.1:8020/jobs/$JOB_ID" \
  | tee "$EXP_ROOT/qa_agent/job_snapshot.json"
```

如需快速联调，也可以使用：

```bash
curl -sS http://127.0.0.1:8020/jobs/quick-run \
  -H 'Content-Type: application/json' \
  -d @"$EXP_ROOT/qa_agent/job_request.json" \
  | tee "$EXP_ROOT/qa_agent/job_quick_run_response.json"
```

但正式实验优先保留两步 job 形式。

### 7.4 本阶段必须保存的内容

- `qa_agent/job_request.json`
- `qa_agent/job_create_response.json`
- `qa_agent/job_run_response.json`
- `qa_agent/job_snapshot.json`
- job artifacts 下载结果或引用
- release rows 或 packaged rows
- dispatch result
- dispatch log 摘录
- 覆盖统计报告

### 7.5 作业接受条件

只有满足以下条件时，才把该 job 作为正式实验输入：

- job 状态为 completed，或失败状态与失败原因已完整归档
- 所有正式 QA 都能追溯到同一个 job id
- question 与 golden dispatch 状态均可追踪
- `qa_id`、question、cypher、answer、difficulty 没有被实验侧改写
- 难度与 family 覆盖已统计

---

## 八、覆盖检查与作业接受 / 重跑决策

### 8.1 覆盖检查

对 `qa-agent` 发布结果做只读统计：

1. `difficulty` 分布
2. `query_type` 分布
3. `structure_family` 分布
4. 空结果 / 非空结果比例
5. dispatch 成功 / 部分成功 / 失败分布

### 8.2 接受原则

覆盖检查只用于决定“接受该 job”还是“另起一个新 job”，不得用于手工改写该 job 的发布行。

如果本轮必须严格达到每个 `Lx` `5` 题，只有两种合规方式：

1. 使用 `qa-agent` 已支持的作业参数或发布机制达成
2. 若当前 `qa-agent` 不支持该约束，则把“不支持精确分层控制”记录为实验限制，不在实验侧补造样本

### 8.3 重跑原则

覆盖不足时，可以重新发起新的 `qa-agent` job。

要求：

- 每个 job 单独保留 job id、request、snapshot、release 与 dispatch 证据
- 多 job 结果可以在总结中并列分析
- 不得把多个 job 的样本在实验侧重新洗牌后伪装成单一发布集

### 8.4 必须禁止的做法

不允许：

- 依据“是否容易成功”筛样本
- 先试跑再把失败样本排除出正式实验集
- 用“看起来更像 demo”的题替换难样本
- 从历史 candidate 文件里补题
- 手工修改 gold cypher 以适配当前 schema
- 手工修改 question 或 answer 以绕过 qa-agent 的一致性约束

### 8.5 作业归档落盘

建议保存：

- `qa_agent/job_request.json`
- `qa_agent/job_snapshot.json`
- `qa_agent/release_rows.jsonl`
- `qa_agent/dispatch_result.json`
- `qa_agent/coverage_report.json`

一旦接受某个 job，该 job 的发布结果不在同一实验 id 中修改。

---

## 九、按难度归档与分析正式样本

### 9.1 轮次结构

`qa-agent` 已经完成正式发送后，实验侧的“轮次”只表示分析分组，不再表示手动提交批次。

建议按难度生成分析目录：

- `round-001-L1`
- `round-002-L2`
- `round-003-L3`
- `round-004-L4`
- `round-005-L5`
- `round-006-L6`
- `round-007-L7`
- `round-008-L8`

### 9.2 每组固定动作

每个难度组都按以下顺序：

1. 记录轮次开始时环境快照
2. 从已接受的 `qa-agent` job / release 中筛出该难度样本
3. 按 `qa_id` 归档每题证据目录
4. 更新统一索引
5. 生成本轮 `summary.json`

注意：

- 这里的筛出只用于分析归档，不改变样本来源
- 如果某个 `Lx` 样本数不是 `5`，按实际数量统计，并在总结中说明

### 9.3 每组结束必须输出

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

每个 `qa_id` 只保存实验相关内容：

```text
qa/qa-0001/
  experiment_sample.json
  timing.json
  repair_apply_attempt.json
  notes.md
```

不得保存：

- `generator_request.json`
- `generator_result.json`
- `testing_submission.json`
- `testing_result.json`
- `issue_ticket.json`
- `repair_analysis.json`
- `service_refs.json`
- prompt、raw output、generated Cypher、evaluation、semantic review、repair suggestion 的正文副本
- 上述服务证据的服务路径、文件路径或引用清单

正式服务证据统一从运行中心界面查看，实验目录不再承担服务证据浏览入口职责。

### 10.1.1 实验字段裁剪原则

正式实验归档只保存实验侧内容：

- 实验身份
- qa-agent 作业与 release 维度
- 实验分组维度
- dispatch 统计
- repair apply 阻断证据
- 耗时与聚合统计
- 人工复盘结论

#### 必须完整保留的实验字段

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

这些字段属于正式服务证据，不进入实验落盘，也不保留路径引用：

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

### 10.2 耗时归档要求

每个 `qa_id` 都必须额外生成：

- `timing.json`

建议字段：

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

规则：

- 一律使用 `ms`
- 一律使用带时区的 ISO 8601 时间
- 未触发阶段填 `null`
- 只能部分还原时，记录 `timing_status=partial`
- 完全无法还原时，记录 `timing_status=missing`
- 先记录服务总耗时，再记录服务内部关键子阶段耗时
- `semantic_review_elapsed_ms` 仅表示 `testing-agent` 内部子阶段

### 10.2.1 耗时字段提取优先级

优先按以下顺序还原时间：

1. 服务持久化记录中的时间戳
2. API 请求发出与响应返回时间
3. 结构化日志时间戳
4. 服务普通日志中的时间近似推断

如果同一阶段存在多种时间来源，优先选择更接近服务内部处理边界的记录。

服务总耗时建议按以下边界定义：

1. `qa_agent_elapsed_ms`
   从 `qa-agent` 接收触发请求，到完成下游 dispatch 为止
2. `cypher_generator_elapsed_ms`
   从 `cypher-generator-agent` 接收问题，到返回生成结果或提交 testing 为止
3. `testing_agent_elapsed_ms`
   从 `testing-agent` 接收 submission，到形成最终评测状态为止
4. `repair_agent_elapsed_ms`
   从 `repair-agent` 接收 issue ticket，到形成 repair 结果并完成 apply 阻断尝试为止

### 10.3 统一索引表

建议维护：

- `indexes/qa_index.jsonl`
- `indexes/qa_index.csv`

字段建议：

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
- 先比较各服务总耗时
- 对比主要阶段耗时分位数
- 判断慢点是否集中在某个服务，或进一步集中在其内部子阶段

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
5. 所有 `qa_id` 是否都有 `timing.json`
6. 实验归档中是否没有 prompt、raw output、generated Cypher、evaluation、repair suggestion 或服务路径引用
7. 最终总结是否能明确回答能力边界问题

### 12.1 必须能回答的问题

最终至少要能明确回答：

- 当前稳定能力边界在哪一层
- 首个明显失稳层级是哪一层
- 失败主要发生在哪个阶段
- 慢主要集中在哪个服务
- 如果某个服务较慢，是否能继续定位到内部子阶段
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
2. `qa-agent` 作业生成与 dispatch 手册
3. 证据归档与汇总脚本使用手册
