const difficultyGrid = document.getElementById('difficulty-grid');
const taskTableBody = document.getElementById('task-table-body');
const difficultyFilter = document.getElementById('difficulty-filter');
const idSearch = document.getElementById('id-search');
const tableMeta = document.getElementById('table-meta');
const pageSizeSelect = document.getElementById('page-size');
const prevPageButton = document.getElementById('prev-page');
const nextPageButton = document.getElementById('next-page');
const pageIndicator = document.getElementById('page-indicator');
const runtimeCenterView = document.getElementById('runtime-center-view');
const userQueryView = document.getElementById('user-query-view');
const runtimeViewTab = document.getElementById('runtime-view-tab');
const userQueryViewTab = document.getElementById('user-query-view-tab');
const userQueryForm = document.getElementById('user-query-form');
const queryQuestion = document.getElementById('query-question');
const userQueryStatus = document.getElementById('user-query-status');
const userQueryResult = document.getElementById('user-query-result');
const userQueryHistory = document.getElementById('user-query-history');
const userQueryProgress = document.getElementById('user-query-progress');
const userQueryProgressText = document.getElementById('user-query-progress-text');
const userQueryProgressPercent = document.getElementById('user-query-progress-percent');
const userQueryProgressFill = document.getElementById('user-query-progress-fill');
const userQueryProgressSteps = document.getElementById('user-query-progress-steps');

const generationLabels = {
  generated: '生成成功',
  generation_pending: '生成中',
  clarification_required: '需要澄清',
  generation_failed: '生成失败',
  unsupported_query_shape: '不支持的查询形态',
  service_failed: '服务失败',
};

const verdictLabels = {
  pass: '通过',
  fail: '失败',
  clarification_required: '需要澄清',
  unsupported_query_shape: '不支持',
  pending: '待定',
};

const verdictColors = {
  pass: '#177245',
  fail: '#af3d36',
  clarification_required: '#946200',
  unsupported_query_shape: '#7657a6',
  pending: '#8a8177',
};

let currentPage = 1;
let currentPagination = {
  page: 1,
  page_size: 20,
  total: 0,
  total_pages: 1,
  has_previous: false,
  has_next: false,
};
let activeUserQueryPollId = '';

const queryProgressSteps = [
  {key: 'submit', label: '提交问题', percent: 16},
  {key: 'cga', label: 'CGA 生成 Cypher', percent: 42},
  {key: 'tugraph', label: 'TuGraph 查询', percent: 68},
  {key: 'diagnostic', label: '诊断生成', percent: 86},
  {key: 'feedback', label: '反馈整理', percent: 100},
];

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

function tone(status) {
  switch (status) {
    case 'pass':
    case 'passed':
    case 'generated':
    case 'ok':
    case 'completed':
      return 'ok';
    case 'fail':
    case 'failed':
    case 'service_failed':
    case 'query_failed':
      return 'danger';
    case 'unsupported_query_shape':
      return 'neutral';
    case 'running':
    case 'pending':
    case 'generation_pending':
    case 'generation_failed':
    case 'clarification_required':
      return 'warn';
    default:
      return 'neutral';
  }
}

function percentage(value, total) {
  if (!total) {
    return 0;
  }
  return Math.round((value / total) * 1000) / 10;
}

function pieStyle(bucket) {
  const total = bucket.total || 0;
  if (!total) {
    return 'background: #ece2d3;';
  }
  const segments = Object.keys(verdictLabels)
    .map((key) => ({key, value: bucket[key] || 0, color: verdictColors[key] || '#8a8177'}))
    .filter((item) => item.value > 0);
  if (!segments.length) {
    return 'background: #ece2d3;';
  }
  let cursor = 0;
  const stops = segments.map((item) => {
    const end = cursor + percentage(item.value, total);
    const stop = `${item.color} ${cursor}% ${end}%`;
    cursor = end;
    return stop;
  });
  if (cursor < 100) {
    stops.push(`#ece2d3 ${cursor}% 100%`);
  }
  return `background: conic-gradient(${stops.join(', ')});`;
}

