from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


SemanticType = Literal["vertex", "edge", "property", "metric", "path_pattern"]
MatchType = Literal["exact", "synonym", "text", "embedding"]


class RetrievalBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CandidateEvidence(RetrievalBaseModel):
    term: str
    source: str
    matched_text: str


class SemanticCandidate(RetrievalBaseModel):
    semantic_type: SemanticType
    semantic_id: str
    semantic_name: str
    score: float = Field(ge=0.0, le=1.0)
    match_type: MatchType
    evidence: list[CandidateEvidence]
    owner: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CandidateRetrievalResult(RetrievalBaseModel):
    schema_version: str = "candidate_retrieval_v1"
    candidates: list[SemanticCandidate]
