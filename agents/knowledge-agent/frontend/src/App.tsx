import { useState } from "react";
import type { FormEvent } from "react";

import { DiffViewer } from "./components/DiffViewer";
import { applyRepair, fetchPromptPackage } from "./lib/api";
import type { KnowledgeType, RepairChange } from "./lib/api";

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
      </main>
    </div>
  );
}
