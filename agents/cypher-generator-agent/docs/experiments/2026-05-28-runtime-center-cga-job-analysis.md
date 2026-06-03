# CGA OSI 重写后运行中心实验问题清单

## 2026-05-28 8 样本 Job 运行中心结果

- 本轮样本来源：qa-agent 280 条样本池 `/home/mabingjie/apps/qa-agent/artifacts/experiment_pools/pool_20260520T111838Z.jsonl`
- 抽取 job：`job_76a8e9d22f60`，共 8 条 QA 样本
- 发送记录：`/home/mabingjie/apps/qa-agent/artifacts/experiment_runs/send_job_to_cga8000_testing8003_after_decomposer_schema_rewrite_20260528T085013Z.jsonl`
- 本轮运行服务：CGA `118.196.92.128:8000`，运行中心 `118.196.92.128:8001`，testing-agent `8003`
- 本轮生成数据对应 CGA 版本：`60b446e`；本文档提交后远端 `DEPLOYED_REVISION=2660ce3`，该提交只包含实验文档变更
- 语义模型：`network_schema_v10`
- 运行中心落盘数据：`goldens=8`，`submissions=5`，`submission_attempts=5`，`generation_failures=3`，`issue_tickets=3`，`repair_analyses=3`
- 最新结果：`passed=1`，`failed=3`，`generation_failed/clarification_required=3`，`pending=1`

本轮 8 条样本中，`qa_76e37da317b4` 已通过：`统计系统中一共有多少个服务。` 生成 `MATCH (svc:Service) RETURN count(svc.id) AS service_count`，testing-agent 执行结果与 golden 一致。

其余 7 条样本暴露出几个集中问题。下面的归类不是互斥集合，同一 QA 可能同时落入多个类别。

- 新问题：投影字段丢失（4）：`qa_9cfa692813d5`、`qa_c80a82efe561`、`qa_c2508f2c0bac`、`qa_a5f4b0253af3`。题干明确要求多个返回字段，但 DSL/Cypher 只保留 ID 或错误终点对象。
- 新问题：参数化 Cypher 与 testing-agent 契约不一致（1）：`qa_c2508f2c0bac`。CGA compiler 输出了 `parameters={"quality_of_service":"Gold"}`，testing-agent 执行时只拿到 Cypher 文本，报 `Undefined parameter: $quality_of_service`。
- 新问题：控制词/Top-N 被误送入 literal resolver（2）：`qa_526d49332ed1`、`qa_c3e83dd7ad32`。`所有` 被当成 `Service.elem_type` 的取值，`前3` 被当成 `NetworkElement.location` 的取值，导致合法问题被错误澄清。
- 新问题：字段短语到 owner/property 的绑定错误（1）：`qa_6494b2085699`。`IP地址为10.0.0.4的网元` 应绑定到 `NetworkElement.ip_address`，实际 resolver 期望 `Tunnel.id`。
- 新问题：多跳路径降级为局部单跳（1）：`qa_a5f4b0253af3`。期望 `Service -> Tunnel -> dst NetworkElement -> Port`，实际生成 `Tunnel -[:TUNNEL_SRC]-> NetworkElement`。
- 新问题：运行中心状态与落盘 repair 数据关联不完整（4）：`qa_c2508f2c0bac`、`qa_9cfa692813d5`、`qa_c80a82efe561`、`qa_a5f4b0253af3`。存在 testing 执行错误但 verdict 仍 pending、repair analysis 文件存在但详情页显示未读取到诊断记录的情况。

## 2026-05-29 MIR-001 后重跑进度

- 本轮修复范围：MIR-001 投影槽位覆盖，包括 `slot_terms`、property projection 落地、projection coverage 校验、裸 vertex projection 拒绝、`vertex_full` 显式表达。
- 远端部署版本：`b9a9d8a`。
- 重跑时间：2026-05-28 20:43 左右（Asia/Shanghai）。
- 重跑发送记录：`/home/mabingjie/apps/qa-agent/artifacts/experiment_runs/send_current8_to_cga8000_testing8003_after_mir001_20260528T124349Z.jsonl`。
- 本轮运行服务：CGA `118.196.92.128:8000`，运行中心 `118.196.92.128:8001`，testing-agent `8003`。
- 健康检查：CGA、运行中心、testing-agent 均为 `ok`。
- 运行中心任务数：`8`。
- 首次 MIR-001 远端重跑结果：`passed=3`，`failed=3`，`clarification_required/pending=2`。后续严格闭环重跑见下一小节。

### MIR-001 阶段进展总览（历史）

> 这是 MIR-001 严格闭环重跑后的历史状态。MIR-003 参数内联完成后的最新状态见“2026-05-29 MIR-003 参数内联远端烟测”。

| QA ID | 旧问题类型 | 最新状态 | 进展判断 |
| --- | --- | --- | --- |
| `qa_9cfa692813d5` | 单点 vertex lookup 多字段投影丢失 | `generated / state=passed / strict_check=fail` | projection 已修复。最新 Cypher 返回 `id/name/elem_type/quality_of_service/bandwidth/latency` 六个字段；strict 仍提示“返回字段或字段值不一致”，后续要核对 golden 字段名、别名和值口径。 |
| `qa_c80a82efe561` | 单跳 traversal 终点多字段投影丢失 | `generated / state=passed / strict_check=fail` | projection 已修复。最新 Cypher 返回 `Tunnel.id/name/bandwidth`；strict 仍提示字段或字段值不一致。 |
| `qa_76e37da317b4` | 基础 count 聚合基准样本 | `generated / state=passed / strict_check=pass` | 保持严格通过。 |
| `qa_c2508f2c0bac` | 枚举 literal + 多字段投影 + 参数传递 | `generated / state=tugraph_execution_failed` | MIR-001 阶段 projection 已补齐，但当时仍因 testing-agent/TuGraph 未接收 `$quality_of_service` 参数而执行失败；该参数契约问题后续已由 MIR-003 闭环。 |
| `qa_526d49332ed1` | `所有` 被误送入 literal resolver | `generated / state=passed / strict_check=fail` | 不再因为“所有”直接澄清，但生成路径仍只覆盖 `Tunnel -> NetworkElement`，未完整覆盖“服务经过隧道穿过网元”。 |
| `qa_c3e83dd7ad32` | `前3` 被误送入 literal resolver | `generation_failed / clarification_required` | 仍待修。Top-N/排序/limit 语义仍被 literal resolver 当成取值处理。 |
| `qa_6494b2085699` | `IP地址` 到 `NetworkElement.ip_address` owner/property 绑定错误 | `generation_failed / clarification_required` | 仍待修。仍无法把 `10.0.0.4` 绑定到 `NetworkElement.ip_address`。 |
| `qa_a5f4b0253af3` | 服务-隧道-目的网元-端口多跳路径降级 | `generated / state=issue_ticket_created / strict_check=fail` | 仍待修。最新生成仍选错方向/路径，未覆盖 `Service -> Tunnel -> TUNNEL_DST -> NetworkElement -> HAS_PORT -> Port`。 |

