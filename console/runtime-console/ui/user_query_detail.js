const userQueryDetailMeta = document.getElementById('user-query-detail-meta');
const userQueryDetail = document.getElementById('user-query-detail');

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

function codeBlock(value) {
  return `<pre>${escapeHtml(pretty(value))}</pre>`;
}

function tone(status) {
  switch (status) {
    case 'generated':
    case 'completed':
    case 'ok':
      return 'ok';
    case 'service_failed':
    case 'query_failed':
    case 'failed':
      return 'danger';
    case 'generation_failed':
    case 'clarification_required':
    case 'unsupported_query_shape':
      return 'warn';
    default:
      return 'neutral';
  }
}

function statusLabel(status) {
  return {
    completed: '查询完成',
    query_failed: '查询失败',
    generated: '生成成功',
    generation_failed: '生成失败',
    clarification_required: '需要澄清',
    unsupported_query_shape: '不支持的查询形态',
    service_failed: '服务失败',
  }[status] || status || '未记录';
}

function formatDurationMs(value) {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    return '未记录';
  }
  if (value >= 1000) {
    const seconds = Math.round((value / 1000) * 10) / 10;
    return `${seconds} s`;
  }
  return `${Math.round(value)} ms`;
}

function rawTuGraphError(tugraphResponse) {
  if (!tugraphResponse || typeof tugraphResponse !== 'object') {
    return '';
  }
  const rawError = tugraphResponse.error_message || tugraphResponse.error || tugraphResponse.errors;
  return rawError ? pretty(rawError) : '';
}

function hasTuGraphResponse(record) {
  return Boolean(record && (record.has_tugraph_response || record.tugraph_response || record.tugraph_execution));
}

function renderTugraphDownloadLink(record, className = 'download-link') {
  if (!hasTuGraphResponse(record)) {
    return `<span class="${escapeHtml(className)} is-disabled" aria-disabled="true">无 TuGraph 结果</span>`;
  }
  const downloadUrl = `/api/v1/user-queries/${encodeURIComponent(record.user_query_id)}/download`;
  return `<a class="${escapeHtml(className)}" href="${escapeHtml(downloadUrl)}">下载 TuGraph JSON</a>`;
}

function previewColumns(rows) {
  const columns = [];
  rows.forEach((row) => {
    if (!row || typeof row !== 'object' || Array.isArray(row)) {
      if (!columns.includes('value')) {
        columns.push('value');
      }
      return;
    }
    Object.keys(row).forEach((key) => {
      if (!columns.includes(key)) {
        columns.push(key);
      }
    });
  });
  return columns;
}

function cellValue(row, column) {
  if (!row || typeof row !== 'object' || Array.isArray(row)) {
    return column === 'value' ? row : '';
  }
  return row[column];
}

function renderPreviewTable(preview) {
  const rows = (preview && preview.rows) || [];
  if (!rows.length) {
    return '<p class="empty">无可展示结果行。</p>';
  }
  const columns = previewColumns(rows);
  return `
    <div class="table-shell query-result-table">
      <table>
        <thead>
          <tr>${columns.map((column) => `<th>${escapeHtml(column)}</th>`).join('')}</tr>
        </thead>
        <tbody>
          ${rows
            .map(
              (row) => `
                <tr>
                  ${columns.map((column) => `<td>${escapeHtml(pretty(cellValue(row, column)))}</td>`).join('')}
                </tr>
              `
            )
            .join('')}
        </tbody>
      </table>
    </div>
  `;
}

function renderRawTuGraphResponse(tugraphResponse) {
  if (!tugraphResponse) {
    return '<p class="empty">未收到 TuGraph 返回。</p>';
  }
  const rawText = pretty(tugraphResponse);
  const maxChars = 12000;
  if (rawText.length <= maxChars) {
    return codeBlock(tugraphResponse);
  }
  const partialText = `${rawText.slice(0, maxChars)}\n... 超出限制，下载 TuGraph JSON 可查看全量返回。`;
  return `
    <p class="limit-notice">原始返回超出页面展示限制，以下仅显示前 ${escapeHtml(maxChars)} 个字符。</p>
    ${codeBlock(partialText)}
  `;
}

