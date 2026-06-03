# CGA OSI Follow-up Modification IR

> 更新日期：2026-06-02
> 状态：MIR 收口摘要
> 适用分支：`cypher-generation-osi`

## 1. 文档定位

本文记录 OSI 重写后所有后续修改项。已完成 MIR 不再保留完整实施流水账，只保留触发问题、关键修复、结果和剩余边界。未完成问题统一进入“遗留问题清单”，后续按 run id 和样本 id 继续小步追加。

当前基线：

- 本地 CGA：`708 passed`。
- 完整远端 680：`run680_l1l4_tail_cleanup_highc_20260602T073622Z`，`passed=407`。
- 局部 twofix：`run680_l1l4_tail_twofix_20260602T075447Z`，2 条目标样本均通过，完整 680 尚未复跑。

## 2. 总体修改原则

1. LLM 只填空或做 bounded fallback，不独自决定最终结构。
2. 不静默吞语义，实义词必须进入正确槽位。
3. coverage 按 slot 和结构判断，不按“词是否被任意候选命中”判断。
4. builder/compiler 不猜业务意图。
5. 错误要在靠前阶段暴露。
6. 每个修改项都要沉淀 regression。
7. 词义归一、计数口径、operator、裸对象输出口径都必须是封闭枚举或确定性查表。
8. 宁可认输，不静默猜测；不默认方向、不默认 id、不默认最高分候选。

## 3. MIR 状态总览

| MIR / 阶段 | 状态 | 结果摘要 |
| --- | --- | --- |
| MIR-001 Projection Slot Coverage | Done | 禁止显式字段塌缩为裸 vertex / id。 |
| MIR-002 Decomposer Slot Hard Cut | Done / absorbed | schema/prompt 瘦身已吸收进主路径，不再单独追性能。 |
| MIR-003 Executable Cypher Inline Output | Done | 对外输出可执行内联 Cypher，template/params 只留 trace。 |
| MIR-004 Literal Candidate Filtering | Done | limit/order/path 等结构词不再误送 LiteralResolver。 |
| MIR-005 Redundant Output Removal | Done / absorbed | decomposer 输出瘦身完成，端到端延迟主要由确定性主路径接管。 |
| MIR-006 Structural Requirements Gate | Done | coverage gate 成为主防线，后续多轮深化。 |
| MIR-007 LLM handoff | Retired | 多轮 deterministic/LLM handoff 被 MIR-010 主路径取代。 |
| MIR-008 Compact GU Contract | Partly done | schema guard 已收紧并还债一部分，GU 深水区仍未闭环。 |
| MIR-009 Retrieval Reranker | Done | reranker 已接入；激进候选闭包扩展未进入当前基线。 |
| MIR-010 Deterministic Main Path | Done | F1-F6、0-hop、多跳、fallback 路径接入。 |
| MIR-011 Literal Pass-through | Done | L2 literal 澄清归零过，当前 L1/L2 已达标。 |
| MIR-012 Named Path Projection Role | Done / absorbed | path role binding 问题被后续 multi-owner / source identity 修复吸收。 |
| MIR-013 Deterministic Projection Coverage | Done | 271 -> 318，projection 覆盖完成第一大段收益。 |
| MIR-014 Deterministic Structural Coverage | Done | 318 -> 330，结构槽位完整性有正收益但边际下降。 |
| Stage 2 GU Boundary / Schema | Done with debt paid partly | 330 -> 342；schema guard 债后来从约 57 降到 24。 |
| Gate Terms Tightening | Done | 342 -> 358；L1 达标，L4 大幅抬升。 |
| L4 Property Count | Done | 358 -> 370；`count(property)` vs `count(vertex)` 口径落地。 |
| Schema Guard Debt | Done | 370 -> 376；L1/L2 接近或达到 95。 |
| Dual-owner Projection | Done | 376 -> 387；L1/L2 达标，L3/L4 抬升。 |
| Direction B1 | Rolled back | 387 -> 381，净负；诊断证明方向不是主瓶颈。 |
| L4 Aggregate Deepening | Done | 387 -> 396；L4 75.3% -> 87.1%，testing fail 下降。 |
| L3 Coverage / Projection | Done | 396 -> 404；L3 抬升，L1/L2 保持达标。 |
| Naked Object Projection Policy | Scoped / Done | 修复默认 id 债；作用域收窄到 projection 角色，结构锚点不进判定。 |
| Reserved Alias Scope | Done | compiler reserved words 泛化，不与业务 schema 强绑定。 |
| L1-L4 Tail Cleanup | Partly verified | 完整 run 407；twofix 2 条局部通过，完整 680 未复跑。 |

## 4. 最新指标链路

