import { useEffect, useState } from "react";
import type { FormEvent } from "react";

import { DiffViewer } from "./components/DiffViewer";
import {
  applyRepair,
  fetchKnowledgeDocument,
  fetchPromptPackage,
  listKnowledgeDocuments,
  saveKnowledgeDocument,
} from "./lib/api";
import type { KnowledgeDocumentDetail, KnowledgeDocumentSummary, KnowledgeDocumentType, KnowledgeType, RepairChange } from "./lib/api";

const KNOWLEDGE_TYPE_OPTIONS: Array<{ value: KnowledgeType; label: string; note: string }> = [
  { value: "cypher_syntax", label: "Cypher Syntax", note: "TuGraph 方言限制和改写规则" },
  { value: "few_shot", label: "Few-shot", note: "高质量问句与 Cypher 示例" },
  { value: "system_prompt", label: "System Prompt", note: "生成策略和输出约束" },
  { value: "business_knowledge", label: "Business Knowledge", note: "术语别名与业务语义映射" },
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

  const [documents, setDocuments] = useState<KnowledgeDocumentSummary[]>([]);
  const [selectedDocType, setSelectedDocType] = useState<KnowledgeDocumentType>("business_knowledge");
  const [documentDetail, setDocumentDetail] = useState<KnowledgeDocumentDetail | null>(null);
  const [documentContent, setDocumentContent] = useState("");
  const [documentBusy, setDocumentBusy] = useState(false);
  const [documentSaveBusy, setDocumentSaveBusy] = useState(false);
  const [documentError, setDocumentError] = useState("");
  const [documentStatus, setDocumentStatus] = useState("");

  useEffect(() => {
    let active = true;

    async function loadDocuments() {
      setDocumentError("");
      try {
        const response = await listKnowledgeDocuments();
        if (!active) {
          return;
        }
        setDocuments(response.documents);
        if (!response.documents.some((item) => item.doc_type === selectedDocType)) {
          const preferred = response.documents.find((item) => item.editable) ?? response.documents[0];
          if (preferred) {
            setSelectedDocType(preferred.doc_type);
          }
        }
      } catch (error) {
        if (active) {
          setDocumentError(error instanceof Error ? error.message : "获取知识文档失败");
        }
      }
    }

    loadDocuments();
    return () => {
      active = false;
    };
  }, [selectedDocType]);

  useEffect(() => {
    let active = true;

    async function loadDocument() {
      setDocumentBusy(true);
      setDocumentError("");
      setDocumentStatus("");
      try {
        const response = await fetchKnowledgeDocument(selectedDocType);
        if (!active) {
          return;
        }
        setDocumentDetail(response);
        setDocumentContent(response.content);
      } catch (error) {
        if (active) {
          setDocumentError(error instanceof Error ? error.message : "读取知识文档失败");
          setDocumentDetail(null);
          setDocumentContent("");
        }
      } finally {
        if (active) {
          setDocumentBusy(false);
        }
      }
    }

    loadDocument();
    return () => {
      active = false;
    };
  }, [selectedDocType]);

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

  function toggleKnowledgeType(type: KnowledgeType) {
    setSelectedTypes((current) =>
      current.includes(type) ? current.filter((item) => item !== type) : [...current, type],
    );
  }

  async function handleDocumentSave() {
    if (!documentDetail?.editable) {
      return;
    }
    setDocumentSaveBusy(true);
    setDocumentError("");
    setDocumentStatus("");
    try {
      const response = await saveKnowledgeDocument(selectedDocType, documentContent);
      setDocumentDetail(response.document);
      setDocumentContent(response.document.content);
      setDocuments((current) =>
        current.map((item) => (item.doc_type === response.document.doc_type ? response.document : item)),
      );
      setDocumentStatus(`已保存 ${formatDateTime(response.document.updated_at)}`);
    } catch (error) {
      setDocumentError(error instanceof Error ? error.message : "保存知识文档失败");
    } finally {
      setDocumentSaveBusy(false);
    }
  }

  const documentDirty = Boolean(documentDetail && documentContent !== documentDetail.content);

  return (
    <div className="app-shell">
      <div className="ambient ambient-left" />
      <div className="ambient ambient-right" />

      <header className="hero">
        <div className="hero-copy">
          <p className="eyebrow">Knowledge Agent Console</p>
          <h1>让 Text2Cypher 的知识获取和修复回写都落在一个可见界面里。</h1>
          <p className="hero-text">
            左侧直接生成给 Cypher agent 使用的知识提示词，右侧把修复建议写回知识文档，并用接近 git diff 的方式检查实际变更。
          </p>
        </div>

        <aside className="hero-aside">
          <div className="signal">
            <span className="signal-label">Knowledge Sources</span>
            <strong>Schema + Syntax + Prompt + Few-shot + Business</strong>
          </div>
          <div className="signal">
            <span className="signal-label">Repair Output</span>
            <strong>Document / Section / Before / After</strong>
          </div>
        </aside>
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
              <p className="field-hint">`schema` 不会出现在这里，因为它是只读来源，不允许被界面改写。</p>
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

        <section className="surface surface-editor">
          <div className="surface-header editor-header">
            <div>
              <p className="surface-kicker">03 · 知识展示与编辑</p>
              <h2>查看全部知识文档，直接修改可编辑知识</h2>
            </div>
            <div className="editor-status">
              {documentBusy ? "读取中" : documentDirty ? "有未保存修改" : documentDetail?.editable ? "已同步" : "只读"}
            </div>
          </div>

          <div className="knowledge-editor-layout">
            <nav className="document-list" aria-label="Knowledge documents">
              {documents.map((document) => {
                const active = document.doc_type === selectedDocType;
                return (
                  <button
                    key={document.doc_type}
                    type="button"
                    className={`document-tab ${active ? "document-tab-active" : ""}`}
                    onClick={() => setSelectedDocType(document.doc_type)}
                  >
                    <span>
                      <strong>{document.title}</strong>
                      <small>{document.filename}</small>
                    </span>
                    <em>{document.editable ? "可编辑" : "只读"}</em>
                  </button>
                );
              })}
            </nav>

            <div className="document-editor">
              <div className="document-toolbar">
                <div>
                  <span className="document-title">{documentDetail?.title ?? "Knowledge Document"}</span>
                  <span className="document-meta">
                    {documentDetail
                      ? `${documentDetail.filename} · ${documentDetail.size} bytes · ${formatDateTime(documentDetail.updated_at)}`
                      : "等待选择文档"}
                  </span>
                </div>
                {documentDetail?.editable ? (
                  <button
                    className="primary-action document-save"
                    type="button"
                    disabled={!documentDirty || documentSaveBusy}
                    onClick={handleDocumentSave}
                  >
                    {documentSaveBusy ? "保存中..." : "保存知识"}
                  </button>
                ) : (
                  <span className="readonly-badge">Schema 只读</span>
                )}
              </div>

              {documentError ? <p className="error-banner">{documentError}</p> : null}
              {documentStatus ? <p className="success-banner">{documentStatus}</p> : null}

              {documentDetail?.editable ? (
                <textarea
                  className="knowledge-document-textarea"
                  value={documentContent}
                  onChange={(event) => setDocumentContent(event.target.value)}
                  spellCheck={false}
                />
              ) : (
                <pre className="knowledge-document-viewer">
                  {documentContent || (documentBusy ? "正在读取知识文档..." : "选择 schema 可查看只读内容。")}
                </pre>
              )}
            </div>
          </div>
        </section>
      </main>
    </div>
  );
}

function formatDateTime(value: string): string {
  if (!value) {
    return "未知时间";
  }
  return new Date(value).toLocaleString();
}
