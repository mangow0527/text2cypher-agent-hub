import { JobRecord } from "../lib/api";

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
  statusFilter: "all" | JobRecord["status"];
  onQueryChange: (value: string) => void;
  onStatusFilterChange: (value: "all" | JobRecord["status"]) => void;
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
          onChange={(event) => onStatusFilterChange(event.target.value as "all" | JobRecord["status"])}
        >
          <option value="all">全部状态</option>
          <option value="created">created</option>
          <option value="schema_ready">schema_ready</option>
          <option value="skeleton_ready">skeleton_ready</option>
          <option value="cypher_ready">cypher_ready</option>
          <option value="validated">validated</option>
          <option value="questions_ready">questions_ready</option>
          <option value="roundtrip_done">roundtrip_done</option>
          <option value="deduped">deduped</option>
          <option value="packaged">packaged</option>
          <option value="completed">completed</option>
          <option value="failed">failed</option>
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
                <span className={`status-pill status-${job.status}`}>{job.status}</span>
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
