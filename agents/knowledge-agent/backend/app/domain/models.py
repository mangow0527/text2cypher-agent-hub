from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.domain.agent.models import AgentRun, CreateRepairAgentRunRequest


KnowledgeType = Literal["cypher_syntax", "few_shot", "system_prompt", "business_knowledge"]
KnowledgeDocumentType = Literal["schema", "cypher_syntax", "few_shot", "system_prompt", "business_knowledge"]


class PromptPackageRequest(BaseModel):
    id: str = Field(min_length=1)
    question: str = Field(min_length=1)


class PromptPackageResponse(BaseModel):
    status: Literal["ok"]
    id: str
    prompt: str


class ApplyRepairRequest(BaseModel):
    id: str = Field(min_length=1)
    suggestion: str = Field(min_length=1)
    knowledge_types: Optional[list[KnowledgeType]] = None


class RepairChange(BaseModel):
    doc_type: KnowledgeType
    section: str
    before: str
    after: str


class StatusResponse(BaseModel):
    status: Literal["ok"]


class RepairAgentRunResponse(StatusResponse):
    run: AgentRun


class RepairAgentRunsResponse(StatusResponse):
    runs: list[AgentRun] = Field(default_factory=list)


class RejectRepairAgentRunRequest(BaseModel):
    reason: str = Field(min_length=1)


class KnowledgeDocumentSummary(BaseModel):
    doc_type: KnowledgeDocumentType
    title: str
    filename: str
    editable: bool
    size: int
    updated_at: str


class KnowledgeDocumentDetail(KnowledgeDocumentSummary):
    content: str


class KnowledgeDocumentsResponse(StatusResponse):
    documents: list[KnowledgeDocumentSummary]


class KnowledgeDocumentDetailResponse(KnowledgeDocumentDetail):
    status: Literal["ok"]


KnowledgeTreeNodeKind = Literal[
    "group",
    "concept",
    "schema_label",
    "business_semantic",
    "relation_path",
    "few_shot",
    "rule",
]


class KnowledgeTreeNode(BaseModel):
    id: str
    parent_id: Optional[str] = None
    title: str
    kind: KnowledgeTreeNodeKind
    concept: Optional[str] = None
    source_file: Optional[str] = None
    section_id: Optional[str] = None
    editable: bool
    content_preview: str = ""
    children: list["KnowledgeTreeNode"] = Field(default_factory=list)


class KnowledgeTreeResponse(StatusResponse):
    tree: list[KnowledgeTreeNode]


class KnowledgeTreeNodeDetail(KnowledgeTreeNode):
    content: str = ""
    warning: Optional[str] = None


class KnowledgeTreeNodeDetailResponse(StatusResponse):
    node: KnowledgeTreeNodeDetail


class UpdateKnowledgeTreeNodeRequest(BaseModel):
    content: str


class CreateKnowledgeTreeNodeRequest(BaseModel):
    parent_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    kind: KnowledgeTreeNodeKind
    content: str = ""
    concept: Optional[str] = None


class KnowledgeTreeMutationResponse(StatusResponse):
    node: KnowledgeTreeNodeDetail
    tree: list[KnowledgeTreeNode]


class UpdateKnowledgeDocumentResponse(StatusResponse):
    document: KnowledgeDocumentDetail


class UpdateKnowledgeDocumentRequest(BaseModel):
    content: str


class RedispatchResult(BaseModel):
    trace_id: str
    qa_id: str
    status: str
    attempt: int
    max_attempts: int
    dispatch: dict


class ApplyRepairResponse(StatusResponse):
    changes: list[RepairChange] = Field(default_factory=list)
    redispatch: RedispatchResult
