# Graph Cypher Generation 文档索引

> 更新日期：2026-06-02
> 适用分支：`main`
> 状态：v1 基础 IR 已闭环，后续进入 680 回归驱动的小步收尾

## 阅读顺序

1. [IR Closeout Status](./2026-05-28-ir-closeout-status.md)：当前代码事实、验收证据、最新 680 指标和遗留问题。评审优先读这一份。
2. [CGA OSI Follow-up Modification IR](./2026-05-28-cga-osi-follow-up-modification-ir.md)：MIR-001 起的后续修改记录，已完成项已精简为摘要。
3. [Development IR](./2026-05-27-development-ir.md)：v1 实施单元的当前完成状态和仍需维护的工程边界。
4. [Overall Architecture](./2026-05-27-overall-architecture-design.md)：端到端生成链路和组件边界。
5. [Graph Semantic Model Specification v1](./2026-05-27-graph-semantic-model-spec-v1.md)：单一权威语义模型定义。
6. [Restricted Query DSL v1](./2026-05-27-restricted-query-dsl-v1-design.md)：NL 理解到 Cypher 编译之间的受限中间表示。
7. [LiteralResolver v1](./2026-05-27-literal-resolver-v1-design.md)：字面值、枚举、ID、名称和时间解析。
8. [Repair and Clarification Controller v1](./2026-05-27-repair-clarification-controller-v1-design.md)：校验失败后的 repair、clarification、unsupported 决策。
9. [Cypher Self-Validation v1](./2026-05-27-cypher-self-validation-v1-design.md)：不连接数据库的 Cypher 静态校验边界。
10. [Observability v1](./2026-05-27-observability-v1-design.md)：trace、stage、指标和排障视图。
11. [Schema Versioning Policy](./2026-05-27-schema-versioning-policy.md)：各类 schema 的演进和兼容策略。
12. [Graph-native Terminology](./2026-05-27-graph-terminology-design.md) 与 [Network Topology Vocabulary](./2026-05-27-network-topology-vocabulary.md)：术语与网络拓扑示例词汇。

## 实验和运行分析

- [Runtime Center CGA Job Analysis](./experiments/2026-05-28-runtime-center-cga-job-analysis.md)：运行中心样本分析、MIR 跟踪和回归结果。

## 当前工程事实

- CGA 已从早期 I/O stub 演进为完整的 graph-native generation pipeline。
- CGA 不连接 TuGraph，不执行 `EXPLAIN`、dry-run、probe query 或正式查询；执行与等价性评测属于 testing-agent。
- 默认语义语料为 `services/cypher_generator_agent/app/semantic_model/artifacts/tugraph_network_semantic_model.yaml`。
- 默认静态 literal 语料为 `services/cypher_generator_agent/app/semantic_model/artifacts/tugraph_value_index.json`，不是 live value-index。
- 当前本地 CGA 全量测试基线：`PYTHONPATH=. pytest services/cypher_generator_agent/tests -q` -> `708 passed`。
- 最新完整远端 680 基线：`run680_l1l4_tail_cleanup_highc_20260602T073622Z`，`passed=407`，`generated=434`，`generation_failed=230`，`clarification_required=15`，`service_failed=1`。
- 最新小样本补丁验证：`run680_l1l4_tail_twofix_20260602T075447Z`，`qa_2d0c3389a92f` 与 `qa_651a0605545c` 均通过。该结果只支持“局部回收 +2”的投影，不等同于完整 680 重跑。

## 最新 L1-L4 状态

以完整远端 run `run680_l1l4_tail_cleanup_highc_20260602T073622Z` 为准：

| 难度 | 通过 / 总数 | 准确率 | 状态 |
| --- | ---: | ---: | --- |
| L1 | 83 / 84 | 98.8% | 达标 |
| L2 | 84 / 86 | 97.7% | 达标 |
| L3 | 76 / 85 | 89.4% | 未达 95%，剩余以 projection / coverage 尾巴和少量 GU 为主 |
| L4 | 75 / 85 | 88.2% | 未达 95%，局部补丁后预计 76 / 85 = 89.4%，仍需完整复跑确认 |

## 关键边界

- 不允许 DSL unsupported case fallback 到 raw LLM Cypher。
- 不允许在结构不唯一时用默认方向、默认 ID、最高分候选等启发式猜测。
- 裸对象 projection 不再默认返回 `id`；输出对象需要按字段词、对象泛指词、澄清三分处理。
- path/relation 结构锚点不是输出对象，不进入裸对象输出口径判定。
- compiler 只编译合法 DSL，不为了接住畸形 plan 改 compiler。

## 后续阅读提示

已完成 IR/MIR 的详细流水账已从主文档中压缩为摘要。需要回查某轮实验原始数据时，优先使用 run id；不要把远端大 JSON 拉回本地。