### MIR-001 修复结论

MIR-001 对“用户要求多个返回字段但最终 Cypher 只返回 ID”的问题已经生效。首次远端重跑时两个最典型的投影回归样本 `qa_9cfa692813d5`、`qa_c80a82efe561` 均已通过；严格闭环重跑后，二者仍能生成完整 projection，但 testing-agent 的 `strict_check` 对字段或字段值仍报 fail。后续分析应把“CGA projection 是否完整”和“testing strict 是否完全一致”分开记录，避免把运行中心的 `state=passed` 误读成严格比对通过。

### 2026-05-29 本地严格闭环补充

- 本地 golden matrix 已新增 projection-slot 切片：`gq-031`、`gq-032`、`gq-033`，对应单点多字段投影、单跳终点多字段投影、filter + projection 区分。
- `grounded_understanding_v1` 已拒绝裸 `semantic_type=vertex` projection，并拒绝 edge/metric 等非 projection 类型；显式 `property` 与 `vertex_full` 保留。
- 运行中心字段说明已补充 `slot_terms` 和 `projection_coverage_missing`，用于解释返回字段覆盖缺失。
- 本地验证：
  - `PYTHONPATH=. pytest services/cypher_generator_agent/tests -q` -> `484 passed in 3.99s`
  - `PYTHONPATH=. pytest tests/test_runtime_results_service_api.py -q` -> `32 passed in 0.29s`

### 2026-05-29 远端严格闭环重跑

- 远端部署标识：`DEPLOYED_REVISION=b9a9d8a+mir001-strict-20260529`。
- 重跑时间：2026-05-29 11:07 左右（Asia/Shanghai）。
- 重跑前清理：已清空本轮 8 条样本在 testing/CGA/repair 数据目录下的旧记录，共删除 40 个落盘文件。
- 重跑发送记录：`/home/mabingjie/apps/qa-agent/artifacts/experiment_runs/send_current8_to_cga8000_testing8003_after_mir001_strict_20260529.jsonl`。
- 发送 run：`dispatch_20260529T030743Z`，8 条样本全部 dispatch 成功。
- 本轮运行服务：CGA `118.196.92.128:8000`，运行中心 `118.196.92.128:8001`，testing-agent `8003`。
- 健康检查：CGA 与运行中心均为 `ok`。
- 生成状态汇总：`generated=6`，`generation_failed/clarification_required=2`。
- testing-agent 状态汇总：`state=passed` 4 条，`issue_ticket_created` 1 条，`tugraph_execution_failed` 1 条，未进入 execution/evaluation 2 条。
- 严格比对状态：只有 `qa_76e37da317b4` 的 `strict_check=pass`；`qa_9cfa692813d5`、`qa_526d49332ed1`、`qa_c80a82efe561` 虽然 `evaluation.verdict=pass/state=passed`，但 `strict_check=fail`，运行中心和 testing-agent 的“通过”口径需要后续单独澄清。

| QA ID | MIR-001 阶段远端状态 | 生成结果 / 失败信息 | 判断 |
| --- | --- | --- | --- |
| `qa_9cfa692813d5` | `generated / state=passed / strict_check=fail` | `MATCH (svc:Service) RETURN svc.id, svc.name, svc.elem_type, svc.quality_of_service, svc.bandwidth, svc.latency` | MIR-001 的 projection 行为已生效，六个字段都已进入 Cypher；但 testing-agent strict check 仍提示“返回字段或字段值不一致”，需要继续核对 golden 字段别名/值口径。 |
| `qa_c80a82efe561` | `generated / state=passed / strict_check=fail` | `MATCH (svc:Service)-[:SERVICE_USES_TUNNEL]->(tun:Tunnel) RETURN tun.id, tun.name, tun.bandwidth` | projection 已补齐；strict check 仍提示字段或字段值不一致，属于 testing/golden 口径或字段值对齐问题。 |
| `qa_76e37da317b4` | `generated / state=passed / strict_check=pass` | `MATCH (svc:Service) RETURN count(svc.id) AS service_count` | 保持通过，是本轮唯一 strict fully pass 样本。 |
| `qa_c2508f2c0bac` | `generated / state=tugraph_execution_failed` | `WHERE svc.quality_of_service = $quality_of_service`，执行错误 `Undefined parameter: $quality_of_service` | MIR-001 阶段 projection 已补齐，但参数化 Cypher 与 testing-agent 执行契约当时仍未闭环；后续 MIR-003 已解决该执行契约问题。 |
| `qa_526d49332ed1` | `generated / state=passed / strict_check=fail` | `MATCH (tun:Tunnel)-[:PATH_THROUGH]->(ne:NetworkElement) RETURN ne.name, ne.vendor` | 不再因“所有”误澄清；但生成路径仍未显式覆盖 `Service -> Tunnel -> NetworkElement`，strict check 失败，后续应归入路径/关系覆盖 MIR。 |
| `qa_c3e83dd7ad32` | `generation_failed / clarification_required` | 澄清问题：`我没有确定“前3”对应的值，请选择或补充。` | Top-N/limit 仍被当成 literal 解析，仍待后续 MIR。 |
| `qa_6494b2085699` | `generation_failed / clarification_required` | 澄清问题：`我没有确定“10.0.0.4”对应的值，请选择或补充。` | `IP地址 -> NetworkElement.ip_address` owner/property 绑定仍待后续 MIR。 |
| `qa_a5f4b0253af3` | `generated / state=issue_ticket_created / strict_check=fail` | `MATCH (tun:Tunnel)-[:TUNNEL_SRC]->(ne:NetworkElement) RETURN tun.id, tun.name`，expected rows 54 / actual rows 20 | 仍是多跳路径与方向绑定问题，且 testing-agent 已创建 issue ticket。 |

远端严格闭环后的结论：

- `MIR-001` 的核心行为已经部署到远端：多字段 projection 不再被塌缩成单个 `id`。
- 运行中心本轮数据不能简单写成“4 条通过”，因为 `evaluation.verdict/state` 与 `strict_check` 出现不一致；后续实验报告应同时记录二者。
- MIR-001 阶段的剩余失败已经从“projection 被吞”转移到四类后续问题：参数执行契约、Top-N/limit 槽位、IP 字段 owner 绑定、多跳路径/方向覆盖。其中参数执行契约后续已由 MIR-003 闭环。

## 2026-05-29 MIR-002.7 本地 LLM token/latency 采样

