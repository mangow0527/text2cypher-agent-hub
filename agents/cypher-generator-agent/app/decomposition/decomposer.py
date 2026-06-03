from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from pydantic import ValidationError

from services.cypher_generator_agent.app.core.result import ClarificationRequest

from .models import (
    QUESTION_DECOMPOSITION_SCHEMA_VERSION,
    DecompositionAttemptError,
    QuestionDecomposition,
    QuestionDecompositionClarification,
    QuestionDecompositionClarificationPayload,
    QuestionDecompositionFailure,
    QuestionDecompositionOutcome,
    parse_question_decomposition_response,
)
from .prompt import build_question_decomposition_prompt, build_question_decomposition_schema


class StructuredLLMClient(Protocol):
    provider: str

    def generate_structured(
        self,
        *,
        prompt: str,
        schema_name: str,
        schema: Mapping[str, Any],
        attempt: int,
    ) -> Mapping[str, Any]:
        """Return a provider-native structured object, not free-form text."""


class QuestionDecomposer:
    def __init__(self, llm_client: StructuredLLMClient, *, max_schema_retries: int = 2) -> None:
        if max_schema_retries < 0:
            raise ValueError("max_schema_retries must be non-negative")
        self._llm_client = llm_client
        self._max_schema_retries = max_schema_retries

    def decompose(self, question: str) -> QuestionDecompositionOutcome:
        schema = build_question_decomposition_schema()
        provider = _provider_name(self._llm_client)
        errors: list[DecompositionAttemptError] = []

        for attempt in range(1, self._max_schema_retries + 2):
            prompt = build_question_decomposition_prompt(
                question,
                attempt_no=attempt,
                json_schema=schema,
            )
            try:
                payload = self._llm_client.generate_structured(
                    prompt=prompt,
                    schema_name=QUESTION_DECOMPOSITION_SCHEMA_VERSION,
                    schema=schema,
                    attempt=attempt,
                )
            except Exception as exc:
                errors.append(_attempt_error(attempt, exc))
                return QuestionDecompositionFailure(
                    status="service_failed",
                    reason="model_invocation_failed",
                    message=str(exc) or "LLM provider invocation failed.",
                    provider=provider,
                    error_type=exc.__class__.__name__,
                    attempts=attempt,
                    retry_count=attempt - 1,
                    errors=errors,
                )

            try:
                response = parse_question_decomposition_response(payload)
            except ValidationError as exc:
                errors.append(_attempt_error(attempt, exc))
                if attempt <= self._max_schema_retries:
                    continue
                return QuestionDecompositionFailure(
                    status="generation_failed",
                    reason="question_decomposer_schema_invalid",
                    message="LLM output did not satisfy question_decomposition_v1.",
                    provider=provider,
                    error_type=exc.__class__.__name__,
                    attempts=attempt,
                    retry_count=attempt - 1,
                    errors=errors,
                )

            if isinstance(response, QuestionDecompositionClarificationPayload):
                return QuestionDecompositionClarification(
                    schema_version=response.schema_version,
                    original_question=response.original_question,
                    clarification=ClarificationRequest(question=response.clarification_question),
                    missing_referents=response.missing_referents,
                )
            return response

        raise RuntimeError("unreachable decomposition retry state")


def _provider_name(llm_client: StructuredLLMClient) -> str:
    return str(getattr(llm_client, "provider", "unknown"))


def _attempt_error(attempt: int, exc: Exception) -> DecompositionAttemptError:
    return DecompositionAttemptError(
        attempt=attempt,
        error_type=exc.__class__.__name__,
        message=str(exc),
    )
