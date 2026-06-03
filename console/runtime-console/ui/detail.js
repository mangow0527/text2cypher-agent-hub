const taskMeta = document.getElementById('task-meta');
const overviewGrid = document.getElementById('overview-grid');
const pipelineView = document.getElementById('pipeline-view');

const cgaStageTitles = {
  graph_model_loader: '语义模型加载',
  input_clarification_gate: '输入澄清门',
  question_decomposer: '问题结构化拆解',
  candidate_retrieval: '语义候选召回',
  literal_resolver: '字面值解析',
  grounded_understanding: '语义落地理解',
  semantic_binder: '语义绑定计划',
  semantic_validator: '语义正确性校验',
  repair_controller: '修复与澄清决策',
  dsl_builder: '受限 DSL 构建',
  dsl_parser: 'DSL 解析',
  dsl_structural_coverage_gate: 'DSL 结构覆盖闸门',
  cypher_compiler: 'Cypher 编译',
  cypher_self_validation: 'Cypher 自校验',
  output: '服务输出',
};

const questionDecompositionFieldHints = {
  schema_version: '问题拆解结果的结构版本，当前应为 question_decomposition_v1。',
  result_type: '拆解结果类型。decomposition 表示已完成结构化拆解，clarification_required 表示问题缺少明确指代、需要先反问用户。',
  intent_type: '问题意图类型，例如 lookup、list、count、aggregate、top_n、path、compare 或 unknown。',
  original_question: '进入问题拆解阶段的原始用户问题，用来和拆解结果对照。',
  target_concepts: '旧 trace 兼容字段：早期 decomposer 输出的目标概念视图；当前 schema 不再输出，召回改读 substantive_terms。',
  literal_candidates: '检索角色轴字段：用户问题中用来限定某个概念的具体值。新 schema 中每项包含 text、kind_hint、attached_to。',
  relation_phrases: '旧 trace 兼容字段：早期 decomposer 输出的关系短语视图；当前 schema 不再输出，关系词从 substantive_terms 中 slot=path 的词推导。',
  relation_mentions: '兼容旧 trace 的关系短语字段，含义等同于 relation_phrases。',
  relationships_mentioned: '兼容旧 trace 的关系短语字段，含义等同于 relation_phrases。',
  literal_candidate_objects: '保留 literal_candidates 的结构化对象列表，供后续字面值解析读取 text、kind_hint、attached_to。',
  literal_requests: '由工程代码生成的字面值解析请求，明确 raw_literal 应该绑定到哪个 vertex/edge 的哪个 property。',
  skipped_literal_candidates: '按 slot 判定后未送入 literal resolver 的候选词。结构控制词会在这里留下 raw、slot 和跳过原因。',
  substantive_terms: '实义词对象数组，每项包含 text、slot 和可选 attached_to；slot 表示该词进入 projection、filter、group_by、order_by、limit、path 或 unknown。',
  text: '用户问题中的原始表层词。',
  slot: '该词在查询计划中的语义角色，是判断 projection、filter、group_by、order_by、limit、path 的权威字段。',
  attached_to: '该词修饰或归属的表层概念，例如 “Gold” attached_to “服务”。',
  kind_hint: '字面值类型提示，例如 enum_or_name、id、number、datetime 或 unknown。',
  raw: '原始候选词文本。',
  reason: '系统记录该项被跳过、失败或进入修复的原因。',
  stopword_terms: '覆盖报告兼容字段：已忽略的礼貌语、连接词、助词或查询引导词；当前 question_decomposer 不再输出 stopword 列表。',
  modality_terms: '覆盖分类轴字段：表达近似、不确定或软约束的词，例如“大概”“应该”“可能”。',
  time_terms: '覆盖分类轴字段：时间或时间范围表达，例如“最近”“2024 年”“过去 7 天”。',
  unparsed_terms: '覆盖分类轴字段：无法可靠分类但可能影响语义的残留词；如果非空，通常意味着需要澄清或生成失败。',
  output_shape: '回答结果的形态，例如 rows 表示多行结果、scalar 表示单个值、grouped_rows 表示分组统计结果。',
  coverage: '覆盖率报告，记录 substantive_terms 中哪些词已覆盖、哪些仍缺失；返回字段覆盖明细位于 projection_terms。',
  filters: '后续阶段可能补充的过滤条件结构；Question Decomposer 本身不再输出旧的过滤短语字段。',
  mock_intent: '本地 mock 流程使用的测试意图标记，真实 LLM 流程通常不依赖它。',
};

const commonFieldHints = {
  schema_version: '当前对象的结构版本，用来判断字段契约。',
  trace_schema_version: 'CGA trace 的结构版本，当前 GraphTrace 使用 cga_graph_trace_v1。',
  trace_id: '本次生成链路的 trace 标识。',
  question_id: '输入问题或 QA 样本的稳定标识。',
  generation_run_id: '本次生成尝试的运行标识。',
  source_question: '进入 CGA 的原始自然语言问题。',
  question: '当前阶段处理的自然语言问题。',
  status: '当前对象或阶段的状态。',
  final_status: 'CGA 全链路最终状态。',
  started_at: '阶段或 trace 开始时间。',
  finished_at: 'trace 结束时间。',
  duration_ms: '阶段耗时，单位毫秒。',
  input_ref: '阶段输入在 trace 中的存储引用。',
  output_ref: '阶段输出在 trace 中的存储引用。',
  input: '阶段收到的结构化输入。',
  output: '阶段产生的结构化输出。',
  metrics: '阶段记录的计数或统计指标。',
  errors: '阶段执行时产生的错误列表。为空表示没有错误。',
  warnings: '阶段执行时产生的警告列表。为空表示没有警告。',
  code: '错误、警告或校验问题的稳定代码。',
  message: '错误、警告或校验问题的人类可读说明。',
  details: '补充诊断上下文，通常用于排查具体字段、候选或校验规则。',
  llm_calls: '该阶段保存的 LLM 调用记录，包含 prompt、raw output、解析结果和 token 用量。',
  token_usage: 'LLM 调用的 token 统计。',
  token_usage_total: 'LLM 调用消耗的总 token 数。',
  raw_output: '模型或下游服务返回的原始文本。',
  parsed_output: '原始输出解析后的结构化对象。',
  prompt: '发给模型的完整提示词。',
  call_id: 'LLM 调用的唯一标识。',
  schema_name: '本次结构化输出要求遵循的 schema 名称。',
  attempt: '本阶段或 LLM 调用的尝试次数。',
  confidence: '候选、绑定或解析结果的置信度。',
};

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