function renderCgaDiagnostic(diagnostic) {
  const data = diagnostic || {status: 'failed', title: '诊断生成失败', error_message: '未收到诊断结果。'};
  const status = data.status || 'failed';
  if (status === 'failed') {
    return `
      <section class="query-block diagnostic-block tone-danger">
        <h3>结果诊断</h3>
        <div class="diagnostic-title-row">
          <strong>诊断生成失败</strong>
          <span class="status-pill tone-danger">诊断失败</span>
        </div>
        <p>当前无法生成面向业务用户的诊断说明，请展开技术详情或联系支持人员查看 CGA 运行信息。</p>
        <p class="limit-notice danger-text">失败原因：${escapeHtml(data.error_message || '未记录')}</p>
      </section>
    `;
  }
  return `
    <section class="query-block diagnostic-block">
      <h3>结果诊断</h3>
      <div class="diagnostic-title-row">
        <strong>${escapeHtml(data.title || '查询反馈')}</strong>
        <span class="status-pill tone-${tone(status === 'not_required' ? 'completed' : 'generation_failed')}">${escapeHtml(status === 'not_required' ? '无需诊断' : '已生成诊断')}</span>
      </div>
      <p>${escapeHtml(data.summary || '暂无诊断摘要。')}</p>
      ${data.main_reason ? `<div class="diagnostic-section"><h4>主要原因</h4><p>${escapeHtml(data.main_reason)}</p></div>` : ''}
      ${renderSuggestedQuestions(data.suggested_questions)}
    </section>
  `;
}

function renderSuggestedQuestions(items) {
  if (!Array.isArray(items) || !items.length) {
    return '';
  }
  return `
    <div class="diagnostic-section">
      <h4>建议改问</h4>
      <ul>${items.map((item) => `<li>${escapeHtml(item)}</li>`).join('')}</ul>
    </div>
  `;
}

function renderCgaEvidence(record) {
  const cgaGeneration = record.cga_generation || null;
  const trace = cgaGeneration && typeof cgaGeneration === 'object' ? cgaGeneration.trace || {} : {};
  const finalOutputs = trace.final_outputs || {};
  return `
    <details class="query-block cga-evidence-block">
      <summary>CGA 落盘信息</summary>
      <div class="trace-substep">
        <h3>CGA 全流程</h3>
        <div class="query-metrics">
          <article class="overview-card"><div><h3>Trace ID</h3><p>${escapeHtml(trace.trace_id || record.generation_run_id || '未记录')}</p></div></article>
          <article class="overview-card"><div><h3>Trace Schema</h3><p>${escapeHtml(trace.trace_schema_version || '未记录')}</p></div></article>
          <article class="overview-card"><div><h3>最终状态</h3><p>${escapeHtml(trace.final_status || record.generation_status || '未记录')}</p></div></article>
          <article class="overview-card"><div><h3>阶段数量</h3><p>${escapeHtml(Array.isArray(trace.stages) ? trace.stages.length : 0)}</p></div></article>
        </div>
      </div>
      <div class="trace-substep">
        <h3>GraphTrace v1 阶段明细</h3>
        ${renderCgaStageTable(trace.stages)}
      </div>
      <div class="trace-substep">
        <h3>用户可见提示 / 失败与澄清</h3>
        ${codeBlock({
          user_visible_notices: cgaGeneration && typeof cgaGeneration === 'object' ? cgaGeneration.user_visible_notices || finalOutputs.user_visible_notices || [] : [],
          failure: finalOutputs.failure || (cgaGeneration && typeof cgaGeneration === 'object' ? cgaGeneration.failure : null),
          clarification: finalOutputs.clarification || (cgaGeneration && typeof cgaGeneration === 'object' ? cgaGeneration.clarification : null),
        })}
      </div>
      <details class="trace-substep">
        <summary>原始 CGA JSON</summary>
        ${codeBlock(record.cga_generation || record.cga_error || '未记录')}
      </details>
    </details>
  `;
}

function renderCgaStageTable(stages) {
  if (!Array.isArray(stages) || !stages.length) {
    return '<p class="empty">未读取到阶段明细。</p>';
  }
  return `
    <div class="trace-table-shell">
      <table class="trace-table">
        <thead>
          <tr>
            <th>阶段</th>
            <th>状态</th>
            <th>耗时</th>
            <th>错误 / 警告</th>
          </tr>
        </thead>
        <tbody>
          ${stages
            .map(
              (stage) => `
                <tr>
                  <td>${escapeHtml(stage.stage || '未记录')}</td>
                  <td><span class="status-pill tone-${tone(stage.status)}">${escapeHtml(stage.status || '未记录')}</span></td>
                  <td>${escapeHtml(formatDurationMs(stage.duration_ms))}</td>
                  <td>${escapeHtml(stageIssueSummary(stage))}</td>
                </tr>
              `
            )
            .join('')}
        </tbody>
      </table>
    </div>
  `;
}

