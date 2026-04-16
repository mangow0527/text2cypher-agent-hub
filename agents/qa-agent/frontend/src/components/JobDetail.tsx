import { artifactUrl, JobRecord } from "../lib/api";

function readCoverage(metrics: Record<string, unknown>) {
  return (metrics.difficulty_coverage as {
    all_levels?: string[];
    covered_levels?: string[];
  }) || null;
}

function readDispatch(metrics: Record<string, unknown>) {
  return (metrics.dispatch as {
    enabled?: boolean;
    status?: string;
    success?: number;
    failed?: number;
    message?: string;
  }) || null;
}

function readSelection(metrics: Record<string, unknown>) {
  return (metrics.selection as {
    final_count?: number;
  }) || null;
}

function readDispatchHistory(metrics: Record<string, unknown>) {
  return (metrics.dispatch_history as Array<{
    dispatch_id?: string;
    created_at?: string;
    status?: string;
    success?: number;
    failed?: number;
  }>) || [];
}

export function JobDetail({
  job,
  onRedispatch,
  onDelete,
  onClose,
  dispatchBusy,
}: {
  job?: JobRecord;
  onRedispatch: (jobId: string) => Promise<void>;
  onDelete: (jobId: string) => Promise<void>;
  onClose: () => void;
  dispatchBusy: boolean;
}) {
  if (!job) {
    return (
      <section className="surface detail-surface result-surface">
        <div className="empty-state">
          <div className="eyebrow">本批结果</div>
          <h3>还没有可展示的批次</h3>
          <p>创建一批 QA 后，这里只会展示结果数量、难度标签和下载入口。</p>
        </div>
      </section>
    );
  }

  const coverage = readCoverage(job.metrics);
  const dispatch = readDispatch(job.metrics);
  const selection = readSelection(job.metrics);
  const dispatchHistory = readDispatchHistory(job.metrics);
  const finalCount = Number(selection?.final_count ?? job.metrics.sample_count ?? 0);
  const coveredLevels = new Set(coverage?.covered_levels || []);

  return (
    <section className="surface detail-surface result-surface">
      <div className="detail-hero result-hero">
        <div>
          <div className="eyebrow">本批结果</div>
          <h3>{job.job_id}</h3>
          <p>这一批生成了 {finalCount} 条 QA，对外只关注结果和覆盖情况。</p>
        </div>
        <div className="detail-hero-actions">
          <span className={`status-pill status-${job.status}`}>{job.status}</span>
          <button className="button tertiary" type="button" onClick={onClose}>
            收起
          </button>
        </div>
      </div>

      <div className="result-summary-grid">
        <div className="result-summary-card">
          <span>生成数量</span>
          <strong>{finalCount}</strong>
        </div>
        <div className="result-summary-card">
          <span>最近更新</span>
          <strong className="small-strong">{new Date(job.updated_at).toLocaleString()}</strong>
        </div>
      </div>

      <div className="result-block">
        <div className="result-block-head">
          <h4>难度覆盖</h4>
          <p>覆盖到的等级会高亮显示。</p>
        </div>
        <div className="coverage-strip large">
          {Array.from({ length: 8 }, (_, index) => `L${index + 1}`).map((level) => (
            <span key={level} className={`coverage-pill ${coveredLevels.has(level) ? "covered" : "missing"}`}>
              {level}
            </span>
          ))}
        </div>
      </div>

      <div className="result-block">
        <div className="result-block-head">
          <h4>批次操作</h4>
          <p>下载最终结果，或重新发送这一批 QA。</p>
        </div>
        <div className="result-action-row">
          <a className="button secondary" href={artifactUrl(job.job_id, "releases")} target="_blank" rel="noreferrer">
            下载 QA 结果
          </a>
          <a className="button secondary" href={artifactUrl(job.job_id, "report")} target="_blank" rel="noreferrer">
            下载报告
          </a>
          <button className="button primary" type="button" disabled={dispatchBusy} onClick={() => void onRedispatch(job.job_id)}>
            {dispatchBusy ? "重新发送中..." : "重新发送本批 QA"}
          </button>
          <button className="button tertiary danger" type="button" onClick={() => void onDelete(job.job_id)}>
            删除本批次
          </button>
        </div>
      </div>

      {dispatch ? (
        <div className="result-block">
          <div className="result-block-head">
            <h4>发送结果</h4>
            <p>{dispatch.enabled ? "下游同步状态" : dispatch.message || "未启用发送"}</p>
          </div>
          <div className="result-summary-grid compact">
            <div className="result-summary-card">
              <span>状态</span>
              <strong className="small-strong">{dispatch.status || "unknown"}</strong>
            </div>
            <div className="result-summary-card">
              <span>成功 / 失败</span>
              <strong className="small-strong">
                {dispatch.success ?? 0} / {dispatch.failed ?? 0}
              </strong>
            </div>
          </div>
          {dispatchHistory.length ? (
            <div className="dispatch-history">
              {dispatchHistory.slice().reverse().map((item) => (
                <div key={item.dispatch_id || item.created_at} className="dispatch-history-row">
                  <span>{item.created_at ? new Date(item.created_at).toLocaleString() : "unknown"}</span>
                  <span>{item.status || "unknown"}</span>
                  <span>成功 {item.success ?? 0}</span>
                  <span>失败 {item.failed ?? 0}</span>
                </div>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