function pretty(value) {
  if (value === null || value === undefined || value === '') {
    return '未提供';
  }
  if (typeof value === 'string') {
    return value;
  }
  return JSON.stringify(value, null, 2);
}

function tone(status) {
  switch (status) {
    case 'passed':
    case 'pass':
    case 'generated':
    case 'success':
    case 'ok':
    case 'applied':
    case true:
      return 'ok';
    case 'failed':
    case 'fail':
    case 'service_failed':
    case 'apply_failed':
    case 'rejected':
    case false:
      return 'danger';
    case 'running':
    case 'generation_pending':
    case 'generation_failed':
    case 'unsupported_query_shape':
    case 'clarification_required':
    case 'waiting_human_review':
    case 'apply_paused':
      return 'warn';
    case 'cancelled':
    case 'skipped':
    case 'not_sent':
    case 'not_recorded':
    case 'not_started':
    case 'not_repairable':
      return 'neutral';
    default:
      return 'neutral';
  }
}

function setPipelineMessage(message) {
  pipelineView.innerHTML = `<p class="empty">${escapeHtml(message)}</p>`;
}

function metricCard(label, value, status = null, className = '') {
  const cardClass = ['overview-card', className].filter(Boolean).join(' ');
  return `
    <article class="${escapeHtml(cardClass)}">
      <div>
        <h3>${escapeHtml(label)}</h3>
        <p>${escapeHtml(value ?? '未提供')}</p>
      </div>
      ${status === null ? '' : `<span class="status-pill tone-${tone(status)}">${escapeHtml(status)}</span>`}
    </article>
  `;
}

function optionalMetricCard(label, value, status = null) {
  if (value === null || value === undefined || value === '' || (Array.isArray(value) && !value.length)) {
    return '';
  }
  return metricCard(label, value, status);
}

function codeBlock(value) {
  return `<pre>${escapeHtml(pretty(value))}</pre>`;
}

function cypherOverviewCard(label, value, status = null, className = '') {
  const cardClass = ['overview-card', 'cypher-overview-card', className].filter(Boolean).join(' ');
  return `
    <article class="${escapeHtml(cardClass)}">
      <div>
        <h3>${escapeHtml(label)}</h3>
        ${codeBlock(value)}
      </div>
      ${status === null ? '' : `<span class="status-pill tone-${tone(status)}">${escapeHtml(status)}</span>`}
    </article>
  `;
}

function inlineValue(value) {
  if (value === null || value === undefined || value === '') {
    return '未记录';
  }
  if (Array.isArray(value)) {
    return `${value.length} 条`;
  }
  if (typeof value === 'object') {
    return JSON.stringify(value, null, 2);
  }
  return String(value);
}

function formatDurationMs(value) {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    return '未记录';
  }
  if (value >= 60000) {
    const minutes = Math.round((value / 60000) * 10) / 10;
    return `${minutes} min`;
  }
  if (value >= 1000) {
    const seconds = Math.round((value / 1000) * 10) / 10;
    return `${seconds} s`;
  }
  return `${Math.round(value)} ms`;
}

const stageMetricLabels = {
  llm_call_count: ['LLM 调用', '次'],
  candidate_count: ['候选召回', '个'],
  literal_count: ['字面值', '个'],
  skipped_literal_candidate_count: ['跳过 literal candidate', '个'],
  operation_count: ['DSL 操作', '个'],
  checked_rule_count: ['校验规则', '条'],
  retry_count: ['重试', '次'],
  repair_attempt_count: ['修复轮次', '轮'],
};

function formatStageMetricKey(key) {
  const mapped = stageMetricLabels[key];
  if (mapped) {
    return mapped;
  }
  return [String(key).replace(/_/g, ' '), ''];
}

function formatStageMetricValue(value) {
  if (value === true) {
    return '是';
  }
  if (value === false) {
    return '否';
  }
  if (Array.isArray(value)) {
    return `${value.length}`;
  }
  if (value && typeof value === 'object') {
    return JSON.stringify(value);
  }
  return String(value);
}

function formatStageMetrics(metrics) {
  if (!metrics || typeof metrics !== 'object' || Array.isArray(metrics)) {
    return '无阶段指标';
  }
  const parts = Object.entries(metrics)
    .filter(([, value]) => value !== null && value !== undefined && value !== '')
    .map(([key, value]) => {
      const [label, unit] = formatStageMetricKey(key);
      return `${label}: ${formatStageMetricValue(value)}${unit}`;
    });
  return parts.length ? parts.join(' · ') : '无阶段指标';
}

function fallbackFieldHint(key, section) {
  if (!key) {
    return '未命名字段。';
  }
  if (key.endsWith('_id')) {
    return '稳定标识字段，用来把当前对象与 trace、样本或下游记录关联。';
  }
  if (key.endsWith('_count')) {
    return '计数字段，表示当前阶段记录的对应对象数量。';
  }
  if (key.endsWith('_at')) {
    return '时间戳字段，用来定位该事件或记录发生的时间。';
  }
  if (key.endsWith('_ms')) {
    return '耗时字段，单位毫秒。';
  }
  if (key.includes('cypher')) {
    return 'Cypher 相关字段，用来展示生成、编译或校验后的查询文本。';
  }
  if (key.includes('dsl')) {
    return '受限 DSL 相关字段，用来展示 Cypher 编译前的中间查询结构。';
  }
  if (key.includes('literal')) {
    return '字面值解析相关字段，用来追踪自然语言中的值如何进入或跳过 resolver。';
  }
  if (key.includes('candidate')) {
    return '候选召回相关字段，用来记录可供后续阶段选择的语义对象。';
  }
  if (key.includes('coverage')) {
    return '覆盖率相关字段，用来判断用户问题中的语义词是否被后续计划覆盖。';
  }
  if (key.includes('projection')) {
    return '返回字段相关字段，用来判断最终查询会返回哪些对象或属性。';
  }
  if (key.includes('filter')) {
    return '过滤条件相关字段，用来记录属性约束和值绑定。';
  }
  if (key.includes('repair')) {
    return '修复流程相关字段，用来记录失败后的重试、澄清或终止决策。';
  }
  return section === 'metrics'
    ? '阶段指标字段，记录该阶段执行过程中的数量、耗时或调用统计。'
    : 'trace 原始字段，运行中心按服务落盘内容原样展示。';
}

