from pathlib import Path
from typing import Dict

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
import uvicorn

from .config import Settings, settings
from .service import RuntimeResultsService


def create_app() -> FastAPI:
    runtime_settings = Settings()
    app = FastAPI(title="Runtime Results Service", version="1.0.0")
    ui_dir = Path(__file__).resolve().parents[1] / "ui"
    app.mount("/ui", StaticFiles(directory=ui_dir), name="runtime-results-ui")
    results_service = RuntimeResultsService(
        testing_data_dir=runtime_settings.testing_data_dir,
        repair_data_dir=runtime_settings.repair_data_dir,
        cypher_generator_agent_base_url=runtime_settings.cypher_generator_agent_base_url,
        testing_service_base_url=runtime_settings.testing_service_base_url,
        repair_service_base_url=runtime_settings.repair_service_base_url,
        knowledge_agent_base_url=runtime_settings.knowledge_agent_base_url,
        qa_generator_base_url=runtime_settings.qa_generator_base_url,
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

    @app.get("/health")
    async def healthcheck() -> Dict[str, str]:
        return {"status": "ok", "service": "runtime_results_service"}

    @app.get("/api/v1/tasks")
    async def list_tasks(
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        difficulty: str | None = Query(None),
        q: str | None = Query(None),
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