- 采样目的：验证 `MIR-002 Decomposer Substantive Slot Hard Cut` 去掉独立 `slot_terms` 输出后，是否达到 output token 与 decomposer 耗时下降目标。
- 采样时间：2026-05-29 15:30 左右（Asia/Shanghai）。
- 采样方式：本地直接调用 `QuestionDecomposer`，使用 `.env` 中 `CYPHER_GENERATOR_AGENT_LLM_PROVIDER=openai_compatible` / `qwen3-32b` 配置；每条 query 连续跑 3 次。
- 采样字段：provider `usage.prompt_tokens`、`usage.completion_tokens`、`usage.total_tokens`、decomposer 端到端耗时、`retry_count`。

原始采样：

| Query | Run | input tokens | output tokens | total tokens | decomposer 耗时 | retry count |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `查询所有服务使用的隧道，返回隧道的 ID、名称和带宽` | 1 | 2836 | 246 | 3082 | 10540 ms | 0 |
| 同上 | 2 | 2836 | 246 | 3082 | 11018 ms | 0 |
| 同上 | 3 | 2836 | 243 | 3079 | 8534 ms | 0 |
| `Gold 服务使用了哪些隧道` | 1 | 2828 | 251 | 3079 | 9846 ms | 0 |
| 同上 | 2 | 2828 | 251 | 3079 | 10226 ms | 0 |
| 同上 | 3 | 2828 | 251 | 3079 | 9424 ms | 0 |

中位数汇总：

| Query | input tokens 中位数 | output tokens 中位数 | total tokens 中位数 | decomposer 耗时中位数 | retry count max |
| --- | ---: | ---: | ---: | ---: | ---: |
| `查询所有服务使用的隧道，返回隧道的 ID、名称和带宽` | 2836 | 246 | 3082 | 10540 ms | 0 |
| `Gold 服务使用了哪些隧道` | 2828 | 251 | 3079 | 9846 ms | 0 |

结论：

- 第一条 query 的 MIR-002.0 baseline output tokens 为 `270`，本轮中位数为 `246`，下降约 `8.9%`，未达到 `30-40%` 预期。
- 第一条 query 的耗时中位数为 `10.54s`，第二条为 `9.85s`，均未进入 `4-5s` 目标区间。
- 按 MIR-002.7 验收规则，实测与预期偏差超过 30%，本轮停止继续性能优化；后续应先决策是否切换 provider guided decoding、缩短 prompt schema、或调整模型/服务端参数。

## 2026-05-29 MIR-003 参数内联远端烟测

- 本轮修复范围：MIR-003 `Executable Cypher Inline Output with Template Trace`，即对外主 Cypher 使用内联后的 `cypher_executable/cypher`，trace 保留 `cypher_template/parameters/parameter_sources`。
- 远端部署标识：`2deb163+mir002-mir003-20260529153514`。
- 重跑时间：2026-05-29 15:36 左右（Asia/Shanghai）。
- 重跑前清理：已清空本轮 8 条样本在 testing/CGA/repair 数据目录下旧记录，共删除 26 个落盘文件。
- 重跑发送记录：`/home/mabingjie/apps/qa-agent/artifacts/experiment_runs/send_current8_to_cga8000_testing8003_after_mir003_inline_20260529T1536.jsonl`。
- 发送 run：`dispatch_20260529T073559Z`，8 条样本全部 dispatch 成功。
- 本轮运行服务：CGA `118.196.92.128:8000`，运行中心 `118.196.92.128:8001`，testing-agent `8003`。
- 健康检查：CGA 与运行中心均为 `ok`。
- testing-agent 状态汇总：`state=passed` 5 条，`issue_ticket_created` 1 条，`generation_failed/clarification_required` 2 条。
- 严格比对状态：`strict_check=pass` 1 条，`strict_check=fail` 5 条；2 条 generation_failed 未进入 execution/evaluation。

| QA ID | 当前远端状态 | 生成结果 / 失败信息 | 判断 |
| --- | --- | --- | --- |
| `qa_9cfa692813d5` | `generated / state=passed / strict_check=fail` | `MATCH (svc:Service) RETURN svc.id, svc.name, svc.elem_type, svc.quality_of_service, svc.bandwidth, svc.latency` | projection 仍完整；strict 仍提示“返回字段或字段值不一致”，属于既有 testing/golden 口径问题。 |
| `qa_c80a82efe561` | `generated / state=passed / strict_check=fail` | `MATCH (svc:Service)-[:SERVICE_USES_TUNNEL]->(tun:Tunnel) RETURN tun.id, tun.name, tun.bandwidth` | projection 仍完整；strict 仍提示字段或字段值不一致。 |
| `qa_76e37da317b4` | `generated / state=passed / strict_check=pass` | `MATCH (svc:Service) RETURN count(svc.id) AS service_count` | 保持 strict pass。 |
| `qa_c2508f2c0bac` | `generated / state=passed / strict_check=fail` | `MATCH (svc:Service) WHERE svc.quality_of_service = 'Gold' RETURN svc.id, svc.name, svc.bandwidth` | MIR-003 生效：`generated_cypher` 不含 `$quality_of_service`，TuGraph 执行成功，不再出现 `Undefined parameter`；strict 仍因字段或字段值口径失败。 |
| `qa_526d49332ed1` | `generated / state=passed / strict_check=fail` | `MATCH (tun:Tunnel)-[:PATH_THROUGH]->(ne:NetworkElement) RETURN ne.name, ne.vendor` | 不再因“所有”澄清；仍未完整覆盖 `Service -> Tunnel -> NetworkElement` 路径。 |
| `qa_c3e83dd7ad32` | `generation_failed / clarification_required` | 澄清问题：`我没有确定“3”对应的值，请选择或补充。` | Top-N/limit 仍被当成 literal 解析，仍待后续 MIR。 |
| `qa_6494b2085699` | `generation_failed / clarification_required` | 澄清问题：`我没有确定“10.0.0.4”对应的值，请选择或补充。` | `IP地址 -> NetworkElement.ip_address` owner/property 绑定仍待后续 MIR。 |
| `qa_a5f4b0253af3` | `generated / state=issue_ticket_created / strict_check=fail` | `MATCH (tun:Tunnel)-[:TUNNEL_SRC]->(ne:NetworkElement) RETURN ne.id AS network_element_id`，expected rows 54 / actual rows 20 | 仍是多跳路径与方向绑定问题，testing-agent 已创建 issue ticket。 |

远端烟测结论：

- MIR-003 的原始失败点已经消除：`qa_c2508f2c0bac` 不再输出参数占位符，也不再触发 `Undefined parameter: $quality_of_service`。
- 本轮没有改变 Top-N、IP owner/property、多跳路径方向和 strict mismatch 问题；这些仍按后续 MIR 处理。