const stageFieldHints = {
  __default: {
    input: {
      _summary: '这里展示该阶段收到的上游结构化数据。',
    },
    output: {
      _summary: '这里展示该阶段处理完成后传给下游的结构化结果。',
    },
    metrics: {
      _summary: '这里展示该阶段的计数指标、错误和警告。',
      metrics: '阶段内部记录的计数或统计值。',
      errors: '阶段执行时产生的错误列表。为空表示没有错误。',
      warnings: '阶段执行时产生的警告列表。为空表示没有警告。',
    },
  },
  graph_model_loader: {
    input: {
      _summary: '这里记录本次 CGA 读取哪一份图语义模型。',
      model_path: '语义模型 YAML 文件路径。CGA 会基于这份模型做对象召回、语义校验和 Cypher 编译。',
    },
    output: {
      _summary: '这里记录语义模型加载后的模型版本和规模。',
      model_name: '语义模型名称，用来标识本次使用的是哪套业务语义层。',
      model_checksum: '模型内容校验值，用来确认语义模型版本是否一致。',
      vertices: '点类型数量，也就是模型里定义了多少类图节点。',
      edges: '边/关系类型数量，也就是模型里定义了多少类图关系。',
      path_patterns: '命名路径模板数量，用来复用常见多跳图查询路径。',
    },
  },
  input_clarification_gate: {
    input: {
      _summary: '这里展示进入流水线前的原始问题和输入检查信息。',
      question: '用户提交的自然语言问题。',
    },
    output: {
      _summary: '这里展示问题是否足够清楚、是否可以进入后续语义生成流程。',
      status: '输入检查结果。pass 表示可以继续，clarification_required 表示问题本身需要先澄清。',
      reason_code: '触发拦截或澄清的原因代码。',
      clarification: '需要向用户提出的澄清问题。',
    },
  },
  question_decomposer: {
    input: {
      _summary: '这里展示发给问题拆解阶段的自然语言问题。',
      question: '用户提交的自然语言问题。',
    },
    output: {
      _summary: '这里展示 LLM 对问题的结构化拆解结果。',
      ...questionDecompositionFieldHints,
    },
  },
  candidate_retrieval: {
    input: {
      _summary: '这里展示候选召回阶段收到的问题拆解结果。',
      ...questionDecompositionFieldHints,
    },
    output: {
      _summary: '这里展示语义层召回到的候选对象，供后续 LLM 在候选集合内选择。',
      candidates: '召回的语义候选列表，可能包含点、边、属性、指标或路径模板。',
      semantic_type: '候选对象类型，例如 vertex、edge、property、metric 或 path_pattern。',
      semantic_id: '语义对象的稳定标识。',
      semantic_name: '语义对象名称，通常是模型中的点、边、属性或指标名。',
      score: '召回相似度或匹配分数。',
      match_type: '候选命中方式，例如 exact、synonym、text 或 embedding。',
      evidence: '召回证据，说明哪个用户词命中了哪条模型信息。',
      owner: '属性候选所属的点或边。',
      metadata: '候选对象的补充元信息，例如合法值、方向语义或属性类型。',
    },
    metrics: {
      _summary: '这里展示候选召回规模。',
      candidate_count: '本阶段召回出的语义候选总数。',
      errors: '候选召回阶段产生的错误列表。为空表示没有错误。',
      warnings: '候选召回阶段产生的警告列表。为空表示没有警告。',
    },
  },
  literal_resolver: {
    input: {
      _summary: '这里展示本阶段实际会解析哪些 literal，以及哪些候选因 slot 语义被跳过。',
      literal_requests: '送入 literal resolver 的解析请求。每项包含 raw_literal、期望 vertex/edge、expected_property 和 literal_kind_hint。',
      raw_literal: '用户问题中的原始字面值文本。',
      expected_vertex: 'resolver 应在该点类型下查找属性值。',
      expected_edge: 'resolver 应在该边类型下查找属性值。',
      expected_property: 'resolver 应匹配的属性名。',
      literal_kind_hint: '字面值类型提示，例如 enum_or_name、id、numeric、time 或 unknown。',
      skipped_literal_candidates: '按 slot 判定后未送入 literal resolver 的候选词。结构控制词会在这里留下 raw、slot 和跳过原因。',
      raw: '被跳过候选的原始文本。',
      slot: '该候选在 substantive_terms 中的语义槽位。slot 是是否进入 resolver 的判定锚点。',
      reason: '跳过原因，例如 slot=limit 表示该词是 limit 控制词，不是过滤值。',
    },
    output: {
      _summary: '这里展示字面值解析结果，例如把 “Gold” 解析成某个枚举值。',
      raw_literal: '用户问题里的原始字面值。',
      resolved: '是否成功解析到语义层或 value-index 中的确定值。',
      resolved_value: '解析后的标准值。',
      normalized_value: 'resolver 标准化后的值，用于后续绑定和 Cypher 参数。',
      match_type: '解析命中方式，例如 exact、synonym 或 fuzzy。',
      expected: '期望匹配的语义字段。',
      expected_vertex: '解析目标点类型。',
      expected_edge: '解析目标边类型。',
      expected_property: '解析目标属性。',
      evidence: '解析证据，说明命中了哪条 value-index 或属性合法值。',
      error_code: '解析失败原因。',
      alternatives: '可供用户选择的候选值。',
      value_index_miss: '是否因为 value-index 没有命中而失败。',
      requires_user_choice: '是否需要用户从 alternatives 中选择。',
    },
    metrics: {
      _summary: '这里展示 literal resolver 的解析数量和跳过数量。',
      literal_count: '实际送入 resolver 并产出解析结果的 literal 数量。',
      skipped_literal_candidate_count: '因结构槽位被跳过的 literal candidate 数量，例如 slot=limit 的 Top-N 数字。',
      errors: 'literal resolver 阶段产生的错误列表。为空表示没有错误。',
      warnings: 'literal resolver 阶段产生的警告列表。为空表示没有警告。',
    },
  },
  grounded_understanding: {
    input: {
      _summary: '这里展示语义落地阶段收到的拆解结果、召回候选和已解析 literal。',
      decomposition: '问题结构化拆解结果。',
      resolved_literals: 'literal resolver 已解析成功或失败的结果列表。',
      attempt_no: '语义落地理解的尝试轮次。',
      repair_context: '修复回灌时补充给本阶段的上下文。',
    },
    output: {
      _summary: '这里展示 LLM 在候选集合内做出的语义选择。',
      query_shape: '被识别出的查询形态。',
      selected_vertices: '最终选择的点类型。',
      selected_edges: '最终选择的边/关系类型。',
      selected_properties: '最终选择的属性字段。',
      selected_literals: '最终纳入语义计划的已解析字面值。',
      filters: '语义层选择出的过滤条件。',
      projection: '语义层选择出的返回字段或对象。',
      group_by: '语义层选择出的分组维度。',
      measures: '语义层选择出的聚合度量。',
      sort: '语义层选择出的排序规则。',
      limit: '语义层选择出的数量限制。',
      coverage: '本阶段给出的语义覆盖报告。',
      unsupported: '如果查询形态不支持，这里记录原因。',
      assumptions: '系统在高置信场景下做出的假设。',
    },
    metrics: {
      _summary: '这里展示语义落地阶段的 LLM 调用情况。',
      llm_call_count: '本阶段发起的 LLM 调用次数。',
      token_usage: '本阶段 LLM 调用的 token 明细。',
      token_usage_total: '本阶段 LLM 调用消耗的总 token 数。',
      errors: '语义落地阶段产生的错误列表。为空表示没有错误。',
      warnings: '语义落地阶段产生的警告列表。为空表示没有警告。',
    },
  },
  semantic_binder: {
    input: {
      _summary: '这里展示语义落地结果，binder 会把它收敛成稳定绑定计划。',
      query_shape: '语义落地阶段选择的查询形态。',
      selected_vertices: '待绑定的点候选。',
      selected_edges: '待绑定的边候选。',
      selected_properties: '待绑定的属性候选。',
      selected_literals: '待绑定的 literal 解析结果。',
      filters: '待绑定的过滤条件。',
      projection: '待绑定的返回字段。',
    },
    output: {
      _summary: '这里展示稳定的语义绑定计划，供校验器和 DSL 构建器使用。',
      query_shape: '查询形态。',
      vertex_bindings: '已确认的点类型绑定。',
      edge_bindings: '已确认的边/关系绑定。',
      property_bindings: '已确认的属性绑定。',
      literal_bindings: '已确认的 literal 到属性值绑定。',
      metric_bindings: '已确认的指标绑定。',
      path_pattern_bindings: '已确认的命名路径模板绑定。',
      filters: '已绑定到具体字段和值的过滤条件。',
      group_by: '已绑定的分组维度。',
      measures: '已绑定的聚合度量。',
      projection: '准备返回给用户的字段或对象。',
      sort: '排序规则。',
      limit: '数量限制。',
      assumptions: '绑定过程中保留的假设。',
    },
  },
  semantic_validator: {
    input: {
      _summary: '这里展示语义校验器收到的绑定计划和覆盖率报告。',
      binding_plan: 'semantic_binder 输出的稳定绑定计划。',
      coverage: '问题拆解阶段生成并经 pipeline 补齐的覆盖率报告。',
    },
    output: {
      _summary: '这里展示语义正确性校验结果。',
      is_valid: '语义绑定是否通过校验。',
      errors: '语义错误，例如类型不匹配、字面值无法解析或覆盖缺失。',
      warnings: '不阻断生成的风险提示。',
      assumptions: '系统继续执行时采用的假设。',
      coverage: '语义覆盖报告，包含实质词覆盖和 projection_terms 返回字段覆盖。projection_terms.uncovered 非空时，表示返回字段覆盖缺失。',
      projection_terms: '返回字段覆盖明细。required 是用户要求返回的字段词，covered 是已进入 DSL projection 的字段词，uncovered 是遗漏字段词。',
      projection_coverage_missing: '返回字段覆盖缺失错误码。出现该错误说明用户要求返回的字段没有全部进入 projection，应进入修复而不是静默生成。',
    },
  },
  repair_controller: {
    input: {
      _summary: '这里展示校验失败或不确定时，修复决策器收到的上下文。',
      validator_errors: '语义校验器产生的错误列表。',
      repair_history: '此前修复尝试的历史，用来避免震荡。',
    },
    output: {
      _summary: '这里展示系统决定继续修复、反问用户还是终止。',
      decision: '系统决策，例如 ask_user、retry_llm 或 generation_failed。',
      reason_code: '做出该决策的主要原因。',
      clarification: '需要展示给用户的澄清反问。',
      assumptions: '如果继续执行，需要向用户说明的假设。',
    },
  },
  dsl_builder: {
    input: {
      _summary: '这里展示 DSL 构建器收到的已校验绑定计划。',
      query_shape: '绑定计划中的查询形态。',
      vertex_bindings: 'DSL 构建可使用的点绑定。',
      edge_bindings: 'DSL 构建可使用的边绑定。',
      property_bindings: 'DSL 构建可使用的属性绑定。',
      literal_bindings: 'DSL 构建可使用的 literal 绑定。',
      filters: '需要写入 DSL 的过滤条件。',
      projection: '需要写入 DSL 的返回字段。',
      sort: '需要写入 DSL 的排序规则。',
      limit: '需要写入 DSL 的数量限制。',
    },
    output: {
      _summary: '这里展示由语义绑定计划构建出的受限 DSL。',
      schema_version: 'DSL schema 版本。',
      query_id: '本次 DSL 对应的问题或运行标识。',
      query_shape: 'DSL 表达的查询形态。',
      source_question: 'DSL 来源自然语言问题。',
      bindings: 'DSL 中使用的点、边、指标或路径绑定。',
      operations: 'DSL 操作序列，例如匹配、过滤、聚合、排序。',
      filters: 'DSL 过滤条件。',
      projection: 'DSL 要返回的字段或对象。',
      assumptions: '生成 DSL 时保留的系统假设。',
    },
  },
  dsl_parser: {
    input: {
      _summary: '这里展示待解析和校验的受限 DSL。',
      schema_version: 'DSL schema 版本。',
      query_shape: 'DSL 声明的查询形态。',
      bindings: 'DSL 声明的绑定对象。',
      operations: 'DSL 声明的操作序列。',
      projection: 'DSL 声明的返回字段。',
    },
    output: {
      _summary: '这里展示 DSL 解析结果，确认 DSL 结构可以被编译器消费。',
      query_shape: '解析后的查询形态。',
      operation_count: '解析出的 DSL 操作数量。',
      errors: 'DSL 结构错误。',
    },
  },
  dsl_structural_coverage_gate: {
    input: {
      _summary: '这里展示结构需求派生结果以及已通过 parser 的 DSL。',
      structural_requirements: '从 question_decomposition_v1 的既有 slot 确定性派生出的结构需求，不是 LLM 新输出字段。',
      dsl: '待检查结构覆盖的受限 DSL。',
    },
    output: {
      _summary: '这里展示 DSL 是否覆盖了题干已识别出的结构需求。',
      coverage_result: '结构覆盖检查结果，包含 is_valid 与缺失项。',
      missing: '未被 DSL 覆盖的结构需求，例如 aggregate、group_by、order_by、limit 或 path hop 数量。',
      path_order_confidence: 'path 词顺序置信度；low 时只做数量充分性检查。',
    },
  },
  cypher_compiler: {
    input: {
      _summary: '这里展示编译器收到的 DSL 查询形态。',
      query_shape: '编译器要处理的 DSL 查询形态。',
    },
    output: {
      _summary: '这里展示 DSL 编译成 Cypher 后的结果。v1 对外执行的是内联后的 cypher_executable/cypher，模板和参数仅作为编译中间产物保留。',
      cypher_template: '编译器内部生成的参数化 Cypher 模板，例如 WHERE svc.quality_of_service = $quality_of_service；它不直接交给 testing-agent 执行。',
      parameters: '模板参数字典，例如 {"quality_of_service":"Gold"}；用于 trace、回归分析和未来切换参数化执行协议，不是 v1 的执行契约。',
      parameter_sources: '每个模板参数的来源元信息，包含参数名、取值、来源阶段、resolver_match_type 和 resolved 状态。',
      cypher_executable: '参数内联后的可执行 Cypher，是 v1 提交给 testing-agent / TuGraph 的主产物，不应包含 $param。',
      cypher: 'v1 中与 cypher_executable 相同，作为对外主输出字段保留。',
      expected_return_aliases: '编译器期望 RETURN 中出现的字段别名。',
    },
  },
  cypher_self_validation: {
    input: {
      _summary: '这里展示自校验器收到的最终 Cypher 和预期 RETURN 别名。',
      cypher: '准备提交 testing-agent 的最终 Cypher。',
      expected_return_aliases: '编译器期望 RETURN 子句包含的别名。',
    },
    output: {
      _summary: '这里展示不连接数据库前的 Cypher 静态自校验结果。',
      valid: 'Cypher 是否通过自校验。',
      mode: '自校验模式，例如 generated_query。',
      checks: '逐项自校验规则结果。',
      errors: '阻断提交的静态错误，例如写操作、未知 label、RETURN 形态不一致，或最终执行 Cypher 仍残留 $param。',
      warnings: '不阻断提交的风险提示。',
      checked_rules: '本次执行过的校验规则。',
    },
  },
  output: {
    output: {
      _summary: '这里展示 CGA 最终对外输出的状态。',
      status: '最终状态，例如 generated、clarification_required 或 generation_failed。',
      has_dsl: '是否已经生成 DSL。',
      has_cypher: '是否已经生成可提交的 Cypher。',
      clarification: '如果需要反问用户，这里记录澄清内容。',
    },
  },
};

