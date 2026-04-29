from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


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
