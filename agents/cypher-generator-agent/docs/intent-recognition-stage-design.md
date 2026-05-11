# 意图识别模块设计

本文档说明 cypher-generator-agent 中意图识别模块的设计边界、三阶段处理流程、输出契约和运行时落盘要求。

意图分类体系以 [intent-classification.md](./intent-classification.md) 为准。该文件描述“有哪些意图”；本文档描述“如何识别这些意图”。

## 1. 模块定位

意图识别只回答一个问题：用户这个自然语言问题最终想得到什么形态的答案。

它识别的是查询结构层面的答案形态，例如明细、路径、指标、分组、排名、对比、趋势、占比、集合操作、存在性判断。它不负责识别具体业务实体、属性、关系、路径、指标和值。

后续链路应这样使用意图识别结果：

```text
自然语言问题
  -> 意图识别：判断答案形态
  -> 语义视图匹配：识别业务对象、属性、关系、路径、指标、条件和值
  -> planner：合并 intent、语义视图匹配结果和结构特征，生成 LogicalQueryPlan
  -> schema/path planning
  -> deterministic Cypher renderer
  -> semantic + schema + execution preflight
```

因此，intent 是 planner 的结构信号，不是业务语义的唯一来源。若 intent 与语义视图匹配结果冲突，planner 和 preflight 应给出诊断，必要时进入澄清反问。

## 2. 总体流程

意图识别采用三阶段级联。越靠前的阶段越强调高精度和低成本，越靠后的阶段越强调复杂边界判定能力。

```text
自然语言问题
  -> 阶段一：规则匹配
       accept          -> 输出 IntentRecognitionResult
       fallback        -> 阶段二
  -> 阶段二：embedding 召回
       accept          -> 输出 IntentRecognitionResult
       fallback        -> 阶段三
  -> 阶段三：受控 LLM 渐进式分层判定
       一级候选判定 accept             -> 二级候选判定
       一级候选判定 need_full_taxonomy -> 一级全量兜底
       一级 clarify                    -> 输出澄清诊断，不继续生成 Cypher
       二级候选判定 accept             -> 输出 IntentRecognitionResult
       二级候选判定 need_full_taxonomy -> 二级全量兜底
       二级 clarify                    -> 输出澄清诊断，不继续生成 Cypher
```

三个阶段使用同一套意图分类标准：

- `intent-classification.md`：面向人阅读的分类设计文档。
- `resources/intent/taxonomy.yaml`：运行时使用的意图枚举、中文名、说明和结构特征定义。
- `resources/intent/rules.yaml`：第一阶段规则资产。
- 远端 RAG intent collection：第二阶段运行时主召回源，保存已向量化的意图匹配语料。
- `resources/intent/embedding_corpus.jsonl`：第二阶段语料源文件，用于版本管理、离线评测、重建远端 RAG 索引和本地兜底。
- `resources/intent/llm_fewshots.yaml`：第三阶段 LLM few-shot、易混边界和输出约束。

这些资产必须保持同一个 taxonomy version。修改分类体系时，应先改 `intent-classification.md`，再同步机器可读资产。

## 3. 统一输出

基础输出：

```jsonc
{
  // 一级意图，表示用户最终答案形态。
  "primary_intent": "ranking_query",

  // 二级意图，表示更细的查询结构类别。
  "secondary_intent": "metric_ranking_query",

  // 当前阶段对判定结果的置信度。
  "confidence": 0.86,

  // 最终接受该结果的阶段。
  "source": "embedding",

  // accept 表示已接受；fallback_embedding / fallback_llm 表示需要进入下一阶段；
  // clarify 表示无法安全判定，需要澄清反问。
  "decision": "accept"
}
```

建议落盘诊断信息：