function stageFieldHint(stageKey, section) {
  return {
    ...(stageFieldHints.__default?.[section] || {}),
    ...(stageFieldHints[stageKey]?.[section] || {}),
  };
}

function payloadFieldKeys(payload) {
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
    return [];
  }
  return Object.keys(payload).filter((key) => key !== '_summary');
}

const llmCallFieldKeys = new Set([
  'llm_calls',
  'llm_call',
  'llm_attempts',
  'llm_primary_attempts',
  'llm_secondary_attempts',
  'llm_disambiguation_attempts',
]);

function stripLlmCallsFromPayload(payload) {
  if (Array.isArray(payload)) {
    return payload.map((item) => stripLlmCallsFromPayload(item));
  }
  if (!payload || typeof payload !== 'object') {
    return payload;
  }
  return Object.fromEntries(
    Object.entries(payload)
      .filter(([key]) => !llmCallFieldKeys.has(key))
      .map(([key, value]) => [key, stripLlmCallsFromPayload(value)]),
  );
}

function looksLikeLlmCall(value) {
  return Boolean(
    value &&
      typeof value === 'object' &&
      !Array.isArray(value) &&
      (value.prompt || value.prompt_markdown || value.rendered_prompt || value.raw_output || value.raw_response || value.raw_text),
  );
}

