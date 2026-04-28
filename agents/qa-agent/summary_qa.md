# QA 对生成流程

## 1. 构建 Schema 与连接上下文

做什么：

- 读取输入的schema，归一化成 `CanonicalSchemaSpec`
- 解析 TuGraph 连接配置

代码：

```python
# app/orchestrator/service.py
resolved_schema_input = self._run_stage(
    job,
    JobStatus.SCHEMA_READY,
    "Resolved and normalized schema",
    lambda: self.schema_service.normalize(
        self.source_resolver.resolve_schema(job.request.schema_source, job.request.schema_input)
    ),
)
schema = resolved_schema_input
self.artifact_store.write_json(paths["schema"], schema.model_dump())
job.artifacts["schema"] = str(paths["schema"])
resolved_tugraph = self.source_resolver.resolve_tugraph(job.request.tugraph_source, job.request.tugraph_config)
self.schema_compatibility_service.assert_compatible(schema, resolved_tugraph)
```

提示词：

- 无。

作用：

- 统一后续生成、校验、问题构造使用的 schema 事实。
- 在生成前确认图数据库中存在需要使用的节点和边标签。

## 2. 生成覆盖规格

做什么：

- 每个 `CoverageSpec` 绑定一个目标难度、查询意图、结构族、查询类型、拓扑、模板变体和 schema 槽位。

* 前 8 个规格覆盖 L1-L8。
* target 超过 8 时，同一难度切换到下一组 `template_id`，减少结构重复。
* 使用 `edge_constraints` 尽量构建连续有向路径，避免生成违反边方向的多跳查询。

代码：

```python
# app/orchestrator/service.py
coverage_specs = self.coverage_service.build_specs(
    schema=schema,
    limits=limits,
    target_qa_count=self._query_plan_target_count(
        job.request.output_config.target_qa_count,
        limits.max_skeletons,
    ),
    diversity_key=diversity_key,
)
```

```python
# app/domain/coverage/service.py
LEVEL_BLUEPRINTS = [
    {"difficulty": "L1", "template_ids": ["l1_lookup_node", "l1_project_property"], "query_type": "LOOKUP"},
    {"difficulty": "L2", "template_ids": ["l2_filter_entity", "l2_project_filtered_property"], "query_type": "FILTER"},
    {"difficulty": "L3", "template_ids": ["l3_one_hop", "l3_one_hop_projection"], "query_type": "PATH"},
    {"difficulty": "L4", "template_ids": ["l4_count", "l4_count_property"], "query_type": "AGGREGATION"},
    {"difficulty": "L5", "template_ids": ["l5_two_hop", "l5_two_hop_projection"], "query_type": "MULTI_HOP"},
    {"difficulty": "L6", "template_ids": ["l6_two_hop_filtered_aggregate", "l6_two_hop_target_filtered_aggregate"], "query_type": "MULTI_HOP"},
    {"difficulty": "L7", "template_ids": ["l7_three_hop", "l7_three_hop_projection"], "query_type": "MULTI_HOP"},
    {"difficulty": "L8", "template_ids": ["l8_with_nested_aggregation", "l8_with_path_refine_aggregation"], "query_type": "SUBQUERY"},
]
```

```python
# app/domain/coverage/service.py
path = self._continuous_path(triplets, length=3, variant_index=variant_index)
edge, source, target = path[0]
edge2, _, target2 = path[1] if len(path) > 1 else (edge, source, target)
edge3, _, target3 = path[2] if len(path) > 2 else (edge2, target, target2)
```

提示词：

- 无。

作用：

- 将难度覆盖、查询类型、结构族和 schema 绑定前置，确保后续候选可追踪。

## 3. 生成 Cypher 候选

做什么：

- 将 `CoverageSpec` 先实例化为模板候选，作为 few-shot/兜底参考。
- 有 `model_config` 时调用 LLM 批量生成 Cypher 候选。
- LLM 候选必须通过本地硬筛：`MATCH` 起点、schema 节点/边/属性、实际边方向、难度分类、normalized Cypher 去重。
- LLM 候选不足或不合格时，用模板候选兜底补齐。
- 每个候选包含：`cypher`、`query_types`、`structure_family`、`bound_schema_items`、`bound_values`、`difficulty`。
- 对候选按 normalized Cypher 去重，优先保留通过硬筛的 LLM 候选。

代码：

