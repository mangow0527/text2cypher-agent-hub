const taskMeta = document.getElementById('task-meta');
const overviewGrid = document.getElementById('overview-grid');
const pipelineView = document.getElementById('pipeline-view');

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
    case 'generation_failed':
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

function metricCard(label, value, status = null) {
  return `
    <article class="overview-card">
      <div>
        <h3>${escapeHtml(label)}</h3>
        <p>${escapeHtml(value ?? '未提供')}</p>
      </div>
      ${status === null ? '' : `<span class="status-pill tone-${tone(status)}">${escapeHtml(status)}</span>`}
    </article>
  `;
}

function codeBlock(value) {
  return `<pre>${escapeHtml(pretty(value))}</pre>`;
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

function emptyCypherText(value) {
  return value ? value : '未生成可评测 Cypher';
}

function generationCypherText(section) {
  if (section.generation_status === 'clarification_required') {
    return '需要澄清';
  }
  return emptyCypherText(section.generated_cypher || section.parsed_cypher);
}

function renderCgaLlmPrompt(item, fallbackTitle, fallbackRawOutputTitle) {
  const title = item?.title_zh || fallbackTitle;
  const rawOutputTitle = item?.raw_output_title_zh || fallbackRawOutputTitle;
  const attempts = Array.isArray(item?.attempts) ? item.attempts : [];
  if (attempts.length) {
    return attempts
      .map((attempt, index) => {
        const suffix = attempt.call_id ? ` · ${attempt.call_id}` : ` #${index + 1}`;
        const meta = [
          attempt.stage ? `阶段：${attempt.stage}` : null,
          attempt.model ? `模型：${attempt.model}` : null,
          attempt.accepted === true ? '采用：是' : null,
          attempt.accepted === false ? '采用：否' : null,
          attempt.rejected_reason ? `拒绝原因：${attempt.rejected_reason}` : null,
        ].filter(Boolean).join(' · ');
        return `
          <h3>${escapeHtml(`${title}${suffix}`)}</h3>
          ${meta ? `<p class="empty">${escapeHtml(meta)}</p>` : ''}
          ${codeBlock(attempt.prompt || item?.empty_label_zh || '本次未触发')}
          <h3>${escapeHtml(rawOutputTitle)}</h3>
          ${codeBlock(attempt.raw_output || item?.empty_raw_output_label_zh || '本次未触发或未记录返回')}
        `;
      })
      .join('');
  }
  const body = item?.triggered ? item.prompt : (item?.empty_label_zh || '本次未触发');
  const rawOutputBody = item?.triggered ? (item.raw_output || item?.empty_raw_output_label_zh || '本次未触发或未记录返回') : (item?.empty_label_zh || '本次未触发');
  return `
    <h3>${escapeHtml(title)}</h3>
    ${codeBlock(body)}
    <h3>${escapeHtml(rawOutputTitle)}</h3>
    ${codeBlock(rawOutputBody)}
  `;
}

function renderCgaLlmPrompts(prompts = {}) {
  const traceV2Order = [
    ['intent_primary_classification', '意图识别：一级分类 LLM 判定', '意图识别：一级分类 LLM 原始返回'],
    ['intent_secondary_classification', '意图识别：二级分类 LLM 判定', '意图识别：二级分类 LLM 原始返回'],
    ['intent_recognition_fallback', '意图识别 LLM 兜底提示词', '意图识别 LLM 原始返回'],
    ['semantic_view_disambiguation', '语义视图匹配：受控 LLM 消歧', '语义视图匹配：受控 LLM 消歧原始返回'],
    ['cypher_generation_fallback', 'Renderer 失败后的 Cypher 兜底生成', 'Cypher 兜底生成 LLM 原始返回'],
  ];
  const hasTraceV2Prompts = traceV2Order
    .slice(0, 3)
    .some(([key]) => Object.prototype.hasOwnProperty.call(prompts, key));
  if (hasTraceV2Prompts) {
    return traceV2Order
      .map(([key, title, rawTitle]) => renderCgaLlmPrompt(prompts[key], title, rawTitle))
      .join('');
  }
  return [
    renderCgaLlmPrompt(prompts.intent_recognition_fallback, '意图识别 LLM 兜底提示词', '意图识别 LLM 原始返回'),
    renderCgaLlmPrompt(prompts.cypher_generation_fallback, 'Renderer 失败后的 Cypher 兜底提示词', 'Cypher 生成 LLM 原始返回'),
  ].join('');
}

function chainMetric(label, item, valueKey = 'label_zh') {
  const value = item && typeof item === 'object' ? item[valueKey] : item;
  const raw = item && typeof item === 'object' ? item.value || item.reason || item.decision || item.source : null;
  const rawText = raw ? `（原始值：${raw}）` : '';
  return metricCard(label, `${value || '未记录'}${rawText}`);
}

const cgaTraceLayerTitles = {
  orchestration: '服务编排层',
  intent_recognition: '意图识别层',
  semantic_view_matching: '语义视图匹配层',
  planning: '规划层',
  generation: '生成与提交层',
};

function renderLegacyCgaChainSummary(chain) {
  const intent = chain.intent || {};
  const validation = chain.validation || {};
  const knowledge = chain.knowledge || {};
  const preflight = chain.preflight || {};
  return `
    <h3>生成链路摘要</h3>
    <div class="field-grid">
      ${chainMetric('生成状态', chain.generation_status)}
      ${chainMetric('生成方式', chain.generation_mode)}
      ${chainMetric('生成门禁', chain.gate)}
      ${chainMetric('失败原因', chain.failure_reason)}
      ${metricCard('意图识别', `${intent.decision_label_zh || '未记录'} · ${intent.source || '未知来源'} · 置信度 ${intent.confidence ?? '未记录'}`)}
      ${metricCard('意图类型', [intent.primary_intent, intent.secondary_intent].filter(Boolean).join(' / ') || '未记录')}
      ${metricCard('语义校验', validation.label_zh || '未记录')}
      ${metricCard('知识选择', `${knowledge.source_label_zh || '未记录'} · ${(knowledge.selection_trace || []).length} 条 trace`)}
      ${metricCard('预检结果', `${preflight.label_zh || '未记录'}${preflight.reason_label_zh ? ` · ${preflight.reason_label_zh}` : ''}`)}
    </div>
  `;
}

function renderCgaTraceLayers(layers = []) {
  if (!layers.length) {
    return '';
  }
  return `
    <h3>CGA 分层链路</h3>
    ${layers
      .map((layer) => {
        const fields = Array.isArray(layer.fields) ? layer.fields : [];
        const title = layer.title_zh || cgaTraceLayerTitles[layer.key] || layer.key || '未命名层级';
        return `
          <h3>${escapeHtml(title)}</h3>
          <div class="field-grid">
            ${fields.map((field) => metricCard(field.label_zh || '字段', inlineValue(field.value))).join('')}
          </div>
          <h3>${escapeHtml(title)}原始证据</h3>
          ${codeBlock(layer.raw || {})}
        `;
      })
      .join('')}
  `;
}

function renderOverview(detail) {
  const summary = detail.summary || {};
  taskMeta.textContent = `${summary.id || detail.id} · ${summary.question || '未提供问题文本'}`;
  overviewGrid.innerHTML = [
    metricCard('自然语言问题', summary.question || '未提供'),
    metricCard('难度', summary.difficulty || '未标注'),
    metricCard('当前尝试次数', summary.attempt_no || 0),
    metricCard('生成状态', summary.generation_status || '未提供', summary.generation_status),
    metricCard('最终结论', summary.final_verdict || detail.final_verdict, summary.final_verdict || detail.final_verdict),
    metricCard('当前阶段', summary.current_stage || 'pending'),
    metricCard('更新时间', summary.updated_at || detail.updated_at || '未提供'),
  ].join('');
}

function renderCypherGenerator(section) {
  const chain = section.chain_summary || {};
  const traceLayers = Array.isArray(section.trace_layers) ? section.trace_layers : [];
  return `
    <details class="pipeline-step" open>
      <summary>
        <span>cypher-generator-agent</span>
        <span class="status-pill tone-${tone(section.generation_status)}">${escapeHtml(section.generation_status || 'unknown')}</span>
      </summary>
      <h3>生成对照</h3>
      <h3>自然语言问题</h3>
      ${codeBlock(section.question || '未提供')}
      <h3>标准 Cypher</h3>
      ${codeBlock(section.golden_cypher)}
      <h3>生成 Cypher</h3>
      ${codeBlock(generationCypherText(section))}
      <h3>LLM 调用提示词</h3>
      ${renderCgaLlmPrompts(section.llm_prompts || {})}
      ${traceLayers.length ? renderCgaTraceLayers(traceLayers) : renderLegacyCgaChainSummary(chain)}
      <h3>生成运行 ID</h3>
      ${codeBlock(section.generation_run_id || '未提供')}
      <h3>CGA 分层证据快照</h3>
      ${codeBlock(section.prompt_markdown)}
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

function renderRepairAgent(section) {
  const repairState = section.repair_state || {};
  const applyState = section.knowledge_apply_state || {};
  const redispatchState = section.redispatch_state || {};
  const statusLabel = repairState.label_zh || section.status || (section.analysis_id ? 'recorded' : 'not recorded');
  const statusTone = repairState.value || (section.status === 'not_repairable' ? 'not_repairable' : (section.analysis_id ? 'applied' : 'pending'));
  const nonRepairableReason = section.status === 'not_repairable'
    ? `
      <h3>不修复原因</h3>
      ${codeBlock(section.non_repairable_reason || 'repair-agent 判定该问题不是 knowledge-agent 知识缺口。')}
    `
    : '';
  return `
    <details class="pipeline-step" open>
      <summary>
        <span>repair-agent</span>
        <span class="status-pill tone-${tone(statusTone)}">${escapeHtml(statusLabel)}</span>
      </summary>
      <h3>repair 状态</h3>
      <div class="field-grid">
        ${metricCard('诊断状态', `${repairState.label_zh || '未记录'}${repairState.raw_status ? `（原始值：${repairState.raw_status}）` : ''}`, repairState.value)}
        ${metricCard('知识应用状态', `${applyState.label_zh || '未记录'}${applyState.raw_status ? `（原始值：${applyState.raw_status}）` : ''}`, applyState.value)}
        ${metricCard('redispatch 状态', `${redispatchState.label_zh || '未记录'}${redispatchState.reason ? `（原因：${redispatchState.reason}）` : ''}`, redispatchState.value)}
        ${metricCard('applied 原始标记', section.applied ?? '未记录')}
      </div>
      ${repairState.message ? `<h3>诊断状态说明</h3>${codeBlock(repairState.message)}` : ''}
      ${applyState.message ? `<h3>知识应用说明</h3>${codeBlock(applyState.message)}` : ''}
      ${redispatchState.message ? `<h3>redispatch 说明</h3>${codeBlock(redispatchState.message)}` : ''}
      <h3>发给诊断大模型的完整提示词</h3>
      ${codeBlock(section.llm_prompt_markdown)}
      <h3>大模型原始返回</h3>
      ${codeBlock(section.raw_output)}
      ${nonRepairableReason}
      <h3>发送给 knowledge-agent 的报文</h3>
      ${codeBlock(section.knowledge_agent_request)}
      <h3>knowledge-agent 响应</h3>
      ${codeBlock(section.knowledge_agent_response)}
    </details>
  `;
}

function renderPipeline(detail) {
  const pipeline = detail.pipeline || {};
  pipelineView.innerHTML = [
    renderCypherGenerator(pipeline.cypher_generator_agent || {}),
    renderTestingAgent(pipeline.testing_agent || {}),
    renderRepairAgent(pipeline.repair_agent || {}),
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
