from __future__ import annotations

import time

from fastapi import FastAPI
from fastapi import Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.domain.knowledge.prompt_service import PromptService
from app.domain.knowledge.repair_service import RepairService
from app.domain.models import ApplyRepairRequest, ApplyRepairResponse, PromptPackageRequest, PromptPackageResponse, StatusResponse
from app.integrations.openai.model_gateway import ModelGateway
from app.logging import setup_logging
from app.storage.knowledge_store import KnowledgeStore


app = FastAPI(title="Knowledge Agent")
logger = setup_logging()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

knowledge_store = KnowledgeStore()
knowledge_store.bootstrap_defaults()
prompt_service = PromptService(knowledge_store)
repair_service = RepairService(knowledge_store, ModelGateway())


@app.middleware("http")
async def log_requests(request: Request, call_next):
    started = time.perf_counter()
    client = request.client.host if request.client else "unknown"
    try:
        response = await call_next(request)
    except Exception as exc:  # pragma: no cover - exercised in live runs
        duration_ms = int((time.perf_counter() - started) * 1000)
        logger.exception(
            "request_failed method=%s path=%s client=%s duration_ms=%s error=%s",
            request.method,
            request.url.path,
            client,
            duration_ms,
            exc,
        )
        return JSONResponse(status_code=500, content={"status": "error", "message": "Internal Server Error"})

    duration_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "request_completed method=%s path=%s client=%s status=%s duration_ms=%s",
        request.method,
        request.url.path,
        client,
        response.status_code,
        duration_ms,
    )
    return response


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/knowledge/rag/prompt-package", response_model=PromptPackageResponse)
def build_prompt_package(request: PromptPackageRequest) -> PromptPackageResponse:
    prompt = prompt_service.build_prompt(request.question)
    logger.info(
        "prompt_package_built id=%s question_length=%s prompt_length=%s",
        request.id,
        len(request.question),
        len(prompt),
    )
    return PromptPackageResponse(status="ok", id=request.id, prompt=prompt)


@app.post("/api/knowledge/repairs/apply", response_model=ApplyRepairResponse)
def apply_repair(request: ApplyRepairRequest) -> ApplyRepairResponse:
    changes = repair_service.apply(request.suggestion, request.knowledge_types)
    logger.info(
        "repair_applied id=%s knowledge_types=%s change_count=%s",
        request.id,
        request.knowledge_types or [],
        len(changes),
    )
    return ApplyRepairResponse(status="ok", changes=changes)