```jsonc
{
  // 规则阶段命中的规则 ID。没有命中时为空。
  "rule_hit": "ranking_topn_metric",

  // embedding 阶段召回的候选意图，供运行中心展示和排查。
  "embedding_candidates": [
    {
      "primary_intent": "ranking_query",
      "secondary_intent": "metric_ranking_query",
      "score": 0.82
    }
  ],

  // embedding 召回使用的数据源。当前运行态以远端 RAG intent collection 为主。
  "embedding_store": "rag_vector",
  "embedding_collection": "nl2cypher_intent_examples_v1",

  // LLM 阶段的一级意图判定调用记录。候选优先和全量兜底都记录在这里。
  "llm_primary_attempts": [
    {
      "attempt_type": "candidate_first",
      "prompt": "...",
      "response": "...",
      "decision": "need_full_taxonomy"
    },
    {
      "attempt_type": "full_taxonomy_fallback",
      "prompt": "...",
      "response": "...",
      "decision": "accept"
    }
  ],

  // LLM 阶段的二级意图判定调用记录。只有一级意图接受并进入二级判定时才有值。
  "llm_secondary_attempts": [],

  // 简短中文理由，用于解释为什么接受或为什么需要澄清。
  "reason": "问题要求按数量取前 N，最终答案形态是排名表。"
}
```

结构特征可以作为可选 hint 输出给 planner，例如是否需要路径、是否有聚合、是否有排序截断、是否按时间展开。结构特征不替代语义视图匹配结果。

## 4. 阶段一：规则匹配

阶段一处理表达非常稳定、边界清楚的问题。它的目标不是覆盖所有问题，而是在低成本下直接接受高确定性意图。

输入：

```jsonc
{
  // 原始用户问题。
  "question": "查询端口利用率最高的前 10 个端口",

  // 文本归一化后的问题，用于规则匹配。
  "normalized_question": "查询端口利用率最高的前 10 个端口",

  // 来自 taxonomy.yaml 的意图枚举和优先级。
  "taxonomy_version": 3
}
```

主要动作：

- 做轻量文本归一化，例如统一全角半角、大小写、常见标点和空白。
- 按 `rules.yaml` 匹配稳定关键词、句式和优先级。
- 对明显高置信场景直接输出 intent，例如“是否存在”“前 N”“按 X 统计”“占比”“趋势”等。
- 对容易误伤的场景做资格门控，例如只有“前 5 条记录”但没有排序语义时，不能直接归为排名查询。
- 如果没有高置信命中，或者命中结果与资格门控冲突，则进入第二阶段。

接受条件：

- 规则命中明确的一级和二级意图。
- 命中表达与 `intent-classification.md` 中的分类边界一致。
- 没有出现会改变最终答案形态的强冲突信号。

输出：

```jsonc
{
  "primary_intent": "ranking_query",
  "secondary_intent": "derived_metric_ranking_query",
  "confidence": 0.95,
  "source": "rule",
  "decision": "accept",
  "reason": "命中“最高的前 10 个”排名表达。"
}
```

若不接受：

```jsonc
{
  "primary_intent": null,
  "secondary_intent": null,
  "confidence": 0.0,
  "source": "rule",
  "decision": "fallback_embedding",
  "reason": "规则未命中高置信意图。"
}
```

## 5. 阶段二：embedding 召回

阶段二处理规则难以覆盖的同义表达、口语表达和省略表达。它通过相似样本召回候选意图，但仍然只识别答案形态。

当前运行态以远端 RAG intent collection 为主召回源。CGA 在阶段二不应把本地 JSONL 文件当作主检索库，而是调用 RAG 服务完成 top-k 召回；本地 `embedding_corpus.jsonl` 主要用于维护语料、离线评测、重建远端索引，以及在远端 RAG 不可用时作为临时 fallback。

输入：

```jsonc
{
  "question": "哪些端口利用率排在最前面",
  "normalized_question": "哪些端口利用率排在最前面",
  "taxonomy_version": 3,
  "embedding_store": "rag_vector",
  "rag_collection": "nl2cypher_intent_examples_v1",
  "top_k": 5
}
```