MIR-003 后，参数执行契约已不再列为剩余问题；当时主要未解决问题从“projection coverage / 参数占位符执行失败”收敛到以下几类：

- strict mismatch：`qa_9cfa692813d5`、`qa_c80a82efe561`、`qa_c2508f2c0bac`、`qa_526d49332ed1` 等样本已能执行并得到 `state=passed`，但 `strict_check=fail`，需要核对 testing/golden 的字段别名、字段值、列顺序或结果口径。
- 控制词和查询结构槽位：`前3`、排序、limit 仍可能进入 literal resolver，而不是 DSL 的 `limit/order_by`。该 literal 误解析问题后续已由 MIR-004 的 slot 过滤防线消除，但聚合/order/limit 生成能力仍待后续处理。
- 字段短语到 owner/property 绑定：`IP地址` 仍未稳定绑定到 `NetworkElement.ip_address`。
- 多跳路径覆盖与方向词：`目的网元`、端口 hop、服务使用隧道关系仍可能被压缩成局部单跳。
- repair-agent 展示闭环：testing-agent 日志显示失败样本投递 repair-agent 时 8002 返回 500，会影响运行中心 repair 诊断展示。

## 2026-05-29 MIR-004 slot literal 过滤远端重跑

- 本轮修复范围：MIR-004 `Slot-Authoritative Literal Candidate Filtering`，即 literal request 构造前按 `substantive_terms[].slot` 过滤结构控制词，并在 trace/运行中心解释被跳过的 literal candidate。
- 远端部署标识：`ba68344+mir004-runtime-center-20260529`。
- 重跑时间：2026-05-29 16:44-16:46 左右（Asia/Shanghai）。
- 初始 QA Agent job dispatch：`job_76a8e9d22f60/dispatch` 返回 `partial`，原因是 QA Agent 调 CGA 的客户端超时为 10s，8 条 question 请求均超时；golden 已成功送达 testing-agent。
- 有效重跑方式：绕过 QA Agent 短超时，直接对 CGA `8000` 逐条长超时 POST 8 条 question，同时向 testing-agent `8003` 发送 golden。
- 重跑记录：
  - `/home/mabingjie/apps/qa-agent/artifacts/experiment_runs/current8_after_mir004_direct_rerun_20260529T084801Z.json`
  - `/home/mabingjie/apps/qa-agent/artifacts/experiment_runs/current8_after_mir004_runtime_summary_20260529T084801Z.json`
- 本轮运行服务：CGA `118.196.92.128:8000`，运行中心 `118.196.92.128:8001`，testing-agent `8003`，QA Agent `8020`。
- 健康检查：四个服务均为 `ok`。
- testing-agent 状态汇总：`final_verdict=pass` 4 条，`final_verdict=fail` 3 条，`pending` 1 条。
- 严格比对状态：`strict_check=pass` 1 条，`strict_check=fail` 6 条，`not_run` 1 条。

| QA ID | 当前远端状态 | 生成结果 / 失败信息 | 判断 |
| --- | --- | --- | --- |
| `qa_9cfa692813d5` | `generated / final_verdict=pass / strict_check=fail` | 返回 `service_id/service_name/service_elem_type/service_quality_of_service/service_bandwidth/service_latency`，执行成功 10 行。 | projection 仍完整；剩余 strict mismatch 属于字段别名/字段值口径问题。 |
| `qa_c80a82efe561` | `generated / final_verdict=pass / strict_check=fail` | 返回 `tunnel_id/tunnel_name/tunnel_bandwidth`，执行成功 20 行。 | projection 仍完整；strict mismatch 待核。 |
| `qa_76e37da317b4` | `generated / final_verdict=pass / strict_check=pass` | `MATCH (svc:Service) RETURN count(svc.id) AS service_count`。 | 基准 count 样本保持 strict pass。 |
| `qa_c2508f2c0bac` | `generated / final_verdict=pass / strict_check=fail` | `WHERE svc.quality_of_service = 'Gold'`，执行成功 4 行。 | MIR-003 参数内联仍正常；strict mismatch 待核。 |
| `qa_526d49332ed1` | `generated / final_verdict=fail / strict_check=fail` | `MATCH (tun:Tunnel)-[:PATH_THROUGH]->(ne:NetworkElement) RETURN ne.name, ne.vendor`，执行成功 72 行。 | 不再是 literal 澄清问题；剩余问题是服务路径覆盖不足。 |
| `qa_c3e83dd7ad32` | `generated / final_verdict=fail / strict_check=fail` | `MATCH (tun:Tunnel)-[:TUNNEL_SRC]->(ne:NetworkElement) RETURN ne.id AS network_element_id`，执行成功 20 行；golden 期望 location 分组计数、降序、`LIMIT 3`。 | MIR-004 原始失败点已消除：不再因“3”触发 `literal_unresolved/clarification_required`；trace 中 `literal_resolver.input.skipped_literal_candidates=[{"raw":"3","slot":"limit","reason":"slot=limit"}]`、`skipped_literal_candidate_count=1`。剩余失败是聚合、group_by、order_by、limit 没有进入 DSL/Cypher。 |
| `qa_6494b2085699` | `clarification_required / final_verdict=pending / strict_check=not_run` | 仍澄清：`我没有确定“10.0.0.4”对应的值，请选择或补充。` | IP owner/property 绑定仍待修。 |
| `qa_a5f4b0253af3` | `generated / final_verdict=fail / strict_check=fail` | `MATCH (tun:Tunnel)-[:TUNNEL_SRC]->(ne:NetworkElement) RETURN tun.id, tun.name`，执行成功 20 行。 | 多跳路径与方向词问题仍待修。 |

远端重跑结论：

- MIR-004 的确定性工程防线已生效：`qa_c3e83dd7ad32` 从 `clarification_required` 推进到 `generated`，原始的“limit 数字被送入 literal resolver”问题闭环。
- 该样本仍未通过评测，新的主要失败点是 aggregate/group/order/limit 结构没有落入 DSL；这应作为独立 MIR 处理，而不是继续扩展 literal 过滤逻辑。
- 运行中心字段说明已更新，详情页可以看到阶段输入和 `skipped_literal_candidates`，不再显示模板化占位解释。

## 2026-05-29 MIR-005 decomposer 冗余输出字段删除

