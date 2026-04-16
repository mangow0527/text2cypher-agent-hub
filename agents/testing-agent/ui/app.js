const tabs = Array.from(document.querySelectorAll('.workspace-tab'));
const panels = Array.from(document.querySelectorAll('.tab-panel'));
const serviceGrid = document.getElementById('service-grid');
const structureDiagram = document.getElementById('structure-diagram');
const dataFlowDiagram = document.getElementById('data-flow-diagram');

const consoleRunForm = document.getElementById('console-run-form');
const runBanner = document.getElementById('run-banner');
const stageGrid = document.getElementById('stage-grid');
const timeline = document.getElementById('timeline');
const generationView = document.getElementById('generation-view');
const evaluationView = document.getElementById('evaluation-view');
const issueView = document.getElementById('issue-view');
const repairView = document.getElementById('repair-view');

function activateTab(tabName) {
  tabs.forEach((tab) => {
    tab.classList.toggle('is-active', tab.dataset.tab === tabName);
  });
  panels.forEach((panel) => {
    panel.classList.toggle('is-active', panel.id === `tab-${tabName}`);
  });
}

tabs.forEach((tab) => {
  tab.addEventListener('click', () => activateTab(tab.dataset.tab));
});

function pretty(value) {
  return JSON.stringify(value ?? {}, null, 2);
}

function statusTone(status) {
  switch (status) {
    case 'online':
    case 'success':
      return 'ok';
    case 'failed':
    case 'offline':
      return 'danger';
    default:
      return 'neutral';
  }
}

function renderServiceCards(services) {
  serviceGrid.innerHTML = services
    .map(
      (service) => `
        <article class="service-card">
          <div class="service-card-head">
            <div>
              <h3>${service.label_zh}</h3>
              <p>${service.label_en}</p>
            </div>
            <span class="status-pill tone-${statusTone(service.status)}">${service.status}</span>
          </div>
          <dl>
            <div><dt>端口 Port</dt><dd>${service.port}</dd></div>
            <div><dt>地址 URL</dt><dd>${service.base_url}</dd></div>
            <div><dt>职责 Role</dt><dd>${service.description_zh}</dd></div>
            <div><dt>关键接口 Endpoints</dt><dd>${service.key_endpoints.join('<br />')}</dd></div>
          </dl>
        </article>
      `
    )
    .join('');
}

function renderStructureDiagram(links) {
  structureDiagram.innerHTML = links
    .map(
      (link) => `
        <div class="diagram-item">
          <strong>${link.source}</strong>
          <span>→</span>
          <strong>${link.target}</strong>
          <p>${link.label_zh} / ${link.label_en}</p>
        </div>
      `
    )
    .join('');
}

function renderDataObjects(objects) {
  dataFlowDiagram.innerHTML = objects
    .map(
      (obj) => `
        <div class="diagram-item">
          <strong>${obj.label_zh}</strong>
          <p>${obj.label_en}</p>
          <p>来源 Source: ${obj.source_zh}</p>
          <p>去向 Target: ${obj.target_zh}</p>
          <p>${obj.meaning_zh}</p>
        </div>
      `
    )
    .join('');
}

function renderStages(stages) {
  stageGrid.innerHTML = Object.values(stages)
    .map(
      (stage) => `
        <article class="stage-card">
          <header>
            <h4>${stage.label_zh}</h4>
            <p>${stage.label_en}</p>
          </header>
          <span class="status-pill tone-${statusTone(stage.status)}">${stage.status}</span>
        </article>
      `
    )
    .join('');
}

function renderTimeline(items) {
  timeline.innerHTML = items
    .map(
      (item) => `
        <article class="timeline-item">
          <span class="timeline-dot tone-${statusTone(item.status)}"></span>
          <div>
            <h4>${item.label_zh}</h4>
            <p>${item.label_en}</p>
          </div>
          <span class="status-pill tone-${statusTone(item.status)}">${item.status}</span>
        </article>
      `
    )
    .join('');
}

function renderArtifacts(artifacts) {
  generationView.textContent = pretty({
    generation: artifacts.generation,
    submission: artifacts.submission,
  });
  evaluationView.textContent = pretty({
    execution: artifacts.execution,
    evaluation: artifacts.evaluation,
  });
  issueView.textContent = pretty(artifacts.knowledge_repair.issue_ticket);
  repairView.textContent = pretty(artifacts.knowledge_repair.krss_response);
}

async function loadArchitecture() {
  const response = await fetch('/api/v1/runtime/architecture');
  const payload = await response.json();
  renderServiceCards(payload.services || []);
  renderStructureDiagram(payload.links || []);
  renderDataObjects(payload.data_objects || []);
}

async function runConsoleFlow(payload) {
  runBanner.textContent = '联调进行中...';
  const response = await fetch('/api/v1/runtime/console-runs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.detail ? JSON.stringify(data.detail) : '联调失败');
  }
  runBanner.textContent = `${data.title_zh} / ${data.title_en}`;
  renderStages(data.stages || {});
  renderTimeline(data.timeline || []);
  renderArtifacts(data.artifacts || {});
}

function fillSuccessExample() {
  consoleRunForm.id.value = 'qa-console-success';
  consoleRunForm.question.value = '查询网络设备名称';
}

function fillFailureExample() {
  consoleRunForm.id.value = 'qa-console-failure';
  consoleRunForm.question.value = '查询一个会触发失败闭环的设备名称';
}

document.getElementById('fill-success').addEventListener('click', fillSuccessExample);
document.getElementById('fill-failure').addEventListener('click', fillFailureExample);

consoleRunForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    await runConsoleFlow({
      id: consoleRunForm.id.value.trim(),
      question: consoleRunForm.question.value.trim(),
    });
  } catch (error) {
    runBanner.textContent = `联调失败: ${String(error)}`;
    stageGrid.innerHTML = '';
    timeline.innerHTML = '';
  }
});

loadArchitecture().catch((error) => {
  serviceGrid.innerHTML = `<article class="panel"><pre>${String(error)}</pre></article>`;
});
