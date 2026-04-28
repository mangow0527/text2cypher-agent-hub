import { useEffect, useMemo, useState } from "react";
import {
  checkSchemaCompatibility,
  createJob,
  CreateJobPayload,
  deleteJob,
  getJob,
  getQAStats,
  getImport,
  ImportRecord,
  importQa,
  JobRecord,
  listImports,
  listJobs,
  QAStats,
  redispatchJob,
  resolveSchema,
  runJob,
  testTuGraph,
} from "../lib/api";
import { ImportComposer } from "../components/ImportComposer";
import { ImportDetail } from "../components/ImportDetail";
import { ImportList } from "../components/ImportList";
import { JobComposer } from "../components/JobComposer";
import { JobDetail } from "../components/JobDetail";
import { JobList, JobStatusFilter, jobStatusLabel } from "../components/JobList";
import { QAStatsPanel } from "../components/QAStatsPanel";

const ACTIVE_STATUSES = new Set([
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
const JOBS_PER_PAGE = 8;

export function App() {
  const [jobs, setJobs] = useState<JobRecord[]>([]);
  const [imports, setImports] = useState<ImportRecord[]>([]);
  const [qaStats, setQaStats] = useState<QAStats | undefined>();
  const [selectedJob, setSelectedJob] = useState<JobRecord | undefined>();
  const [selectedImport, setSelectedImport] = useState<ImportRecord | undefined>();
  const [detailOpen, setDetailOpen] = useState(false);
  const [focus, setFocus] = useState<"jobs" | "imports">("jobs");
  const [jobBusy, setJobBusy] = useState(false);
  const [dispatchBusy, setDispatchBusy] = useState(false);
  const [importBusy, setImportBusy] = useState(false);
  const [jobPage, setJobPage] = useState(1);
  const [jobQuery, setJobQuery] = useState("");
  const [jobStatusFilter, setJobStatusFilter] = useState<JobStatusFilter>("all");
  const [jobMessage, setJobMessage] = useState("准备就绪");
  const [importMessage, setImportMessage] = useState("可随时导入现成 QA");

  async function refreshJobs(preserveSelection = true) {
    const nextJobs = (await listJobs()).sort(
      (left, right) => new Date(right.updated_at).getTime() - new Date(left.updated_at).getTime(),
    );
    setJobs(nextJobs);
    const nextTotalPages = Math.max(1, Math.ceil(nextJobs.length / JOBS_PER_PAGE));
    setJobPage((current) => Math.min(current, nextTotalPages));
    if (!preserveSelection) {
      return;
    }
    const selectedId = selectedJob?.job_id;
    if (selectedId) {
      const fresh = nextJobs.find((job) => job.job_id === selectedId);
      if (fresh) {
        setSelectedJob(await getJob(fresh.job_id));
        return;
      }
    }
    if (!selectedId && nextJobs[0]) {
      setSelectedJob(nextJobs[0]);
    }
  }

  async function refreshImports(preserveSelection = true) {
    const nextImports = (await listImports()).sort(
      (left, right) => new Date(right.updated_at).getTime() - new Date(left.updated_at).getTime(),
    );
    setImports(nextImports);
    if (!preserveSelection) {
      return;
    }
    const selectedId = selectedImport?.import_id;
    if (selectedId) {
      const fresh = nextImports.find((item) => item.import_id === selectedId);
      if (fresh) {
        setSelectedImport(await getImport(fresh.import_id));
        return;
      }
    }
    if (!selectedId && nextImports[0]) {
      setSelectedImport(nextImports[0]);
    }
  }

  async function refreshQAStats() {
    setQaStats(await getQAStats());
  }

  useEffect(() => {
    void refreshJobs(false);
    void refreshImports(false);
    void refreshQAStats();
  }, []);

  useEffect(() => {
    if (!selectedJob || !ACTIVE_STATUSES.has(selectedJob.status)) {
      return;
    }
    const timer = window.setInterval(() => {
      void refreshJobs();
      void refreshQAStats();
    }, 2000);
    return () => window.clearInterval(timer);
  }, [selectedJob?.job_id, selectedJob?.status]);

  async function handleCreate(payload: {
    mode: "online" | "offline";
    schemaSourceType: "inline" | "file" | "url";
    schemaText: string;
    schemaFilePath: string;
    schemaUrl: string;
    tugraphSourceType: "env" | "inline";
    tugraphBaseUrl: string;
    tugraphUser: string;
    tugraphPassword: string;
    tugraphGraph: string;
    targetQaCount: number;
  }) {
    setJobBusy(true);
    setJobMessage("正在创建任务...");
    try {
      const request: CreateJobPayload = {
        mode: payload.mode,
        schema_source: {
          type: payload.schemaSourceType,
        },
        tugraph_source: {
          type: payload.tugraphSourceType,
        },
        tugraph_config: {
          base_url: payload.tugraphBaseUrl || undefined,
          username: payload.tugraphUser || undefined,
          password: payload.tugraphPassword || undefined,
          graph: payload.tugraphGraph || undefined,
        },
        output_config: {
          target_qa_count: payload.targetQaCount,
        },
      };

      if (payload.schemaSourceType === "inline") {
        const schema = JSON.parse(payload.schemaText);
        request.schema_input = schema;
        request.schema_source.inline_json = schema;
      } else if (payload.schemaSourceType === "file") {
        request.schema_source.file_path = payload.schemaFilePath;
      } else {
        request.schema_source.url = payload.schemaUrl;
      }

      const created = await createJob(request);
      setSelectedJob(created);
      setDetailOpen(true);
      setFocus("jobs");
      setJobs((previous) => [created, ...previous.filter((item) => item.job_id !== created.job_id)]);
      setJobPage(1);
      setJobMessage(`任务 ${created.job_id.slice(0, 12)} 已创建，正在运行...`);

      void runJob(created.job_id)
        .then(async (completed) => {
          setSelectedJob(completed);
          setJobMessage(
            completed.status === "completed"
              ? `任务完成，已生成 ${Number((completed.metrics.selection as { final_count?: number } | undefined)?.final_count ?? completed.metrics.sample_count ?? 0)} 条 QA`
              : `任务结束：${jobStatusLabel(completed.status)}`,
          );
          await refreshJobs(false);
          await refreshQAStats();
        })
        .catch((error) => {
          setJobMessage(`任务运行失败：${error instanceof Error ? error.message : "未知错误"}`);
          void refreshJobs(false);
        })
        .finally(() => {
          setJobBusy(false);
        });

      await refreshJobs(false);
    } catch (error) {
      setJobBusy(false);
      setJobMessage(`创建失败：${error instanceof Error ? error.message : "未知错误"}`);
    }
  }

  async function handlePreflight(payload: {
    schemaSourceType: "inline" | "file" | "url";
    schemaText: string;
    schemaFilePath: string;
    schemaUrl: string;
    tugraphSourceType: "env" | "inline";
    tugraphBaseUrl: string;
    tugraphUser: string;
    tugraphPassword: string;
    tugraphGraph: string;
    targetQaCount: number;
  }) {
    const schemaPayload: {
      schema_input?: unknown;
      schema_source: CreateJobPayload["schema_source"];
    } = {
      schema_source: { type: payload.schemaSourceType },
    };

    if (payload.schemaSourceType === "inline") {
      const schema = JSON.parse(payload.schemaText);
      schemaPayload.schema_input = schema;
      schemaPayload.schema_source.inline_json = schema;
    } else if (payload.schemaSourceType === "file") {
      schemaPayload.schema_source.file_path = payload.schemaFilePath;
    } else {
      schemaPayload.schema_source.url = payload.schemaUrl;
    }

    const schemaResult = await resolveSchema(schemaPayload);
    const tugraphResult = await testTuGraph({
      tugraph_source: { type: payload.tugraphSourceType },
      tugraph_config: {
        base_url: payload.tugraphBaseUrl || undefined,
        username: payload.tugraphUser || undefined,
        password: payload.tugraphPassword || undefined,
        graph: payload.tugraphGraph || undefined,
      },
    });
    const compatibilityResult = await checkSchemaCompatibility({
      schema_input: schemaPayload.schema_input,
      schema_source: schemaPayload.schema_source,
      tugraph_source: { type: payload.tugraphSourceType },
      tugraph_config: {
        base_url: payload.tugraphBaseUrl || undefined,
        username: payload.tugraphUser || undefined,
        password: payload.tugraphPassword || undefined,
        graph: payload.tugraphGraph || undefined,
      },
    });
    const compatibilityMessage = compatibilityResult.ok
      ? "Schema 与当前图匹配"
      : `Schema 不匹配：缺少节点 ${compatibilityResult.missing_nodes.join(", ") || "无"}；缺少边 ${compatibilityResult.missing_edges.join(", ") || "无"}`;
    return `Schema ${schemaResult.summary.node_type_count} 节点 / ${schemaResult.summary.edge_type_count} 边；TuGraph ${
      tugraphResult.ok ? `已连接 ${tugraphResult.resolved_config.graph ?? "unknown"}` : "预检失败"
    }；${compatibilityMessage}`;
  }

  async function handleSelect(jobId: string) {
    const job = await getJob(jobId);
    setSelectedJob(job);
    setDetailOpen(true);
    setFocus("jobs");
    setJobMessage(
      job.status === "completed"
        ? `任务完成，可直接下载结果`
        : `任务当前处于${jobStatusLabel(job.status)}`,
    );
  }

  async function handleRedispatch(jobId: string) {
    setDispatchBusy(true);
    setJobMessage("正在重新发送本批 QA...");
    try {
      const updated = await redispatchJob(jobId);
      setSelectedJob(updated);
      setDetailOpen(true);
      setFocus("jobs");
      const latest = ((updated.metrics.dispatch_history as Array<Record<string, unknown>> | undefined) || []).slice(-1)[0];
      setJobMessage(
        latest
          ? `重发完成：成功 ${Number(latest.success ?? 0)}，失败 ${Number(latest.failed ?? 0)}`
          : "重发完成",
      );
      await refreshJobs(false);
    } catch (error) {
      setJobMessage(`重发失败：${error instanceof Error ? error.message : "未知错误"}`);
    } finally {
      setDispatchBusy(false);
    }
  }

  async function handleDeleteJob(jobId: string) {
    if (!window.confirm(`确定删除批次 ${jobId} 吗？`)) {
      return;
    }
    setJobBusy(true);
    try {
      await deleteJob(jobId);
      const nextJobs = jobs.filter((job) => job.job_id !== jobId);
      setJobs(nextJobs);
      const nextTotalPages = Math.max(1, Math.ceil(nextJobs.length / JOBS_PER_PAGE));
      setJobPage((current) => Math.min(current, nextTotalPages));
      if (selectedJob?.job_id === jobId) {
        setSelectedJob(nextJobs[0]);
      }
      setJobMessage(`批次 ${jobId.slice(0, 12)} 已删除`);
      if (!nextJobs.length) {
        setSelectedJob(undefined);
      }
      await refreshQAStats();
    } catch (error) {
      setJobMessage(`删除失败：${error instanceof Error ? error.message : "未知错误"}`);
    } finally {
      setJobBusy(false);
    }
  }

  async function handleImport(payload: {
    sourceType: "inline" | "file";
    payloadText: string;
    filePath: string;
  }) {
    setImportBusy(true);
    setImportMessage("正在导入 QA...");
    try {
      const record = await importQa(
        payload.sourceType === "inline"
          ? { source_type: "inline", payload_text: payload.payloadText }
          : { source_type: "file", file_path: payload.filePath },
      );
      setSelectedImport(record);
      setDetailOpen(true);
      setFocus("imports");
      setImports((previous) => [record, ...previous.filter((item) => item.import_id !== record.import_id)]);
      setImportMessage(`导入完成，共 ${record.sample_count} 条 QA`);
      await refreshImports(false);
      await refreshQAStats();
    } catch (error) {
      setImportMessage(`导入失败：${error instanceof Error ? error.message : "未知错误"}`);
    } finally {
      setImportBusy(false);
    }
  }

  async function handleSelectImport(importId: string) {
    const record = await getImport(importId);
    setSelectedImport(record);
    setDetailOpen(true);
    setFocus("imports");
    setImportMessage(`当前选中导入批次 ${record.import_id.slice(0, 12)}`);
  }

  const activeJob = useMemo(
    () => jobs.find((job) => ACTIVE_STATUSES.has(job.status)) ?? selectedJob,
    [jobs, selectedJob],
  );

  const completedCount = jobs.filter((job) => job.status === "completed").length;
  const failedCount = jobs.filter((job) => job.status === "failed").length;
  const filteredJobs = jobs.filter((job) => {
    const matchesStatus =
      jobStatusFilter === "all"
        ? true
        : jobStatusFilter === "running"
          ? ACTIVE_STATUSES.has(job.status)
          : job.status === jobStatusFilter;
    const finalCount = Number((job.metrics.selection as { final_count?: number } | undefined)?.final_count ?? job.metrics.sample_count ?? 0);
    const keyword = jobQuery.trim().toLowerCase();
    const matchesKeyword = keyword
      ? [job.job_id, jobStatusLabel(job.status), String(finalCount)].some((value) => value.toLowerCase().includes(keyword))
      : true;
    return matchesStatus && matchesKeyword;
  });
  const totalJobPages = Math.max(1, Math.ceil(filteredJobs.length / JOBS_PER_PAGE));
  const safeJobPage = Math.min(jobPage, totalJobPages);
  const pagedJobs = filteredJobs.slice((safeJobPage - 1) * JOBS_PER_PAGE, safeJobPage * JOBS_PER_PAGE);
  const canShowDetail = focus === "jobs" ? Boolean(selectedJob) : Boolean(selectedImport);

  useEffect(() => {
    setJobPage(1);
  }, [jobQuery, jobStatusFilter]);

  return (
    <main className="workspace-shell">
      <header className="workspace-header">
        <div>
          <div className="eyebrow">QA Agent</div>
          <h1>评测 QA 生成工作台</h1>
          <p>查看 QA 对生成进度、难度覆盖和结果下载。</p>
        </div>
        <button className="button secondary" onClick={() => { void refreshJobs(false); void refreshImports(false); void refreshQAStats(); }}>
          刷新
        </button>
      </header>

      <section className="summary-bar">
        <div className="summary-item">
          <span>总任务</span>
          <strong>{jobs.length}</strong>
        </div>
        <div className="summary-item">
          <span>运行中</span>
          <strong>{jobs.filter((job) => ACTIVE_STATUSES.has(job.status)).length}</strong>
        </div>
        <div className="summary-item">
          <span>已完成</span>
          <strong>{completedCount}</strong>
        </div>
        <div className="summary-item">
          <span>失败</span>
          <strong>{failedCount}</strong>
        </div>
      </section>

      <QAStatsPanel stats={qaStats} />

      <section className="current-run-banner">
        <div>
          <div className="eyebrow">当前批次</div>
          <h2>{activeJob ? activeJob.job_id : "还没有批次"}</h2>
          <p>
            {focus === "jobs" ? jobMessage : importMessage}
          </p>
        </div>
        <div className="banner-meta">
          {canShowDetail ? (
            <button className="button tertiary" type="button" onClick={() => setDetailOpen((current) => !current)}>
              {detailOpen ? "收起结果面板" : "展开结果面板"}
            </button>
          ) : null}
          <span className={`status-pill status-${activeJob?.status ?? "created"}`}>
            {jobStatusLabel(activeJob?.status)}
          </span>
        </div>
      </section>

      <section className={detailOpen && canShowDetail ? "workspace-grid detail-open" : "workspace-grid detail-closed"}>
        <aside className="left-column">
          <div className="panel-stack">
            <JobComposer onCreate={handleCreate} onPreflight={handlePreflight} busy={jobBusy} message={jobMessage} />
            <ImportComposer onImport={handleImport} busy={importBusy} message={importMessage} />
          </div>
        </aside>

        <section className="middle-column">
          <div className="tab-row">
            <button className={focus === "jobs" ? "tab active" : "tab"} onClick={() => setFocus("jobs")}>
              生成任务
            </button>
            <button className={focus === "imports" ? "tab active" : "tab"} onClick={() => setFocus("imports")}>
              手动导入
            </button>
          </div>
          {focus === "jobs" ? (
            <JobList
              jobs={pagedJobs}
              totalJobs={jobs.length}
              filteredCount={filteredJobs.length}
              selectedJobId={selectedJob?.job_id}
              onSelect={handleSelect}
              onDelete={handleDeleteJob}
              page={safeJobPage}
              totalPages={totalJobPages}
              onPageChange={setJobPage}
              query={jobQuery}
              statusFilter={jobStatusFilter}
              onQueryChange={setJobQuery}
              onStatusFilterChange={setJobStatusFilter}
            />
          ) : (
            <ImportList imports={imports} selectedImportId={selectedImport?.import_id} onSelect={handleSelectImport} />
          )}
        </section>

        {detailOpen && canShowDetail ? (
          <section className="right-column">
            {focus === "jobs" ? (
              <JobDetail
                job={selectedJob}
                onRedispatch={handleRedispatch}
                onDelete={handleDeleteJob}
                onClose={() => setDetailOpen(false)}
                dispatchBusy={dispatchBusy}
              />
            ) : (
              <ImportDetail record={selectedImport} onClose={() => setDetailOpen(false)} />
            )}
          </section>
        ) : null}
      </section>
    </main>
  );
}