- 本轮修复范围：删除 decomposer LLM 输出中的 `target_concepts`、`relation_phrases`、`stopword_terms`，收紧 prompt/schema/LLM 简化契约；`attached_to` 改为仅在消歧需要时输出；`modality_terms`、`unparsed_terms` 本轮保留。
- 审计结论：retriever 已改读 `substantive_terms`、literal、原问题和既有工程信号，不再读取 `target_concepts/relation_phrases`；coverage 不依赖 decomposer 的 `stopword_terms` 判断遗漏词。
- 运行中心：旧 trace 中的 `target_concepts/relation_phrases/stopword_terms` 仍能展示，但字段说明已改为兼容/历史含义，不再描述为当前 decomposer 输出。
- 本地验证：
  - `PYTHONPATH=. pytest services/cypher_generator_agent/tests -q` -> `517 passed in 3.71s`
  - `PYTHONPATH=. pytest tests/test_runtime_results_service_api.py -q` -> `32 passed in 0.32s`

轻量 LLM 采样（各 1 次，`qwen3-32b`，无 schema retry）：

| Query | prompt tokens | completion tokens | total tokens | decomposer 耗时 |
| --- | ---: | ---: | ---: | ---: |
| `查询所有服务使用的隧道，返回隧道的 ID、名称和带宽` | 2797 | 197 | 2994 | 7417 ms |
| `Gold 服务使用了哪些隧道` | 2789 | 173 | 2962 | 5649 ms |

结论：

- 对比 MIR-002.7 本地采样，completion tokens 从 `246/251` 降到 `197/173`，端到端耗时也下降。
- 第一条 query 仍未达到 MIR-005 预期的约 `155 completion tokens`，因此本轮只记录收益，不追加新的压缩策略；远端 8 样本重跑见下。

### MIR-005 远端部署与 8 样本重跑

- 本轮远端部署标识：`9aee174+mir005-20260529`。
- 重跑时间：2026-05-29 17:20-17:22 左右（Asia/Shanghai）。
- 重跑方式：清理当前 8 条样本在 testing/CGA/repair 数据目录下的旧记录后，直接长超时调用 CGA `8000` 和 testing-agent `8003`。
- 重跑记录：
  - `/home/mabingjie/apps/qa-agent/artifacts/experiment_runs/current8_after_mir005_direct_rerun_20260529T092051Z.json`
  - `/home/mabingjie/apps/qa-agent/artifacts/experiment_runs/current8_after_mir005_runtime_summary_20260529T092206Z.json`
- 服务状态：CGA `8000`、运行中心 `8001`、testing-agent `8003` 均 health ok。
- final verdict 汇总：`pass=5`，`fail=2`，`pending=1`。
- strict check 汇总：`pass=1`，`fail=6`，`not_run=1`。
- 契约检查：8 条样本的 `question_decomposer` 输出字段均为 `intent_type/literal_candidates/modality_terms/original_question/output_shape/result_type/schema_version/substantive_terms/time_terms/unparsed_terms`，不再包含 `target_concepts/relation_phrases/stopword_terms`。

| QA ID | 远端状态 | completion tokens / decomposer 耗时 | 判断 |
| --- | --- | ---: | --- |
| `qa_9cfa692813d5` | `generated / final_verdict=pass / strict_check=fail` | `196 / 6705 ms` | projection 仍完整；strict mismatch 仍是字段别名/字段值口径问题。 |
| `qa_c80a82efe561` | `generated / final_verdict=pass / strict_check=fail` | `197 / 9004 ms` | 单跳终点 projection 仍完整；strict mismatch 待核。 |
| `qa_76e37da317b4` | `generated / final_verdict=pass / strict_check=pass` | `129 / 7347 ms` | 基准 count 样本保持 strict pass。 |
| `qa_c2508f2c0bac` | `generated / final_verdict=pass / strict_check=fail` | `238 / 12119 ms` | 参数内联仍正常；strict mismatch 来自 `bandwidth` 与 `service_bandwidth` 别名口径。 |
| `qa_526d49332ed1` | `generated / final_verdict=pass / strict_check=fail` | `203 / 5351 ms` | testing-agent 判为语义等价，但 strict 仍因字段别名口径失败；路径覆盖问题仍需后续 MIR 重新审视。 |
| `qa_c3e83dd7ad32` | `generated / final_verdict=fail / strict_check=fail` | `244 / 9861 ms` | 不再 clarification；仍缺 location 聚合、排序和 `LIMIT 3`。 |
| `qa_6494b2085699` | `clarification_required / final_verdict=pending / strict_check=not_run` | `319 / 11570 ms` | IP owner/property 绑定仍未闭环；decomposer 已把 `10.0.0.4` attached_to `网元`。 |
| `qa_a5f4b0253af3` | `generated / final_verdict=fail / strict_check=fail` | `205 / 6076 ms` | 多跳路径与方向词仍未闭环。 |

远端结论：

- MIR-005 的 schema/prompt slimming 已在远端生效，旧三字段不再进入 decomposer 输出。
- 8 样本 final verdict 从 MIR-004 后的 `pass=4/fail=3/pending=1` 变为 `pass=5/fail=2/pending=1`；这主要来自 testing-agent 对 `qa_526d49332ed1` 判为 semantic equivalent，不代表路径语义问题已经完全解决。
- 性能收益存在但不均匀：多数样本 completion tokens 落在 `196-244`，简单 count 为 `129`，IP 复杂样本为 `319`；本轮不追加新压缩策略。

## 2026-05-29 MIR-006 结构覆盖闸门与 repair prompt 远端重跑

- 本轮修复范围：MIR-006 `Structural Requirements and DSL Coverage Gate`，包括从 decomposition 确定性派生 `structural_requirements`、DSL 构建后 compile 前的结构覆盖闸门、`dsl_structural_coverage_gate` trace 阶段，以及结构缺失进入 repair 时的 `structural_repair_guidance` prompt 增强。
- 本地验证：`PYTHONPATH=. pytest -q && git diff --check` -> `684 passed, 2 warnings`。warning 仍是既有 FastAPI `on_event` deprecation。
- 远端部署标识：
  - 首次结构闸门部署：`627f8a1+mir006-20260529`
  - repair prompt 补齐部署：`627f8a1+mir006-repair-20260529`
- 重跑时间：2026-05-29 20:59-21:01 左右（Asia/Shanghai）。
- 重跑方式：直接长超时调用 CGA `8000` 提交 8 条 question；CGA 与运行中心重启后 health check 均为 `ok`。
- 提交结果：8 条 question 全部 HTTP `204`。
- final verdict 汇总：`pass=4`，`fail=3`，`pending=1`。
- 生成状态汇总：`generated=4`，`generation_failed=3`，`clarification_required=1`。

