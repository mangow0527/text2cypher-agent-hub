function resolveApiBase(): string {
  const configured = import.meta.env.VITE_API_BASE as string | undefined;
  if (configured && configured.trim()) {
    return configured.trim().replace(/\/+$/, "");
  }
  if (typeof window !== "undefined") {
    return `${window.location.protocol}//${window.location.hostname}:8010`;
  }
  return "http://127.0.0.1:8010";
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

export type KnowledgeType =
  | "cypher_syntax"
  | "few_shot"
  | "system_prompt"
  | "business_knowledge";

export type KnowledgeDocumentType = KnowledgeType | "schema";

export interface PromptPackageResponse {
  status: "ok";
  id: string;
  prompt: string;
}

export interface RepairChange {
  doc_type: KnowledgeType;
  section: string;
  before: string;
  after: string;
}

export interface ApplyRepairResponse {
  status: "ok";
  changes: RepairChange[];
}

export type AgentRunStatus =
  | "created"
  | "running"
  | "needs_review"
  | "approved"
  | "applied"
  | "redispatched"
  | "completed"
  | "rejected"
  | "failed";

export interface RootCause {
  type: string;
  summary: string;
  suggested_fix: string;
  evidence: string[];
}

export interface AgentAction {
  action: "tool_call" | "request_human_review" | "final";
  tool_name: string | null;
  arguments: Record<string, unknown>;
  status: "ready_for_review" | "rejected" | null;
  reason_summary: string;
  summary: string;
}

export interface AgentTraceEntry {
  step: number;
  action: AgentAction;
  observation: Record<string, unknown>;
  error: string | null;
}

export interface CandidateChange {
  operation: "add" | "modify" | "delete";
  doc_type: KnowledgeType;
  section: string;
  target_key: string;
  new_content: string;
  rationale: string;
  risk: "low" | "medium" | "high";
  confidence: number;
  duplicate_checked: boolean;
  conflict_checked: boolean;
}

export interface GapDiagnosis {
  gap_type:
    | "knowledge_missing"
    | "retrieval_miss"
    | "prompt_orchestration_gap"
    | "generator_noncompliance"
    | "knowledge_conflict"
    | "unknown";
  reason: string;
  suggested_action: string;
}

export interface ValidationSummary {
  prompt_package_built: boolean;
  before_after_improved: boolean;
  redispatch_status: string;
  remaining_risks: string[];
}

export interface AgentDecision {
  action: "continue" | "human_review" | "apply" | "reject" | "complete";
  reason: string;
}

export interface AgentRun {
  run_id: string;
  qa_id: string;
  goal: string;
  root_cause: RootCause;
  status: AgentRunStatus;
  trace: AgentTraceEntry[];
  memory_hits: Array<Record<string, unknown>>;
  evidence: Array<Record<string, unknown>>;
  gap_diagnosis: GapDiagnosis;
  candidate_changes: CandidateChange[];
  validation: ValidationSummary;
  decision: AgentDecision | null;
  errors: string[];
}

export interface RepairAgentRunResponse {
  status: "ok";
  run: AgentRun;
}

export interface RepairAgentRunsResponse {
  status: "ok";
  runs: AgentRun[];
}

export interface KnowledgeDocumentSummary {
  doc_type: KnowledgeDocumentType;
  title: string;
  filename: string;
  editable: boolean;
  size: number;
  updated_at: string;
}

export interface KnowledgeDocumentDetail extends KnowledgeDocumentSummary {
  content: string;
}

export type KnowledgeTreeNodeKind =
  | "group"
  | "concept"
  | "schema_label"
  | "business_semantic"
  | "relation_path"
  | "few_shot"
  | "rule";

export interface KnowledgeTreeNode {
  id: string;
  parent_id: string | null;
  title: string;
  kind: KnowledgeTreeNodeKind;
  concept: string | null;
  source_file: string | null;
  section_id: string | null;
  editable: boolean;
  content_preview: string;
  children: KnowledgeTreeNode[];
}

export interface KnowledgeTreeNodeDetail extends KnowledgeTreeNode {
  content: string;
  warning: string | null;
}

export interface KnowledgeDocumentsResponse {
  status: "ok";
  documents: KnowledgeDocumentSummary[];
}

export interface UpdateKnowledgeDocumentResponse {
  status: "ok";
  document: KnowledgeDocumentDetail;
}

export interface KnowledgeTreeResponse {
  status: "ok";
  tree: KnowledgeTreeNode[];
}

export interface KnowledgeTreeNodeDetailResponse {
  status: "ok";
  node: KnowledgeTreeNodeDetail;
}

export interface KnowledgeTreeMutationResponse {
  status: "ok";
  node: KnowledgeTreeNodeDetail;
  tree: KnowledgeTreeNode[];
}

export async function fetchPromptPackage(payload: {
  id: string;
  question: string;
}): Promise<PromptPackageResponse> {
  const response = await fetch(`${API_BASE}/api/knowledge/rag/prompt-package`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return parseJsonOrThrow(response);
}

export async function applyRepair(payload: {
  id: string;
  suggestion: string;
  knowledge_types?: KnowledgeType[];
}): Promise<ApplyRepairResponse> {
  const response = await fetch(`${API_BASE}/api/knowledge/repairs/apply`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return parseJsonOrThrow(response);
}

export async function listRepairAgentRuns(status?: AgentRunStatus): Promise<RepairAgentRunsResponse> {
  const query = status ? `?status=${encodeURIComponent(status)}` : "";
  const response = await fetch(`${API_BASE}/api/knowledge/agent/repair-runs${query}`);
  return parseJsonOrThrow(response);
}

export async function fetchRepairAgentRun(runId: string): Promise<RepairAgentRunResponse> {
  const response = await fetch(`${API_BASE}/api/knowledge/agent/repair-runs/${encodeURIComponent(runId)}`);
  return parseJsonOrThrow(response);
}

export async function approveRepairAgentRun(runId: string): Promise<RepairAgentRunResponse> {
  const response = await fetch(`${API_BASE}/api/knowledge/agent/repair-runs/${encodeURIComponent(runId)}/approve`, {
    method: "POST",
  });
  return parseJsonOrThrow(response);
}

export async function rejectRepairAgentRun(runId: string, reason: string): Promise<RepairAgentRunResponse> {
  const response = await fetch(`${API_BASE}/api/knowledge/agent/repair-runs/${encodeURIComponent(runId)}/reject`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reason }),
  });
  return parseJsonOrThrow(response);
}

