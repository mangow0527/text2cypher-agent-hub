# Graph Cypher Generation Development IR

> 更新日期：2026-06-02
> 状态：v1 基础开发 IR 已闭环，本文保留当前工程事实与仍需遵守的开发边界
> 适用分支：`cypher-generation-osi`

## 1. 当前目标

本文档最初用于把 Graph Semantic Model 驱动的 Cypher 生成拆成可开发、可验收、可测试的 IR。到 2026-06-02，基础 IR 已完成，当前目标从“搭建 pipeline”转为“在 680 回归集上小步提升确定性覆盖，并为 L5-L8 的语义/GU 阶段保留清晰边界”。

当前 CGA 的职责：

- 读取 TuGraph Graph Semantic Model artifact。
- 从自然语言构造候选、literal、结构需求和受限 DSL。
- 编译出 read-only TuGraph Cypher。
- 做静态 self-validation 和完整 trace 输出。
- 不连接 TuGraph，不执行 Cypher，不做 runtime repair。

非目标保持不变：

- 不定义数据库执行、结果解释、空结果分析。
- 不允许 LLM 直接生成 raw Cypher 绕过 DSL。
- 不把真实语义不唯一的问题伪装成确定性规则。
- 不用 compiler 接住上游畸形 plan。

## 2. 当前实现分层

实际代码已经形成如下主要边界：

```text
services/cypher_generator_agent/app/
  api/                         # FastAPI contract and testing-agent submission
  core/                        # pipeline orchestration, deterministic assemblers
  semantic_model/              # model loader, registry, artifacts
  decomposition/               # question decomposition and structural requirements
  retrieval/                   # candidate retrieval and reranking
  literals/                    # literal resolver and static value index
  understanding/               # grounded understanding / fallback schema
  binding/                     # binding plan hydration and validation support
  validation/                  # semantic validator and coverage gate
  dsl/                         # restricted DSL models and builder
  compiler/                    # DSL to Cypher compiler, projection helpers
  cypher_validation/           # readonly/schema/shape static validation
  repair/                      # clarification, failed, unsupported decisions
  observability/               # trace and metric payloads
```

## 3. 已完成 IR 矩阵

| IR | 状态 | 当前说明 |
| --- | --- | --- |
| IR-00 Project Contract Baseline | Done | API contract 和 no-DB-execution 边界已稳定。 |
| IR-01 Graph Model Fixture | Done | semantic artifact 和 golden fixture 已成为常规回归输入。 |
| IR-02 Graph Model Loader / Registry | Done | 支持 OSI semantic model wrapper 和 registry 查表。 |
| IR-03a/03b Cypher Self-Validation | Done | readonly、schema reference、shape、dialect 静态校验已接入。 |
| IR-04 Restricted DSL | Done | raw Cypher escape hatch 仍禁止。 |
| IR-05 Cypher Compiler | Done | 支持当前确定性 DSL 形态；reserved alias 已泛化处理。 |
| IR-06 Observability | Done | trace stage 和 final output contract 已稳定。 |
| IR-07 LiteralResolver | Done | 静态 value index、raw pass-through、数值/时间解析已接入。 |
| IR-08 Candidate Retriever | Done | 召回与 rerank 接入；激进闭包扩展不在当前基线。 |
| IR-09 Semantic Binder | Done | 输出稳定 binding plan。 |
| IR-10 Semantic Validator | Done | coverage gate 是当前主要质量防线之一。 |
| IR-11 DSL Builder | Done | 支持 projection、path、aggregate、top-N、two-step 等当前形态。 |
| IR-12 Pipeline Orchestrator | Done | deterministic 主路径和 fallback 路径均可观测。 |
| IR-13 Question Decomposer | Done | 结构槽位输出已服务确定性 assembler。 |
| IR-14 Grounded LLM Understanding | Done, with backlog | schema guard 已收紧并还债一部分；深水区仍在。 |
| IR-15 Repair / Clarification | Done | clarification/failed/unsupported 路径稳定。 |
| IR-16 Trace and Testing-Agent Contract | Done | generated 与非成功输出均带 trace。 |
| IR-16.5 Performance Baseline | Done | 本地 baseline writer 存在，CI artifact 不是当前重点。 |
| IR-17 Variable Path Traversal | Done | 多跳路径确定性能力已接入，但 L3 仍有 coverage/projection 尾巴。 |
| IR-18 Metric / Ad Hoc Aggregate | Done | L4 聚合经过多轮深化，仍有少量尾巴。 |
| IR-19 Top-N and Two-Step Aggregate | Done | 当前 DSL/assembler 支持主流 shape。 |
| IR-20 Golden Regression Matrix | Done | 本地 CGA 全量测试当前为 `708 passed`。 |