| 阶段 | Run | passed | 备注 |
| --- | --- | ---: | --- |
| MIR-013 后 | `run680_after_mir013_patch2_20260601T045909Z` | 318 | projection coverage 第一段闭环。 |
| MIR-014 后 | MIR-014 clean baseline | 330 | structural coverage 小幅净正。 |
| Stage 2 GU 后 | `run680_stage2_gu_boundary_highc_20260601T135437Z` | 342 | GU/schema 收紧净正但有 schema guard 债。 |
| Gate terms | `run680_l1l4_gate_terms_tight_highc_20260601T144648Z` | 358 | L1 达标。 |
| L4 property count | `run680_l4_property_count_valuefix_highc_20260601T152642Z` | 370 | L4 属性计数口径。 |
| Schema guard debt | `run680_schema_guard_debt_highc_20260601T155409Z` | 376 | schema_invalid 显著下降。 |
| Dual-owner projection | `run680_projection_dual_owner_highc_20260601T163914Z` | 387 | L1/L2 达标。 |
| L4 aggregate deepening | `run680_l4_aggregate_deepening_fix2_highc_20260602T020318Z` | 396 | L4 到 87.1%。 |
| L3 coverage/projection | `run680_l3_cov_projection_highc_20260602T023306Z` | 404 | L3 到约 84.7%。 |
| Reserved alias / naked scope | `run680_reserved_alias_scope_highc_20260602T064928Z` | 404 | 修口径债，保持基线。 |
| L1-L4 tail cleanup | `run680_l1l4_tail_cleanup_highc_20260602T073622Z` | 407 | 完整 run，L3/L4 仍未达 95。 |
| L1-L4 twofix | `run680_l1l4_tail_twofix_20260602T075447Z` | N/A | 仅 2 条局部验证通过，不是完整 run。 |

## 5. 最新分级结果

完整 run `run680_l1l4_tail_cleanup_highc_20260602T073622Z`：

| 难度 | 通过 / 总数 | 准确率 | 结论 |
| --- | ---: | ---: | --- |
| L1 | 83 / 84 | 98.8% | 达标 |
| L2 | 84 / 86 | 97.7% | 达标 |
| L3 | 76 / 85 | 89.4% | 未达标 |
| L4 | 75 / 85 | 88.2% | 未达标；twofix 后投影约 89.4% |
| L5 | 52 / 85 | 61.2% | 新阶段主战场 |
| L6 | 20 / 85 | 23.5% | 新阶段主战场 |
| L7 | 17 / 85 | 20.0% | 新阶段主战场 |
| L8 | 0 / 85 | 0.0% | 新阶段主战场 |

## 6. 已完成修改项压缩摘要

### Projection 和 coverage

- MIR-001 / MIR-013：显式 projection 字段必须按 owner.property 落 DSL，不得塌缩成 vertex/id。
- 第三刀 A：multi-owner / dual-owner projection 补全，要求每端唯一绑定才落。
- coverage property 粒度：同 owner 多属性不能互相伪覆盖，例如 `Tunnel.name` 不能覆盖 `Tunnel.bandwidth`。
- L3 coverage/projection 深化：补 source/target identity、alias/source 残留中的确定性部分。

### Structural requirements

- MIR-014：aggregate / group_by / order_by / limit / path hop 从 structural requirements 派生完整性约束。
- gate terms：`信息/详情/详细信息/服务节点/连接关系/对应关系` 等泛指词进入封闭枚举，不再产生虚假结构要求。
- 清单外词仍保留拦截，避免误放真字段缺失。

### Aggregation

- 第二刀：属性计数短语落 `count(owner.property)`，实体计数维持 `count(vertex)`。
- L4 aggregate deepening：补多 measure、非空过滤、比较 operator、属性计数残留。
- L1-L4 tail twofix：`qa_2d0c3389a92f` 与 `qa_651a0605545c` 均回到 `count(Service.quality_of_service/latency)` 并通过。

### GU / schema

- Stage 2：候选边界与 schema guard 收紧。
- Schema guard debt：误拦松开，真畸形仍拦；schema_invalid 从约 57 降到约 24 的阶段性结果。
- 深水区仍保留，不用确定性猜测替代 GU 裁决。

### Naked object projection

- 取消裸对象默认 `id`。
- projection 角色三分：明确字段 -> 字段；对象泛指 -> `vertex_full`；纯无字段输出 -> clarification。
- path/relation 结构锚点不是输出对象，不进入该判定。
- `qa_1d2d1e86f70b` 修复为 `MATCH (svc:Service) RETURN svc AS service`。

### Compiler reserved alias

- `end` 等保留词不再直接作为 projection alias。
- 规则按通用 Cypher/TuGraph reserved words 处理，不依赖业务 schema 名称。

## 7. 遗留问题清单

| 优先级 | 问题 | 样本/证据 | 当前判断 |
| --- | --- | --- | --- |
| P0 | L3 未达 95 | 完整 run L3 76/85 | projection/coverage/alias/source 尾巴和少量 GU。 |
| P0 | L4 未达 95 | 完整 run L4 75/85，twofix 投影 76/85 | 聚合 coverage、常量 projection、个别口径尾巴。 |
| P0 | failure reason enum 债 | `qa_b531b4399998` | `invalid_aggregate_property_type` 类原因需稳定映射，避免 service_failed。 |
| P1 | `node_type` 常量 projection | `qa_608c7837ad26` | 当前 DSL 表达力不足，不应靠 compiler hack。 |
| P1 | GU 候选裁决深水区 | L3/L4 少量、L5-L8 大量 | 下一阶段需要独立 GU/语义层计划。 |
| P1 | L5-L8 低准确率 | L5 61.2%，L6 23.5%，L7 20%，L8 0 | L1-L4 确定性经验不能直接外推。 |
| P2 | Static value index freshness | artifact release 后才更新 | 产品说明与运行文档持续提示。 |
| P2 | TuGraph/testing-agent 高并发状态 | token/session 执行失败 | 运维 skill 已记录，实验前清理，失败后局部重跑。 |

## 8. 后续执行原则

- L1-L4 尾巴优先局部重跑，不再每个小补丁全量 680。
- 只有当局部回收足够或需要里程碑指标时才全量 680。
- L5-L8 另开阶段，重点放在 GU 约束、语义层素材、decomposition 和产品化认输路径。
- 所有新增规则仍需封闭枚举/确定性查表/不唯一退兜底。