主要动作：

- 使用 embedding 模型将问题向量化。
- 调用远端 RAG 服务，在 intent collection 中召回 top-k 相似样本。
- 将相似样本映射为候选 intent。
- 计算 top1 分数、top1/top2 margin、top-k 共识度。
- 结合轻量结构特征做后置 gate，例如“是否真的有排名语义”“是否真的有分组语义”“是否真的要求布尔回答”。
- 如果候选稳定，则接受；如果候选分数不足、边界接近或结构 gate 不通过，则进入第三阶段。
- 如果远端 RAG 不可用，可以使用本地 JSONL index 临时兜底；兜底结果必须在诊断信息中标明，避免和主链路召回结果混淆。

候选输出示例：

```jsonc
{
  "retrieval_source": "rag_vector",
  "rag_collection": "nl2cypher_intent_examples_v1",
  "top_candidates": [
    {
      "primary_intent": "ranking_query",
      "secondary_intent": "derived_metric_ranking_query",
      "score": 0.81,
      "matched_example": "查询端口利用率最高的前 10 个端口"
    },
    {
      "primary_intent": "record_retrieval_query",
      "secondary_intent": "entity_list_query",
      "score": 0.63,
      "matched_example": "查询所有端口"
    }
  ],
  "margin": 0.18,
  "consensus": {
    "top_k": 5,
    "winner_count": 3
  }
}
```

接受条件：

- top1 相似度达到阈值。
- top1 与 top2 的距离足够大。
- top-k 候选对一级或二级意图有足够共识。
- 结构 gate 没有发现与候选 intent 明显冲突的信号。

输出：

```jsonc
{
  "primary_intent": "ranking_query",
  "secondary_intent": "derived_metric_ranking_query",
  "confidence": 0.81,
  "source": "embedding",
  "decision": "accept",
  "reason": "top-k 候选集中在排名查询，且问题含有“排在最前面”的排序语义。"
}
```

若不接受：

```jsonc
{
  "primary_intent": "ranking_query",
  "secondary_intent": "derived_metric_ranking_query",
  "confidence": 0.54,
  "source": "embedding",
  "decision": "fallback_llm",
  "reason": "top1 分数不足，且 ranking_query 与 record_retrieval_query 边界接近。"
}
```

## 6. 阶段三：受控 LLM 渐进式分层判定

阶段三只在前两阶段无法稳定判断时触发。它使用大模型处理复杂表达和相邻边界，但仍然只做意图识别，不能生成 Cypher，不能替代语义视图匹配。

阶段三不是一上来就把完整分类体系塞给模型，而是采用“前置候选依据优先，全量分类兜底”的方式：

- 一级候选判定：先把规则和 embedding 产生的候选整理成中文候选卡片，让 LLM 在这些候选里判断一级意图。
- 一级全量兜底：只有候选卡片不足以判断，且问题本身不是澄清场景时，才提供全部一级意图和判定优先级。
- 二级候选判定：一级意图确定后，先把该一级下由规则和 embedding 支持的二级候选整理成候选卡片。
- 二级全量兜底：只有二级候选不足以判断时，才提供该一级下的完整二级分类。

这样做的目标是先充分利用前两阶段已经召回的证据，减少 prompt 体积和模型选择空间；同时保留全量分类兜底，避免前两阶段召回偏差把 LLM 锁死在错误候选里。

### 6.1 前置候选依据

进入 LLM 前，不能把 `rule_diagnostics`、`embedding_topk` 这类工程字段原样塞进提示词。服务应先把它们整理成模型可读的候选依据。

候选依据示例：