function llmCallsFromValue(value) {
  if (!value) {
    return [];
  }
  if (Array.isArray(value)) {
    return value.flatMap((item) => (looksLikeLlmCall(item) ? [item] : llmCallsFromPayload(item)));
  }
  if (!value || typeof value !== 'object') {
    return [];
  }
  if (looksLikeLlmCall(value)) {
    return [value];
  }
  return llmCallsFromPayload(value);
}

function llmCallsFromPayload(payload) {
  if (!payload || typeof payload !== 'object') {
    return [];
  }
  const calls = [];
  for (const [key, value] of Object.entries(payload)) {
    calls.push(...llmCallsFromValue(value));
  }
  return calls;
}

function renderStageSectionHelp(stage, section, payload) {
  const hints = stageFieldHint(stage.key, section);
  const keys = payloadFieldKeys(payload);
  const items = keys.map((key) => {
    const description = hints[key] || commonFieldHints[key] || fallbackFieldHint(key, section);
    return `<div><dt>${escapeHtml(key)}</dt><dd>${escapeHtml(description)}</dd></div>`;
  });
  if (!items.length && !hints._summary) {
    return '';
  }
  return `
    <aside class="field-help">
      <h4>字段说明</h4>
      ${hints._summary ? `<p>${escapeHtml(hints._summary)}</p>` : ''}
      ${
        items.length ? `<dl>${items.join('')}</dl>` : `<p>下方没有结构化字段，或本阶段没有额外记录。</p>`
      }
    </aside>
  `;
}

function humanAnswerType(value) {
  const labels = {
    free_text: '自由文本回答',
    single_choice: '单选',
    multi_choice: '多选',
    confirmation: '确认',
  };
  return labels[value] || value || '';
}

function humanDecision(value) {
  const labels = {
    ask_user: '请求用户澄清',
    continue_with_assumption: '带假设继续',
    retry_llm: '回灌 LLM 修正',
    generation_failed: '生成失败',
    unsupported_query_shape: '不支持的查询形态',
  };
  return labels[value] || value || '';
}