```python
# app/orchestrator/service.py
candidates = self._run_stage(
    job,
    JobStatus.CYPHER_READY,
    f"Instantiated candidates (attempt {attempt}/{attempt_count})",
    lambda current_coverage_specs=skeletons: self._dedupe_candidates(
        self._instantiate_candidates_from_specs(
            schema,
            limits,
            llm_config if llm_cypher_enabled else None,
            current_coverage_specs,
        )
    ),
)
```

```python
# app/domain/generation/service.py
def instantiate_candidates_from_specs(self, schema, specs, limits, model_config=None):
    template_candidates = [
        self._build_candidate_from_coverage_spec(schema, spec, variant_index=index)
        for index, spec in enumerate(specs)
    ]
    if not model_config:
        return template_candidates[: limits.max_skeletons]
    llm_candidates = self._safe_build_coverage_llm_candidates_batch(schema, specs, template_candidates, model_config)
    return self._merge_llm_with_template_fallback(llm_candidates, template_candidates, limits.max_skeletons)
```

```python
# app/domain/generation/service.py
def _coverage_llm_candidate_valid(self, schema, candidate):
    if not candidate.cypher.strip().upper().startswith("MATCH "):
        return False
    if self.difficulty_service.classify(candidate.cypher) != candidate.difficulty:
        return False
    if not self._cypher_uses_known_schema_items(schema, candidate.cypher):
        return False
    return self._cypher_edges_follow_schema(schema, candidate.cypher)
```

```python
# app/domain/generation/service.py
templates = {
    "l1_lookup_node": f"MATCH (n:{node}) RETURN n",
    "l1_project_property": f"MATCH (n:{node}) RETURN n.{prop} AS value",
    "l2_filter_entity": f"MATCH (n:{node}) WHERE n.{prop} = {value} RETURN n",
    "l2_project_filtered_property": f"MATCH (n:{node}) WHERE n.{prop} = {value} RETURN n.{prop} AS value",
    "l3_one_hop": f"MATCH (a:{node})-[:{edge}]->(b:{target}) RETURN b",
    "l3_one_hop_projection": f"MATCH (a:{node})-[:{edge}]->(b:{target}) RETURN a.{prop} AS source, b.{prop2} AS target",
    "l4_count": f"MATCH (n:{node}) RETURN count(n) AS total",
    "l4_count_property": f"MATCH (n:{node}) RETURN count(n.{prop}) AS total",
    "l5_two_hop": f"MATCH (a:{node})-[:{edge}]->(:{target})-[:{edge2}]->(c:{target2}) RETURN c",
    "l5_two_hop_projection": f"MATCH (a:{node})-[:{edge}]->(b:{target})-[:{edge2}]->(c:{target2}) RETURN b.{prop2} AS via, c.{prop3} AS target",
    "l6_two_hop_filtered_aggregate": f"MATCH (a:{node})-[:{edge}]->(:{target})-[:{edge2}]->(c:{target2}) WHERE a.{prop} = {value} RETURN c.{prop3} AS key, count(*) AS total ORDER BY total {order_direction} LIMIT {limit_value}",
    "l6_two_hop_target_filtered_aggregate": f"MATCH (a:{node})-[:{edge}]->(:{target})-[:{edge2}]->(c:{target2}) WHERE c.{prop3} = {value2} RETURN a.{prop} AS key, count(c) AS total ORDER BY total {order_direction} LIMIT {limit_value}",
    "l7_three_hop": f"MATCH (a:{node})-[:{edge}]->(:{target})-[:{edge2}]->(:{target2})-[:{edge3}]->(d:{target3}) RETURN d",
    "l7_three_hop_projection": f"MATCH (a:{node})-[:{edge}]->(b:{target})-[:{edge2}]->(c:{target2})-[:{edge3}]->(d:{target3}) RETURN a.{prop} AS source, b.{prop2} AS via1, c.{prop3} AS via2, d AS target",
    "l8_with_nested_aggregation": f"MATCH (a:{node})-[:{edge}]->(b:{target}) WITH a, count(b) AS first_total MATCH (a)-[:{edge}]->(:{target})-[:{edge2}]->(c:{target2}) WHERE c.{prop3} = {value2} RETURN a.{prop} AS key, first_total AS first_total, count(c) AS total",
    "l8_with_path_refine_aggregation": f"MATCH (a:{node})-[:{edge}]->(:{target})-[:{edge2}]->(c:{target2}) WITH a, count(c) AS first_total MATCH (a)-[:{edge}]->(:{target})-[:{edge2}]->(c2:{target2}) RETURN a.{prop} AS key, first_total AS first_total, count(c2) AS total ORDER BY first_total {order_direction} LIMIT {limit_value}",
}
```