```jsonc
{
  // 原始用户问题。
  "question": "查询服务 A 到端口 P 经过哪些资源",

  // 候选依据列表，来自规则阶段的弱命中、embedding top-k、相似样本和边界诊断。
  "candidate_evidence": [
    {
      // 候选编号，只用于提示词引用。
      "candidate_id": "c1",

      // 候选一级意图。
      "primary_intent": "relationship_path_query",

      // 候选二级意图，可以为空；一级候选判定时主要看 primary_intent。
      "secondary_intent": "path_trace_query",

      // 面向模型的中文名称。
      "candidate_name": "关系/路径查询 - 路径明细查询",

      // 这类意图的简短含义。
      "definition": "返回路径、经过顺序、节点链路明细或路径对象。",

      // 为什么前两阶段召回了它。这里应写成人能读懂的中文，不放裸分数字段。
      "supporting_signals": [
        "问题包含“到端口”和“经过哪些资源”，可能在询问路径经过顺序。",
        "相似样本：查询服务到端口的完整路径。"
      ],

      // 召回置信信息，用于辅助判断，不要求模型做数学计算。
      "retrieval_summary": "embedding 分数 0.59，与 top1 差距很小。",

      // 已知风险或易混点。
      "conflict_risk": "也可能只是想查询相关资源明细，需要和关联明细查询区分。"
    }
  ],

  // 候选之间的主要冲突。
  "confusable_boundaries": [
    "关联明细查询返回相关对象字段；路径明细查询返回路径、顺序或链路结构。"
  ]
}
```

候选字段使用边界：

| 字段 | 是否进入 LLM | 用途 |
|---|---|---|
| `candidate_id` | 是 | 让 LLM 在理由中引用候选，便于运行中心追踪。 |
| `primary_intent` | 是 | 一级候选判定的可选范围。 |
| `secondary_intent` | 是 | 二级候选判定的可选范围；一级候选判定中仅作为辅助展示，可为空。 |
| `candidate_name` | 是 | 给 LLM 看的中文候选名，避免只看到英文枚举。 |
| `definition` | 是 | 解释这个候选意图的答案形态。 |
| `supporting_signals` | 是 | 前两阶段为什么召回该候选，应压缩成人可读中文，每个候选最多 2 条。 |
| `conflict_risk` | 是 | 当前候选最容易混淆的对象，帮助 LLM 判断是否需要兜底或澄清。 |
| `confusable_boundaries` | 是 | 候选之间的共性边界，最多 2 条，每条一句话。 |
| `retrieval_summary` | 默认不进 LLM | 只落盘给运行中心，用于解释召回分数、margin、top-k 稳定性；只有当分数差距本身是决策关键时，才转写成一句中文 `supporting_signals`。 |
| `raw_embedding_score` | 否 | 只落盘，不直接给 LLM。 |
| `raw_margin` | 否 | 只落盘，不直接给 LLM。 |
| `raw_rule_id` | 否 | 只落盘，不直接给 LLM。 |
| `raw_matched_keywords` | 否 | 只落盘；如果关键词对判断有帮助，应转写成 `supporting_signals`。 |
| `raw_retrieved_examples` | 否 | 只落盘；进入 LLM 时每个候选最多保留 1 条最相似样本，并转写成 `supporting_signals`。 |

喂给 LLM 的候选卡片应控制为紧凑中文格式，而不是完整 JSON。推荐格式：

```markdown
## 候选 c1：关系/路径查询 - 路径明细查询

- intent: `relationship_path_query.path_trace_query`
- 含义: 返回路径、经过顺序、节点链路明细。
- 依据: 命中“到端口”“经过哪些资源”；相似样本“查询服务到端口的完整路径”。
- 易混: 可能与“关联明细查询”混淆，后者只返回相关对象字段。
```

候选卡片约束：

- 一级候选最多 3 个，极限 4 个。
- 二级候选最多 3 个。
- 每个候选卡片最多 4 行。
- 每个候选最多 1 条相似样本。
- 不在候选卡片中展示原始分数、margin、规则 ID、完整 top-k JSON 或完整召回样本列表。

### 6.2 一级候选判定

