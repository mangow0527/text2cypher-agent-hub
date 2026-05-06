import { useEffect, useState } from "react";
import type { FormEvent } from "react";

import { DiffViewer } from "./components/DiffViewer";
import {
  applyRepair,
  approveRepairAgentRun,
  createKnowledgeTreeNode,
  deleteKnowledgeTreeNode,
  fetchRepairAgentRun,
  fetchKnowledgeTree,
  fetchKnowledgeTreeNode,
  fetchPromptPackage,
  listRepairAgentRuns,
  rejectRepairAgentRun,
  updateKnowledgeTreeNode,
} from "./lib/api";
import type {
  AgentRun,
  AgentRunStatus,
  KnowledgeTreeNode,
  KnowledgeTreeNodeDetail,
  KnowledgeTreeNodeKind,
  KnowledgeType,
  RepairChange,
} from "./lib/api";

const KNOWLEDGE_TYPE_OPTIONS: Array<{ value: KnowledgeType; label: string; note: string }> = [
  { value: "cypher_syntax", label: "Cypher Syntax", note: "TuGraph 方言限制和改写规则" },
  { value: "few_shot", label: "Few-shot", note: "高质量问句与 Cypher 示例" },
  { value: "system_prompt", label: "System Prompt", note: "生成策略和输出约束" },
  { value: "business_knowledge", label: "Business Knowledge", note: "术语别名与业务语义映射" },
];

const CREATABLE_NODE_KINDS: Array<{ value: KnowledgeTreeNodeKind; label: string }> = [
  { value: "business_semantic", label: "业务语义" },
  { value: "relation_path", label: "关系路径" },
  { value: "few_shot", label: "Few-shot" },
  { value: "rule", label: "通用规则" },
];

const AGENT_STATUS_OPTIONS: Array<{ value: AgentRunStatus; label: string }> = [
  { value: "created", label: "已创建" },
  { value: "running", label: "运行中" },
  { value: "needs_review", label: "待审核" },
  { value: "approved", label: "已批准" },
  { value: "applied", label: "已写入" },
  { value: "redispatched", label: "已重派" },
  { value: "completed", label: "已完成" },
  { value: "rejected", label: "已拒绝" },
  { value: "failed", label: "失败" },
];