提示词：

- 当前 CoverageSpec 主路径使用 `prompts/cypher_candidate_batch.txt` 批量生成 Cypher 候选。
- `prompts/cypher_candidate_bundle.txt` 保留给旧 Skeleton/QueryPlan 路径。

保留提示词内容：

```text
你是一个严格的 Text2Cypher Cypher 批量生成器。
任务：针对一组请求，一次性输出每个请求对应的高质量 Cypher 候选集合。
硬约束：只输出 JSON；所有候选都必须使用 Cypher；必须严格基于 schema 摘要、结构族、难度和 QueryPlan；必须完整保留过滤、排序、聚合、路径长度、Top-K、分组、多阶段 WITH 等结构语义；优先使用 TuGraph 常见可执行子集。
few_shots 和 template_cypher 只是结构示范与兜底参考，不得机械复制；候选必须不同但等价、可执行、符合难度。
输出格式：{"items":[{"request_id":"...","candidates":[{"mode":"llm_direct","cypher":"MATCH ... RETURN ..."},{"mode":"llm_refine","cypher":"MATCH ... RETURN ..."}]}]}
```

作用：

- 用 LLM 扩展 Cypher 结构与表达多样性。
- 用本地硬筛和模板兜底控制 schema、边方向、难度与 job 成功率。

## 4. 执行校验与难度校验

做什么：

- 对每条 Cypher 候选执行静态和运行时校验。
- 校验内容包括语法起点、schema 节点/边/方向、类型值、查询类型、结构族、难度分类、计划约束、TuGraph 执行结果和结果 sanity。
- 过滤掉未通过校验的候选。
- 合并多次 attempt 中的有效候选，并按 Cypher 去重。

代码：

```python
# app/orchestrator/service.py
validated = [
    self.validation_service.validate(candidate, schema, job.request.validation_config, resolved_tugraph)
    for candidate in current_candidates
]
validated = [
    item
    for item in validated
    if all([
        item.validation.syntax,
        item.validation.schema_valid,
        item.validation.type_value,
        item.validation.query_type_valid,
        item.validation.family_valid,
        item.validation.difficulty_valid,
        item.validation.plan_valid,
        item.validation.runtime,
        item.validation.result_sanity,
    ])
]
```

```python
# app/domain/validation/service.py
result.syntax = candidate.cypher.strip().upper().startswith("MATCH")
result.schema_valid = self._schema_items_valid(candidate, schema)
structure_check = self.structure_rule_validator.validate(
    query_type=candidate.query_types[0],
    structure_family=candidate.structure_family,
    cypher=candidate.cypher,
)
result.query_type_valid = structure_check["query_type_valid"]
result.family_valid = structure_check["family_valid"]
classified_difficulty = self.difficulty_service.classify(candidate.cypher)
result.difficulty_valid = candidate.difficulty == classified_difficulty
runtime_meta, result_signature, runtime_ok = self.graph_executor.execute(candidate.cypher, tugraph_config)
```

```python
# app/domain/validation/service.py
def _schema_items_valid(self, candidate, schema):
    nodes = [node for node in candidate.bound_schema_items.get("nodes", []) if node]
    edges = [edge for edge in candidate.bound_schema_items.get("edges", []) if edge]
    if any(node not in schema.node_types for node in nodes):
        return False
    if any(edge not in schema.edge_types for edge in edges):
        return False
    if not schema.edge_constraints:
        return True
    for index, edge in enumerate(edges):
        if index + 1 >= len(nodes):
            continue
        allowed_pairs = {tuple(pair) for pair in schema.edge_constraints.get(edge, [])}
        if allowed_pairs and (nodes[index], nodes[index + 1]) not in allowed_pairs:
            return False
    return True
```

提示词：

- 无。

作用：

- 防止无效 Cypher、schema 外实体、错误边方向、结构族不匹配、难度不匹配、不可执行结果进入 QA 生成阶段。

## 5. 生成中文 QA 问题

做什么：

- 从验证通过的候选中按质量和多样性 shortlist。
- 调用大模型批量生成中文标准问题与变体。
- 批量失败时 fallback 到单条生成。
- 生成 `QASample`，包含标准问题、变体、Cypher、答案、难度、查询类型、校验信息和 provenance。

代码：