function stageIssueSummary(stage) {
  const errors = Array.isArray(stage.errors) ? stage.errors : [];
  const warnings = Array.isArray(stage.warnings) ? stage.warnings : [];
  const parts = [];
  if (errors.length) {
    parts.push(`errors=${errors.length}: ${errors.map((item) => item.message || item.code || item.type || '').filter(Boolean).join('；')}`);
  }
  if (warnings.length) {
    parts.push(`warnings=${warnings.length}: ${warnings.map((item) => item.message || item.code || item.type || '').filter(Boolean).join('；')}`);
  }
  return parts.join('\n') || '无';
}

function userQueryIdFromLocation() {
  const parts = window.location.pathname.split('/').filter(Boolean);
  return decodeURIComponent(parts[parts.length - 1] || '');
}

function renderUserQueryDetail(record) {
  const preview = record.result_preview || {};
  const tugraphResponse = record.tugraph_response || record.tugraph_execution || null;
  const errorMessage = rawTuGraphError(tugraphResponse);
  const truncatedNotice = preview.truncated
    ? `<p class="limit-notice">超出页面展示限制，已展示前 ${escapeHtml(preview.preview_row_limit)} 行，可下载 TuGraph JSON 查看完整结果。</p>`
    : '';
  userQueryDetailMeta.textContent = `${record.user_query_id} · ${record.updated_at || record.created_at || '未记录时间'}`;
  userQueryDetail.innerHTML = `
    <div class="query-result-head">
      <div>
        <h3>${escapeHtml(record.question || '未提供问题')}</h3>
        <p>${escapeHtml(record.user_query_id)} · ${escapeHtml(record.updated_at || record.created_at || '')}</p>
      </div>
      ${renderTugraphDownloadLink(record)}
    </div>
    <div class="query-metrics">
      <article class="overview-card">
        <div>
          <h3>查询状态</h3>
          <p><span class="status-pill tone-${tone(record.status)}">${escapeHtml(statusLabel(record.status))}</span></p>
        </div>
      </article>
      <article class="overview-card">
        <div>
          <h3>生成状态</h3>
          <p><span class="status-pill tone-${tone(record.generation_status)}">${escapeHtml(statusLabel(record.generation_status))}</span></p>
        </div>
      </article>
      <article class="overview-card">
        <div>
          <h3>TuGraph 行数</h3>
          <p>${escapeHtml(preview.row_count ?? 0)}</p>
        </div>
      </article>
      <article class="overview-card">
        <div>
          <h3>生成耗时</h3>
          <p>${escapeHtml(formatDurationMs(record.cga_elapsed_ms))}</p>
        </div>
      </article>
    </div>
    ${renderCgaDiagnostic(record.cga_diagnostic)}
    <div class="query-block">
      <h3>生成 Cypher</h3>
      ${codeBlock(record.generated_cypher || '未生成 Cypher')}
    </div>
    ${errorMessage ? `<p class="limit-notice danger-text">${escapeHtml(errorMessage)}</p>` : ''}
    <div class="query-block">
      <h3>TuGraph 查询结果</h3>
      ${renderRawTuGraphResponse(tugraphResponse)}
    </div>
    <div class="query-block">
      <h3>结果预览</h3>
      ${truncatedNotice}
      ${renderPreviewTable(preview)}
    </div>
    ${renderCgaEvidence(record)}
  `;
}

async function loadUserQueryDetail() {
  const userQueryId = userQueryIdFromLocation();
  if (!userQueryId) {
    throw new Error('missing user query id');
  }
  const response = await fetch(`/api/v1/user-queries/${encodeURIComponent(userQueryId)}`);
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`);
  }
  const payload = await response.json();
  renderUserQueryDetail(payload);
}

loadUserQueryDetail().catch((error) => {
  userQueryDetailMeta.textContent = `详情加载失败: ${String(error)}`;
  userQueryDetail.innerHTML = `<p class="empty">详情加载失败: ${escapeHtml(String(error))}</p>`;
});
