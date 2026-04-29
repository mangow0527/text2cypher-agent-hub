import { artifactUrl, JobRecord } from "../lib/api";
import { jobStatusLabel } from "./JobList";

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

function readBusinessStages(metrics: Record<string, unknown>) {
  return (metrics.business_stages as Array<{
    key: string;
    label: string;
    status: "pending" | "running" | "completed" | "failed" | "skipped";
    duration_ms?: number | null;
    message?: string;
  }>) || [];
}

function isCoverageSpecFlow(job: JobRecord, businessStages: Array<{ message?: string }>) {
  const stageText = [
    ...job.stages.map((stage) => stage.summary),
    ...businessStages.map((stage) => stage.message || ""),
  ].join(" ");
  return /coverage specs/i.test(stageText);
}

function buildUserProgress(job: JobRecord) {
  const status = job.status;
  const failed = status === "failed";
  const verifying = new Set(["validated", "questions_ready", "roundtrip_done", "deduped", "packaged"]);
  const complete = status === "completed";
  const verifyingStarted = verifying.has(status) || complete;

  return [
    {
      key: "created",
      label: "创建任务",
      description: failed ? "任务已创建" : "任务已进入生成队列",
      status: failed || verifyingStarted || complete ? "completed" : "running",
    },
    {
      key: "verified",
      label: "验证 QA",
      description: complete ? "QA 对已通过校验" : failed ? "验证或生成失败" : verifyingStarted ? "正在校验生成结果" : "等待生成结果",
      status: complete ? "completed" : failed ? "failed" : verifyingStarted ? "running" : "pending",
    },
    {
      key: "completed",
      label: "完成结果",
      description: complete ? "结果已可下载" : failed ? "未生成可下载结果" : "等待任务完成",
      status: complete ? "completed" : failed ? "failed" : "pending",
    },
  ];
}

const userProgressLabel: Record<string, string> = {
  pending: "等待中",
  running: "进行中",
  completed: "已完成",
  failed: "失败",
};

export function JobDetail({
  job,
  onRegenerate,
  onRedispatch,
  onDelete,
  onClose,
  regenerateBusy,
  dispatchBusy,
}: {
  job?: JobRecord;
  onRegenerate: (jobId: string) => Promise<void>;
  onRedispatch: (jobId: string) => Promise<void>;
  onDelete: (jobId: string) => Promise<void>;
  onClose: () => void;
  regenerateBusy: boolean;
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
  const selection = readSelection(job.metrics);
  const businessStages = readBusinessStages(job.metrics);
  const finalCount = Number(selection?.final_count ?? job.metrics.sample_count ?? 0);
  const hasReleases = Boolean(job.artifacts.releases);
  const hasReport = Boolean(job.artifacts.report);
  const canRegenerate = job.status === "failed";
  const coveredLevels = new Set(coverage?.covered_levels || []);
  const allLevels = coverage?.all_levels?.length ? coverage.all_levels : Array.from({ length: 8 }, (_, index) => `L${index + 1}`);
  const coveragePercent = Math.round((coveredLevels.size / Math.max(allLevels.length, 1)) * 100);
  const userProgress = buildUserProgress(job);
  const completedUserStages = userProgress.filter((stage) => stage.status === "completed").length;
  const runningUserStages = userProgress.some((stage) => stage.status === "running") ? 0.5 : 0;
  const progressPercent = Math.round(((completedUserStages + runningUserStages) / userProgress.length) * 100);
  const coverageSpecFlow = isCoverageSpecFlow(job, businessStages);

  return (
    <section className="surface detail-surface result-surface">
      <div className="detail-hero result-hero">
        <div>
          <div className="eyebrow">本批结果</div>
          <h3>{job.job_id}</h3>
          <p>这一批生成了 {finalCount} 条 QA，对外只关注结果和覆盖情况。</p>
        </div>
        <div className="detail-hero-actions">
          {!coverageSpecFlow ? <span className="status-pill status-skipped">历史批次</span> : null}
          <span className={`status-pill status-${job.status}`}>{jobStatusLabel(job.status)}</span>
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
          <span>难度覆盖</span>
          <strong>{coveredLevels.size}/{allLevels.length}</strong>
        </div>
        <div className="result-summary-card">
          <span>最近更新</span>
          <strong className="small-strong">{new Date(job.updated_at).toLocaleString()}</strong>
        </div>
      </div>

      <div className="result-block">
        <div className="result-block-head">
          <h4>生成进度</h4>
          <p>创建、验证、完成</p>
        </div>
        <div className="job-progress-shell">
          <div className="job-progress-bar">
            <div className="job-progress-fill" style={{ width: `${progressPercent}%` }} />
          </div>
          <span className="job-progress-label">{progressPercent}%</span>
        </div>
        <div className="business-stage-list">
          {userProgress.map((stage, index) => (
            <div key={stage.key} className={`business-stage-card business-stage-${stage.status}`}>
              <div className="business-stage-top">
                <strong><span>{index + 1}</span>{stage.label}</strong>
                <span className={`status-pill status-${stage.status}`}>{userProgressLabel[stage.status] || stage.status}</span>
              </div>
              <div className="business-stage-meta">
                <span>{stage.description}</span>
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="result-block">
        <div className="result-block-head">
          <h4>难度覆盖</h4>
          <p>当前批次覆盖 {coveredLevels.size}/{allLevels.length}</p>
        </div>
        <div className="coverage-meter" aria-label={`难度覆盖 ${coveragePercent}%`}>
          <div style={{ width: `${coveragePercent}%` }} />
        </div>
        <div className="coverage-strip large">
          {allLevels.map((level) => (
            <span key={level} className={`coverage-pill ${coveredLevels.has(level) ? "covered" : "missing"}`}>
              {level}
            </span>
          ))}
        </div>
      </div>

      <div className="result-block">
        <div className="result-block-head">
          <h4>批次操作</h4>
          <p>{canRegenerate ? "重新生成失败任务，或查看已有产物。" : "下载最终结果，或重新发送这一批 QA。"}</p>
        </div>
        <div className="result-action-row">
          {canRegenerate ? (
            <button className="button primary" type="button" disabled={regenerateBusy} onClick={() => void onRegenerate(job.job_id)}>
              {regenerateBusy ? "重新生成中..." : "重新生成"}
            </button>
          ) : null}
          {hasReleases ? (
            <a className="button secondary" href={artifactUrl(job.job_id, "releases")} target="_blank" rel="noreferrer">
              下载 QA 结果
            </a>
          ) : (
            <button className="button secondary" type="button" disabled>
              下载 QA 结果
            </button>
          )}
          {hasReport ? (
            <a className="button secondary" href={artifactUrl(job.job_id, "report")} target="_blank" rel="noreferrer">
              下载报告
            </a>
          ) : (
            <button className="button secondary" type="button" disabled>
              下载报告
            </button>
          )}
          {hasReleases ? (
            <button className="button primary" type="button" disabled={dispatchBusy} onClick={() => void onRedispatch(job.job_id)}>
              {dispatchBusy ? "重新发送中..." : "重新发送本批 QA"}
            </button>
          ) : null}
          <button className="button tertiary danger" type="button" onClick={() => void onDelete(job.job_id)}>
            删除本批次
          </button>
        </div>
      </div>
    </section>
  );
}