一级候选判定先让 LLM 使用前置候选依据。如果候选里已经有足够证据，应直接选择候选一级意图；如果候选不足但问题表达完整，应返回 `need_full_taxonomy`，由服务发起一级全量兜底；如果问题本身缺少动作或答案形态，应返回 `clarify`。

提示词模板：

```markdown
# 任务

你是 cypher-generator-agent 的意图识别模块。当前只做一级意图候选判定。

你只需要判断用户问题最终想得到什么形态的答案。

你不能做以下事情：
- 不要识别业务实体、字段、关系、路径、指标和值。
- 不要生成 Cypher。
- 不要补充用户没有表达的业务条件。
- 不要判断二级意图。

# 用户问题

{{question}}

# 前置候选依据

前两阶段没有直接接受结果，但已经召回了以下候选。请先认真利用这些候选依据判断。

{{candidate_evidence_cards}}

# 易混边界

{{confusable_boundaries}}

# 判断规则

1. 如果候选依据已经足以判断一级意图，输出 `decision=accept`，并选择候选中的一级意图。
2. 如果候选依据不足以判断，但用户问题本身有明确动作和答案形态，输出 `decision=need_full_taxonomy`。
3. 如果用户问题本身缺少动作、目标或答案形态，输出 `decision=clarify`，并给出中文澄清问题。

# 输出要求

只输出一个 JSON 对象，不要输出 Markdown，不要解释 JSON 之外的内容。

JSON 字段：
- `primary_intent`: string 或 null
- `confidence`: 0 到 1 的数字
- `source`: 固定为 `llm`
- `decision`: 只能是 `accept`、`need_full_taxonomy` 或 `clarify`
- `reason`: 中文理由
- `clarification_question`: 仅当 `decision=clarify` 时填写中文澄清问题

不要在 `accept` 时输出候选依据之外的一级意图。
```

### 6.3 一级全量兜底

一级全量兜底只在一级候选判定返回 `need_full_taxonomy` 时触发。它提供完整一级分类和判定优先级，但仍然只做一级意图判定。

提示词模板：

```markdown
# 任务

你是 cypher-generator-agent 的意图识别模块。当前只做一级意图全量兜底判定。

候选判定阶段认为前置候选依据不足，因此现在提供完整一级分类供你选择。

你不能做以下事情：
- 不要识别业务实体、字段、关系、路径、指标和值。
- 不要生成 Cypher。
- 不要补充用户没有表达的业务条件。
- 不要判断二级意图。

# 用户问题

{{question}}

# 候选阶段诊断

{{candidate_stage_summary}}

# 一级意图分类

你只能从以下一级意图中选择一个。

- `record_retrieval_query`：明细/清单查询。返回实体、资源、记录或属性明细，不以统计值为最终答案。
- `relationship_path_query`：关系/路径查询。返回关系、路径、可达结果或拓扑结构，图结构本身是答案的一部分。
- `metric_query`：指标查询。返回一个或少量聚合指标，如数量、均值、最大值、去重数。
- `breakdown_query`：分布/分组查询。按一个或多个维度分组，返回维度到指标的表格。
- `ranking_query`：排名查询。按属性或指标排序，返回最高、最低、最多、最少、前 N、后 N。
- `comparison_query`：对比查询。明确比较两个或多个对象、分组、集合、指标或时间段。
- `trend_query`：趋势查询。按时间维度返回变化趋势、时间序列或周期变化。
- `composition_query`：占比/构成查询。返回比例、占比、构成、覆盖率、利用率等派生指标。
- `set_operation_query`：集合操作查询。返回差集、交集、并集或基于集合成员关系过滤的结果。
- `existence_query`：存在性查询。判断实体、关系、路径或条件是否存在，返回布尔语义。

# 判定优先级

1. 是否存在、有没有、是否可达、是否满足：优先 `existence_query`。
2. 共同、只属于、未使用、差异、并集：优先 `set_operation_query`。
3. 趋势、按时间粒度、同比、环比、变化率：优先 `trend_query`。
4. 最高、最低、最多、最少、前 N、后 N、排名：优先 `ranking_query`。
5. 明确比较两个或多个对象、指标、分组或时间段：优先 `comparison_query`。
6. 占比、比例、构成、覆盖率、利用率：优先 `composition_query`。
7. 各、每个、按 X、分布、分组统计：优先 `breakdown_query`。
8. 单个或少量聚合指标：优先 `metric_query`。
9. 路径、关系、可达资源或拓扑结构本身是答案：优先 `relationship_path_query`。
10. 返回实体、资源、记录或属性明细：选择 `record_retrieval_query`。

# 输出要求

只输出一个 JSON 对象，不要输出 Markdown，不要解释 JSON 之外的内容。

JSON 字段：
- `primary_intent`: string 或 null
- `confidence`: 0 到 1 的数字
- `source`: 固定为 `llm`
- `decision`: 只能是 `accept` 或 `clarify`
- `reason`: 中文理由
- `clarification_question`: 仅当 `decision=clarify` 时填写中文澄清问题

如果完整一级分类下仍无法安全判断，输出 `decision=clarify`。
不要输出上述一级意图列表之外的值。
```

