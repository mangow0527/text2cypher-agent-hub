from pathlib import Path
from typing import Dict

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from .schemas import PromptSnapshotResponse, QAQuestionRequest, QueryGeneratorRepairReceipt, QueryQuestionResponse, RepairPlan
from .config import get_settings
from .service import get_generator_status, get_workflow_service, test_tugraph_connection

app = FastAPI(title="Query Generator Service", version="1.0.0")
ui_dir = Path(__file__).resolve().parents[1] / "ui"
app.mount("/ui", StaticFiles(directory=ui_dir), name="query-generator-ui")


@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse(url="/console")


@app.get("/console", include_in_schema=False)
async def console() -> FileResponse:
    return FileResponse(ui_dir / "index.html")


@app.get("/health")
async def healthcheck() -> Dict[str, str]:
    return {"status": "ok", "service": "query_generator_service"}


@app.get("/api/v1/generator/status")
async def generator_status() -> Dict[str, object]:
    return get_generator_status()


@app.get("/api/v1/questions/{id}", response_model=QueryQuestionResponse)
async def get_question_run(id: str) -> QueryQuestionResponse:
    run = get_workflow_service().get_run(id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"No generation run found for id={id}")
    return run


@app.get("/api/v1/questions/{id}/prompt", response_model=PromptSnapshotResponse)
async def get_prompt_snapshot(id: str) -> PromptSnapshotResponse:
    snapshot = get_workflow_service().get_prompt_snapshot(id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail=f"No prompt snapshot found for id={id}")
    return snapshot


@app.post("/api/v1/qa/questions", response_model=QueryQuestionResponse)
async def ingest_question(request: QAQuestionRequest) -> QueryQuestionResponse:
    try:
        return await get_workflow_service().ingest_question(request)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/v1/internal/repair-plans", response_model=QueryGeneratorRepairReceipt)
async def accept_repair_plan(plan: RepairPlan) -> QueryGeneratorRepairReceipt:
    return get_workflow_service().accept_repair_plan(plan)


@app.get("/api/v1/tugraph/connection-test")
async def tugraph_connection_test() -> Dict[str, object]:
    return await test_tugraph_connection()


if __name__ == "__main__":
    settings = get_settings()
    uvicorn.run("services.query_generator_agent.app.main:app", host=settings.host, port=settings.port, reload=False)