function humanReason(value) {
  const labels = {
    literal_unresolved: '字面值无法解析',
    ambiguous_binding: '语义绑定存在歧义',
    coverage_missing: '问题中有实质词未覆盖',
    type_mismatch: '语义类型不匹配',
    unsupported_query_shape: '不支持的查询形态',
  };
  return labels[value] || value || '';
}

function compactParts(parts) {
  return parts.filter((part) => part !== null && part !== undefined && part !== '').join(' · ');
}

function optionLabel(option) {
  if (option && typeof option === 'object') {
    return option.label || option.summary || option.description || option.value || option.id || JSON.stringify(option);
  }
  return String(option ?? '');
}

function formatAlternatives(alternatives) {
  if (!Array.isArray(alternatives) || !alternatives.length) {
    return '';
  }
  return `候选：${alternatives.map(optionLabel).filter(Boolean).join('、')}`;
}

function renderClarificationList(title, items, formatter) {
  if (!Array.isArray(items) || !items.length) {
    return '';
  }
  return `
    <h3>${escapeHtml(title)}</h3>
    <div class="clarification-list">
      ${items.map((item) => formatter(item)).join('')}
    </div>
  `;
}

function renderClarificationItem(primary, meta, alternatives = '') {
  return `
    <article class="clarification-item">
      <strong>${escapeHtml(primary || '未命名项')}</strong>
      ${meta ? `<small>${escapeHtml(meta)}</small>` : ''}
      ${alternatives ? `<small>${escapeHtml(alternatives)}</small>` : ''}
    </article>
  `;
}

function renderUnresolvedItem(item = {}) {
  const meta = compactParts([
    item.expected ? `期望语义：${item.expected}` : '',
    item.code ? `原因：${item.code}` : '',
    item.value_index_miss ? 'value-index 未命中' : '',
  ]);
  return renderClarificationItem(item.term || item.literal || item.raw_literal, meta, formatAlternatives(item.alternatives));
}

function renderValidationError(item = {}) {
  const meta = compactParts([
    item.code ? `代码：${item.code}` : '',
    item.action ? `动作：${item.action}` : '',
    item.literal ? `字面值：${item.literal}` : '',
    item.property ? `期望语义：${item.property}` : '',
  ]);
  return renderClarificationItem(item.message || item.reason || item.code, meta, formatAlternatives(item.alternatives));
}

function renderClarificationBlock(clarification) {
  if (!clarification || typeof clarification !== 'object') {
    return '';
  }
  const options = Array.isArray(clarification.options) ? clarification.options : [];
  const sourceStage =
    clarification.source_stage_label_zh ||
    cgaStageTitles[clarification.source_stage] ||
    cgaStageTitles[clarification.source_step] ||
    clarification.source_stage ||
    clarification.source_step;
  const fieldCards = [
    optionalMetricCard('澄清问题', clarification.question_zh || clarification.question || clarification.user_message),
    optionalMetricCard('系统决策', humanDecision(clarification.decision), clarification.decision),
    optionalMetricCard('触发原因', humanReason(clarification.reason_code)),
    optionalMetricCard('触发阶段', sourceStage),
    optionalMetricCard('回答方式', humanAnswerType(clarification.expected_answer_type)),
  ].join('');
  const optionBlock = options.length
    ? `
      <h3>澄清选项</h3>
      <div class="clarification-options">${options
        .map((option) => `<span>${escapeHtml(optionLabel(option) || '未命名选项')}</span>`)
        .join('')}</div>
    `
    : clarification.no_option_reason
      ? `
        <h3>澄清选项</h3>
        <p class="empty">${escapeHtml(clarification.no_option_reason)}</p>
      `
      : '';
  return `
    <section class="clarification-box">
      <h3>澄清反问</h3>
      ${fieldCards ? `<div class="field-grid">${fieldCards}</div>` : ''}
      ${renderClarificationList('未解析项', clarification.unresolved_items, renderUnresolvedItem)}
      ${renderClarificationList('校验错误', clarification.validation_errors, renderValidationError)}
      ${optionBlock}
    </section>
  `;
}

function emptyCypherText(value) {
  return value ? value : '未生成可评测 Cypher';
}

function generationCypherText(section) {
  if (section.generation_status === 'clarification_required') {
    return '需要澄清';
  }
  return emptyCypherText(section.generated_cypher || section.parsed_cypher);
}

function tableCellValue(value) {
  if (value === null || value === undefined || value === '') {
    return '未记录';
  }
  if (typeof value === 'object') {
    return JSON.stringify(value, null, 2);
  }
  return String(value);
}

function renderTraceTable(table = {}) {
  const columns = Array.isArray(table.columns) ? table.columns : [];
  const rows = Array.isArray(table.rows) ? table.rows : [];
  if (!columns.length) {
    return '';
  }
  const colgroup = columns
    .map((column) => {
      const width = Number(column.width || 0);
      return width > 0 ? `<col style="width: ${width}px" />` : '<col />';
    })
    .join('');
  const emptyRow = `
    <tr>
      <td class="empty-cell" colspan="${columns.length}">未记录</td>
    </tr>
  `;
  return `
    <div class="trace-table-block">
      <h3>${escapeHtml(table.title_zh || '明细表')}</h3>
      <div class="trace-table-shell">
        <table class="trace-table">
          <colgroup>${colgroup}</colgroup>
          <thead>
            <tr>${columns.map((column) => `<th>${escapeHtml(column.label_zh || column.key || '字段')}</th>`).join('')}</tr>
          </thead>
          <tbody>
            ${
              rows.length
                ? rows
                    .map(
                      (row) => `
                        <tr>${columns
                          .map((column) => `<td>${escapeHtml(tableCellValue(row?.[column.key]))}</td>`)
                          .join('')}</tr>
                      `,
                    )
                    .join('')
                : emptyRow
            }
          </tbody>
        </table>
      </div>
    </div>
  `;
}

