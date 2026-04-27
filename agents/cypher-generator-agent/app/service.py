from __future__ import annotations

from functools import lru_cache
from typing import Callable, Dict, Protocol
from uuid import uuid4

from .clients import (
    CypherLLMClient,
    KnowledgeAgentClient,
    OpenAICompatibleCypherGenerator,
    TestingAgentClient,
)
from .config import Settings, get_settings
from .models import (
    GeneratedCypherSubmissionRequest,
    GenerationFailureReason,
    GenerationRunResult,
    QAQuestionRequest,
)
from .parser import parse_model_output
from .preflight import run_preflight_check
from .prompt_runtime import render_llm_prompt


MAX_GENERATION_ATTEMPTS = 3


class KnowledgeContextProvider(Protocol):
    async def fetch_context(self, id: str, question: str) -> str:
        ...


class CypherModelInvoker(Protocol):
    async def generate_from_prompt(self, *, task_id: str, question_text: str, llm_prompt: str) -> Dict[str, str]:
        ...


class GeneratedCypherSubmitter(Protocol):
    async def submit(self, payload: GeneratedCypherSubmissionRequest) -> Dict[str, object]:
        ...


class CypherGeneratorAgentService:
    def __init__(
        self,
        *,
        knowledge_client: KnowledgeContextProvider,
        llm_client: CypherModelInvoker,
        testing_client: GeneratedCypherSubmitter,
        generation_run_id_factory: Callable[[], str] | None = None,
        readonly_call_whitelist: set[str] | None = None,
    ) -> None:
        self.knowledge_client = knowledge_client
        self.llm_client = llm_client
        self.testing_client = testing_client
        self.generation_run_id_factory = generation_run_id_factory or (lambda: str(uuid4()))
        self.readonly_call_whitelist = readonly_call_whitelist or set()

    async def ingest_question(self, request: QAQuestionRequest) -> GenerationRunResult:
        generation_run_id = self.generation_run_id_factory()
        try:
            ko_context = await self.knowledge_client.fetch_context(id=request.id, question=request.question)
        except Exception:
            return GenerationRunResult(
                generation_run_id=generation_run_id,
                generation_status="service_failed",
                reason="knowledge_agent_context_unavailable",
            )
        if not ko_context.strip():
            return GenerationRunResult(
                generation_run_id=generation_run_id,
                generation_status="service_failed",
                reason="knowledge_agent_context_unavailable",
            )

        last_reason: GenerationFailureReason | None = None
        for _generation_attempt_index in range(1, MAX_GENERATION_ATTEMPTS + 1):
            llm_prompt = render_llm_prompt(
                question=request.question,
                ko_context=ko_context,
                extra_constraint_reason=last_reason,
            )
            try:
                raw_generation = await self.llm_client.generate_from_prompt(
                    task_id=request.id,
                    question_text=request.question,
                    llm_prompt=llm_prompt,
                )
            except Exception:
                return GenerationRunResult(
                    generation_run_id=generation_run_id,
                    generation_status="service_failed",
                    reason="model_invocation_failed",
                )

            raw_output_value = raw_generation.get("raw_output")
            if not isinstance(raw_output_value, str):
                return GenerationRunResult(
                    generation_run_id=generation_run_id,
                    generation_status="service_failed",
                    reason="model_invocation_failed",
                )
            raw_output = raw_output_value
            parsed = parse_model_output(raw_output)
            if parsed.reason is not None:
                last_reason = parsed.reason
                continue

            preflight_check = run_preflight_check(
                parsed.parsed_cypher,
                readonly_call_whitelist=self.readonly_call_whitelist,
            )
            if not preflight_check.accepted:
                last_reason = preflight_check.reason
                continue

            submission = GeneratedCypherSubmissionRequest(
                id=request.id,
                question=request.question,
                generation_run_id=generation_run_id,
                generated_cypher=parsed.parsed_cypher,
                input_prompt_snapshot=llm_prompt,
            )
            try:
                await self.testing_client.submit(payload=submission)
            except Exception:
                return GenerationRunResult(
                    generation_run_id=generation_run_id,
                    generation_status="service_failed",
                    reason="testing_agent_submission_failed",
                )
            return GenerationRunResult(
                generation_run_id=generation_run_id,
                generation_status="submitted_to_testing",
            )

        return GenerationRunResult(
            generation_run_id=generation_run_id,
            generation_status="generation_failed",
            reason="generation_retry_exhausted",
            last_reason=last_reason,
        )


def build_workflow_service(settings: Settings) -> CypherGeneratorAgentService:
    return CypherGeneratorAgentService(
        knowledge_client=KnowledgeAgentClient(
            base_url=settings.knowledge_agent_url,
            timeout_seconds=settings.request_timeout_seconds,
        ),
        llm_client=CypherLLMClient(
            llm_generator=OpenAICompatibleCypherGenerator(
                base_url=settings.llm_base_url or "",
                api_key=settings.llm_api_key or "",
                model=settings.llm_model or "",
                timeout_seconds=settings.request_timeout_seconds,
                temperature=settings.llm_temperature,
            ),
        ),
        testing_client=TestingAgentClient(
            base_url=settings.testing_agent_url,
            timeout_seconds=settings.request_timeout_seconds,
        ),
        readonly_call_whitelist=set(settings.readonly_call_whitelist),
    )


@lru_cache(maxsize=1)
def get_workflow_service() -> CypherGeneratorAgentService:
    return build_workflow_service(get_settings())


def get_generator_status() -> Dict[str, object]:
    settings = get_settings()
    return {
        "llm_enabled": settings.llm_enabled,
        "llm_provider": settings.llm_provider,
        "llm_model": settings.llm_model,
        "active_mode": "llm" if settings.llm_enabled else "disabled",
        "knowledge_agent_configured": bool(settings.knowledge_agent_url),
        "testing_agent_configured": bool(settings.testing_agent_url),
    }
