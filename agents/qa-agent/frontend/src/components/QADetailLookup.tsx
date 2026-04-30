import { FormEvent } from "react";
import { QADetail } from "../lib/api";

interface QADetailLookupProps {
  query: string;
  detail?: QADetail;
  message: string;
  busy: boolean;
  actionBusy: boolean;
  onQueryChange: (value: string) => void;
  onSearch: () => void;
  onRedispatch: () => void;
  onDelete: () => void;
}

function renderAnswer(answer: unknown): string {
  if (answer === undefined || answer === null || answer === "") {
    return "[]";
  }
  return JSON.stringify(answer, null, 2);
}

function answerCount(answer: unknown): number {
  return Array.isArray(answer) ? answer.length : answer ? 1 : 0;
}

export function QADetailLookup({
  query,
  detail,
  message,
  busy,
  actionBusy,
  onQueryChange,
  onSearch,
  onRedispatch,
  onDelete,
}: QADetailLookupProps) {
  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    onSearch();
  }

  return (
    <section className="surface qa-detail-lookup" data-testid="qa-lookup-panel">
      <div className="panel-heading-row compact">
        <div>
          <div className="eyebrow">QA 详情</div>
          <h3>按 QA ID 查询</h3>
        </div>
        {detail ? <span className="status-pill status-success">{detail.difficulty ?? "未知难度"}</span> : null}
      </div>

      <form className="qa-detail-search" onSubmit={handleSubmit}>
        <input
          className="text-input compact-input"
          data-testid="qa-id-input"
          type="text"
          value={query}
          onChange={(event) => onQueryChange(event.target.value)}
          placeholder="输入 QA ID，例如 qa_001"
        />
        <button className="button primary" type="submit" disabled={busy || !query.trim()}>
          {busy ? "查询中..." : "查询"}
        </button>
      </form>
      <div className="notice">{message}</div>

      {detail ? (
        <div className="qa-detail-result" data-testid="qa-detail-panel">
          <div className="result-summary-grid compact">
            <div className="result-summary-card">
              <span>QA ID</span>
              <strong className="small-strong">{detail.id}</strong>
            </div>
            <div className="result-summary-card">
              <span>来源批次</span>
              <strong className="small-strong">{detail.job_id}</strong>
            </div>
            <div className="result-summary-card">
              <span>Release 文件</span>
              <strong className="small-strong">{detail.source_file}</strong>
            </div>
            <div className="result-summary-card">
              <span>Answer 行数</span>
              <strong>{answerCount(detail.answer)}</strong>
            </div>
          </div>

          <div className="result-block">
            <div className="result-block-head">
              <h4>Question</h4>
            </div>
            <p className="qa-detail-copy">{detail.question ?? "-"}</p>
          </div>

          <div className="result-block">
            <div className="result-block-head">
              <h4>Cypher</h4>
            </div>
            <pre className="code-block"><code>{detail.cypher ?? ""}</code></pre>
          </div>

          <div className="result-block">
            <div className="result-block-head">
              <h4>Answer</h4>
            </div>
            <pre className="code-block"><code>{renderAnswer(detail.answer)}</code></pre>
          </div>

          <div className="result-action-row">
            <button
              className="button secondary"
              data-testid="qa-redispatch-button"
              type="button"
              onClick={onRedispatch}
              disabled={actionBusy}
            >
              {actionBusy ? "处理中..." : "重新发送"}
            </button>
            <button className="button danger" data-testid="qa-delete-button" type="button" onClick={onDelete} disabled={actionBusy}>
              删除
            </button>
          </div>
        </div>
      ) : null}
    </section>
  );
}