function renderOverview(detail) {
  const summary = detail.summary || {};
  const pipeline = detail.pipeline || {};
  const generator = pipeline.cypher_generator_agent || {};
  const testing = pipeline.testing_agent || {};
  const goldenCypher = generator.golden_cypher || testing.golden_cypher;
  const generatedCypher = generator.generated_cypher || generator.parsed_cypher || testing.actual_cypher;
  taskMeta.textContent = `${summary.id || detail.id} · ${summary.question || '未提供问题文本'}`;
  overviewGrid.innerHTML = [
    metricCard('自然语言问题', summary.question || '未提供', null, 'overview-card-question'),
    metricCard('最终结论', summary.final_verdict || detail.final_verdict, summary.final_verdict || detail.final_verdict, 'overview-card-kpi overview-card-status-only'),
    metricCard('生成状态', summary.generation_status || '未提供', summary.generation_status, 'overview-card-kpi overview-card-status-only'),
    metricCard('生成耗时', formatDurationMs(summary.cga_duration_ms ?? generator.cga_duration_ms), null, 'overview-card-kpi'),
    metricCard('难度', summary.difficulty || '未标注', null, 'overview-card-kpi'),
    metricCard('当前尝试次数', summary.attempt_no || 0, null, 'overview-card-kpi'),
    metricCard('当前阶段', summary.current_stage || 'pending', null, 'overview-card-kpi'),
    metricCard('澄清反问', summary.clarification_summary || '未触发澄清', null, 'overview-card-secondary'),
    metricCard('更新时间', summary.updated_at || detail.updated_at || '未提供', null, 'overview-card-secondary'),
    cypherOverviewCard('标准 Cypher', goldenCypher || '未提供', null, 'overview-card-code'),
    cypherOverviewCard('生成 Cypher', generationCypherText({ ...generator, generated_cypher: generatedCypher }), null, 'overview-card-code'),
  ].join('');
}

function renderCgaFlowSummary(flow = {}, section = {}) {
  const summary = flow.summary || {};
  return `
    <h3>CGA 全流程</h3>
    <div class="field-grid">
      ${metricCard('Trace ID', flow.trace_id || '未记录')}
      ${metricCard('Trace Schema', flow.schema_version || section.trace_schema_version || '未记录')}
      ${metricCard('最终状态', flow.final_status || section.generation_status || '未记录', flow.final_status || section.generation_status)}
      ${metricCard('语义模型', summary.semantic_model || '未记录')}
      ${metricCard('当前阶段', summary.current_stage_title_zh || summary.current_stage || '未记录')}
      ${metricCard('LLM 调用数', summary.llm_call_count ?? 0)}
      ${metricCard('开始时间', flow.started_at || '未记录')}
      ${metricCard('结束时间', flow.finished_at || '未记录')}
    </div>
  `;
}

function renderCgaFlowStages(flow = {}) {
  const stages = Array.isArray(flow.stages) ? flow.stages : [];
  const table = {
    title_zh: 'GraphTrace v1 阶段明细',
    columns: [
      { key: 'title_zh', label_zh: '阶段', width: 180 },
      { key: 'key', label_zh: 'stage key', width: 220 },
      { key: 'status', label_zh: '状态', width: 120 },
      { key: 'duration_ms', label_zh: '耗时 ms', width: 100 },
      { key: 'metrics_summary', label_zh: '阶段指标', width: 420 },
      { key: 'error_summary', label_zh: '错误 / 警告', width: 520 },
    ],
    rows: stages.map((stage) => ({
      ...stage,
      title_zh: stage.title_zh || cgaStageTitles[stage.key] || stage.key,
      metrics_summary: formatStageMetrics(stage.metrics),
      error_summary: [stage.errors?.length ? `errors=${stage.errors.length}` : '', stage.warnings?.length ? `warnings=${stage.warnings.length}` : '']
        .filter(Boolean)
        .join(' · ') || '无',
    })),
  };
  return `
    ${renderTraceTable(table)}
    ${stages
      .map((stage) => {
        const stageInput = stripLlmCallsFromPayload(stage.input);
        const stageOutput = stripLlmCallsFromPayload(stage.output);
        const stageMetrics = stripLlmCallsFromPayload(stage.metrics);
        return `
          <details class="trace-substep">
            <summary>
              <span>${escapeHtml(stage.title_zh || cgaStageTitles[stage.key] || stage.key || '未命名阶段')}</span>
              <span class="status-pill tone-${tone(stage.status)}">${escapeHtml(stage.status || 'unknown')}</span>
            </summary>
            <h3>阶段输入</h3>
            ${renderStageSectionHelp(stage, 'input', stageInput)}
            ${codeBlock(stageInput)}
            <h3>阶段输出</h3>
            ${renderStageSectionHelp(stage, 'output', stageOutput)}
            ${codeBlock(stageOutput)}
            ${renderStageLlmCalls(stage)}
            <h3>阶段指标 / 错误 / 警告</h3>
            ${renderStageSectionHelp(stage, 'metrics', { ...stageMetrics, errors: stage.errors, warnings: stage.warnings })}
            ${codeBlock({ metrics: stageMetrics, errors: stage.errors, warnings: stage.warnings })}
          </details>
        `;
      })
      .join('')}
  `;
}

function renderLlmCallCard(call, index, fallbackTitle = 'LLM 调用') {
  const stageTitle = call.stage_title_zh || cgaStageTitles[call.stage] || call.stage || `${fallbackTitle} ${index + 1}`;
  const prompt = call.prompt || call.prompt_markdown || call.rendered_prompt || '未记录';
  const rawOutput = call.raw_output || call.raw_response || call.raw_text || call.output || '未记录';
  const parsedOutput = call.parsed_output || call.payload || null;
  const error = call.error || (call.error_type ? { type: call.error_type, message: call.message } : null);
  return `
    <section class="llm-call-card">
      <div class="task-card-head">
        <strong>${escapeHtml(stageTitle)}</strong>
        <span class="status-pill tone-${tone(call.status || (error ? 'failed' : 'success'))}">${escapeHtml(call.status || (error ? 'failed' : 'success'))}</span>
      </div>
      <div class="field-grid">
        ${metricCard('Call ID', call.call_id || `llm-${index + 1}`)}
        ${metricCard('Schema', call.schema_name || '未记录')}
        ${metricCard('模型', call.model || '未记录')}
        ${metricCard('Attempt', call.attempt ?? '未记录')}
      </div>
      <h3>发给 LLM 的提示词</h3>
      ${codeBlock(prompt)}
      <h3>LLM 原始返回</h3>
      ${codeBlock(rawOutput)}
      <h3>解析后输出 / 错误</h3>
      ${codeBlock({ parsed_output: parsedOutput, error })}
    </section>
  `;
}

function renderStageLlmCalls(stage = {}) {
  const calls = [
    ...llmCallsFromPayload(stage.input),
    ...llmCallsFromPayload(stage.output),
    ...llmCallsFromPayload(stage.metrics),
  ];
  if (!calls.length) {
    return '';
  }
  return `
    <section class="stage-llm-calls">
      <h3>本阶段 LLM 调用</h3>
      ${calls.map((call, index) => renderLlmCallCard(call, index, stage.title_zh || cgaStageTitles[stage.key] || 'LLM 调用')).join('')}
    </section>
  `;
}

