class RepairServiceApp {
  constructor() {
    this.baseUrl = '';
    this.currentTicket = null;
    this.currentPlan = null;
    this.init();
  }

  init() {
    this.bindEvents();
    this.loadServiceStatus();
  }

  bindEvents() {
    document.getElementById('lookup-ticket')?.addEventListener('click', () => this.lookupTicket());
    document.getElementById('create-plan')?.addEventListener('click', () => this.createPlan());
    
    // Handle Enter key for inputs
    document.getElementById('ticket-lookup-id')?.addEventListener('keypress', (e) => {
      if (e.key === 'Enter') this.lookupTicket();
    });
  }

  async loadServiceStatus() {
    try {
      const response = await fetch(`${this.baseUrl}/api/v1/status`);
      const data = await response.json();
      
      const statusView = document.getElementById('status-view');
      statusView.textContent = JSON.stringify(data, null, 2);
      
      this.updateBanner('服务状态已加载', 'success');
    } catch (error) {
      this.updateBanner('加载服务状态失败: ' + error.message, 'error');
      document.getElementById('status-view').textContent = JSON.stringify({ error: error.message }, null, 2);
    }
  }

  async lookupTicket() {
    const ticketId = document.getElementById('ticket-lookup-id')?.value.trim();
    if (!ticketId) {
      this.updateBanner('请输入问题单ID', 'warning');
      return;
    }

    try {
      this.updateBanner('正在查询问题单...', 'info');
      const response = await fetch(`${this.baseUrl}/api/v1/issues/${ticketId}`);
      
      if (!response.ok) {
        throw new Error(response.status === 404 ? '问题单不存在' : '查询失败');
      }
      
      const ticket = await response.json();
      this.currentTicket = ticket;
      
      this.displayTicket(ticket);
      this.updateBanner('问题单查询成功', 'success');
      
      // Auto-fill the plan form
      document.getElementById('plan-ticket-id').value = ticketId;
      document.getElementById('plan-original-id').value = ticket.id;
      
    } catch (error) {
      this.updateBanner('查询问题单失败: ' + error.message, 'error');
      document.getElementById('ticket-view').textContent = JSON.stringify({ error: error.message }, null, 2);
    }
  }