## 4. 当前测试入口

本地全量：

```bash
PYTHONPATH=. pytest services/cypher_generator_agent/tests -q
```

当前已知结果：

```text
708 passed
```

常用局部入口：

- `services/cypher_generator_agent/tests/integration/test_pipeline_mvp.py`
- `services/cypher_generator_agent/tests/compiler/`
- `services/cypher_generator_agent/tests/validation/`
- zero-hop / multihop / aggregate 相关 targeted tests

运行注意：

- 本地测试经常需要显式设置 `PYTHONPATH=.`。
- 不要把远端 680 大 JSON 拉回本地；需要聚合时在远端运行脚本输出摘要。
- 远端 680 频繁重跑前清理运行中心；若只验证少量修复，优先局部重发样本。

## 5. 当前确定性能力

已落地：

- F1-F6 主要形态确定性 assembler。
- projection 完整性，包括 multi-owner / dual-owner / property 粒度 coverage。
- gate 泛指词归一，封闭枚举，不开放匹配。
- aggregate 结构覆盖、property count vs vertex count、多 measure、非空过滤、比较 operator。
- L3 coverage/projection 深化，包括 alias/source identity 的部分修复。
- 裸对象 projection 口径：明确字段 -> 字段；对象泛指 -> vertex_full；纯无字段输出 -> clarification；不默认 id。
- path/relation 结构锚点不进入裸对象输出口径判定。
- compiler reserved alias 泛化，不再与业务 schema 强绑定。

已验证为不适合落地或需要后续阶段：

- B1 确定性方向 override：净负，已回退。方向不是 L3/L4 主瓶颈。
- 激进候选闭包扩展：能提高候选命中，但对 passed 净负，不在当前干净基线。
- GU 深水区裁决：不适合用确定性规则硬猜。

## 6. 最新远端状态

完整 run `run680_l1l4_tail_cleanup_highc_20260602T073622Z`：

| 指标 | 数值 |
| --- | ---: |
| passed | 407 |
| generated | 434 |
| generated testing fail | 27 |
| generation_failed | 230 |
| clarification_required | 15 |
| service_failed | 1 |

分级：

| 难度 | 准确率 |
| --- | ---: |
| L1 | 98.8% |
| L2 | 97.7% |
| L3 | 89.4% |
| L4 | 88.2% |
| L5 | 61.2% |
| L6 | 23.5% |
| L7 | 20.0% |
| L8 | 0.0% |

局部 twofix run 已确认 `qa_2d0c3389a92f` 和 `qa_651a0605545c` 通过。完整 680 未在 twofix 后重跑，因此文档中的 L4 `~89.4%` 只能作为投影值。

## 7. 遗留问题

| 类别 | 问题 | 当前处理原则 |
| --- | --- | --- |
| L3 tail | coverage/projection/binding 残留，如端点 anchor、source identity。 | 只修确定性清楚的；牵连 GU 则标记。 |
| L4 tail | 聚合 coverage、常量 projection、少量口径残留。 | 不回退第二刀 count 口径；DSL 缺表达力时另开。 |
| failure reason | `qa_b531b4399998` enum 债。 | 补稳定 failure reason，不让 service_failed 干扰诊断。 |
| GU / semantic | L5-L8 主体低，候选裁决与语义层缺口明显。 | 单独阶段处理，不混入 L1-L4 收尾。 |
| value index | 静态索引非实时。 | 用户文档中保持透明。 |
| testing infra | TuGraph/session/token 高并发失败需要清理后重跑 testing-agent。 | 操作 skill 已记录，实验前后按流程执行。 |

## 8. 开发原则

1. 确定性规则必须封闭、可审查、可测试。
2. 不唯一就退兜底、澄清或失败，不猜测。
3. coverage gate 不放过真缺失，也不把泛指词误当结构要求。
4. builder/compiler 只处理合法 DSL，不替上游修语义。
5. 修复必须配 regression；已完成刀的回归测试不得回退。
6. 远端验证优先局部重跑，只有需要总体指标时才全量 680。