function renderDifficultyCounts(bucket, statuses) {
  return statuses
    .map(({key, label_zh}) => {
      const label = verdictLabels[key] || label_zh || key;
      return `<span><i class="legend-dot" style="background: ${escapeHtml(verdictColors[key] || '#8a8177')}"></i>${escapeHtml(label)}: ${escapeHtml(bucket[key] || 0)}</span>`;
    })
    .join('');
}

function renderDifficultyDuration(bucket) {
  const count = bucket.cga_duration_count || 0;
  return `
    <div class="difficulty-duration-card">
      <span>平均生成耗时</span>
      <strong>${escapeHtml(formatDurationMs(bucket.avg_cga_duration_ms))}</strong>
      <small>${count ? `${escapeHtml(count)} 条已记录` : '暂无已记录样本'}</small>
    </div>
  `;
}

function renderDifficultySummary(summary) {
  const buckets = summary.buckets || [];
  const statuses = summary.statuses || Object.keys(verdictLabels).map((key) => ({key, label_zh: verdictLabels[key]}));
  if (!buckets.length) {
    difficultyGrid.innerHTML = '<p class="empty">暂无难度统计数据。</p>';
    return;
  }
  difficultyGrid.innerHTML = buckets
    .map(
      (bucket) => `
        <article class="difficulty-card">
          <div class="difficulty-card-head">
            <strong>${escapeHtml(bucket.difficulty)}</strong>
            <span>${escapeHtml(bucket.total)} 个样本</span>
          </div>
          <div class="pie" style="${pieStyle(bucket)}" aria-label="${escapeHtml(bucket.difficulty)} 最终结论分布"></div>
          ${renderDifficultyDuration(bucket)}
          <div class="difficulty-counts">
            ${renderDifficultyCounts(bucket, statuses)}
          </div>
        </article>
      `
    )
    .join('');
}

function renderPagination() {
  const page = currentPagination.page || 1;
  const totalPages = currentPagination.total_pages || 1;
  pageIndicator.textContent = `第 ${page} / ${totalPages} 页`;
  prevPageButton.disabled = !currentPagination.has_previous;
  nextPageButton.disabled = !currentPagination.has_next;
}

function renderTaskTable(tasks) {
  if (!tasks.length) {
    taskTableBody.innerHTML = '<tr><td colspan="9" class="empty-cell">暂无符合新契约的运行任务。</td></tr>';
    tableMeta.textContent = `共 ${currentPagination.total || 0} 个任务，当前页 0 个`;
    renderPagination();
    return;
  }
  tableMeta.textContent = `共 ${currentPagination.total} 个任务，当前页 ${tasks.length} 个`;
  taskTableBody.innerHTML = tasks
    .map(
      (task) => `
        <tr data-task-id="${escapeHtml(task.id)}" tabindex="0">
          <td>${escapeHtml(task.difficulty)}</td>
          <td><strong>${escapeHtml(task.id)}</strong></td>
          <td><span class="status-pill tone-${tone(task.generation_status)}">${escapeHtml(generationLabels[task.generation_status])}</span></td>
          <td><span class="status-pill tone-${tone(task.final_verdict)}">${escapeHtml(verdictLabels[task.final_verdict] || '待定')}</span></td>
          <td>${escapeHtml(task.current_stage || 'pending')}</td>
          <td>${escapeHtml(task.attempt_no ?? 0)}</td>
          <td>${escapeHtml(task.clarification_summary || '未触发')}</td>
          <td>${escapeHtml(task.updated_at || '未提供')}</td>
          <td>${escapeHtml(task.question || '未提供问题文本')}</td>
        </tr>
      `
    )
    .join('');
  Array.from(taskTableBody.querySelectorAll('tr[data-task-id]')).forEach((row) => {
    const openDetail = () => {
      window.location.href = `/console/tasks/${encodeURIComponent(row.dataset.taskId)}`;
    };
    row.addEventListener('click', openDetail);
    row.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        openDetail();
      }
    });
  });
  renderPagination();
}