### 6.4 二级候选判定

二级候选判定只在一级意图已接受后触发。它先使用当前一级意图下的前置候选依据，而不是直接展示该一级下的完整二级分类。

提示词模板：

```markdown
# 任务

你是 cypher-generator-agent 的意图识别模块。当前只做二级意图候选判定。

一级意图已经确定为：`{{primary_intent}}`，中文名：{{primary_intent_name}}。

你只需要在这个一级意图下面判断最合适的二级意图。

你不能做以下事情：
- 不要改变一级意图。
- 不要选择其他一级意图下面的二级意图。
- 不要识别业务实体、字段、关系、路径、指标和值。
- 不要生成 Cypher。

# 用户问题

{{question}}

# 前置候选依据

前两阶段在当前一级意图下召回了以下二级候选。请先认真利用这些候选依据判断。

{{candidate_evidence_cards}}

# 当前一级内部的易混边界

{{secondary_confusable_boundaries}}

# 判断规则

1. 如果候选依据已经足以判断二级意图，输出 `decision=accept`，并选择候选中的二级意图。
2. 如果候选依据不足以判断，但用户问题在当前一级意图下仍有明确答案形态，输出 `decision=need_full_taxonomy`。
3. 如果当前一级意图明确，但用户表达无法区分二级答案形态，输出 `decision=clarify`，并给出中文澄清问题。

# 输出要求

只输出一个 JSON 对象，不要输出 Markdown，不要解释 JSON 之外的内容。

JSON 字段：
- `primary_intent`: 必须等于 `{{primary_intent}}`
- `secondary_intent`: string 或 null
- `confidence`: 0 到 1 的数字
- `source`: 固定为 `llm`
- `decision`: 只能是 `accept`、`need_full_taxonomy` 或 `clarify`
- `reason`: 中文理由
- `clarification_question`: 仅当 `decision=clarify` 时填写中文澄清问题

不要在 `accept` 时输出候选依据之外的二级意图。
```

### 6.5 二级全量兜底

二级全量兜底只在二级候选判定返回 `need_full_taxonomy` 时触发。它只展示已接受一级意图下面的完整二级分类，不展示其他一级意图。

提示词模板：