  async createPlan() {
    const ticketId = document.getElementById('plan-ticket-id')?.value.trim();
    const originalId = document.getElementById('plan-original-id')?.value.trim();
    
    if (!ticketId || !originalId) {
      this.updateBanner('请填写问题单ID和原始问题ID', 'warning');
      return;
    }

    try {
      this.updateBanner('正在创建修复计划...', 'info');
      
      // Get ticket first
      const ticketResponse = await fetch(`${this.baseUrl}/api/v1/issues/${ticketId}`);
      if (!ticketResponse.ok) {
        throw new Error('问题单不存在');
      }
      
      const ticket = await ticketResponse.json();
      
      const planResponse = await fetch(`${this.baseUrl}/api/v1/repair-plans`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(ticket)
      });
      
      if (!planResponse.ok) {
        throw new Error('创建修复计划失败');
      }
      
      const planEnvelope = await planResponse.json();
      this.currentPlan = planEnvelope.plan;
      
      this.displayPlan(planEnvelope.plan);
      this.updateBanner('修复计划创建成功', 'success');
      
      // Show dispatch info
      this.displayDispatchInfo(planEnvelope.plan);
      
    } catch (error) {
      this.updateBanner('创建修复计划失败: ' + error.message, 'error');
      document.getElementById('plan-view').textContent = JSON.stringify({ error: error.message }, null, 2);
    }
  }

  displayTicket(ticket) {
    const ticketView = document.getElementById('ticket-view');
    ticketView.textContent = JSON.stringify({
      ticket_id: ticket.ticket_id,
      id: ticket.id,
      difficulty: ticket.difficulty,
      question: ticket.question,
      verdict: ticket.evaluation.verdict,
      dimensions: ticket.evaluation.dimensions,
      root_cause_analyzer: this.analyzeRootCause(ticket),
      created_at: new Date().toISOString()
    }, null, 2);
  }

  displayPlan(plan) {
    const planView = document.getElementById('plan-view');
    planView.textContent = JSON.stringify({
      plan_id: plan.plan_id,
      ticket_id: plan.ticket_id,
      id: plan.id,
      root_cause: plan.root_cause,
      confidence: plan.confidence,
      state: plan.state,
      analysis_summary: plan.analysis_summary,
      actions: plan.actions,
      created_at: new Date().toISOString()
    }, null, 2);
    
    this.updateChips(plan);
  }

  displayDispatchInfo(plan) {
    const dispatchView = document.getElementById('dispatch-view');
    const dispatchInfo = {
      plan_id: plan.plan_id,
      dispatch_status: 'completed',
      targets: plan.actions.map(action => ({
        target_service: action.target_service,
        action_type: action.action_type,
        dispatch_status: action.dispatch_status || 'sent'
      })),
      dispatched_at: new Date().toISOString()
    };
    
    dispatchView.textContent = JSON.stringify(dispatchInfo, null, 2);
  }

  analyzeRootCause(ticket) {
    const dimensions = ticket.evaluation.dimensions;
    const evidence = ticket.evaluation.evidence;
    
    if (dimensions.syntax_validity === 'fail' || dimensions.schema_alignment === 'fail') {
      return 'generator_logic_issue';
    }
    
    if (dimensions.result_correctness === 'fail' && dimensions.question_alignment === 'fail') {
      return 'mixed_issue';
    }
    
    if (evidence.some(e => e.includes('ambiguous'))) {
      return 'qa_question_issue';
    }
    
    if (evidence.some(e => e.includes('knowledge'))) {
      return 'knowledge_gap_issue';
    }
    
    return 'unknown';
  }

  updateChips(plan) {
    const chipsContainer = document.getElementById('chips');
    chipsContainer.innerHTML = '';
    
    // Root cause chip
    const rootCauseChip = document.createElement('div');
    rootCauseChip.className = `chip ${this.getRootCauseClass(plan.root_cause)}`;
    rootCauseChip.textContent = `根因: ${this.getRootCauseLabel(plan.root_cause)}`;
    chipsContainer.appendChild(rootCauseChip);
    
    // Confidence chip
    const confidenceChip = document.createElement('div');
    confidenceChip.className = 'chip info';
    confidenceChip.textContent = `置信度: ${(plan.confidence * 100).toFixed(1)}%`;
    chipsContainer.appendChild(confidenceChip);
    
    // State chip
    const stateChip = document.createElement('div');
    stateChip.className = `chip ${this.getStateClass(plan.state)}`;
    stateChip.textContent = `状态: ${this.getStateLabel(plan.state)}`;
    chipsContainer.appendChild(stateChip);
    
    // Actions chip
    const actionsChip = document.createElement('div');
    actionsChip.className = 'chip';
    actionsChip.textContent = `操作: ${plan.actions.length} 项`;
    chipsContainer.appendChild(actionsChip);
  }

  getRootCauseClass(rootCause) {
    const classMap = {
      'generator_logic_issue': 'error',
      'knowledge_gap_issue': 'warning',
      'qa_question_issue': 'info',
      'mixed_issue': 'warning',
      'unknown': 'info'
    };
    return classMap[rootCause] || 'info';
  }

  getRootCauseLabel(rootCause) {
    const labelMap = {
      'generator_logic_issue': '生成逻辑问题',
      'knowledge_gap_issue': '知识缺失',
      'qa_question_issue': '问题表达',
      'mixed_issue': '混合问题',
      'unknown': '未知原因'
    };
    return labelMap[rootCause] || '未知原因';
  }

  getStateClass(state) {
    const classMap = {
      'received_ticket': 'info',
      'analyzing': 'info',
      'counterfactual_checking': 'warning',
      'repair_plan_created': 'success',
      'dispatched': 'success'
    };
    return classMap[state] || 'info';
  }

  getStateLabel(state) {
    const labelMap = {
      'received_ticket': '已接收',
      'analyzing': '分析中',
      'counterfactual_checking': '对照实验中',
      'repair_plan_created': '计划已创建',
      'dispatched': '已分发'
    };
    return labelMap[state] || '未知状态';
  }

  updateBanner(message, type = 'info') {
    const banner = document.getElementById('banner');
    banner.textContent = message;
    banner.className = `banner ${type}`;
  }
}

// Initialize the app when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
  new RepairServiceApp();
});

// Global error handler
window.addEventListener('error', (event) => {
  console.error('Global error:', event.error);
  const app = window.repairApp;
  if (app) {
    app.updateBanner('发生错误: ' + event.message, 'error');
  }
});