```python
# app/orchestrator/service.py
qa_samples = self._run_stage(
    job,
    JobStatus.QUESTIONS_READY,
    f"Generated QA samples (attempt {attempt}/{attempt_count})",
    lambda current_validated=self._shortlist_validated_samples(
        list(aggregated_validated),
        job.request.output_config.target_qa_count,
    ): self._select_best_by_question(
        self._generate_questions(
            current_validated,
            schema,
            llm_config,
            limits.max_variants_per_question,
            job.request.mode.value,
        )
    ),
)
```

```python
# app/domain/questioning/service.py
bundle_text = self.model_gateway.generate_text(
    "question_bundle_batch",
    batch_config,
    requests_json=json.dumps(requests_payload, ensure_ascii=False),
)
parsed = self._parse_batch(bundle_text)
```

```python
# app/orchestrator/service.py
def _generate_questions(self, validated, schema, llm_config, max_variants, mode):
    try:
        return self.question_service.generate_batch(validated, schema, llm_config, max_variants)
    except Exception:
        with ThreadPoolExecutor(max_workers=self._parallelism(len(validated), mode)) as executor:
            results = list(executor.map(lambda item: self._safe_generate_question(item, schema, llm_config, max_variants), validated))
        return [item for item in results if item is not None]
```

提示词：

- 主路径：`prompts/question_bundle_batch.txt`
- fallback：`prompts/question_bundle.txt`

主路径提示词：

```text
你是一个严格的 Text2Cypher 中文问句批量构造器。

任务：针对一组已通过执行校验的 Cypher 请求，一次性为每个请求输出：
1. 1 条严格等价、自然、专业的中文标准问题
2. 一组固定风格的等价中文变体
3. 对标准问题的结构化一致性自检
4. 通过自检的变体 style

硬约束：
1. 只能表达 Cypher 中已有的语义，不得新增条件。
2. 不得遗漏排序、聚合、时间、比较、Top-K、路径长度、数量限制、分组、WITH 分阶段语义。
3. 问题必须像真实用户会问的话，不能出现元话术。
4. 如果 Cypher 返回的是计数、聚合值或分组结果，问题中必须明确问的是“多少/统计/排名/分组统计”。
5. 如果 Cypher 含有 LIMIT N，标准问题里必须明确体现数量限制。
6. 绝对禁止把 Cypher 语句、属性访问表达式、代码块、关键字片段直接写进问题中。
7. 你必须输出 canonical_checks，逐项检查 filters、temporal、ordering、topk_limit、aggregation_grouping、path_hops、comparison、return_target。
8. 只有在标准问题通过全部必要语义检查时，canonical_pass 才能为 true。
9. 你必须输出 approved_styles，只列出你确认与 Cypher 严格等价的 style。
10. 只输出 JSON，不要解释，不要 Markdown。

输出格式：
{"items":[{"request_id":"...","canonical_question":"...","variants":[{"style":"natural_short","question":"..."}],"canonical_pass":true,"canonical_checks":{"filters":true,"temporal":true,"ordering":true,"topk_limit":true,"aggregation_grouping":true,"path_hops":true,"comparison":true,"return_target":true},"approved_styles":["natural_short"]}]}

请求列表:
{requests_json}
```

作用：

- 把可执行 Cypher 转成用户可读、自然、严格等价的中文 QA 问题。
- 让模型同时产出自检字段，供下一步一致性校验使用。

## 6. 一致性校验

做什么：

- 读取问题生成阶段 provenance 中的 `canonical_pass/canonical_checks/approved_styles`。
- 校验标准问题是否自然语言。
- 校验问题是否保留 LIMIT、聚合等关键语义。
- 将生成后的中文问题和 Cypher 交给大模型做二次一致性审查。
- 大模型返回 `PASS` 才允许进入最终结果；返回 `FAIL` 会过滤该 QA。
- 过滤未通过 roundtrip 的 QA。
- 只保留 approved style 对应的问题变体。

代码：

```python
# app/orchestrator/service.py
roundtrip = self._run_stage(
    job,
    JobStatus.ROUNDTRIP_DONE,
    f"Completed roundtrip checks (attempt {attempt}/{attempt_count})",
    lambda current_qa=list(aggregated_qa): self._apply_roundtrip(
        job,
        current_qa,
        llm_config,
        job.request.mode.value,
    ),
)
```