```markdown
# 任务

你是 cypher-generator-agent 的意图识别模块。当前只做二级意图全量兜底判定。

一级意图已经确定为：`{{primary_intent}}`，中文名：{{primary_intent_name}}。

候选判定阶段认为前置二级候选依据不足，因此现在提供该一级下的完整二级分类供你选择。

你不能做以下事情：
- 不要改变一级意图。
- 不要选择其他一级意图下面的二级意图。
- 不要识别业务实体、字段、关系、路径、指标和值。
- 不要生成 Cypher。

# 用户问题

{{question}}

# 候选阶段诊断

{{candidate_stage_summary}}

# 当前一级意图下的完整二级分类

运行时只替换为 `{{primary_intent}}` 下的完整二级意图列表。示例：

## 当一级意图是 `relationship_path_query` 时

- `relationship_detail_query`：关系详情查询。返回边、关系对象或关系属性。
- `path_trace_query`：路径明细查询。返回确定路径、路径顺序、节点链路明细或路径对象。
- `reachable_entity_query`：可达实体查询。返回从某实体出发可到达的实体集合。
- `path_enumeration_query`：路径枚举查询。返回两个实体之间的所有路径或多条候选路径。
- `topology_subgraph_query`：拓扑子图查询。返回局部拓扑子图，通常包含节点和关系。

# 当前一级内部的易混边界

{{secondary_confusable_boundaries}}

# 输出要求

只输出一个 JSON 对象，不要输出 Markdown，不要解释 JSON 之外的内容。

JSON 字段：
- `primary_intent`: 必须等于 `{{primary_intent}}`
- `secondary_intent`: string 或 null
- `confidence`: 0 到 1 的数字
- `source`: 固定为 `llm`
- `decision`: 只能是 `accept` 或 `clarify`
- `reason`: 中文理由
- `clarification_question`: 仅当 `decision=clarify` 时填写中文澄清问题

如果完整二级分类下仍无法安全判断，输出 `decision=clarify`。
不要输出当前一级意图之外的二级意图。
```

### 6.6 输出与边界

接受输出示例：

```jsonc
{
  "primary_intent": "relationship_path_query",
  "secondary_intent": "path_trace_query",
  "confidence": 0.8,
  "source": "llm",
  "decision": "accept",
  "reason": "用户问“经过哪些资源”，需要返回路径经过顺序或路径明细。"
}
```

澄清输出示例：

```jsonc
{
  "primary_intent": "relationship_path_query",
  "secondary_intent": null,
  "confidence": 0.0,
  "source": "llm",
  "decision": "clarify",
  "reason": "问题可以理解为查询连接关系，也可以理解为查询完整路径。",
  "clarification_question": "你是想查看两者之间的连接关系详情，还是查看完整路径和经过顺序？"
}
```

阶段三的关键边界：

- LLM 不能直接生成 Cypher。
- LLM 不能替代语义视图匹配。
- LLM 不能凭空补业务实体、字段、关系或路径。
- `need_full_taxonomy` 不是澄清，也不是失败，只表示当前候选依据不足，需要服务重组更完整的分类提示词。
- 一级候选判定 `accept` 时只能输出候选依据中的一级意图；一级全量兜底 `accept` 时只能输出 taxonomy 中的一级意图。
- 二级候选判定 `accept` 时只能输出候选依据中的二级意图；二级全量兜底 `accept` 时只能输出已接受一级意图下面的二级意图。
- LLM 可以解释选择某个 intent 的理由，但理由只用于诊断，不作为后续规划的唯一依据。

## 7. 澄清反问预留

第一版可以先只落盘澄清诊断，不一定立即实现完整对话式反问。意图识别阶段触发澄清的范围应很窄，只处理“答案形态无法判断”的问题。

触发条件：

- 问题缺少动作或目标，例如“服务 A 和端口 P”。
- 问题同时支持多个答案形态，且文本没有明显倾向，例如既可能要路径，也可能要相关对象明细。
- 一级候选判定和一级全量兜底后，仍无法在一级意图中安全选择。
- 一级意图已经明确，但二级候选判定和二级全量兜底后，仍无法在当前一级意图内部安全选择。

不触发澄清、而是继续兜底的情况：

- 一级候选判定返回 `need_full_taxonomy`，应进入一级全量兜底。
- 二级候选判定返回 `need_full_taxonomy`，应进入二级全量兜底。

