from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
import uvicorn

from .config import Settings, settings
from .diagnostics import RuntimeCgaDiagnosticLLMClient
from .service import RuntimeResultsService
from .user_queries import (
    RuntimeCgaClient,
    RuntimeTuGraphQueryClient,
    RuntimeUserQueryService,
    UserQueryCreateRequest,
)


def create_app() -> FastAPI:
    runtime_settings = Settings()
    app = FastAPI(title="Runtime Results Service", version="1.0.0")
    ui_dir = Path(__file__).resolve().parents[1] / "ui"
    app.mount("/ui", StaticFiles(directory=ui_dir), name="runtime-results-ui")
    results_service = RuntimeResultsService(
        testing_data_dir=runtime_settings.testing_data_dir,
        cypher_generator_agent_base_url=runtime_settings.cypher_generator_agent_base_url,
        testing_service_base_url=runtime_settings.testing_service_base_url,
        qa_generator_base_url=runtime_settings.qa_generator_base_url,
        cga_trace_profile=runtime_settings.cga_trace_profile,
    )
    user_query_service = RuntimeUserQueryService(
        data_dir=runtime_settings.user_query_data_dir,
        cga_client=RuntimeCgaClient(
            base_url=runtime_settings.cypher_generator_agent_base_url,
            timeout_seconds=120.0,
        ),
        tugraph_client=RuntimeTuGraphQueryClient(
            base_url=runtime_settings.testing_service_base_url,
            timeout_seconds=120.0,
        ),
        diagnostic_client=RuntimeCgaDiagnosticLLMClient(
            base_url=runtime_settings.resolved_diagnostic_llm_base_url,
            api_key=runtime_settings.resolved_diagnostic_llm_api_key,
            model=runtime_settings.resolved_diagnostic_llm_model,
            timeout_seconds=runtime_settings.diagnostic_llm_timeout_seconds,
            temperature=runtime_settings.diagnostic_llm_temperature,
        ),
    )

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        return RedirectResponse(url="/console")

    @app.get("/console", include_in_schema=False)
    async def console() -> FileResponse:
        return FileResponse(ui_dir / "index.html")

    @app.get("/console/tasks/{id}", include_in_schema=False)
    async def console_task_detail(id: str) -> FileResponse:
        return FileResponse(ui_dir / "detail.html")

    @app.get("/console/user-queries/{user_query_id}", include_in_schema=False)
    async def console_user_query_detail(user_query_id: str) -> FileResponse:
        return FileResponse(ui_dir / "user_query_detail.html")

    @app.get("/health")
    async def healthcheck() -> Dict[str, str]:
        return {"status": "ok", "service": "runtime_results_service"}

    @app.get("/api/v1/tasks")
    async def list_tasks(
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        difficulty: Optional[str] = Query(None),
        q: Optional[str] = Query(None),
    ) -> Dict[str, object]:
        return await run_in_threadpool(
            results_service.list_tasks,
            page=page,
            page_size=page_size,
            difficulty=difficulty,
            q=q,
        )

    @app.get("/api/v1/tasks/summary")
    async def get_task_summary() -> Dict[str, object]:
        return await run_in_threadpool(results_service.get_task_summary)

    @app.get("/api/v1/runtime/services")
    async def get_runtime_services() -> Dict[str, object]:
        return await results_service.get_runtime_services()

    @app.post("/api/v1/user-queries")
    async def create_user_query(request: UserQueryCreateRequest, background_tasks: BackgroundTasks) -> Dict[str, object]:
        try:
            record = await user_query_service.create_user_query(question=request.question, defer_diagnostic=True)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if (record.get("cga_diagnostic") or {}).get("status") == "pending":
            background_tasks.add_task(user_query_service.complete_cga_diagnostic, str(record["user_query_id"]))
        return record

    @app.get("/api/v1/user-queries")
    async def list_user_queries() -> Dict[str, object]:
        return await run_in_threadpool(user_query_service.list_user_queries)

    @app.get("/api/v1/user-queries/{user_query_id}")
    async def get_user_query(user_query_id: str) -> Dict[str, object]:
        record = await run_in_threadpool(user_query_service.get_user_query, user_query_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"No user query found for user_query_id={user_query_id}")
        return record

    @app.get("/api/v1/user-queries/{user_query_id}/download")
    async def download_user_query(user_query_id: str) -> JSONResponse:
        payload, error = await run_in_threadpool(user_query_service.get_tugraph_download_payload, user_query_id)
        if error == "not_found":
            raise HTTPException(status_code=404, detail=f"No user query found for user_query_id={user_query_id}")
        if error == "no_tugraph_response":
            raise HTTPException(status_code=409, detail="No TuGraph response is available for this user query.")
        return JSONResponse(
            payload,
            headers={"Content-Disposition": f'attachment; filename="{user_query_id}-tugraph.json"'},
        )

    @app.get("/api/v1/tasks/{id}")
    async def get_task(id: str) -> Dict[str, object]:
        task = await run_in_threadpool(results_service.get_task_detail, id)
        if task is None:
            raise HTTPException(status_code=404, detail=f"No runtime task found for id={id}")
        return task

    return app


app = create_app()


if __name__ == "__main__":
    uvicorn.run("console.runtime_console.app.main:app", host=settings.host, port=settings.port, reload=False)