| QA ID | MIR-006 后状态 | 生成结果 / 失败阶段 | 判断 |
| --- | --- | --- | --- |
| `qa_76e37da317b4` | `generated / final_verdict=pass` | `MATCH (svc:Service) RETURN count(svc.id) AS service_count` | 基础 count 样本继续通过；结构闸门未误伤。 |
| `qa_9cfa692813d5` | `generated / final_verdict=pass` | 返回 `service_id/service_name/service_elem_type/service_quality_of_service/service_bandwidth/service_latency`。 | 多字段 projection 继续通过；alias/strict 口径仍不归 MIR-006。 |
| `qa_c2508f2c0bac` | `generated / final_verdict=pass` | `WHERE svc.quality_of_service = 'Gold'`，返回 `service_id/service_name/service_bandwidth`。 | 参数内联和枚举 literal 继续正常；剩余 alias 口径不归 MIR-006。 |
| `qa_c80a82efe561` | `generated / final_verdict=pass` | `MATCH (svc:Service)-[:SERVICE_USES_TUNNEL]->(tun:Tunnel) RETURN tun.id/tun.name/tun.bandwidth`。 | 单跳终点 projection 继续通过。 |
| `qa_526d49332ed1` | `generation_failed / final_verdict=fail` | `dsl_structural_coverage_gate` 拦截，repair 后仍再次失败，最终 `repair_binding_oscillation`。 | MIR-006 已阻止路径覆盖不足的 DSL 继续编译；但 grounded_understanding 收到结构缺失提示后仍未补出 `Service -> Tunnel -> NetworkElement` 的完整路径。 |
| `qa_a5f4b0253af3` | `generation_failed / final_verdict=fail` | `dsl_structural_coverage_gate` 拦截，repair 后仍再次失败，最终 `repair_binding_oscillation`。 | 闸门能抓 hop/projection 覆盖不足；方向正确性和多跳结构补齐仍未收敛。 |
| `qa_c3e83dd7ad32` | `generation_failed / final_verdict=fail` | `dsl_structural_coverage_gate` 拦截，repair 后仍再次失败，最终 `repair_binding_oscillation`。 | 已不再把 `3` 当 literal 澄清；结构闸门抓住 aggregate/group_by/order_by/limit 缺失，但 repair 未能生成正确 top-N 聚合 DSL。 |
| `qa_6494b2085699` | `clarification_required / final_verdict=pending` | literal resolver 后 `repair_controller` 决策 `ask_user`，澄清：`我没有确定“10.0.0.4”对应的值，请选择或补充。` | 仍是 literal owner/property 绑定问题，不归 MIR-006。 |

远端结论：

- MIR-006 的“拦截错误结构”目标已经生效：`qa_526d49332ed1`、`qa_a5f4b0253af3`、`qa_c3e83dd7ad32` 不再继续输出局部合法但结构覆盖不足的 Cypher，而是在 compile 前以 `structural_coverage_missing` 进入 repair。
- MIR-006.4 的 repair prompt 增强已进入链路：第二轮 `grounded_understanding` trace 中带有 repair context，错误码为 `structural_coverage_missing`。
- 当前未闭环的是“补齐能力”而不是“发现能力”：三条结构样本均在第二轮 repair 后仍触发同类结构覆盖失败，并由 repair controller 以 `repair_binding_oscillation` 退出。
- 下一步不应继续扩大覆盖闸门，而应拆出 grounded_understanding 的结构补齐能力：例如 top-N 聚合 shape 选择、multi-hop path pattern/binding 选择、以及方向词到 edge 的选择能力。

## 2026-05-30 MIR-009 / MIR-010 Wave 4 随机 5 job 远端验证

- 本轮目的：验证 MIR-009 retrieval structural reranker、MIR-010 确定性形态拼装主路径、single-shot fallback、F6 grouped top-N DSL 扩展后的远端端到端表现。
- 本轮样本来源：qa-agent 280 条样本池 `/home/mabingjie/apps/qa-agent/artifacts/experiment_pools/pool_20260520T111838Z.jsonl`。
- 抽取方式：从 34 个“每 job 8 条样本”的候选 job 中随机抽取 5 个 job，共 40 条样本。
- 抽取 pool：`/home/mabingjie/apps/qa-agent/artifacts/experiment_pools/pool_random5_jobs_from_full280_20260530T055139Z.jsonl`。
- 发送记录：`/home/mabingjie/apps/qa-agent/artifacts/experiment_runs/dispatch_random5_jobs_from_full280_after_wave4_20260530T055139Z.jsonl`。
- 发送 run：`dispatch_20260530T055153Z`，40 条样本全部 dispatch 成功；golden 和 question 均被远端服务接收。
- 远端部署：`/home/mabingjie/nl2cypher`，短 SHA `3d6428d`。
- 本轮运行服务：CGA `118.196.92.128:8000`，运行中心 `118.196.92.128:8001`，testing-agent `8003`。
- 重跑前清理：已清空运行中心和用户查询历史数据；本轮运行中心只包含这 40 条新任务。
- 运行中心任务数：`40`。

抽中的 5 个 job：

| Job | QA 数 | 难度覆盖 | QA IDs |
| --- | ---: | --- | --- |
| `job_af3390c071d9` | 8 | L1-L8 各 1 条 | `qa_0894cec1e379`, `qa_357ab16752d3`, `qa_53f55dcb76e1`, `qa_bc2be4c107a4`, `qa_65f6a2d6ec7a`, `qa_237709f1e0bf`, `qa_61ccc1eb8b28`, `qa_308fbc0d1fc4` |
| `job_93c80f273cf0` | 8 | L1-L8 各 1 条 | `qa_eab900f5506a`, `qa_8f0ff9964327`, `qa_9a90460769a1`, `qa_13e53c300585`, `qa_317a691f4692`, `qa_ddfe5a145dac`, `qa_8f7828939540`, `qa_0a3dd21f6bb2` |
| `job_bad534a297f3` | 8 | L1-L8 各 1 条 | `qa_bac30fa7a3d4`, `qa_0aaee7c65e04`, `qa_3defdb361c30`, `qa_892258f18489`, `qa_97ec535348bf`, `qa_128758adfbc2`, `qa_3c9aec952d52`, `qa_0272f881bbf0` |
| `job_76a8e9d22f60` | 8 | L1-L8 各 1 条 | `qa_9cfa692813d5`, `qa_c3e83dd7ad32`, `qa_6494b2085699`, `qa_526d49332ed1`, `qa_a5f4b0253af3`, `qa_c80a82efe561`, `qa_c2508f2c0bac`, `qa_76e37da317b4` |
| `job_5aa777f908b1` | 8 | L1-L8 各 1 条 | `qa_fdbee3091b2c`, `qa_ce94edd07de4`, `qa_e996f9cb63c2`, `qa_2d8bfe3a9a0b`, `qa_d8f64bac7115`, `qa_0dd3946aceb2`, `qa_e320794a8a2d`, `qa_504560700cdc` |

运行中心最终汇总：

