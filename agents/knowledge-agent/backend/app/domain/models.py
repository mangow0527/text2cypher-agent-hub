from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


KnowledgeType = Literal["cypher_syntax", "few_shot", "system_prompt", "business_knowledge"]


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