```python
# app/orchestrator/service.py
def _apply_roundtrip(self, job, qa_samples, llm_config, mode):
    with ThreadPoolExecutor(max_workers=self._parallelism(len(qa_samples), mode)) as executor:
        checks = list(executor.map(lambda sample: self.roundtrip_service.check(sample, llm_config), qa_samples))
    output = []
    for sample, (is_valid, approved_variants, approved_styles) in zip(qa_samples, checks):
        sample.validation.roundtrip_check = is_valid
        sample.question_variants_zh = approved_variants
        sample.question_variant_styles = approved_styles
        if job.request.validation_config.roundtrip_required and not is_valid:
            continue
        output.append(sample)
    return output
```

```python
# app/domain/roundtrip/service.py
payload = {
    "canonical_pass": self._read_bool(sample.provenance.get("canonical_pass")),
    "canonical_checks": self._read_json(sample.provenance.get("canonical_checks"), {}),
    "approved_styles": self._read_json(sample.provenance.get("approved_styles"), []),
}
canonical_ok, approved_variants, approved_styles = self._parse_bundle_result(
    json.dumps(payload, ensure_ascii=False),
    sample,
)
if not canonical_ok:
    return False, approved_variants, approved_styles
consistency_text = self.model_gateway.generate_text(
    "question_cypher_consistency",
    model_config,
    question=sample.question_canonical_zh,
    cypher=sample.cypher,
)
return (
    self._parse_consistency_result(consistency_text),
    approved_variants,
    approved_styles,
)
```

提示词：

- 主路径二次校验提示词：
  - `prompts/question_cypher_consistency.txt`

提示词内容：

```text
你是一个严格的中文 QA 一致性审查器。
如果中文问题与 Cypher 严格等价，输出 PASS。
否则输出 FAIL。
判定时必须检查：是否遗漏过滤、排序、Top-K、时间、路径长度、聚合、分组、比较等约束；是否把返回对象、返回属性、统计结果说错；是否出现语义泛化；是否把多跳、时间范围、排序方向等关键语义说浅了。
```

作用：

- 防止中文问题与 Cypher 不等价。
- 防止问题漏掉 LIMIT、聚合、统计等影响难度和答案的关键语义。
- 防止仅依赖问题生成阶段自检导致错误中文问题进入 release。

## 7. 去重、分层、发布与发送

做什么：

- 查看历史是否已经生成过qa对，如果有，优先选择历史中未出现过的问题和 Cypher。
- 将最终 QA 样本发送到两个接口。

代码：

```python
# app/orchestrator/service.py
deduped, selection_meta = self._run_stage(
    job,
    JobStatus.DEDUPED,
    f"Deduplicated and split samples (attempt {attempt}/{attempt_count})",
    lambda current_roundtrip=list(aggregated_roundtrip): self._dedupe_and_split(
        current_roundtrip,
        job.request.output_config.target_qa_count,
        job.request.output_config.split_seed_limit,
        job.request.output_config.split_gold_limit,
        paths["releases"],
    ),
)
```

```python
# app/orchestrator/service.py
self.artifact_store.write_jsonl(paths["releases"], [self._export_sample(item) for item in deduped])
job.artifacts["releases"] = str(paths["releases"])
```

```python
# app/orchestrator/service.py
def _export_sample(self, sample):
    return {
        "id": sample.id,
        "question": sample.question_canonical_zh,
        "cypher": sample.cypher,
        "answer": sample.answer,
        "difficulty": sample.difficulty,
    }
```

```python
# app/orchestrator/service.py
def _build_report_with_dispatch(self, job, samples, target_qa_count, selection_meta):
    dispatch_result = self.qa_dispatcher.dispatch_samples(samples)
    report = self.report_builder.build(samples, stages=job.stages, dispatch=dispatch_result)
    report["selection"] = {
        **selection_meta,
        "requested_count": target_qa_count,
        "final_count": len(samples),
    }
    report["dispatch"] = dispatch_result
    report["performance"] = self._build_performance_summary(report.get("business_stages", []), len(samples))
    return report
```

```python
# app/integrations/qa_dispatcher.py
question_result = self._post_with_retry(
    f"{question_base_url}/api/v1/qa/questions",
    {
        "id": sample.id,
        "question": sample.question_canonical_zh,
    },
)
golden_result = self._post_with_retry(
    f"{golden_base_url}/api/v1/qa/goldens",
    {
        "id": sample.id,
        "cypher": sample.cypher,
        "answer": sample.answer,
        "difficulty": sample.difficulty,
    },
)
```

提示词：

- 无。

作用：

- 生成最终可交付 QA 文件。
- 保证一个 job 的最终结果是多条 QA。
- 将每条 QA 拆成 question 与 golden 两部分，分别发送到两个接口。