function setActiveView(viewName) {
  const isRuntime = viewName === 'runtime';
  runtimeCenterView.classList.toggle('is-hidden', !isRuntime);
  userQueryView.classList.toggle('is-hidden', isRuntime);
  runtimeViewTab.classList.toggle('is-active', isRuntime);
  userQueryViewTab.classList.toggle('is-active', !isRuntime);
  if (!isRuntime) {
    loadUserQueryHistory().catch((error) => {
      userQueryHistory.innerHTML = `<p class="empty">查询记录加载失败: ${escapeHtml(String(error))}</p>`;
    });
  }
}

function initialViewName() {
  const params = new URLSearchParams(window.location.search);
  return params.get('view') === 'user-query' ? 'user-query' : 'runtime';
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

function updateQueryProgress(activeKey, text, options = {}) {
  if (!userQueryProgress) {
    return;
  }
  const currentIndex = activeKey === 'idle' ? -1 : Math.max(0, queryProgressSteps.findIndex((step) => step.key === activeKey));
  const step = currentIndex >= 0 ? queryProgressSteps[currentIndex] : queryProgressSteps[0];
  const percent = options.percent ?? step.percent;
  const failed = Boolean(options.failed);
  userQueryProgress.classList.toggle('is-idle', activeKey === 'idle');
  userQueryProgress.classList.toggle('is-failed', failed);
  userQueryProgressText.textContent = text || step.label;
  userQueryProgressPercent.textContent = `${Math.max(0, Math.min(100, percent))}%`;
  userQueryProgressFill.style.width = `${Math.max(0, Math.min(100, percent))}%`;
  userQueryProgressSteps.innerHTML = queryProgressSteps
    .map((item, index) => {
      const stateClass = index < currentIndex ? 'is-complete' : index === currentIndex ? 'is-active' : '';
      return `<li class="query-progress-step ${stateClass}"><span>${escapeHtml(item.label)}</span></li>`;
    })
    .join('');
}

function updateQueryProgressFromRecord(record) {
  const diagnosticStatus = (record.cga_diagnostic || {}).status;
  if (diagnosticStatus === 'pending') {
    updateQueryProgress('diagnostic', '查询结果已返回，诊断生成中。', {percent: 86});
    return;
  }
  if (diagnosticStatus === 'failed') {
    updateQueryProgress('feedback', '查询结果已返回，诊断生成失败。', {failed: true, percent: 100});
    return;
  }
  if (record.status === 'query_failed' || record.status === 'service_failed') {
    updateQueryProgress('feedback', '查询已返回，反馈整理完成。', {failed: true, percent: 100});
    return;
  }
  updateQueryProgress('feedback', '反馈整理完成。', {percent: 100});
}

function renderCgaDiagnostic(diagnostic) {
  const data = diagnostic || {status: 'failed', title: '诊断生成失败', error_message: '未收到诊断结果。'};
  const status = data.status || 'failed';
  if (status === 'pending') {
    return `
      <section class="query-block diagnostic-block">
        <h3>结果诊断</h3>
        <div class="diagnostic-title-row">
          <strong>诊断生成中</strong>
          <span class="status-pill tone-warn">生成中</span>
        </div>
        <p>${escapeHtml(data.summary || '查询结果已返回，正在生成面向业务用户的诊断说明。')}</p>
      </section>
    `;
  }
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

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function pollUserQueryDiagnostic(userQueryId, attempt = 0) {
  if (!userQueryId || activeUserQueryPollId !== userQueryId) {
    return;
  }
  if (attempt > 40) {
    updateQueryProgress('diagnostic', '诊断生成时间较长，可稍后从查询记录查看。', {percent: 86});
    return;
  }
  await delay(1500);
  if (activeUserQueryPollId !== userQueryId) {
    return;
  }
  const response = await fetch(`/api/v1/user-queries/${encodeURIComponent(userQueryId)}`);
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`);
  }
  const record = await response.json();
  renderUserQueryRecord(record, userQueryResult);
  updateQueryProgressFromRecord(record);
  if ((record.cga_diagnostic || {}).status === 'pending') {
    await pollUserQueryDiagnostic(userQueryId, attempt + 1);
    return;
  }
  await loadUserQueryHistory();
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

function renderUserQueryRecord(record, target) {
  const resultTarget = target || userQueryResult;
  if (!record) {
    resultTarget.innerHTML = '<p class="empty">暂无用户查询结果。</p>';
    return;
  }
  const preview = record.result_preview || {};
  const tugraphResponse = record.tugraph_response || record.tugraph_execution || null;
  const errorMessage = rawTuGraphError(tugraphResponse);
  const truncatedNotice = preview.truncated
    ? `<p class="limit-notice">超出页面展示限制，已展示前 ${escapeHtml(preview.preview_row_limit)} 行，可下载 TuGraph JSON 查看完整结果。</p>`
    : '';
  resultTarget.innerHTML = `
    <div class="query-result-head">
      <div>
        <h3>${escapeHtml(record.question)}</h3>
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

function renderUserQueryHistory(payload) {
  const items = (payload && payload.items) || [];
  if (!items.length) {
    userQueryHistory.innerHTML = '<p class="empty">暂无查询记录。</p>';
    return;
  }
  userQueryHistory.innerHTML = `
    <div class="table-shell query-history-shell">
      <table class="query-history-table">
        <colgroup>
          <col style="width: 260px" />
          <col style="width: 160px" />
          <col style="width: 110px" />
          <col style="width: 270px" />
          <col style="width: 480px" />
          <col style="width: 140px" />
        </colgroup>
        <thead>
          <tr>
            <th>查询编号</th>
            <th>状态</th>
            <th>行数</th>
            <th>更新时间</th>
            <th>问题</th>
            <th>下载</th>
          </tr>
        </thead>
        <tbody>
          ${items
            .map(
              (item) => `
                <tr data-user-query-id="${escapeHtml(item.user_query_id)}" tabindex="0">
                  <td><strong>${escapeHtml(item.user_query_id)}</strong></td>
                  <td><span class="status-pill tone-${tone(item.status)}">${escapeHtml(statusLabel(item.status))}</span></td>
                  <td>${escapeHtml(item.row_count ?? 0)}</td>
                  <td>${escapeHtml(item.updated_at || item.created_at || '')}</td>
                  <td>${escapeHtml(item.question || '未提供问题')}</td>
                  <td>${renderTugraphDownloadLink(item, 'history-download')}</td>
                </tr>
              `
            )
            .join('')}
        </tbody>
      </table>
    </div>
  `;
  Array.from(userQueryHistory.querySelectorAll('tr[data-user-query-id]')).forEach((row) => {
    const openRecord = () => {
      window.location.href = `/console/user-queries/${encodeURIComponent(row.dataset.userQueryId)}`;
    };
    row.addEventListener('click', (event) => {
      if (event.target.closest('a')) {
        return;
      }
      openRecord();
    });
    row.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        openRecord();
      }
    });
  });
}

