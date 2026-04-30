const serviceGrid = document.getElementById('service-grid');
const difficultyGrid = document.getElementById('difficulty-grid');
const taskTableBody = document.getElementById('task-table-body');
const difficultyFilter = document.getElementById('difficulty-filter');
const idSearch = document.getElementById('id-search');
const tableMeta = document.getElementById('table-meta');
const pageSizeSelect = document.getElementById('page-size');
const prevPageButton = document.getElementById('prev-page');
const nextPageButton = document.getElementById('next-page');
const pageIndicator = document.getElementById('page-indicator');

const generationLabels = {
  generated: '生成成功',
  generation_failed: '生成失败',
  service_failed: '服务失败',
};

const verdictLabels = {
  pass: '通过',
  fail: '失败',
  pending: '待定',
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

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

function tone(status) {
  switch (status) {
    case 'pass':
    case 'passed':
    case 'generated':
    case 'ok':
      return 'ok';
    case 'fail':
    case 'failed':
    case 'service_failed':
      return 'danger';
    case 'running':
    case 'pending':
    case 'generation_failed':
      return 'warn';
    default:
      return 'neutral';
  }
}

function renderServiceCards(services) {
  serviceGrid.innerHTML = services
    .map(
      (service) => `
        <article class="service-card">
          <div class="task-card-head">
            <div>
              <strong>${escapeHtml(service.label_zh)}</strong>
              <p>${escapeHtml(service.label_en)}</p>
            </div>
            <span class="status-pill tone-${tone(service.status)}">${escapeHtml(service.status)}</span>
          </div>
          <div class="task-card-meta">
            <span>Port ${escapeHtml(service.port)}</span>
            <span>${escapeHtml(service.base_url)}</span>
          </div>
          <p>${escapeHtml(service.description_zh)}</p>
        </article>
      `
    )
    .join('');
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
  const passed = percentage(bucket.pass || 0, total);
  const failed = percentage(bucket.fail || 0, total);
  const pending = percentage(bucket.pending || 0, total);
  const passedEnd = passed;
  const failedEnd = passed + failed;
  const pendingEnd = failedEnd + pending;
  return `background: conic-gradient(#177245 0 ${passedEnd}%, #af3d36 ${passedEnd}% ${failedEnd}%, #8c5b0a ${failedEnd}% ${pendingEnd}%, #ece2d3 ${pendingEnd}% 100%);`;
}

function renderDifficultySummary(summary) {
  const buckets = summary.buckets || [];
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
          <div class="difficulty-counts">
            <span><i class="legend-dot ok"></i>${verdictLabels.pass}: ${escapeHtml(bucket.pass || 0)}</span>
            <span><i class="legend-dot danger"></i>${verdictLabels.fail}: ${escapeHtml(bucket.fail || 0)}</span>
            <span><i class="legend-dot warn"></i>${verdictLabels.pending}: ${escapeHtml(bucket.pending || 0)}</span>
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
    taskTableBody.innerHTML = '<tr><td colspan="8" class="empty-cell">暂无符合新契约的运行任务。</td></tr>';
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

async function loadServices() {
  const response = await fetch('/api/v1/runtime/services');
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`);
  }
  const payload = await response.json();
  renderServiceCards(payload.services || []);
}

async function loadTaskSummary() {
  const response = await fetch('/api/v1/tasks/summary');
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`);
  }
  const payload = await response.json();
  renderDifficultySummary(payload);
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

Promise.all([loadServices(), loadTaskSummary(), loadTasks()]).catch((error) => {
  tableMeta.textContent = `任务索引加载失败: ${String(error)}`;
  taskTableBody.innerHTML = `<tr><td colspan="8" class="empty-cell">${escapeHtml(String(error))}</td></tr>`;
});

setInterval(() => {
  loadServices().catch(() => {});
}, 10000);

setInterval(() => {
  loadTaskSummary().catch(() => {});
  loadTasks().catch(() => {});
}, 15000);