export async function listKnowledgeDocuments(): Promise<KnowledgeDocumentsResponse> {
  const response = await fetch(`${API_BASE}/api/knowledge/documents`);
  return parseJsonOrThrow(response);
}

export async function fetchKnowledgeDocument(docType: KnowledgeDocumentType): Promise<KnowledgeDocumentDetail> {
  const response = await fetch(`${API_BASE}/api/knowledge/documents/${docType}`);
  return parseJsonOrThrow(response);
}

export async function saveKnowledgeDocument(
  docType: KnowledgeDocumentType,
  content: string,
): Promise<UpdateKnowledgeDocumentResponse> {
  const response = await fetch(`${API_BASE}/api/knowledge/documents/${docType}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
  return parseJsonOrThrow(response);
}

export async function fetchKnowledgeTree(): Promise<KnowledgeTreeResponse> {
  const response = await fetch(`${API_BASE}/api/knowledge/tree`);
  return parseJsonOrThrow(response);
}

export async function fetchKnowledgeTreeNode(nodeId: string): Promise<KnowledgeTreeNodeDetailResponse> {
  const response = await fetch(`${API_BASE}/api/knowledge/tree/nodes/${encodeURIComponent(nodeId)}`);
  return parseJsonOrThrow(response);
}

export async function updateKnowledgeTreeNode(
  nodeId: string,
  content: string,
): Promise<KnowledgeTreeMutationResponse> {
  const response = await fetch(`${API_BASE}/api/knowledge/tree/nodes/${encodeURIComponent(nodeId)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
  return parseJsonOrThrow(response);
}

export async function createKnowledgeTreeNode(payload: {
  parent_id: string;
  title: string;
  kind: KnowledgeTreeNodeKind;
  content: string;
  concept?: string | null;
}): Promise<KnowledgeTreeMutationResponse> {
  const response = await fetch(`${API_BASE}/api/knowledge/tree/nodes`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return parseJsonOrThrow(response);
}

export async function deleteKnowledgeTreeNode(nodeId: string): Promise<{ status: "ok" }> {
  const response = await fetch(`${API_BASE}/api/knowledge/tree/nodes/${encodeURIComponent(nodeId)}`, {
    method: "DELETE",
  });
  return parseJsonOrThrow(response);
}
