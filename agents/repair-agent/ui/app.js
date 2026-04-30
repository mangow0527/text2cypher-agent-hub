class RepairAgentConsole {
  constructor() {
    this.baseUrl = '';
    this.init();
  }

  init() {
    this.bindEvents();
    this.loadServiceStatus();
  }

  bindEvents() {
    document.getElementById('refresh-status')?.addEventListener('click', () => this.loadServiceStatus());
    document.getElementById('lookup-analysis')?.addEventListener('click', () => this.lookupAnalysis());
    document.getElementById('submit-ticket')?.addEventListener('click', () => this.submitIssueTicket());
    document.getElementById('analysis-id')?.addEventListener('keypress', (event) => {
      if (event.key === 'Enter') this.lookupAnalysis();
    });
  }

  async loadServiceStatus() {
    try {
      const response = await fetch(`${this.baseUrl}/api/v1/status`);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      this.renderJson('status-view', await response.json());
      this.updateBanner('服务状态已加载', 'success');
    } catch (error) {
      this.renderJson('status-view', { error: error.message });
      this.updateBanner(`加载服务状态失败: ${error.message}`, 'error');
    }
  }

  async lookupAnalysis() {
    const analysisId = document.getElementById('analysis-id')?.value.trim();
    if (!analysisId) {
      this.updateBanner('请输入 analysis_id', 'warning');
      return;
    }

    try {
      const response = await fetch(`${this.baseUrl}/api/v1/analyses/${encodeURIComponent(analysisId)}`);
      if (!response.ok) throw new Error(response.status === 404 ? '分析记录不存在' : `HTTP ${response.status}`);
      const analysis = await response.json();
      this.renderJson('analysis-view', analysis);
      this.renderOutcome(analysis);
      this.updateBanner('分析记录已加载', 'success');
    } catch (error) {
      this.renderJson('analysis-view', { error: error.message });
      this.updateBanner(`查询分析记录失败: ${error.message}`, 'error');
    }
  }

  async submitIssueTicket() {
    const rawPayload = document.getElementById('issue-ticket-json')?.value.trim();
    if (!rawPayload) {
      this.updateBanner('请输入 IssueTicket JSON', 'warning');
      return;
    }

    let payload;
    try {
      payload = JSON.parse(rawPayload);
    } catch (error) {
      this.updateBanner(`IssueTicket JSON 格式错误: ${error.message}`, 'error');
      return;
    }

    try {
      const response = await fetch(`${this.baseUrl}/api/v1/issue-tickets`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const responsePayload = await response.json().catch(() => ({ status_code: response.status }));
      if (!response.ok) throw new Error(JSON.stringify(responsePayload));
      this.renderJson('submit-view', responsePayload);
      this.renderOutcome(responsePayload);
      this.updateBanner('IssueTicket 已处理', 'success');
    } catch (error) {
      this.renderJson('submit-view', { error: error.message });
      this.updateBanner(`提交 IssueTicket 失败: ${error.message}`, 'error');
    }
  }

  renderOutcome(payload) {
    const chipsContainer = document.getElementById('chips');
    if (!chipsContainer) return;
    chipsContainer.innerHTML = '';

    const status = payload.status || (payload.applied ? 'applied' : 'not_repairable');
    chipsContainer.appendChild(this.createChip(`状态: ${status}`, this.statusClass(status)));

    if (typeof payload.applied === 'boolean') {
      chipsContainer.appendChild(this.createChip(`applied: ${payload.applied}`, payload.applied ? 'success' : 'warning'));
    }

    const request = payload.knowledge_repair_request;
    if (request?.knowledge_types?.length) {
      chipsContainer.appendChild(this.createChip(`knowledge_types: ${request.knowledge_types.join(', ')}`, 'info'));
    }
  }

  createChip(text, type) {
    const chip = document.createElement('div');
    chip.className = `chip ${type}`;
    chip.textContent = text;
    return chip;
  }

  statusClass(status) {
    const classMap = {
      analysis_pending: 'info',
      apply_failed: 'error',
      applied: 'success',
      not_repairable: 'warning',
      repair_apply_paused: 'warning',
    };
    return classMap[status] || 'info';
  }

  renderJson(elementId, payload) {
    const element = document.getElementById(elementId);
    if (element) element.textContent = JSON.stringify(payload, null, 2);
  }

  updateBanner(message, type = 'info') {
    const banner = document.getElementById('banner');
    if (!banner) return;
    banner.textContent = message;
    banner.className = `banner ${type}`;
  }
}

document.addEventListener('DOMContentLoaded', () => {
  window.repairAgentConsole = new RepairAgentConsole();
});

window.addEventListener('error', (event) => {
  window.repairAgentConsole?.updateBanner(`发生错误: ${event.message}`, 'error');
});
