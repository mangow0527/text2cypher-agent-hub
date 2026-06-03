# Cypher Generator Agent IR Closeout Status

> 更新日期：2026-06-02
> Branch: `cypher-generation-osi`
> Scope: `services/cypher_generator_agent`

## Summary

v1 基础 IR 已功能闭环。CGA 现在使用 packaged TuGraph Graph Semantic Model YAML 和静态 value index，完成候选召回、确定性结构拼装、受限 DSL 构造、语义校验、Cypher 编译、自校验和 trace 输出。CGA 仍严格保持 generation-only 边界，不连接 TuGraph，不执行查询。

2026-06-01 到 2026-06-02 的工作重心已经从基础 IR 转为 680 回归集上的小步 MIR：projection 覆盖、结构覆盖、GU schema guard、L4 聚合、L3 coverage/projection、裸对象 projection 口径与 reserved alias 等均已落地或局部验证。当前 L1/L2 已稳定达标，L3/L4 仍未到 95%。

## Current Completion Matrix

| 范围 | 状态 | 当前口径 |
| --- | --- | --- |
| IR-00 到 IR-20 v1 基础实现 | Done | 作为基础工程能力闭环，不再逐条展开实施细节。 |
| Graph Semantic Model loader / registry | Done | 读取 OSI 风格 semantic artifact，提供 vertex / edge / property / metric / path 查表能力。 |
| LiteralResolver | Done | 使用静态 value index；支持枚举、ID、名称、数字、时间与 raw pass-through。 |
| Candidate retrieval / reranker | Done, with backlog | 支持 semantic candidates 和结构相关性 rerank；候选闭包扩展的激进版本已证明净负，不作为当前基线。 |
| Deterministic assembler | Active but mature | 支持 F1-F6、0-hop、多跳、projection、aggregate、group/order/limit、L3/L4 多轮确定性补丁。 |
| Semantic validator / coverage gate | Active but mature | coverage 词义归一、property 粒度覆盖、结构槽位覆盖已落地，仍有 L3/L4 尾巴。 |
| DSL builder / compiler | Done, guarded | compiler 不接畸形 plan；reserved alias 已泛化处理，不与业务 schema 强绑定。 |
| Grounded Understanding fallback | Partly done | schema guard 债已还一部分；深水区 GU 绑定和候选裁决仍是后续主战场。 |
| Trace / testing-agent contract | Done | generated 与非成功结果均带 trace，远端评测通过 qa-agent/testing-agent 完成。 |

## Latest Local Evidence

```bash
PYTHONPATH=. pytest services/cypher_generator_agent/tests -q
```

最新已知本地结果：

```text
708 passed
```

重点局部回归曾覆盖：

- zero-hop / naked object projection
- multihop projection and coverage
- validation / compiler reserved alias
- pipeline MVP
- full CGA suite

## Latest Remote Evidence

完整 680 基线：

| 指标 | `run680_l1l4_tail_cleanup_highc_20260602T073622Z` |
| --- | ---: |
| total | 680 |
| passed | 407 |
| generated | 434 |
| generated testing fail | 27 |
| generation_failed | 230 |
| clarification_required | 15 |
| service_failed | 1 |

分级结果：

| 难度 | 通过 / 总数 | 准确率 |
| --- | ---: | ---: |
| L1 | 83 / 84 | 98.8% |
| L2 | 84 / 86 | 97.7% |
| L3 | 76 / 85 | 89.4% |
| L4 | 75 / 85 | 88.2% |
| L5 | 52 / 85 | 61.2% |
| L6 | 20 / 85 | 23.5% |
| L7 | 17 / 85 | 20.0% |
| L8 | 0 / 85 | 0.0% |

局部补丁验证：

| Run | 样本 | 结果 |
| --- | --- | --- |
| `run680_l1l4_tail_twofix_20260602T075447Z` | `qa_2d0c3389a92f` | passed |
| `run680_l1l4_tail_twofix_20260602T075447Z` | `qa_651a0605545c` | passed |

说明：两条局部通过可把完整 run 的 L4 结果从 `75/85` 投影到约 `76/85 = 89.4%`，但这不是完整 680 实测。

## Major Closed MIR Results

| 阶段 | 远端结果 | 结论 |
| --- | --- | --- |
| MIR-013 projection coverage | passed 271 -> 318 | projection 完整性是第一段大杠杆。 |
| MIR-014 structural coverage | passed 318 -> 330 | aggregate/group/order/limit/path hop 结构槽位有正收益，但边际下降。 |
| Stage 2 GU schema/boundary | passed 330 -> 342 | schema guard 有收益也制造过保守债，后续已还一部分。 |
| Gate generic terms | passed 342 -> 358 | L1 达标，L4 大幅抬升。 |
| L4 property count | passed 358 -> 370 | `count(property)` vs `count(vertex)` 口径收口。 |
| Schema guard debt | passed 370 -> 376 | schema_invalid 明显下降，L2 接近达标。 |
| Dual-owner projection | passed 376 -> 387 | L1/L2 达标，L3/L4 抬升。 |
| Direction B1 | passed 387 -> 381 | 净负，已回退；方向不是 L3/L4 主瓶颈。 |
| L4 aggregate deepening | passed 387 -> 396 | L4 75.3% -> 87.1%，testing fail 下降。 |
| L3 coverage/projection | passed 396 -> 404 | L3 继续抬升，L1/L2 保持达标。 |
| Naked object / reserved alias scope | passed 404 | 修复 `qa_1d2d1e86f70b` 和 reserved alias 口子，保持整体基线。 |

## Open Issues

| 问题 | 当前判断 | 下一步 |
| --- | --- | --- |
| L3 未达 95% | 仍有 projection / coverage / alias/source 尾巴与少量 GU 深水区。 | 只对确定性样本做局部重跑，不再频繁全量 680。 |
| L4 未达 95% | 聚合 coverage 和口径尾巴仍在，局部 twofix 已回收 2 条。 | 对剩余 L4 tail 做小样本验证后再决定是否全量。 |
| `qa_b531b4399998` service_failed | failure reason enum 债，底层原因未被稳定枚举接住。 | 补 enum 归一，避免诊断误报。 |
| `qa_608c7837ad26` 类 node_type 常量投影 | DSL 当前缺少 literal/constant projection 表达。 | 需明确 DSL 表达力是否扩展；不应让 compiler 接畸形输入。 |
| GU 深水区 | 候选多义、语义裁决、L5-L8 主体。 | 单独开 L5-L8 / GU / 语义层阶段，不混入 L1-L4 确定性尾巴。 |
| Static value index freshness | 新实体需等下一次 artifact release。 | 产品文档和用户引导保持透明。 |
| TuGraph/testing-agent 会话状态 | 高并发下可能出现 token/session 类执行失败。 | 运行前清理状态；service_failed 若是网络/会话原因，重跑 testing-agent 流程。 |

## Explicit Boundaries

- CGA 不连接 TuGraph，不执行查询。
- DSL 不支持时不 fallback 到 raw LLM Cypher。
- LLM 只做 bounded decomposition / fallback；字段绑定、路径、聚合、coverage 和 compiler 均有工程防线。
- 不再保留“裸对象 projection 默认 id”的行为。无字段依据时返回 `vertex_full` 或进入 clarification，而不是猜 id。
- path/relation 结构锚点不属于输出对象，不进入裸对象输出口径判定。
