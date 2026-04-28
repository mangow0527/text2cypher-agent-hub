import { JobRecord } from "../lib/api";

export type JobStatusFilter = "all" | "running" | "completed" | "failed";

const runningStatuses = new Set<JobRecord["status"]>([
  "created",
  "schema_ready",
  "skeleton_ready",
  "cypher_ready",
  "validated",
  "questions_ready",
  "roundtrip_done",
  "deduped",
  "packaged",
]);

export function jobStatusLabel(status?: JobRecord["status"]) {
  if (!status) {
    return "待开始";
  }
  if (status === "completed") {
    return "已完成";
  }
  if (status === "failed") {
    return "失败";
  }
  if (runningStatuses.has(status)) {
    return "生成中";
  }
  return status;
}

export function JobList({
  jobs,
  totalJobs,
  filteredCount,
  selectedJobId,
  onSelect,
  onDelete,
  page,
  totalPages,
  onPageChange,
  query,
  statusFilter,
  onQueryChange,
  onStatusFilterChange,
}: {
  jobs: JobRecord[];
  totalJobs: number;
  filteredCount: number;
  selectedJobId?: string;
  onSelect: (jobId: string) => void;
  onDelete: (jobId: string) => Promise<void>;
  page: number;
  totalPages: number;
  onPageChange: (page: number) => void;
  query: string;
  statusFilter: JobStatusFilter;
  onQueryChange: (value: string) => void;
  onStatusFilterChange: (value: JobStatusFilter) => void;
}) {
  return (
    <section className="surface list-surface">
      <div className="panel-heading-row compact">
        <div>
          <div className="eyebrow">批次列表</div>
          <h3>最近生成批次</h3>
          <p>按批次管理结果，支持筛选、分页、删除和详情查看。</p>
        </div>
        {totalPages > 1 ? (
          <div className="pagination-meta">
            <span>
              第 {page} / {totalPages} 页
            </span>
          </div>
        ) : null}
      </div>
      <div className="list-toolbar">
        <input
          className="text-input compact-input"
          value={query}
          onChange={(event) => onQueryChange(event.target.value)}
          placeholder="搜索 job ID / 状态 / 数量"
        />
        <select
          className="text-input compact-input"
          value={statusFilter}
          onChange={(event) => onStatusFilterChange(event.target.value as JobStatusFilter)}
        >
          <option value="all">全部状态</option>
          <option value="running">生成中</option>
          <option value="completed">已完成</option>
          <option value="failed">失败</option>
        </select>
        <div className="list-toolbar-meta">
          共 {filteredCount} / {totalJobs} 条
        </div>
      </div>
      {jobs.length === 0 ? <div className="empty-inline">还没有批次，先创建一批 QA。</div> : null}
      {jobs.length > 0 ? (
        <div className="table-shell">
          <div className="table-head">
            <span>Job ID</span>
            <span>状态</span>
            <span>数量</span>
            <span>更新时间</span>
            <span>操作</span>
          </div>
          <div className="table-body">
            {jobs.map((job) => (
              <div key={job.job_id} className={selectedJobId === job.job_id ? "table-row active" : "table-row"}>
                <button className="row-main-button" onClick={() => onSelect(job.job_id)}>
                  <strong>{job.job_id}</strong>
                </button>
                <span className={`status-pill status-${job.status}`}>{jobStatusLabel(job.status)}</span>
                <span>{Number((job.metrics.selection as { final_count?: number } | undefined)?.final_count ?? job.metrics.sample_count ?? 0)} 条</span>
                <span>{new Date(job.updated_at).toLocaleString()}</span>
                <div className="table-actions">
                  <button type="button" className="button tertiary" onClick={() => onSelect(job.job_id)}>
                    查看
                  </button>
                  <button type="button" className="button tertiary danger" onClick={() => void onDelete(job.job_id)}>
                    删除
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      ) : null}
      {totalPages > 1 ? (
        <div className="pagination-row">
          <button
            type="button"
            className="button tertiary"
            disabled={page <= 1}
            onClick={() => onPageChange(page - 1)}
          >
            上一页
          </button>
          <div className="pagination-pages">
            {Array.from({ length: totalPages }, (_, index) => index + 1).map((pageNumber) => (
              <button
                key={pageNumber}
                type="button"
                className={pageNumber === page ? "page-chip active" : "page-chip"}
                onClick={() => onPageChange(pageNumber)}
              >
                {pageNumber}
              </button>
            ))}
          </div>
          <button
            type="button"
            className="button tertiary"
            disabled={page >= totalPages}
            onClick={() => onPageChange(page + 1)}
          >
            下一页
          </button>
        </div>
      ) : null}
    </section>
  );
}
