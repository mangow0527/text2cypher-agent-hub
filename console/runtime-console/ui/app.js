const taskList = document.getElementById('task-list');
const serviceGrid = document.getElementById('service-grid');
const taskMeta = document.getElementById('task-meta');
const overviewGrid = document.getElementById('overview-grid');
const qualityPill = document.getElementById('quality-pill');
const qualitySummary = document.getElementById('quality-summary');
const qualityFindings = document.getElementById('quality-findings');
const improvementPill = document.getElementById('improvement-pill');
const improvementSummary = document.getElementById('improvement-summary');
const improvementDimensions = document.getElementById('improvement-dimensions');
const improvementHighlights = document.getElementById('improvement-highlights');
const repairSummary = document.getElementById('repair-summary');
const repairDiagnosisGrid = document.getElementById('repair-diagnosis-grid');
const repairDiagnosisFindings = document.getElementById('repair-diagnosis-findings');
const cypherView = document.getElementById('cypher-view');
const evaluationView = document.getElementById('evaluation-view');
const repairView = document.getElementById('repair-view');

let selectedTaskId = null;

function pretty(value) {
  return JSON.stringify(value ?? {}, null, 2);
}

function tone(status) {
  switch (status) {
    case 'passed':
    case 'good':
      return 'ok';
    case 'failed':
    case 'bad':
      return 'danger';
    case 'running':
    case 'risky':
      return 'warn';
    default:
      return 'neutral';
  }
}

function renderTaskList(tasks) {
  if (!tasks.length) {
    taskList.innerHTML = '<p class="empty">暂无可展示的 QA 任务。</p>';
    return;
  }
  if (!selectedTaskId) {
    selectedTaskId = tasks[0].id;
  }
  taskList.innerHTML = tasks
    .map(
      (task) => `
        <button type="button" class="task-card ${task.id === selectedTaskId ? 'is-active' : ''}" data-task-id="${task.id}">
          <div class="task-card-head">
            <strong>${task.id}</strong>
            <span class="status-pill tone-${tone(task.final_verdict === 'pass' ? 'passed' : task.final_verdict === 'fail' ? 'failed' : 'pending')}">${task.final_verdict}</span>
          </div>
          <p>${task.question || '未提供问题文本'}</p>
          <div class="task-card-meta">
            <span>${task.current_stage}</span>
            <span>${task.cypher_quality_label_zh}</span>
            <span>${task.improvement_status}</span>
          </div>
        </button>
      `
    )
    .join('');
  Array.from(document.querySelectorAll('.task-card')).forEach((node) => {
    node.addEventListener('click', () => {
      selectedTaskId = node.dataset.taskId;
      loadTaskDetail();
      renderTaskList(tasks);
    });
  });
}

function renderServiceCards(services) {
  serviceGrid.innerHTML = services
    .map(
      (service) => `
        <article class="service-card">
          <div class="task-card-head">
            <div>
              <strong>${service.label_zh}</strong>
              <p>${service.label_en}</p>
            </div>
            <span class="status-pill tone-${tone(service.status)}">${service.status}</span>
          </div>
          <div class="task-card-meta">
            <span>Port ${service.port}</span>
            <span>${service.base_url}</span>
          </div>
          <p>${service.description_zh}</p>
        </article>
      `
    )
    .join('');
}

function renderOverview(detail) {
  taskMeta.textContent = `${detail.id} · ${detail.question || '未提供问题文本'}`;
  overviewGrid.innerHTML = Object.entries(detail.stages || {})
    .map(
      ([stageKey, stage]) => `
        <article class="overview-card">
          <div>
            <h3>${stage.label_zh}</h3>
            <p>${stage.label_en}</p>
          </div>
          <span class="status-pill tone-${tone(stage.status)}">${stage.status}</span>
          <small>${stageKey}</small>
        </article>
      `
    )
    .join('');
}

function renderQuality(detail) {
  const quality = detail.cypher_quality || {};
  qualityPill.textContent = quality.label_zh || '待评估';
  qualityPill.className = `quality-pill tone-${tone(quality.label || 'pending')}`;
  qualitySummary.textContent = quality.summary_zh || '暂无质量概括。';
  cypherView.textContent = detail.generated_cypher || '// 暂无 Cypher';
  qualityFindings.innerHTML = (quality.findings || [])
    .map((finding) => `<div class="finding-item">${finding}</div>`)
    .join('');
}

function renderArtifacts(detail) {
  evaluationView.textContent = pretty({
    evaluation: detail.artifacts?.evaluation,
    execution: detail.artifacts?.execution,
    golden: detail.artifacts?.golden,
  });
  repairView.textContent = pretty(detail.artifacts?.repair);
}

