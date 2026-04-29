function resolveApiBase(): string {
  const configured = import.meta.env.VITE_API_BASE as string | undefined;
  if (configured && configured.trim()) {
    return configured.trim().replace(/\/+$/, "");
  }
  return "";
}

const API_BASE = resolveApiBase();

async function parseJsonOrThrow(response: Response) {
  const text = await response.text();
  const payload = text ? JSON.parse(text) : {};
  if (!response.ok) {
    const detail =
      typeof payload?.detail === "string"
        ? payload.detail
        : payload?.detail
          ? JSON.stringify(payload.detail)
          : `HTTP ${response.status}`;
    throw new Error(detail);
  }
  return payload;
}

export type JobStatus =
  | "created"
  | "schema_ready"
  | "skeleton_ready"
  | "cypher_ready"
  | "validated"
  | "questions_ready"
  | "roundtrip_done"
  | "deduped"
  | "packaged"
  | "completed"
  | "failed";

export interface JobRecord {
  job_id: string;
  status: JobStatus;
  created_at: string;
  updated_at: string;
  metrics: Record<string, unknown>;
  stages: Array<{
    to_status: JobStatus;
    summary: string;
    duration_ms?: number;
  }>;
  artifacts: Record<string, string>;
  errors?: Array<{ code: string; message: string }>;
}

export interface ImportRecord {
  import_id: string;
  status: "created" | "completed" | "failed";
  created_at: string;
  updated_at: string;
  sample_count: number;
  artifacts: Record<string, string>;
  report: Record<string, unknown>;
  errors?: Array<{ code: string; message: string }>;
}

export interface QADifficultyDefinition {
  level: string;
  paper_band: string;
  title: string;
  definition: string;
  cypher_examples: Array<{
    difficulty: string;
    label: string;
    cypher: string;
  }>;
}

export interface QAStats {
  total_qa_pairs: number;
  generated_qa_pairs: number;
  imported_qa_pairs: number;
  unknown_source_qa_pairs: number;
  difficulty_distribution: Record<string, number>;
  difficulty_percentages: Record<string, number>;
  difficulty_definitions: QADifficultyDefinition[];
  files_processed: number;
  invalid_rows: number;
  latest_updated_at?: string | null;
}

export async function listJobs(): Promise<JobRecord[]> {
  const response = await fetch(`${API_BASE}/jobs`);
  return parseJsonOrThrow(response);
}

export async function createJob(payload: CreateJobPayload): Promise<JobRecord> {
  const response = await fetch(`${API_BASE}/jobs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return parseJsonOrThrow(response);
}

export interface CreateJobPayload {
  mode: "online" | "offline";
  schema_input?: unknown;
  schema_source: {
    type: "inline" | "file" | "url";
    inline_json?: unknown;
    file_path?: string;
    url?: string;
  };
  tugraph_source: {
    type: "env" | "inline";
  };
  tugraph_config?: {
    base_url?: string;
    username?: string;
    password?: string;
    graph?: string;
  };
  output_config?: {
    target_qa_count?: number;
  };
}

export interface SchemaResolveResponse {
  ok: boolean;
  summary: {
    node_type_count: number;
    edge_type_count: number;
    node_types: string[];
    edge_types: string[];
  };
}

export interface TuGraphTestResponse {
  ok: boolean;
  runtime_meta: Record<string, unknown>;
  result_signature: {
    row_count: number;
    columns: string[];
    result_preview: Array<Record<string, unknown>>;
  };
  resolved_config: {
    base_url?: string;
    username?: string;
    graph?: string;
  };
}

export interface SchemaCompatibilityResponse {
  ok: boolean;
  planner: string;
  vertex_labels: string[];
  edge_labels: string[];
  missing_nodes: string[];
  missing_edges: string[];
}

export async function runJob(jobId: string): Promise<JobRecord> {
  const response = await fetch(`${API_BASE}/jobs/${jobId}/run`, { method: "POST" });
  return parseJsonOrThrow(response);
}

export async function quickRunJob(payload: CreateJobPayload): Promise<JobRecord> {
  const response = await fetch(`${API_BASE}/jobs/quick-run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return parseJsonOrThrow(response);
}

export async function resolveSchema(payload: {
  schema_input?: unknown;
  schema_source: CreateJobPayload["schema_source"];
}): Promise<SchemaResolveResponse> {
  const response = await fetch(`${API_BASE}/helpers/schema/resolve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return parseJsonOrThrow(response);
}

export async function testTuGraph(payload: {
  tugraph_source: CreateJobPayload["tugraph_source"];
  tugraph_config?: CreateJobPayload["tugraph_config"];
}): Promise<TuGraphTestResponse> {
  const response = await fetch(`${API_BASE}/helpers/tugraph/test`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return parseJsonOrThrow(response);
}

export async function checkSchemaCompatibility(payload: {
  schema_input?: unknown;
  schema_source: CreateJobPayload["schema_source"];
  tugraph_source: CreateJobPayload["tugraph_source"];
  tugraph_config?: CreateJobPayload["tugraph_config"];
}): Promise<SchemaCompatibilityResponse> {
  const response = await fetch(`${API_BASE}/helpers/schema/compatibility`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return parseJsonOrThrow(response);
}

export async function getJob(jobId: string): Promise<JobRecord> {
  const response = await fetch(`${API_BASE}/jobs/${jobId}`);
  return parseJsonOrThrow(response);
}

export async function deleteJob(jobId: string): Promise<{ ok: boolean; job_id: string }> {
  const response = await fetch(`${API_BASE}/jobs/${jobId}`, { method: "DELETE" });
  return parseJsonOrThrow(response);
}

export async function redispatchJob(jobId: string): Promise<JobRecord> {
  const response = await fetch(`${API_BASE}/jobs/${jobId}/dispatch`, { method: "POST" });
  return parseJsonOrThrow(response);
}

export function artifactUrl(jobId: string, artifactName: string): string {
  return `${API_BASE}/jobs/${jobId}/artifacts/${artifactName}`;
}

export async function importQa(payload: {
  source_type: "inline" | "file";
  payload_text?: string;
  file_path?: string;
}): Promise<ImportRecord> {
  const response = await fetch(`${API_BASE}/qa/import`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return parseJsonOrThrow(response);
}

export async function listImports(): Promise<ImportRecord[]> {
  const response = await fetch(`${API_BASE}/qa/imports`);
  return parseJsonOrThrow(response);
}

export async function getQAStats(): Promise<QAStats> {
  const response = await fetch(`${API_BASE}/qa/stats`);
  return parseJsonOrThrow(response);
}

export async function getImport(importId: string): Promise<ImportRecord> {
  const response = await fetch(`${API_BASE}/qa/imports/${importId}`);
  return parseJsonOrThrow(response);
}

export function importArtifactUrl(importId: string, artifactName: string): string {
  return `${API_BASE}/qa/imports/${importId}/artifacts/${artifactName}`;
}