export default function App() {
  const [promptId, setPromptId] = useState("q_001");
  const [question, setQuestion] = useState("查询协议版本为v2.0的隧道所属网元");
  const [prompt, setPrompt] = useState("");
  const [promptBusy, setPromptBusy] = useState(false);
  const [promptError, setPromptError] = useState("");

  const [repairId, setRepairId] = useState("q_001");
  const [suggestion, setSuggestion] = useState("补充协议版本映射，并强化对应路径示例");
  const [selectedTypes, setSelectedTypes] = useState<KnowledgeType[]>(["business_knowledge", "few_shot"]);
  const [repairBusy, setRepairBusy] = useState(false);
  const [repairError, setRepairError] = useState("");
  const [changes, setChanges] = useState<RepairChange[]>([]);

  const [agentRuns, setAgentRuns] = useState<AgentRun[]>([]);
  const [agentRunStatus, setAgentRunStatus] = useState<AgentRunStatus>("needs_review");
  const [selectedAgentRunId, setSelectedAgentRunId] = useState("");
  const [selectedAgentRun, setSelectedAgentRun] = useState<AgentRun | null>(null);
  const [agentBusy, setAgentBusy] = useState(false);
  const [agentActionBusy, setAgentActionBusy] = useState(false);
  const [agentError, setAgentError] = useState("");
  const [agentStatus, setAgentStatus] = useState("");
  const [rejectReason, setRejectReason] = useState("需要人工补充更多上下文");

  const [tree, setTree] = useState<KnowledgeTreeNode[]>([]);
  const [expandedNodeIds, setExpandedNodeIds] = useState<Set<string>>(
    () => new Set(["group:business_objects", "group:rules", "concept:NetworkElement"]),
  );
  const [selectedNodeId, setSelectedNodeId] = useState("");
  const [selectedNode, setSelectedNode] = useState<KnowledgeTreeNodeDetail | null>(null);
  const [treeContent, setTreeContent] = useState("");
  const [treeBusy, setTreeBusy] = useState(false);
  const [treeSaveBusy, setTreeSaveBusy] = useState(false);
  const [treeError, setTreeError] = useState("");
  const [treeStatus, setTreeStatus] = useState("");
  const [treeSearch, setTreeSearch] = useState("");
  const [newNodeTitle, setNewNodeTitle] = useState("");
  const [newNodeKind, setNewNodeKind] = useState<KnowledgeTreeNodeKind>("business_semantic");
  const [newNodeContent, setNewNodeContent] = useState("");

  useEffect(() => {
    let active = true;

    async function loadTree() {
      setTreeBusy(true);
      setTreeError("");
      try {
        const response = await fetchKnowledgeTree();
        if (!active) {
          return;
        }
        setTree(response.tree);
        const preferred =
          findFirstNode(response.tree, (node) => node.id === "concept:NetworkElement") ??
          findFirstNode(response.tree, (node) => node.kind === "concept") ??
          findFirstNode(response.tree, () => true);
        setSelectedNodeId((current) => {
          if (current && findFirstNode(response.tree, (node) => node.id === current)) {
            return current;
          }
          return preferred?.id ?? "";
        });
      } catch (error) {
        if (active) {
          setTreeError(error instanceof Error ? error.message : "获取知识树失败");
        }
      } finally {
        if (active) {
          setTreeBusy(false);
        }
      }
    }

    loadTree();
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (!selectedNodeId) {
      return;
    }
    let active = true;

    async function loadNode() {
      setTreeBusy(true);
      setTreeError("");
      setTreeStatus("");
      try {
        const response = await fetchKnowledgeTreeNode(selectedNodeId);
        if (!active) {
          return;
        }
        setSelectedNode(response.node);
        setTreeContent(response.node.content);
      } catch (error) {
        if (active) {
          setTreeError(error instanceof Error ? error.message : "读取知识节点失败");
          setSelectedNode(null);
          setTreeContent("");
        }
      } finally {
        if (active) {
          setTreeBusy(false);
        }
      }
    }

    loadNode();
    return () => {
      active = false;
    };
  }, [selectedNodeId]);

  useEffect(() => {
    void loadAgentRuns(agentRunStatus);
  }, [agentRunStatus]);

  async function handlePromptSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPromptBusy(true);
    setPromptError("");
    try {
      const response = await fetchPromptPackage({ id: promptId, question });
      setPrompt(response.prompt);
    } catch (error) {
      setPromptError(error instanceof Error ? error.message : "获取知识失败");
    } finally {
      setPromptBusy(false);
    }
  }

  async function handleRepairSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setRepairBusy(true);
    setRepairError("");
    try {
      const response = await applyRepair({
        id: repairId,
        suggestion,
        knowledge_types: selectedTypes.length ? selectedTypes : undefined,
      });
      setChanges(response.changes);
    } catch (error) {
      setRepairError(error instanceof Error ? error.message : "应用修复失败");
    } finally {
      setRepairBusy(false);
    }
  }

  async function loadAgentRuns(status: AgentRunStatus) {
    setAgentBusy(true);
    setAgentError("");
    setAgentStatus("");
    try {
      const response = await listRepairAgentRuns(status);
      setAgentRuns(response.runs);
      setSelectedAgentRun((current) => {
        const refreshed = current ? response.runs.find((run) => run.run_id === current.run_id) : null;
        if (refreshed) {
          return refreshed;
        }
        return response.runs[0] ?? null;
      });
      setSelectedAgentRunId((current) => {
        if (current && response.runs.some((run) => run.run_id === current)) {
          return current;
        }
        return response.runs[0]?.run_id ?? "";
      });
    } catch (error) {
      setAgentError(error instanceof Error ? error.message : "读取 Agent 修复任务失败");
    } finally {
      setAgentBusy(false);
    }
  }

  async function handleAgentRunSelect(runId: string) {
    setSelectedAgentRunId(runId);
    setAgentBusy(true);
    setAgentError("");
    setAgentStatus("");
    try {
      const response = await fetchRepairAgentRun(runId);
      setSelectedAgentRun(response.run);
    } catch (error) {
      setAgentError(error instanceof Error ? error.message : "读取 Agent Run 失败");
    } finally {
      setAgentBusy(false);
    }
  }

  async function handleAgentApprove() {
    if (!selectedAgentRun) {
      return;
    }
    setAgentActionBusy(true);
    setAgentError("");
    setAgentStatus("");
    try {
      const response = await approveRepairAgentRun(selectedAgentRun.run_id);
      setSelectedAgentRun(response.run);
      await loadAgentRuns(agentRunStatus);
      setAgentStatus("已批准并触发修复闭环");
    } catch (error) {
      setAgentError(error instanceof Error ? error.message : "批准失败");
    } finally {
      setAgentActionBusy(false);
    }
  }

  async function handleAgentReject(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedAgentRun || !rejectReason.trim()) {
      return;
    }
    setAgentActionBusy(true);
    setAgentError("");
    setAgentStatus("");
    try {
      const response = await rejectRepairAgentRun(selectedAgentRun.run_id, rejectReason.trim());
      setSelectedAgentRun(response.run);
      await loadAgentRuns(agentRunStatus);
      setAgentStatus("已拒绝并记录原因");
    } catch (error) {
      setAgentError(error instanceof Error ? error.message : "拒绝失败");
    } finally {
      setAgentActionBusy(false);
    }
  }

  function toggleKnowledgeType(type: KnowledgeType) {
    setSelectedTypes((current) =>
      current.includes(type) ? current.filter((item) => item !== type) : [...current, type],
    );
  }

  function toggleExpanded(nodeId: string) {
    setExpandedNodeIds((current) => {
      const next = new Set(current);
      if (next.has(nodeId)) {
        next.delete(nodeId);
      } else {
        next.add(nodeId);
      }
      return next;
    });
  }

  async function handleTreeSave() {
    if (!selectedNode?.editable) {
      return;
    }
    setTreeSaveBusy(true);
    setTreeError("");
    setTreeStatus("");
    try {
      const response = await updateKnowledgeTreeNode(selectedNode.id, treeContent);
      setTree(response.tree);
      setSelectedNode(response.node);
      setSelectedNodeId(response.node.id);
      setTreeContent(response.node.content);
      setTreeStatus("节点已保存");
    } catch (error) {
      setTreeError(error instanceof Error ? error.message : "保存知识节点失败");
    } finally {
      setTreeSaveBusy(false);
    }
  }

  async function handleTreeCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedNode || !newNodeTitle.trim()) {
      return;
    }
    setTreeSaveBusy(true);
    setTreeError("");
    setTreeStatus("");
    try {
      const response = await createKnowledgeTreeNode({
        parent_id: selectedNode.id,
        title: newNodeTitle,
        kind: newNodeKind,
        content: newNodeContent || "- 新知识节点\n",
      });
      setTree(response.tree);
      setSelectedNode(response.node);
      setSelectedNodeId(response.node.id);
      setTreeContent(response.node.content);
      setExpandedNodeIds((current) => new Set([...current, selectedNode.id]));
      setNewNodeTitle("");
      setNewNodeContent("");
      setTreeStatus("节点已新增");
    } catch (error) {
      setTreeError(error instanceof Error ? error.message : "新增知识节点失败");
    } finally {
      setTreeSaveBusy(false);
    }
  }

  async function handleTreeDelete() {
    if (!selectedNode?.editable) {
      return;
    }
    setTreeSaveBusy(true);
    setTreeError("");
    setTreeStatus("");
    try {
      await deleteKnowledgeTreeNode(selectedNode.id);
      const response = await fetchKnowledgeTree();
      setTree(response.tree);
      const nextNode = findFirstNode(response.tree, (node) => node.editable) ?? findFirstNode(response.tree, () => true);
      setSelectedNodeId(nextNode?.id ?? "");
      setTreeStatus("节点已删除");
    } catch (error) {
      setTreeError(error instanceof Error ? error.message : "删除知识节点失败");
    } finally {
      setTreeSaveBusy(false);
    }
  }

  const visibleTree = filterTree(tree, treeSearch);
  const treeDirty = Boolean(selectedNode && treeContent !== selectedNode.content);
  const selectedNodeWritable = Boolean(selectedNode?.editable && selectedNode.source_file && selectedNode.section_id);

  return (
    <div className="app-shell">
      <header className="hero">
        <div className="hero-copy">
          <p className="eyebrow">Knowledge Agent Console</p>
          <h1>管理 Text2Cypher 生成所需的语义知识、修复建议和 Prompt 上下文。</h1>
          <p className="hero-text">
            以业务对象为中心维护知识树，保留 schema 只读边界，并把每次编辑写回可审计的知识文件。
          </p>
        </div>
      </header>

      <main className="workspace">
        <section className="surface surface-query">
          <div className="surface-header">
            <p className="surface-kicker">01 · 获取相关知识</p>
            <h2>输入问题，直接拿到可供生成模型使用的知识提示词</h2>
          </div>

          <form className="stack-form" onSubmit={handlePromptSubmit}>
            <label className="field">
              <span>ID</span>
              <input value={promptId} onChange={(event) => setPromptId(event.target.value)} placeholder="q_001" />
            </label>

            <label className="field">
              <span>Question</span>
              <textarea
                value={question}
                onChange={(event) => setQuestion(event.target.value)}
                rows={5}
                placeholder="输入业务问题，系统会拼成最终知识提示词"
              />
            </label>

            <button className="primary-action" type="submit" disabled={promptBusy}>
              {promptBusy ? "正在获取..." : "获取相关知识"}
            </button>
          </form>

          {promptError ? <p className="error-banner">{promptError}</p> : null}

          <div className="result-shell">
            <div className="result-header">
              <span>Prompt Output</span>
              <span className="result-meta">{prompt ? `${prompt.length} chars` : "等待请求"}</span>
            </div>
            <pre className="prompt-output">{prompt || "这里会显示拼装好的知识提示词字符串。"}</pre>
          </div>
        </section>

        <section className="surface surface-repair">
          <div className="surface-header">
            <p className="surface-kicker">02 · 应用修复</p>
            <h2>选择知识类型并提交修复建议，直接查看修改了哪一段知识</h2>
          </div>

          <form className="stack-form" onSubmit={handleRepairSubmit}>
            <label className="field">
              <span>ID</span>
              <input value={repairId} onChange={(event) => setRepairId(event.target.value)} placeholder="q_001" />
            </label>

            <div className="field">
              <span>Knowledge Types</span>
              <div className="knowledge-type-grid">
                {KNOWLEDGE_TYPE_OPTIONS.map((option) => {
                  const active = selectedTypes.includes(option.value);
                  return (
                    <button
                      key={option.value}
                      type="button"
                      className={`toggle-tile ${active ? "toggle-tile-active" : ""}`}
                      onClick={() => toggleKnowledgeType(option.value)}
                    >
                      <strong>{option.label}</strong>
                      <small>{option.note}</small>
                    </button>
                  );
                })}
              </div>
              <p className="field-hint">schema 是只读来源，不会被 repair 直接改写。</p>
            </div>

            <label className="field">
              <span>Suggestion</span>
              <textarea
                value={suggestion}
                onChange={(event) => setSuggestion(event.target.value)}
                rows={5}
                placeholder="例如：补充协议版本映射，并追加一条网元到隧道到协议的 few-shot"
              />
            </label>

            <button className="primary-action" type="submit" disabled={repairBusy}>
              {repairBusy ? "正在写入..." : "提交修复建议"}
            </button>
          </form>

          {repairError ? <p className="error-banner">{repairError}</p> : null}

          <div className="diff-section">
            <div className="result-header">
              <span>Knowledge Changes</span>
              <span className="result-meta">{changes.length ? `${changes.length} updated` : "等待修复写入"}</span>
            </div>

            {changes.length ? (
              <div className="diff-list">
                {changes.map((change, index) => (
                  <DiffViewer key={`${change.doc_type}-${change.section}-${index}`} change={change} />
                ))}
              </div>
            ) : (
              <div className="empty-state">
                <p>提交修复建议后，这里会显示对应文档和 section 的 before / after 差异。</p>
              </div>
            )}
          </div>
        </section>

        <section className="agent-review-console">
          <div className="console-toolbar">
            <div>
              <p className="surface-kicker">03 · Agent 修复审核台</p>
              <h2>处理需要人工确认的知识修复任务</h2>
            </div>
            <div className="agent-toolbar-actions">
              <select value={agentRunStatus} onChange={(event) => setAgentRunStatus(event.target.value as AgentRunStatus)}>
                {AGENT_STATUS_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
              <button type="button" className="secondary-action" onClick={() => void loadAgentRuns(agentRunStatus)} disabled={agentBusy}>
                {agentBusy ? "刷新中..." : "刷新"}
              </button>
            </div>
          </div>

          {agentError ? <p className="error-banner agent-banner">{agentError}</p> : null}
          {agentStatus ? <p className="success-banner agent-banner">{agentStatus}</p> : null}

          <div className="agent-review-layout">
            <aside className="agent-run-list">
              {agentRuns.length ? (
                agentRuns.map((run) => (
                  <button
                    key={run.run_id}
                    type="button"
                    className={`agent-run-row ${selectedAgentRunId === run.run_id ? "agent-run-row-active" : ""}`}
                    onClick={() => void handleAgentRunSelect(run.run_id)}
                  >
                    <span>
                      <strong>{run.qa_id}</strong>
                      <small>{run.run_id}</small>
                    </span>
                    <span className={`agent-status-pill agent-status-${run.status}`}>{formatAgentStatus(run.status)}</span>
                  </button>
                ))
              ) : (
                <div className="empty-state agent-empty">
                  <p>当前状态下没有修复任务。</p>
                </div>
              )}
            </aside>

            <section className="agent-run-detail">
              {selectedAgentRun ? (
                <>
                  <div className="agent-detail-header">
                    <div>
                      <span className={`agent-status-pill agent-status-${selectedAgentRun.status}`}>
                        {formatAgentStatus(selectedAgentRun.status)}
                      </span>
                      <h3>{selectedAgentRun.qa_id}</h3>
                      <p>{selectedAgentRun.goal}</p>
                    </div>
                    <div className="agent-run-id">{selectedAgentRun.run_id}</div>
                  </div>

                  <div className="agent-meta-grid">
                    <span>根因：{selectedAgentRun.root_cause.type}</span>
                    <span>候选：{selectedAgentRun.candidate_changes.length}</span>
                    <span>Trace：{selectedAgentRun.trace.length}</span>
                    <span>Memory：{selectedAgentRun.memory_hits.length}</span>
                  </div>

                  <div className="agent-section">
                    <h4>Root Cause</h4>
                    <p>{selectedAgentRun.root_cause.summary}</p>
                    <pre>{selectedAgentRun.root_cause.suggested_fix}</pre>
                  </div>

                  <div className="agent-section">
                    <h4>Gap Diagnosis</h4>
                    <p>{selectedAgentRun.gap_diagnosis.gap_type}</p>
                    <pre>{selectedAgentRun.gap_diagnosis.reason || "暂无诊断说明"}</pre>
                  </div>

                  <div className="agent-section">
                    <h4>Candidate Changes</h4>
                    {selectedAgentRun.candidate_changes.length ? (
                      <div className="agent-candidate-list">
                        {selectedAgentRun.candidate_changes.map((change) => (
                          <div key={`${change.doc_type}-${change.target_key}`} className="agent-candidate">
                            <div className="agent-candidate-header">
                              <strong>{change.doc_type}</strong>
                              <span>
                                {change.section} · {change.risk} · {Math.round(change.confidence * 100)}%
                              </span>
                            </div>
                            <code>{change.target_key}</code>
                            <pre>{change.new_content}</pre>
                            <div className="agent-checks">
                              <span>{change.duplicate_checked ? "已查重" : "未查重"}</span>
                              <span>{change.conflict_checked ? "已查冲突" : "未查冲突"}</span>
                            </div>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <p className="agent-muted">暂无候选知识变更。</p>
                    )}
                  </div>

                  <div className="agent-section">
                    <h4>Validation</h4>
                    <div className="agent-meta-grid agent-meta-grid-compact">
                      <span>Prompt：{selectedAgentRun.validation.prompt_package_built ? "已构建" : "未构建"}</span>
                      <span>改善：{selectedAgentRun.validation.before_after_improved ? "是" : "否"}</span>
                      <span>重派：{selectedAgentRun.validation.redispatch_status || "未触发"}</span>
                      <span>风险：{selectedAgentRun.validation.remaining_risks.length}</span>
                    </div>
                  </div>

                  <div className="agent-section">
                    <h4>Trace</h4>
                    {selectedAgentRun.trace.length ? (
                      <div className="agent-trace-list">
                        {selectedAgentRun.trace.map((entry) => (
                          <div key={entry.step} className="agent-trace-row">
                            <span>{entry.step}</span>
                            <strong>{entry.action.tool_name ?? entry.action.action}</strong>
                            <small>{entry.action.reason_summary}</small>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <p className="agent-muted">暂无执行轨迹。</p>
                    )}
                  </div>

                  <div className="agent-actions">
                    <button
                      type="button"
                      className="primary-action"
                      disabled={selectedAgentRun.status !== "needs_review" || agentActionBusy}
                      onClick={() => void handleAgentApprove()}
                    >
                      {agentActionBusy ? "处理中..." : "批准修复"}
                    </button>
                    <form className="agent-reject-form" onSubmit={handleAgentReject}>
                      <input value={rejectReason} onChange={(event) => setRejectReason(event.target.value)} placeholder="拒绝原因" />
                      <button
                        type="submit"
                        className="secondary-action"
                        disabled={selectedAgentRun.status !== "needs_review" || !rejectReason.trim() || agentActionBusy}
                      >
                        拒绝
                      </button>
                    </form>
                  </div>
                </>
              ) : (
                <div className="empty-state agent-empty">
                  <p>选择左侧任务查看详情。</p>
                </div>
              )}
            </section>
          </div>
        </section>

        <section className="knowledge-console">
          <div className="console-toolbar">
            <div>
              <p className="surface-kicker">04 · 知识树管理</p>
              <h2>按业务对象维护知识关系</h2>
            </div>
            <div className="editor-status">
              {treeBusy ? "读取中" : treeDirty ? "有未保存修改" : selectedNode?.editable ? "已同步" : "只读"}
            </div>
          </div>

          <div className="tree-manager">
            <aside className="tree-panel">
              <div className="tree-search-row">
                <input
                  value={treeSearch}
                  onChange={(event) => setTreeSearch(event.target.value)}
                  placeholder="搜索对象、路径、示例"
                />
              </div>
              <div className="tree-list">
                {visibleTree.map((node) => (
                  <TreeNodeView
                    key={node.id}
                    node={node}
                    depth={0}
                    expandedNodeIds={expandedNodeIds}
                    selectedNodeId={selectedNodeId}
                    onToggle={toggleExpanded}
                    onSelect={setSelectedNodeId}
                  />
                ))}
              </div>
            </aside>

            <section className="node-detail">
              <div className="node-detail-header">
                <div>
                  <span className="node-kind">{selectedNode?.kind ?? "knowledge"}</span>
                  <h3>{selectedNode?.title ?? "选择一个知识节点"}</h3>
                  <p>
                    {selectedNode
                      ? `${selectedNode.source_file ?? "logical tree"}${
                          selectedNode.section_id ? ` · ${selectedNode.section_id}` : ""
                        }`
                      : "从左侧知识树选择对象、路径或示例。"}
                  </p>
                </div>
                {selectedNode ? (
                  <span className={`node-state ${selectedNodeWritable ? "node-state-editable" : "node-state-readonly"}`}>
                    {selectedNodeWritable ? "可编辑" : selectedNode.kind === "concept" ? "容器" : "只读"}
                  </span>
                ) : null}
              </div>

              {treeError ? <p className="error-banner">{treeError}</p> : null}
              {treeStatus ? <p className="success-banner">{treeStatus}</p> : null}

              {selectedNode ? (
                <div className="node-meta-grid">
                  <span>对象：{selectedNode.concept ?? "无"}</span>
                  <span>类型：{selectedNode.kind}</span>
                  <span>来源：{selectedNode.source_file ?? "派生节点"}</span>
                  <span>写回：{selectedNode.section_id ?? "不写回"}</span>
                </div>
              ) : null}

              {selectedNodeWritable ? (
                <textarea
                  className="node-editor"
                  value={treeContent}
                  onChange={(event) => setTreeContent(event.target.value)}
                  spellCheck={false}
                />
              ) : (
                <pre className="node-viewer">{treeContent || "只读节点内容会显示在这里。"}</pre>
              )}

              <form className="node-create" onSubmit={handleTreeCreate}>
                <div className="node-create-fields">
                  <input
                    value={newNodeTitle}
                    onChange={(event) => setNewNodeTitle(event.target.value)}
                    placeholder="新增子节点标题"
                    disabled={!selectedNode}
                  />
                  <select
                    value={newNodeKind}
                    onChange={(event) => setNewNodeKind(event.target.value as KnowledgeTreeNodeKind)}
                    disabled={!selectedNode}
                  >
                    {CREATABLE_NODE_KINDS.map((kind) => (
                      <option key={kind.value} value={kind.value}>
                        {kind.label}
                      </option>
                    ))}
                  </select>
                </div>
                <textarea
                  value={newNodeContent}
                  onChange={(event) => setNewNodeContent(event.target.value)}
                  placeholder="新增节点内容，保存后写回对应知识文件"
                  disabled={!selectedNode}
                  rows={3}
                />
                <button type="submit" className="secondary-action" disabled={!selectedNode || !newNodeTitle.trim() || treeSaveBusy}>
                  新增子节点
                </button>
              </form>

              <div className="node-actions">
                <button type="button" className="secondary-action" disabled={!selectedNodeWritable || treeSaveBusy} onClick={handleTreeDelete}>
                  删除节点
                </button>
                <button
                  type="button"
                  className="primary-action document-save"
                  disabled={!selectedNodeWritable || !treeDirty || treeSaveBusy}
                  onClick={handleTreeSave}
                >
                  {treeSaveBusy ? "保存中..." : "保存节点"}
                </button>
              </div>
            </section>
          </div>
        </section>
      </main>
    </div>
  );
}

function TreeNodeView({
  node,
  depth,
  expandedNodeIds,
  selectedNodeId,
  onToggle,
  onSelect,
}: {
  node: KnowledgeTreeNode;
  depth: number;
  expandedNodeIds: Set<string>;
  selectedNodeId: string;
  onToggle: (nodeId: string) => void;
  onSelect: (nodeId: string) => void;
}) {
  const expanded = expandedNodeIds.has(node.id);
  const hasChildren = node.children.length > 0;
  return (
    <div>
      <div
        className={`tree-row ${selectedNodeId === node.id ? "tree-row-active" : ""}`}
        style={{ paddingLeft: `${12 + depth * 18}px` }}
      >
        <button
          type="button"
          className="tree-expander"
          onClick={() => (hasChildren ? onToggle(node.id) : onSelect(node.id))}
          aria-label={hasChildren ? "Toggle node" : "Select node"}
        >
          {hasChildren ? (expanded ? "▾" : "▸") : "•"}
        </button>
        <button type="button" className="tree-label" onClick={() => onSelect(node.id)}>
          <span>{node.title}</span>
          <small>{node.editable ? "编辑" : "只读"}</small>
        </button>
      </div>
      {hasChildren && expanded
        ? node.children.map((child) => (
            <TreeNodeView
              key={child.id}
              node={child}
              depth={depth + 1}
              expandedNodeIds={expandedNodeIds}
              selectedNodeId={selectedNodeId}
              onToggle={onToggle}
              onSelect={onSelect}
            />
          ))
        : null}
    </div>
  );
}

function findFirstNode(
  nodes: KnowledgeTreeNode[],
  predicate: (node: KnowledgeTreeNode) => boolean,
): KnowledgeTreeNode | null {
  for (const node of nodes) {
    if (predicate(node)) {
      return node;
    }
    const child = findFirstNode(node.children, predicate);
    if (child) {
      return child;
    }
  }
  return null;
}

function filterTree(nodes: KnowledgeTreeNode[], search: string): KnowledgeTreeNode[] {
  const keyword = search.trim().toLowerCase();
  if (!keyword) {
    return nodes;
  }
  return nodes
    .map((node) => {
      const children = filterTree(node.children, search);
      const matches =
        node.title.toLowerCase().includes(keyword) ||
        node.content_preview.toLowerCase().includes(keyword) ||
        (node.concept ?? "").toLowerCase().includes(keyword);
      return matches || children.length ? { ...node, children } : null;
    })
    .filter((node): node is KnowledgeTreeNode => Boolean(node));
}

function formatAgentStatus(status: AgentRunStatus): string {
  const match = AGENT_STATUS_OPTIONS.find((option) => option.value === status);
  return match?.label ?? status;
}