| Difficulty | Total | Pass | Fail | Pending |
| --- | ---: | ---: | ---: | ---: |
| L1 | 5 | 5 | 0 | 0 |
| L2 | 5 | 3 | 0 | 2 |
| L3 | 5 | 1 | 4 | 0 |
| L4 | 5 | 1 | 4 | 0 |
| L5 | 5 | 0 | 5 | 0 |
| L6 | 5 | 0 | 5 | 0 |
| L7 | 5 | 0 | 5 | 0 |
| L8 | 5 | 0 | 1 | 4 |
| **合计** | **40** | **10** | **24** | **6** |

Pending 样本均为 `clarification_required / current_stage=query_generation`，不是后台仍在执行：

| QA ID | Difficulty | 问题 |
| --- | --- | --- |
| `qa_308fbc0d1fc4` | L2 | 查询名称为 Service_002 的服务的 ID、名称和服务质量。 |
| `qa_e320794a8a2d` | L2 | 查询延迟等于20的服务的ID、名称和延迟。 |
| `qa_237709f1e0bf` | L8 | 统计各业务使用的隧道总数，以及这些隧道中源节点位于Site_01的网元数量，返回业务名称、隧道总数和网元数量。 |
| `qa_6494b2085699` | L8 | 查询经过IP地址为10.0.0.4的网元的服务的ID、类型、隧道总数及匹配网元数量。 |
| `qa_0a3dd21f6bb2` | L8 | 查询经过网元NetworkElement_005的各业务，统计每个业务使用的隧道总数以及经过该特定网元的隧道数量。 |
| `qa_504560700cdc` | L8 | 统计各业务类型使用的隧道总数，以及其中经过IP地址为10.0.0.1的网元的数量。 |

本轮观察：

- MIR-010 的确定性主路径对简单 0-hop / 单 hop 样本已经有明显效果，L1 `5/5` 通过，L2 `3/5` 通过；这与前序 F1/F2/F3 远端秒级验证一致。
- 中高难度仍大量失败，主要集中在多跳结构、路径 + 端口、路径分组 top-N、属性计数、两阶段聚合和 literal owner/property 绑定。
- `qa_c3e83dd7ad32` 在本轮仍 `generation_failed`，说明 F6 grouped top-N DSL 扩展尚未把该类“服务 -> 隧道 -> 源节点 location 分组 top-N”稳定拼装成功。
- `qa_6494b2085699` 仍 `clarification_required`，说明 `IP地址 -> NetworkElement.ip_address` 绑定问题仍未闭环，不能由 MIR-010 主路径控制流自然消除。
- 多个 L7/L8 样本已进入 generated 后 fail，说明系统开始能产出可执行查询，但结构等价、方向、聚合内容正确性仍是主要短板。

阶段结论：

- MIR-009 / MIR-010 的控制流和确定性主路径已经具备远端运行能力，但 280 池随机 40 条的整体通过率仍只有 `10/40`。
- 当前问题已经从“多轮 LLM 震荡和延迟不可控”转向“确定性拼装语料库覆盖不足、literal owner/property 绑定不足、复杂路径/聚合 DSL 内容正确性不足”。
- 下一步应优先复盘 `generation_failed` 的 failure reason 分布，拆分为 DSL 表达力不足、assembler 唯一性门槛过严、candidate 缺失、literal owner/property 绑定和 testing strict/verdict 口径几类，而不是继续扩大主控制流重构范围。

## 2026-05-30 L2 投影 / vertex lookup 残缺抽样闭环

本轮目的：针对 L2 去重 86 条上一轮暴露出的投影字段不全、`vertex_full` 丢失、vertex lookup owner 推断和 limit-only tail 小缺口做最小修复验证，不做新的架构调整。

本地验证：

- `python -m pytest services/cypher_generator_agent/tests` -> `615 passed in 4.48s`。
- 相关回归覆盖：`服务ID` / `服务名称` / `编号` / `等级值` / `服务质量等级` 投影 surface，`节点/详细信息/全部属性信息` -> `vertex_full`，唯一 literal owner 反推 0-hop owner，vertex lookup `LIMIT`，低分 token 级 vertex 噪声不阻断确定性 0-hop。

远端验证：

- 部署后服务：CGA `8000`、运行中心 `8001`、testing-agent `8003` 均 health ok；repair-agent 未启动。
- 8 条问题样本烟测：`smoke_l2_projection_vertex_fix4_20260530T155847Z` 先确认 7/8 `generated + passed`。
- 剩余 `qa_38392098b2fc` 失败原因是 limit 数字 `3` 通过 description token 低分召回 `Port` vertex，导致 deterministic assembler 不敢把 `服务节点` 收敛到 `Service` 0-hop。修复后单条复测 `smoke_single_qa383_fix5_20260530T160229Z` 通过。

抽样结果：

| QA ID | 结果 | 最新生成 |
| --- | --- | --- |
| `qa_ac1628a116ce` | passed | `MATCH (svc:Service) WHERE svc.name = 'Service_002' RETURN svc.name AS service_name` |
| `qa_9dbce40de404` | passed | `MATCH (svc:Service) WHERE svc.bandwidth = 110.0 RETURN svc.bandwidth AS service_bandwidth` |
| `qa_b4a89bcff37b` | passed | `MATCH (svc:Service) WHERE svc.id = 'svc-mpls-vpn-1004' RETURN svc.name AS service_name` |
| `qa_d54077843edf` | passed | `MATCH (svc:Service) WHERE svc.elem_type = 'MPLS-VPN' RETURN svc.id AS id, svc.elem_type AS elem_type` |
| `qa_496e7f48d122` | passed | `MATCH (svc:Service) WHERE svc.latency = 22.0 RETURN svc.id AS service_id, svc.name AS service_name, svc.latency AS service_latency` |
| `qa_7b6cfb452f30` | passed | `MATCH (svc:Service) WHERE svc.latency = 22.0 RETURN svc AS service` |
| `qa_0a356cfd860a` | passed | `MATCH (svc:Service) WHERE svc.name = 'Service_002' RETURN svc AS service` |
| `qa_38392098b2fc` | passed | `MATCH (svc:Service) WHERE svc.name = 'Service_003' RETURN svc AS service LIMIT 3` |

结论：

- 本轮抽样中的 L2 投影 / vertex lookup 残缺已闭环，8/8 通过。
- 这只证明该失败簇已按样本闭环，不代表 L2 全量分布已经刷新；下一步仍需重跑 L2 全量 86 条，重新统计剩余 `coverage_failure`、`compiler_shape_mismatch`、fallback schema 和 semantic verdict 分布。

## 问题明细与当前进度