function taskQueryParams() {
  const params = new URLSearchParams();
  params.set('page', String(currentPage));
  params.set('page_size', pageSizeSelect.value || '20');
  if (difficultyFilter.value) {
    params.set('difficulty', difficultyFilter.value);
  }
  if (idSearch.value.trim()) {
    params.set('q', idSearch.value.trim());
  }
  return params;
}

async function loadTasks() {
  const response = await fetch(`/api/v1/tasks?${taskQueryParams().toString()}`);
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`);
  }
  const payload = await response.json();
  currentPagination = payload.pagination || currentPagination;
  currentPage = currentPagination.page || 1;
  renderTaskTable(payload.tasks || []);
}

async function loadTaskSummary() {
  const response = await fetch('/api/v1/tasks/summary');
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`);
  }
  const payload = await response.json();
  renderDifficultySummary(payload);
}

async function loadUserQueryHistory() {
  const response = await fetch('/api/v1/user-queries');
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`);
  }
  const payload = await response.json();
  renderUserQueryHistory(payload);
}

difficultyFilter.addEventListener('change', () => {
  currentPage = 1;
  loadTasks().catch((error) => {
    tableMeta.textContent = `任务索引加载失败: ${String(error)}`;
  });
});
idSearch.addEventListener('input', () => {
  currentPage = 1;
  loadTasks().catch((error) => {
    tableMeta.textContent = `任务索引加载失败: ${String(error)}`;
  });
});
pageSizeSelect.addEventListener('change', () => {
  currentPage = 1;
  loadTasks().catch((error) => {
    tableMeta.textContent = `任务索引加载失败: ${String(error)}`;
  });
});
prevPageButton.addEventListener('click', () => {
  if (!currentPagination.has_previous) {
    return;
  }
  currentPage -= 1;
  loadTasks().catch((error) => {
    tableMeta.textContent = `任务索引加载失败: ${String(error)}`;
  });
});
nextPageButton.addEventListener('click', () => {
  if (!currentPagination.has_next) {
    return;
  }
  currentPage += 1;
  loadTasks().catch((error) => {
    tableMeta.textContent = `任务索引加载失败: ${String(error)}`;
  });
});

runtimeViewTab.addEventListener('click', () => {
  setActiveView('runtime');
});
userQueryViewTab.addEventListener('click', () => {
  setActiveView('user-query');
});
userQueryForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  const question = queryQuestion.value.trim();
  if (!question) {
    userQueryStatus.textContent = '请输入自然语言问题。';
    updateQueryProgress('idle', '请输入自然语言问题。', {percent: 0});
    return;
  }
  activeUserQueryPollId = '';
  userQueryStatus.textContent = 'CGA 生成中。';
  updateQueryProgress('cga', 'CGA 正在生成 Cypher。', {percent: 42});
  userQueryForm.classList.add('is-running');
  try {
    const response = await fetch('/api/v1/user-queries', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({question}),
    });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const record = await response.json();
    userQueryStatus.textContent = (record.cga_diagnostic || {}).status === 'pending'
      ? '查询结果已返回，诊断生成中。'
      : 'TuGraph 查询完成。';
    renderUserQueryRecord(record, userQueryResult);
    updateQueryProgressFromRecord(record);
    await loadUserQueryHistory();
    if ((record.cga_diagnostic || {}).status === 'pending') {
      activeUserQueryPollId = record.user_query_id;
      pollUserQueryDiagnostic(record.user_query_id).catch((error) => {
        if (activeUserQueryPollId === record.user_query_id) {
          userQueryStatus.textContent = `诊断回填失败: ${String(error)}`;
          updateQueryProgress('diagnostic', '诊断回填失败。', {failed: true, percent: 86});
        }
      });
    }
  } catch (error) {
    userQueryStatus.textContent = `用户查询失败: ${String(error)}`;
    updateQueryProgress('feedback', `用户查询失败: ${String(error)}`, {failed: true, percent: 100});
  } finally {
    userQueryForm.classList.remove('is-running');
  }
});

Promise.all([loadTaskSummary(), loadTasks()]).catch((error) => {
  tableMeta.textContent = `任务索引加载失败: ${String(error)}`;
  taskTableBody.innerHTML = `<tr><td colspan="8" class="empty-cell">${escapeHtml(String(error))}</td></tr>`;
});

setActiveView(initialViewName());
updateQueryProgress('idle', '等待输入。', {percent: 0});

setInterval(() => {
  loadTaskSummary().catch(() => {});
  loadTasks().catch(() => {});
  if (!userQueryView.classList.contains('is-hidden')) {
    loadUserQueryHistory().catch(() => {});
  }
}, 15000);