不合法输出不是澄清反问：

- 如果 LLM 返回不合法 JSON，属于生成级失败。
- 如果候选判定 `accept` 但输出了候选依据之外的意图，属于生成级失败。
- 如果全量兜底输出 taxonomy 之外的一级意图，属于生成级失败。
- 如果二级判定改变一级意图，或输出不属于该一级的二级意图，属于生成级失败。

不应由意图识别阶段澄清的内容：

- 具体业务对象名是否存在。
- 某个字段、指标或关系应该映射到哪个 schema 元素。
- 多条业务路径中应选择哪一条。

这些问题应交给语义视图匹配、planner 或后续 schema/path preflight 处理。

## 8. 与语义视图匹配和 planner 的关系

意图识别输出后，语义视图匹配继续识别业务语义。二者是并行互补关系，不是包含关系。

intent 主要影响：

- planner 选择返回形态，例如明细表、路径、指标值、分组表、排名表、布尔结果。
- planner 选择必要的结构约束，例如是否需要聚合、是否需要排序截断、是否需要时间展开。
- preflight 检查生成计划是否符合用户最终问题形态。

语义视图匹配主要影响：

- 哪些实体、属性、关系、路径、指标、条件和值参与查询。
- 哪些路径候选可用于 schema graph path planning。
- 哪些业务含义需要消歧或澄清。

如果 intent 识别为 `ranking_query`，但语义视图匹配无法找到可排序字段或指标，不能由 intent 直接硬编字段；应由 planner 产生缺失诊断或触发后续澄清。

## 9. 运行中心落盘要求

运行中心展示 cypher-generator-agent 时，意图识别部分建议展示：

- 用户自然语言问题。
- 最终 intent 输出。
- 阶段一规则命中情况。
- 阶段二 embedding top-k 候选、分数、margin 和共识信息。
- 阶段三 LLM 是否触发。
- 若触发 LLM：展示 `llm_primary_attempts` 和 `llm_secondary_attempts`，包括候选优先提示词、全量兜底提示词、原始返回、`attempt_type` 和触发原因。
- 最终接受、放行或澄清原因。

这样可以清楚判断一个样本到底是规则接受、embedding 接受，还是 LLM 兜底接受，也能区分“意图识别失败”和“后续语义视图匹配或 planner 失败”。

## 10. 维护与验证

修改意图识别资产时按以下顺序进行：

1. 更新 `intent-classification.md`，确认分类含义和边界。
2. 同步 `taxonomy.yaml`，确保机器可读意图枚举与文档一致。
3. 更新 `rules.yaml`，只加入高确定性规则。
4. 更新 `embedding_corpus.jsonl`，补充真实表达、同义表达、hard negative 和相邻类别对照样本。
5. 将更新后的意图语料同步或重建到远端 RAG intent collection，并记录 collection 名称、taxonomy version、embedding 模型和构建时间。
6. 更新 `llm_fewshots.yaml`，补充第三阶段需要处理的易混边界。
7. 运行离线评测和远端 RAG 召回抽查，检查规则、embedding 和 LLM fallback 的整体行为。
8. 观察运行中心样本，区分意图识别问题、语义视图匹配问题、planner 问题和渲染问题。

验证时不应只看最终 Cypher 是否正确，还应分别检查：

- 第一阶段有没有过度命中。
- 第二阶段有没有相似样本误召回。
- 第三阶段有没有越界输出、误用业务知识或生成 Cypher。
- 下游失败是否真的由 intent 导致。

## 11. 非目标

意图识别模块不承担以下职责：

- 不生成 Cypher。
- 不决定真实图谱 schema 路径。
- 不选择业务实体、属性、关系、指标或具体值。
- 不替代语义视图匹配。
- 不替代 planner。
- 不把复杂业务场景硬编码成新的 intent 类别。
