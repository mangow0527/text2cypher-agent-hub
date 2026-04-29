from __future__ import annotations

import time

from fastapi import FastAPI
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from app.domain.knowledge.prompt_service import PromptService
from app.domain.knowledge.repair_workflow_service import RepairWorkflowService
from app.domain.knowledge.repair_service import RepairService
from app.domain.models import (
    ApplyRepairRequest,
    ApplyRepairResponse,
    KnowledgeDocumentDetailResponse,
    KnowledgeDocumentsResponse,
    PromptPackageRequest,
    PromptPackageResponse,
    UpdateKnowledgeDocumentRequest,
    UpdateKnowledgeDocumentResponse,
)
from app.errors import AppError
from app.integrations.qa_agent.redispatch_gateway import QARedispatchGateway
from app.integrations.openai.model_gateway import ModelGateway
from app.logging import ModuleLogStore, setup_logging
from app.storage.knowledge_store import KnowledgeStore


app = FastAPI(title="Knowledge Agent")
logger = setup_logging()
module_logs = ModuleLogStore()
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
repair_service = RepairService(knowledge_store, ModelGateway(), module_logs=module_logs)
qa_redispatch_gateway = QARedispatchGateway(module_logs=module_logs)
repair_workflow_service = RepairWorkflowService(repair_service, qa_redispatch_gateway, module_logs=module_logs)


def _preview_text(value: str, limit: int = 120) -> str:
    collapsed = " ".join(value.split())
    if len(collapsed) <= limit:
        return collapsed
    return f"{collapsed[:limit]}..."


async def _request_body_preview(request: Request) -> str:
    try:
        body = await request.body()
        if not body:
            return "<empty>"
        return _preview_text(body.decode("utf-8", errors="ignore"))
    except Exception:  # pragma: no cover - defensive only
        return "<unavailable>"