| QA ID | 具体问题 | 修复方案 |
| --- | --- | --- |
| `qa_9cfa692813d5`（projection 已修复，strict 待核） | `查询所有服务的ID、名称、元素类型、服务质量等级、带宽和时延。` 的 golden 需要返回 `id/name/elem_type/quality_of_service/bandwidth/latency` 六个字段；旧版本只生成 `MATCH (svc:Service) RETURN svc.id AS service_id`。最新版本已经返回六个字段，但 testing-agent 的 `strict_check` 仍提示字段或字段值不一致。 | MIR-001 已修复 projection 丢失问题。下一步不应再按“缺 projection”处理，而应核对 testing/golden 的字段别名、返回值格式、列顺序或字段命名口径。该样本继续保留为 projection coverage 回归用例，同时新增 strict mismatch 观察。 |
| `qa_c80a82efe561`（projection 已修复，strict 待核） | `查询所有服务使用的隧道，返回隧道的 ID、名称和带宽。` 正确选中了 `Service -[:SERVICE_USES_TUNNEL]-> Tunnel`，旧版本只返回 `tun.id AS tunnel_id`。最新版本已返回 `tun.id/tun.name/tun.bandwidth`，但 testing-agent 的 `strict_check` 仍提示字段或字段值不一致。 | MIR-001 已修复终点多字段 projection 丢失问题。下一步应核对 golden 与 TuGraph 实际返回字段/值口径，而不是继续修改 projection resolver。 |
| `qa_c2508f2c0bac`（参数契约已修，strict 待核） | `查询服务质量等级为金牌的所有服务的ID、名称和带宽。` 中 literal resolver 正确把 `金牌` 解析为 `Gold`；MIR-003 后对外 `generated_cypher` 已内联为 `WHERE svc.quality_of_service = 'Gold'`，返回 `id/name/bandwidth`，testing-agent 执行成功，`state=passed / verdict=pass / execution_success=true`。当前仅剩 `strict_check=fail`。 | MIR-001 已补齐 projection，MIR-003 已闭环参数占位符执行失败。下一步应核对 strict mismatch 的字段别名、返回值、列顺序或 golden 口径；该样本继续作为枚举同义词 literal、内联可执行 Cypher、多字段 projection 的回归用例。 |
| `qa_526d49332ed1`（结构闸门已拦截，repair 未收敛） | `查询所有服务经过隧道穿过的网元的名称和厂商。` 的旧问题是 `所有` 被误当 literal 并澄清；MIR-004 后不再澄清。MIR-006 前会生成局部路径 `Tunnel -[:PATH_THROUGH]-> NetworkElement`，缺失 `Service -> Tunnel` 前缀。MIR-006 后该类 DSL 被 `dsl_structural_coverage_gate` 拦截，最终因 repair 后仍未补齐而 `repair_binding_oscillation`。 | 结构覆盖“发现/拦截”已生效。下一步要增强 grounded_understanding 的 multi-hop path 补齐能力，让 repair 能根据 `path_hops_insufficient` 选择 `Service/SERVICE_USES_TUNNEL/Tunnel/PATH_THROUGH/NetworkElement`，而不是重复生成单跳。 |
| `qa_c3e83dd7ad32`（literal 澄清已修，结构闸门已拦截，repair 未收敛） | `统计服务使用的隧道源节点所在位置的网元数量，按数量降序排列，返回前3名。` 的原始问题是 limit 数字“3”被送入 literal resolver。MIR-004 后 `3` 因 `slot=limit` 被跳过，不再澄清；MIR-006 后缺 aggregate/group_by/order_by/limit 的 DSL 会在 compile 前被拦截。当前最新结果是 `generation_failed / repair_binding_oscillation`。 | 下一步不是再加 literal 过滤或覆盖闸门，而是增强 top-N 聚合结构补齐：repair 后应能选择 aggregate-capable query shape，填充 `measures/group_by/sort/limit`，并补上 `Service -> Tunnel -> TUNNEL_SRC -> NetworkElement` 路径。 |
| `qa_6494b2085699`（待修） | `查询经过IP地址为10.0.0.4的网元的服务的ID、类型、隧道总数及匹配网元数量。` 中 decomposer 输出 `literal_candidates=[{"text":"10.0.0.4","kind_hint":"id","attached_to":"IP地址"}]`，但 resolver 期望字段变成 `Tunnel.id`，最后澄清 `10.0.0.4` 未解析。golden 明确应为 `NetworkElement.ip_address = '10.0.0.4'`。 | owner/property 绑定要优先消费字段短语：`IP地址` 应强匹配 `NetworkElement.ip_address`，且“的网元”提供 owner 约束。literal resolver 输入不应只根据当前主路径候选猜 owner；需要把 `attached_to=IP地址` 先解析成 property，再由 property owner 反推 vertex。若 value index miss，也应提示“未在 NetworkElement.ip_address 中找到 10.0.0.4”，而不是泛化为“对应的值”。 |
| `qa_a5f4b0253af3`（结构闸门已拦截，方向/多跳补齐待修） | `查询所有服务使用的隧道目的网元上的端口ID、名称和状态。` 的 golden 路径是 `Service -[:SERVICE_USES_TUNNEL]-> Tunnel -[:TUNNEL_DST]-> NetworkElement -[:HAS_PORT]-> Port`。MIR-006 前会生成局部 `Tunnel -[:TUNNEL_SRC]-> NetworkElement` 并返回错误对象；MIR-006 后该类 hop/projection 覆盖不足会被拦截，最新结果为 `generation_failed / repair_binding_oscillation`。 | 结构覆盖“发现/拦截”已生效，但本 MIR 不直接证明 `TUNNEL_DST` 方向正确性。下一步需要增强 multi-hop path binding 与方向词选择能力，让 repair 能补出 `SERVICE_USES_TUNNEL/TUNNEL_DST/HAS_PORT` 并投影 `Port.id/name/status`。 |

## 2026-05-28 本轮可沉淀的回归集合

建议把本轮 8 条样本作为 OSI 重写后第一批小型回归集合，覆盖以下能力点：

- `qa_76e37da317b4`：基础 count 聚合，应保持通过。
- `qa_9cfa692813d5`：单点 vertex lookup 多字段投影。
- `qa_c80a82efe561`：单跳 traversal 的终点多字段投影。
- `qa_c2508f2c0bac`：枚举同义词 literal 解析、内联可执行 Cypher、多字段投影、strict mismatch 观察。
- `qa_526d49332ed1`：`所有` 不应触发 literal 澄清；路径覆盖不足应被结构闸门拦截，后续作为 multi-hop repair 补齐样本。
- `qa_c3e83dd7ad32`：Top-N/排序/limit 结构不能进入 literal resolver；结构覆盖不足应被闸门拦截，后续作为 aggregate/group/order/limit repair 补齐样本。
- `qa_6494b2085699`：字段短语 `IP地址` 到 `NetworkElement.ip_address` 的绑定。
- `qa_a5f4b0253af3`：服务-隧道-目的网元-端口多跳路径、方向词和终点字段投影。
