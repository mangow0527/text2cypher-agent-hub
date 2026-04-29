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