@app.middleware("http")
async def log_requests(request: Request, call_next):
    started = time.perf_counter()
    client = request.client.host if request.client else "unknown"
    body_preview = await _request_body_preview(request)
    try:
        response = await call_next(request)
    except AppError as exc:
        duration_ms = int((time.perf_counter() - started) * 1000)
        logger.error(
            "request_app_error method=%s path=%s client=%s duration_ms=%s code=%s message=%s body=%s",
            request.method,
            request.url.path,
            client,
            duration_ms,
            exc.code,
            exc.message,
            body_preview,
        )
        module_logs.append(
            module="api",
            level="error",
            operation="request_failed",
            trace_id=None,
            status="error",
            request_body={"method": request.method, "path": request.url.path, "body": body_preview},
            response_body={"code": exc.code, "message": exc.message},
            duration_ms=duration_ms,
        )
        return JSONResponse(
            status_code=500,
            content={"status": "error", "code": exc.code, "message": exc.message},
        )
    except Exception as exc:  # pragma: no cover - exercised in live runs
        duration_ms = int((time.perf_counter() - started) * 1000)
        logger.exception(
            "request_failed method=%s path=%s client=%s duration_ms=%s error_type=%s error=%s body=%s",
            request.method,
            request.url.path,
            client,
            duration_ms,
            exc.__class__.__name__,
            exc,
            body_preview,
        )
        module_logs.append(
            module="api",
            level="error",
            operation="request_failed",
            trace_id=None,
            status="error",
            request_body={"method": request.method, "path": request.url.path, "body": body_preview},
            response_body={"error_type": exc.__class__.__name__, "message": str(exc)},
            duration_ms=duration_ms,
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
    module_logs.append(
        module="api",
        level="info",
        operation="request_completed",
        trace_id=None,
        status=str(response.status_code),
        request_body={"method": request.method, "path": request.url.path, "body": body_preview},
        response_body={"status_code": response.status_code},
        duration_ms=duration_ms,
    )
    return response


@app.exception_handler(RequestValidationError)
async def handle_validation_error(request: Request, exc: RequestValidationError):
    client = request.client.host if request.client else "unknown"
    body_preview = await _request_body_preview(request)
    logger.warning(
        "request_validation_failed method=%s path=%s client=%s errors=%s body=%s",
        request.method,
        request.url.path,
        client,
        exc.errors(),
        body_preview,
    )
    module_logs.append(
        module="api",
        level="warning",
        operation="request_validation_failed",
        trace_id=None,
        status="422",
        request_body={"method": request.method, "path": request.url.path, "body": body_preview},
        response_body={"errors": exc.errors()},
    )
    return JSONResponse(status_code=422, content={"detail": exc.errors()})


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
    module_logs.append(
        module="prompt",
        level="info",
        operation="prompt_package_built",
        trace_id=request.id,
        status="success",
        request_body={"id": request.id, "question": _preview_text(request.question)},
        response_body={"prompt_length": len(prompt)},
    )
    return PromptPackageResponse(status="ok", id=request.id, prompt=prompt)


@app.post("/api/knowledge/repairs/apply", response_model=ApplyRepairResponse)
def apply_repair(request: ApplyRepairRequest) -> ApplyRepairResponse:
    started = time.perf_counter()
    preview = _preview_text(request.suggestion)
    logger.info("repair_requested id=%s knowledge_types=%s suggestion=%s", request.id, request.knowledge_types or [], preview)
    module_logs.append(
        module="repair",
        level="info",
        operation="repair_requested",
        trace_id=request.id,
        status="started",
        request_body={"id": request.id, "knowledge_types": request.knowledge_types or [], "suggestion": preview},
    )
    result = repair_workflow_service.apply(request.id, request.suggestion, request.knowledge_types)
    duration_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "repair_applied id=%s knowledge_types=%s change_count=%s duration_ms=%s suggestion=%s",
        request.id,
        request.knowledge_types or [],
        len(result["changes"]),
        duration_ms,
        preview,
    )
    module_logs.append(
        module="repair",
        level="info",
        operation="repair_applied",
        trace_id=request.id,
        status="success",
        request_body={"id": request.id, "knowledge_types": request.knowledge_types or [], "suggestion": preview},
        response_body={
            "change_count": len(result["changes"]),
            "redispatch_status": result["redispatch"]["status"],
            "attempt": result["redispatch"]["attempt"],
        },
        duration_ms=duration_ms,
    )
    if duration_ms >= settings.slow_request_threshold_ms:
        logger.warning(
            "repair_slow id=%s duration_ms=%s threshold_ms=%s knowledge_types=%s suggestion=%s",
            request.id,
            duration_ms,
            settings.slow_request_threshold_ms,
            request.knowledge_types or [],
            preview,
        )
        module_logs.append(
            module="repair",
            level="warning",
            operation="repair_slow",
            trace_id=request.id,
            status="slow",
            request_body={"id": request.id, "knowledge_types": request.knowledge_types or [], "suggestion": preview},
            response_body={"duration_ms": duration_ms, "threshold_ms": settings.slow_request_threshold_ms},
        )
    return ApplyRepairResponse(status="ok", changes=result["changes"], redispatch=result["redispatch"])


@app.get("/api/knowledge/documents", response_model=KnowledgeDocumentsResponse)
def list_knowledge_documents() -> KnowledgeDocumentsResponse:
    return KnowledgeDocumentsResponse(status="ok", documents=knowledge_store.list_documents())


@app.get("/api/knowledge/documents/{doc_type}", response_model=KnowledgeDocumentDetailResponse)
def read_knowledge_document(doc_type: str) -> KnowledgeDocumentDetailResponse:
    try:
        document = knowledge_store.read_document(doc_type)
    except ValueError as exc:
        raise AppError("KNOWLEDGE_DOCUMENT_NOT_FOUND", str(exc)) from exc
    return KnowledgeDocumentDetailResponse(status="ok", **document)


@app.put("/api/knowledge/documents/{doc_type}", response_model=UpdateKnowledgeDocumentResponse)
def update_knowledge_document(doc_type: str, request: UpdateKnowledgeDocumentRequest) -> UpdateKnowledgeDocumentResponse:
    try:
        document = knowledge_store.save_document(doc_type, request.content)
    except ValueError as exc:
        message = str(exc)
        code = "KNOWLEDGE_DOCUMENT_READ_ONLY" if "read-only" in message else "KNOWLEDGE_DOCUMENT_NOT_FOUND"
        raise AppError(code, message) from exc
    return UpdateKnowledgeDocumentResponse(status="ok", document=document)
