# 意图识别三阶段现状

本文档记录 NL2Cypher ChatBI 场景下，当前意图识别三阶段的资产、运行链路、验证结果和向量数据库部署边界。

## 总体链路

```text
自然语言问题
  -> 第一阶段：规则匹配
  -> 第二阶段：embedding 召回
  -> 第三阶段：LLM 判定
```

统一输出：

```json
{
  "primary_intent": "record_retrieval_query",
  "secondary_intent": "related_record_query",
  "confidence": 0.835,
  "source": "rule",
  "decision": "accept"
}
```

`decision=accept` 表示当前阶段接受该分类；`fallback_embedding` 表示规则阶段放行到 embedding；`fallback_llm` 表示 embedding 阶段不够稳定，需要第三阶段判定。

## 阶段一：规则匹配

资产：

- `services/cypher_generator_agent/config/intent_rules.yaml`

职责：

- 处理关键词和句式边界非常稳定的问题。
- 高置信命中后直接返回 intent。
- 不能稳定判断时进入第二阶段 embedding。

当前状态：

- 已实现 `RuleBasedIntentRecognizer`。
- 已接入 `/api/v1/intents/recognize`。
- 规则种子已同步到当前 10 个一级意图，覆盖明细、关系路径、指标、分组、排名、对比、趋势、占比构成、集合操作和存在性等高确定性表达。
- 已加入 `RuleEligibilityGate`，对复杂路径、复杂分组、复杂排序等高歧义问题做保守放行。

当前边界：

- 规则层优先保证精度，不追求覆盖所有口语表达。
- 带多关系链、复杂条件、复杂路径约束、复杂聚合语义的问题应进入 embedding 或 LLM。

## 阶段二：embedding 召回

资产：

- `services/cypher_generator_agent/config/intent_embedding_corpus.jsonl`
- `services/cypher_generator_agent/config/intent_eval_set.jsonl`
- `tools/build_intent_embedding_index.py`
- `tools/evaluate_intent_recognition.py`
- `requirements-embedding.txt`

职责：

- 处理规则未覆盖的同义表达、口语表达、省略表达。
- 根据相似样本召回最可能的 intent。
- 候选相似度不足、top1/top2 距离过小或 top-k 共识不足时进入第三阶段 LLM。

当前状态：

- 已实现 `EmbeddingIntentRecognizer`。
- 已抽象 `TextEmbedder` 和 `EmbeddingStore` 接口。
- 已提供 `LocalTextEmbedder`、`SentenceTransformerTextEmbedder`、`InMemoryEmbeddingStore` 和 `JsonlEmbeddingStore`。
- 已支持从 seed corpus 构建本地 JSONL embedding index。
- 已支持运行时通过 `NL2CYPHER_INTENT_EMBEDDING_INDEX` 读取预构建 index。
- 已实现 `HybridIntentRecognizer`：规则不接受时进入 embedding。
- seed corpus 已同步到当前 36 个二级意图，每个二级意图至少 5 条正样本。
- 已支持 top-k 候选召回、相似度、top1/top2 margin、top-k consensus 和拒绝原因诊断。
- 已支持本地 hash 向量和真实 `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` 模型试跑。
- 已加入 embedding 后置结构 gate：先抽取轻量结构特征，再校验 top-k 候选 intent 是否符合答案形态。

构建本地 index：

```bash
python tools/build_intent_embedding_index.py \
  --embedder-provider local_hash \
  --local-embedding-dimensions 128 \
  --output /tmp/nl2cypher_intent_embedding_index_local.jsonl \
  --json
```

构建真实模型 index：

```bash
python tools/build_intent_embedding_index.py \
  --embedder-provider sentence_transformer \
  --embedding-model sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 \
  --output /tmp/nl2cypher_intent_embedding_index_sentence_transformer.jsonl \
  --json
```

服务侧可用环境变量：

```bash
export NL2CYPHER_INTENT_EMBEDDER_PROVIDER=sentence_transformer
export NL2CYPHER_INTENT_EMBEDDING_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
export NL2CYPHER_INTENT_EMBEDDING_INDEX=/tmp/nl2cypher_intent_embedding_index_sentence_transformer.jsonl
export NL2CYPHER_INTENT_ACCEPT_THRESHOLD=0.35
export NL2CYPHER_INTENT_MARGIN_THRESHOLD=0.02
export NL2CYPHER_INTENT_CONSENSUS_TOP_K=3
export NL2CYPHER_INTENT_CONSENSUS_MIN_COUNT=2
```

当前边界：

- 本地 JSONL index 适合离线实验、快速 review 和小规模服务验证。
- seed corpus 仍不能代表真实客户问题分布。
- hard negative、相邻意图对照样本和真实客户口语样本仍需要持续补齐。
- 真实 embedding 模型的阈值需要结合真实样本继续标定。

当前 gate 覆盖的主要结构约束：

- `ranking_query`：只有 `前 N/最多返回/限制` 不算排名，必须有最高、最低、最大、最小、升序、降序、从高到低、从长到短等排序信号。
- `metric_query`：数值过滤条件不能直接当成数值指标，普通指标需要统计/聚合信号。
- `breakdown_query`：必须有分组信号和指标信号，`按...降序/排序` 不算分组。
- `relationship_path_query`：必须确认路径、可达、拓扑或路径枚举是答案形态。
- `related_record_query`：需要关系信号，且明确问路径时不抢 path 类。
- `set_operation_query`、`trend_query`、`comparison_query`、`composition_query`、`existence_query`：需要对应的集合、时间、比较、比例、布尔判断信号。

## 阶段三：LLM 判定

资产：

