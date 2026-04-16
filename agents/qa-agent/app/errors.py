from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ErrorCode:
    code: str
    message: str


ERRORS = {
    "OPENAI_NOT_CONFIGURED": ErrorCode("OPENAI_NOT_CONFIGURED", "OpenAI API key is not configured."),
    "OPENAI_REQUEST_ERROR": ErrorCode("OPENAI_REQUEST_ERROR", "OpenAI request failed."),
    "SCHEMA_PARSE_ERROR": ErrorCode("SCHEMA_PARSE_ERROR", "Schema input could not be parsed."),
    "SCHEMA_VALIDATION_ERROR": ErrorCode("SCHEMA_VALIDATION_ERROR", "Schema input failed validation."),
    "SKELETON_BUILD_ERROR": ErrorCode("SKELETON_BUILD_ERROR", "Cypher skeleton generation failed."),
    "CYPHER_INSTANTIATION_ERROR": ErrorCode("CYPHER_INSTANTIATION_ERROR", "Cypher instantiation failed."),
    "SYNTAX_ERROR": ErrorCode("SYNTAX_ERROR", "Cypher syntax validation failed."),
    "TYPE_VALUE_ERROR": ErrorCode("TYPE_VALUE_ERROR", "Type or value validation failed."),
    "TUGRAPH_RUNTIME_ERROR": ErrorCode("TUGRAPH_RUNTIME_ERROR", "TuGraph runtime validation failed."),
    "RESULT_SANITY_ERROR": ErrorCode("RESULT_SANITY_ERROR", "Result sanity validation failed."),
    "QUESTION_GENERATION_ERROR": ErrorCode("QUESTION_GENERATION_ERROR", "Question generation failed."),
    "QUESTION_DRIFT": ErrorCode("QUESTION_DRIFT", "Question drift detected."),
    "ROUNDTRIP_FAILED": ErrorCode("ROUNDTRIP_FAILED", "Roundtrip validation failed."),
    "DEDUP_CONFLICT": ErrorCode("DEDUP_CONFLICT", "Deduplication conflict detected."),
    "PACKAGING_ERROR": ErrorCode("PACKAGING_ERROR", "Artifact packaging failed."),
    "QA_IMPORT_ERROR": ErrorCode("QA_IMPORT_ERROR", "QA import failed."),
}


class AppError(Exception):
    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        self.message = message or ERRORS.get(code, ErrorCode(code, code)).message
        super().__init__(self.message)