function renderRepairDiagnosis(detail) {
  const analysis = detail.artifacts?.repair?.analysis || {};
  const issueTicket = detail.artifacts?.repair?.issue_ticket || {};
  const request = analysis.knowledge_repair_request || {};
  const validationResult = analysis.validation_result || {};
  const promptSnapshotSource = analysis.prompt_snapshot
    ? 'Testing Service 持久化的 RepairAnalysisRecord.prompt_snapshot'
    : issueTicket.generation_evidence?.input_prompt_snapshot
      ? 'Testing Service 持久化的 IssueTicket.generation_evidence.input_prompt_snapshot'
      : '未提供';
  repairSummary.textContent = analysis.rationale || '暂无 repair-agent 诊断摘要。';
  repairDiagnosisGrid.innerHTML = [
    ['Prompt snapshot 来源', promptSnapshotSource],
    ['主根因类型', analysis.primary_knowledge_type || '未提供'],
    ['候选修复类型', (analysis.candidate_patch_types || []).join(', ') || '未提供'],
    ['验证模式', analysis.validation_mode || 'disabled'],
    ['最终下发类型', (request.knowledge_types || []).join(', ') || '未提供'],
  ]
    .map(
      ([label, value]) => `
        <article class="overview-card">
          <div>
            <h3>${label}</h3>
            <p>${value}</p>
          </div>
        </article>
      `
    )
    .join('');
  repairDiagnosisFindings.innerHTML = [
    request.suggestion ? `最终建议: ${request.suggestion}` : null,
    (validationResult.validated_patch_types || []).length
      ? `验证通过: ${(validationResult.validated_patch_types || []).join(', ')}`
      : null,
    (validationResult.rejected_patch_types || []).length
      ? `验证拒绝: ${(validationResult.rejected_patch_types || []).join(', ')}`
      : null,
    ...((validationResult.validation_reasoning || []).map((item) => `验证说明: ${item}`)),
  ]
    .filter(Boolean)
    .map((item) => `<div class="finding-item">${item}</div>`)
    .join('');
}

function improvementTone(status) {
  switch (status) {
    case 'improved':
      return 'ok';
    case 'regressed':
      return 'danger';
    case 'unchanged':
      return 'warn';
    default:
      return 'neutral';
  }
}

function improvementLabel(status) {
  const labels = {
    improved: '已改善',
    regressed: '已回退',
    unchanged: '无明显变化',
    not_comparable: '暂不可比较',
  };
  return labels[status] || '暂不可比较';
}

function improvementOverview(dimensions) {
  const values = Object.values(dimensions || {});
  if (!values.length || values.every((value) => value === 'not_comparable')) {
    return { label: '暂不可比较', tone: 'neutral' };
  }
  const improved = values.filter((value) => value === 'improved').length;
  const regressed = values.filter((value) => value === 'regressed').length;
  const unchanged = values.filter((value) => value === 'unchanged').length;
  const parts = [`${improved} 项改善`, `${regressed} 项回退`, `${unchanged} 项不变`];
  const notComparable = values.filter((value) => value === 'not_comparable').length;
  if (notComparable) {
    parts.push(`${notComparable} 项暂不可比较`);
  }
  return {
    label: parts.join(' / '),
    tone: regressed ? 'danger' : improved ? 'ok' : 'warn',
  };
}

function renderImprovement(detail) {
  const assessment = detail.improvement_assessment || {};
  const dimensions = assessment.dimensions || {};
  const overview = improvementOverview(dimensions);
  improvementPill.textContent = overview.label;
  improvementPill.className = `quality-pill tone-${overview.tone}`;
  improvementSummary.textContent = assessment.summary_zh || '暂无改进评估。';

  improvementDimensions.innerHTML = Object.entries(dimensions)
    .map(
      ([key, value]) => `
        <article class="overview-card">
          <div>
            <h3>${key}</h3>
            <p>${improvementLabel(value)}</p>
          </div>
          <span class="status-pill tone-${improvementTone(value)}">${value}</span>
        </article>
      `
    )
    .join('');
  improvementHighlights.innerHTML = (assessment.highlights || [])
    .map((item) => `<div class="finding-item">${item}</div>`)
    .join('');
}

async function loadTasks() {
  const response = await fetch('/api/v1/tasks');
  const payload = await response.json();
  renderTaskList(payload.tasks || []);
  if (selectedTaskId) {
    loadTaskDetail().catch((error) => {
      taskMeta.textContent = `详情加载失败: ${String(error)}`;
    });
  }
}

async function loadServices() {
  const response = await fetch('/api/v1/runtime/services');
  const payload = await response.json();
  renderServiceCards(payload.services || []);
}

async function loadTaskDetail() {
  if (!selectedTaskId) {
    return;
  }
  const response = await fetch(`/api/v1/tasks/${selectedTaskId}`);
  const payload = await response.json();
  renderOverview(payload);
  renderQuality(payload);
  renderImprovement(payload);
  renderRepairDiagnosis(payload);
  renderArtifacts(payload);
}

Promise.all([loadServices(), loadTasks()]).catch((error) => {
  taskList.innerHTML = `<p class="empty">${String(error)}</p>`;
});

setInterval(() => {
  loadServices().catch(() => {});
  loadTasks().catch(() => {});
}, 5000);