- `services/cypher_generator_agent/config/intent_llm_fewshots.yaml`
- `services/cypher_generator_agent/config/intent_taxonomy.yaml`
- `services/cypher_generator_agent/docs/static-resource-intent-classification.md`

职责：

- 在规则和 embedding 都不能稳定判断时，基于分类标准做最终意图判定。
- 处理相邻意图边界，例如属性投影 vs 关联明细、关联明细 vs 路径查询、分组查询 vs 排名查询、占比构成 vs 派生指标排名。

当前状态：

- 已补充 LLM few-shot 推理样本和易混边界。
- 已通过测试校验 few-shot 引用的 intent 都存在于 taxonomy。
- 已接入运行时 prompt 组装和模型调用。
- LLM 兜底只做意图识别，输出受控 JSON，不生成 Cypher。
- 当 LLM 返回 `decision=accept` 时，语义流水线会带着该 intent 继续进入槽位匹配、业务槽位校验、语义层链接和 Cypher 渲染。

当前边界：

- 当 LLM 返回 `decision=clarify` 或输出不合法时，当前流水线会停止生成，并返回意图兜底未接受的诊断。
- 更细粒度的澄清问题组织方式仍需要和问题澄清模块继续对齐。

## 当前分类标准

当前标准以 `static-resource-intent-classification.md` 和 `intent_taxonomy.yaml` 为准。

| 一级意图 | 中文名 |
|---|---|
| `record_retrieval_query` | 明细/清单查询 |
| `relationship_path_query` | 关系/路径查询 |
| `metric_query` | 指标查询 |
| `breakdown_query` | 分布/分组查询 |
| `ranking_query` | 排名查询 |
| `comparison_query` | 对比查询 |
| `trend_query` | 趋势查询 |
| `composition_query` | 占比/构成查询 |
| `set_operation_query` | 集合操作查询 |
| `existence_query` | 存在性查询 |

关键原则：

- intent 描述用户最终想得到的答案形态。
- 对象、属性、关系、指标和值不放进 intent，而放进 slot、schema linking 和 metric linking。
- 路径、跳数、过滤、聚合函数、排序、limit、时间粒度等进入结构特征。
- 规则、embedding 和 LLM few-shot 使用同一套 taxonomy。

## 当前验证结果

本地 hash 向量：

```text
index build：181 条
独立 eval set：72/72，accuracy=1.0000，source_counts={'rule': 68, 'embedding': 4}
corpus self-test：181/181，accuracy=1.0000，source_counts={'rule': 158, 'embedding': 23}
qa-agent pressure test：80 条样本中 accept=56，fallback_llm=24
```

真实 sentence-transformer 向量：

```text
index build：181 条
独立 eval set：72/72，accuracy=1.0000，source_counts={'rule': 68, 'embedding': 4}
corpus self-test：181/181，accuracy=1.0000，source_counts={'rule': 158, 'embedding': 23}
qa-agent pressure test：80 条样本中 accept=55，fallback_llm=25
```

embedding-only 小扫参：

```text
accept=0.35 margin=0.02：accuracy=0.7639，accept=65，fallback_llm=7
accept=0.35 margin=0.05：accuracy=0.6944，accept=57，fallback_llm=15
accept=0.35 margin=0.08：accuracy=0.6528，accept=53，fallback_llm=19
accept=0.35 margin=0.10：accuracy=0.5833，accept=47，fallback_llm=25
```

说明：

- `intent_eval_set.jsonl` 和 corpus self-test 是分类资产的基础回归集。
- qa-agent pressure test 没有人工 intent 标注，只能观察 source、decision 和可疑样本，不能作为准确率。
- embedding 后置 gate 使 qa-agent pressure test 更保守，减少了 `前 N` 误判排名、过滤误判指标、多跳路径误判分组等硬接场景。
- 真实模型是否比本地 hash 更准确仍需要人工抽样确认。

## 当前结论

第二阶段本地闭环已经完成：

- 有 seed corpus。
- 有本地 JSONL 向量索引构建脚本。
- 有可替换的 embedder/store 接口。
- 有服务侧环境变量切换能力。
- 有离线评测、压测和诊断脚本。
- 已用真实 sentence-transformer 模型完成一轮 index 构建和检索验证。
- 已有第一版 embedding gate，可把明显结构不匹配的候选降级到 `fallback_llm`。

继续向前会进入真实向量数据库部署问题。也就是说，下一步不是继续把 JSONL 文件越做越复杂，而是决定是否部署一个可在线检索、可管理、可重建、可审计的向量索引服务。

## 向量数据库部署边界

当前可以暂时继续使用本地 JSONL index 的条件：

- 语料仍是百级或低千级。
- 主要工作是分类体系、规则边界、seed corpus 和 hard negative 的迭代。
- 评测以离线脚本为主。
- 服务验证只需要单机加载 index。

需要建立真实向量数据库的条件：

- 语料增长到数千条以上，人工文件维护和全量加载开始不方便。
- 在线服务需要稳定 top-k 检索、并发查询和可控延迟。
- 需要按场景、租户、版本、语言、模型名等 metadata filter 检索。
- 需要支持 embedding 模型灰度、索引重建、回滚和审计。
- 需要多人标注、审核和持续更新语料。

推荐分层：

```text
语料源：JSONL / 数据库 / 标注平台
向量索引层：Qdrant / Milvus / pgvector / FAISS
运行时接口：EmbeddingStore.search(question_embedding, top_k)
```

真实部署前需要确定：

- 使用哪一个向量数据库。
- collection/schema 如何设计。
- embedding 向量维度和模型版本如何记录。
- corpus 到 index 的构建流水线如何触发。
- 线上服务如何读取配置、鉴权、监控和回滚。