function renderCgaLlmCalls(flow = {}) {
  const calls = Array.isArray(flow.llm_calls) ? flow.llm_calls : [];
  const stages = Array.isArray(flow.stages) ? flow.stages : [];
  const stageCalls = stages.flatMap((stage) => [
    ...llmCallsFromPayload(stage.input),
    ...llmCallsFromPayload(stage.output),
    ...llmCallsFromPayload(stage.metrics),
  ]);
  if (stageCalls.length) {
    return '';
  }
  if (!calls.length) {
    return `
      <h3>LLM 调用明细</h3>
      <p class="empty">本次 CGA 主链路未触发 LLM 调用，或历史记录未保存 prompt/raw output。</p>
    `;
  }
  return `
    <h3>LLM 调用明细</h3>
    ${calls
      .map((call, index) => renderLlmCallCard(call, index))
      .join('')}
  `;
}

function renderCgaArtifacts(flow = {}) {
  const artifacts = flow.artifacts || {};
  return `
    <h3>DSL / Cypher / 自校验</h3>
    <div class="artifact-grid">
      <section>
        <h3>最终 DSL</h3>
        ${codeBlock(artifacts.dsl)}
      </section>
      <section>
        <h3>最终 Cypher</h3>
        ${codeBlock(artifacts.cypher)}
      </section>
      <section>
        <h3>Cypher 编译输出</h3>
        ${codeBlock(artifacts.compiler)}
      </section>
      <section>
        <h3>Cypher 自校验输出</h3>
        ${codeBlock(artifacts.self_validation)}
      </section>
      <section>
        <h3>用户可见说明</h3>
        ${codeBlock(artifacts.user_visible_notices || [])}
      </section>
      <section>
        <h3>失败 / 澄清输出</h3>
        ${codeBlock({ failure: artifacts.failure, clarification: artifacts.clarification })}
      </section>
    </div>
  `;
}

function renderCypherGenerator(section) {
  const flow = section.cga_flow || {};
  const hasFlow = Array.isArray(flow.stages) && flow.stages.length;
  return `
    <details class="pipeline-step" open>
      <summary>
        <span>cypher-generator-agent</span>
        <span class="status-pill tone-${tone(section.generation_status)}">${escapeHtml(section.generation_status || 'unknown')}</span>
      </summary>
      ${renderClarificationBlock(section.clarification)}
      ${
        hasFlow
          ? `
            ${renderCgaFlowSummary(flow, section)}
            ${renderCgaFlowStages(flow)}
            ${renderCgaLlmCalls(flow)}
            ${renderCgaArtifacts(flow)}
          `
          : `
            <h3>CGA 全流程</h3>
            <p class="empty">未读取到 cga_graph_trace_v1 快照。以下仅保留原始生成证据。</p>
            ${codeBlock(section.prompt_markdown)}
          `
      }
      <h3>生成运行 ID</h3>
      ${codeBlock(section.generation_run_id || '未提供')}
    </details>
  `;
}

function renderTestingAgent(section) {
  const grammar = section.grammar || {};
  const executionAccuracy = section.execution_accuracy || {};
  const semanticReview = section.semantic_review || {};
  const secondary = section.secondary_metrics || {};
  const evaluationTone = grammar.score === undefined && executionAccuracy.score === undefined
    ? 'pending'
    : ((grammar.score === 1 && executionAccuracy.score === 1) ? 'passed' : 'failed');
  return `
    <details class="pipeline-step" open>
      <summary>
        <span>testing-agent</span>
        <span class="status-pill tone-${tone(evaluationTone)}">grammar ${escapeHtml(grammar.score ?? '未评测')}</span>
      </summary>
      <div class="field-grid">
        ${metricCard('grammar score', `${grammar.score ?? '未评测'}（0 = 未通过，1 = 通过）`)}
        ${metricCard('grammar 原因', grammar.parser_error || grammar.message || '无')}
        ${metricCard('EX 得分', executionAccuracy.score ?? '未评测')}
        ${metricCard('EX 原因', executionAccuracy.reason || '未提供')}
        ${metricCard('严格比较结果', (section.strict_check || {}).status || 'not_run')}
        ${metricCard('GLEU', secondary.gleu ?? '未计算')}
        ${metricCard('similarity', secondary.similarity ?? '未计算')}
      </div>
      <h3>golden Cypher</h3>
      ${codeBlock(section.golden_cypher)}
      <h3>golden answer</h3>
      ${codeBlock(section.golden_answer)}
      <h3>actual Cypher</h3>
      ${codeBlock(section.actual_cypher)}
      <h3>执行结果</h3>
      ${codeBlock(section.execution)}
      <h3>严格比较差异</h3>
      ${codeBlock((section.strict_check || {}).evidence || section.strict_check)}
      <h3>语义评判 prompt</h3>
      ${codeBlock(semanticReview.prompt)}
      <h3>语义评判原始返回</h3>
      ${codeBlock(semanticReview.raw_output)}
      <h3>语义评判结构化结果</h3>
      ${codeBlock({
        status: semanticReview.status,
        payload: semanticReview.payload,
        judgement: semanticReview.judgement,
        reasoning: semanticReview.reasoning,
        message: semanticReview.message,
      })}
      <h3>improvement</h3>
      ${codeBlock(section.improvement)}
    </details>
  `;
}

function renderPipeline(detail) {
  const pipeline = detail.pipeline || {};
  pipelineView.innerHTML = [
    renderCypherGenerator(pipeline.cypher_generator_agent || {}),
    renderTestingAgent(pipeline.testing_agent || {}),
  ].join('');
}

function taskIdFromLocation() {
  const parts = window.location.pathname.split('/').filter(Boolean);
  return decodeURIComponent(parts[parts.length - 1] || '');
}

async function loadTaskDetail() {
  const taskId = taskIdFromLocation();
  if (!taskId) {
    throw new Error('missing task id');
  }
  const response = await fetch(`/api/v1/tasks/${encodeURIComponent(taskId)}`);
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`);
  }
  const payload = await response.json();
  renderOverview(payload);
  renderPipeline(payload);
}

loadTaskDetail().catch((error) => {
  taskMeta.textContent = `详情加载失败: ${String(error)}`;
  setPipelineMessage(`详情加载失败: ${String(error)}`);
});
