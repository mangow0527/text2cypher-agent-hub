from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator

from services.cypher_generator_agent.app.core.result import ClarificationRequest

from .coverage_terms import normalize_terms


QUESTION_DECOMPOSITION_SCHEMA_VERSION = "question_decomposition_v1"


class DecompositionBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


IntentType = Literal["lookup", "list", "count", "aggregate", "top_n", "path", "compare", "unknown"]
OutputShape = Literal["rows", "scalar", "grouped_rows", "path", "unknown"]


class LiteralKindHint(str, Enum):
    ENUM_OR_NAME = "enum_or_name"
    ID = "id"
    NUMBER = "number"
    DATETIME = "datetime"
    UNKNOWN = "unknown"


class SlotKind(str, Enum):
    PROJECTION = "projection"
    FILTER = "filter"
    GROUP_BY = "group_by"
    ORDER_BY = "order_by"
    LIMIT = "limit"
    PATH = "path"
    UNKNOWN = "unknown"


class LiteralCandidate(DecompositionBaseModel):
    text: str
    kind_hint: LiteralKindHint
    attached_to: str | None = None

    @field_validator("text")
    @classmethod
    def require_non_empty_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("literal candidate text fields must not be empty")
        return text

    @field_validator("attached_to")
    @classmethod
    def normalize_attached_to(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        return text or None


class SubstantiveTerm(DecompositionBaseModel):
    text: str
    slot: SlotKind
    attached_to: str | None = None

    @field_validator("text")
    @classmethod
    def require_non_empty_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("substantive term text must not be empty")
        return text

    @field_validator("attached_to")
    @classmethod
    def normalize_optional_attached_to(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        return text or None


class QuestionDecomposition(DecompositionBaseModel):
    schema_version: Literal["question_decomposition_v1"] = QUESTION_DECOMPOSITION_SCHEMA_VERSION
    result_type: Literal["decomposition"]
    intent_type: IntentType
    original_question: str
    literal_candidates: list[LiteralCandidate] = Field(default_factory=list)
    substantive_terms: list[SubstantiveTerm] = Field(default_factory=list)
    modality_terms: list[str] = Field(default_factory=list)
    time_terms: list[str] = Field(default_factory=list)
    unparsed_terms: list[str] = Field(default_factory=list)
    output_shape: OutputShape

    @field_validator(
        "modality_terms",
        "time_terms",
        "unparsed_terms",
        mode="after",
    )
    @classmethod
    def normalize_term_list(cls, value: list[str]) -> list[str]:
        return normalize_terms(value)

    @field_validator("substantive_terms", mode="after")
    @classmethod
    def dedupe_substantive_terms(cls, value: list[SubstantiveTerm]) -> list[SubstantiveTerm]:
        terms: list[SubstantiveTerm] = []
        seen: set[tuple[str, SlotKind, str | None]] = set()
        for term in value:
            text = term.text.strip()
            key = (text, term.slot, term.attached_to)
            if not text or key in seen:
                continue
            seen.add(key)
            terms.append(term.model_copy(update={"text": text}))
        return terms

    @field_validator("original_question")
    @classmethod
    def require_original_question(cls, value: str) -> str:
        question = value.strip()
        if not question:
            raise ValueError("original_question must not be empty")
        return question


class QuestionDecompositionClarificationPayload(DecompositionBaseModel):
    schema_version: Literal["question_decomposition_v1"] = QUESTION_DECOMPOSITION_SCHEMA_VERSION
    result_type: Literal["clarification_required"]
    original_question: str
    clarification_question: str
    missing_referents: list[str] = Field(default_factory=list)

    @field_validator("original_question", "clarification_question")
    @classmethod
    def require_non_empty_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("text fields must not be empty")
        return text

    @field_validator("missing_referents", mode="after")
    @classmethod
    def normalize_missing_referents(cls, value: list[str]) -> list[str]:
        return normalize_terms(value)


class QuestionDecompositionClarification(DecompositionBaseModel):
    status: Literal["clarification_required"] = "clarification_required"
    schema_version: Literal["question_decomposition_v1"] = QUESTION_DECOMPOSITION_SCHEMA_VERSION
    original_question: str
    clarification: ClarificationRequest
    missing_referents: list[str] = Field(default_factory=list)


class DecompositionAttemptError(DecompositionBaseModel):
    attempt: int = Field(ge=1)
    error_type: str
    message: str


class QuestionDecompositionFailure(DecompositionBaseModel):
    status: Literal["generation_failed", "service_failed"]
    reason: Literal["question_decomposer_schema_invalid", "model_invocation_failed"]
    message: str
    provider: str
    error_type: str
    attempts: int = Field(ge=1)
    retry_count: int = Field(ge=0)
    errors: list[DecompositionAttemptError] = Field(default_factory=list)


QuestionDecompositionLLMResponse: TypeAlias = Annotated[
    QuestionDecomposition | QuestionDecompositionClarificationPayload,
    Field(discriminator="result_type"),
]
QuestionDecompositionResult: TypeAlias = QuestionDecompositionLLMResponse
QuestionDecompositionOutcome: TypeAlias = (
    QuestionDecomposition | QuestionDecompositionClarification | QuestionDecompositionFailure
)

QUESTION_DECOMPOSITION_RESPONSE_ADAPTER = TypeAdapter(QuestionDecompositionLLMResponse)


def parse_question_decomposition_response(payload: Any) -> QuestionDecompositionLLMResponse:
    return QUESTION_DECOMPOSITION_RESPONSE_ADAPTER.validate_python(payload)


def question_decomposition_json_schema() -> dict[str, Any]:
    return QUESTION_DECOMPOSITION_RESPONSE_ADAPTER.json_schema()
