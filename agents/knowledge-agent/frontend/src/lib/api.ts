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
