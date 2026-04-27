from pathlib import Path
from typing import Dict

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from .models import IssueTicket, RepairAnalysisRecord, RepairIssueTicketResponse

from .config import get_settings
from .service import get_repair_service

app = FastAPI(title="repair-agent", version="1.0.0")
ui_dir = Path(__file__).resolve().parents[1] / "ui"
app.mount("/ui", StaticFiles(directory=ui_dir), name="repair-ui")


@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse(url="/console")


@app.get("/console", include_in_schema=False)
async def console() -> FileResponse:
    return FileResponse(ui_dir / "index.html")


@app.get("/health")
async def healthcheck() -> Dict[str, str]:
    return {"status": "ok", "service": "repair-agent"}


@app.get("/api/v1/status")
async def service_status() -> Dict[str, object]:
    return get_repair_service().get_service_status()


@app.post("/api/v1/issue-tickets", response_model=RepairIssueTicketResponse)
async def create_issue_ticket_response(issue_ticket: IssueTicket) -> RepairIssueTicketResponse:
    return await get_repair_service().create_issue_ticket_response(issue_ticket)


@app.get("/api/v1/analyses/{analysis_id}", response_model=RepairAnalysisRecord)
async def get_repair_analysis(analysis_id: str) -> RepairAnalysisRecord:
    analysis = get_repair_service().get_analysis(analysis_id)
    if analysis is None:
        raise HTTPException(status_code=404, detail=f"No repair analysis found for analysis_id={analysis_id}")
    return analysis


if __name__ == "__main__":
    settings = get_settings()
    uvicorn.run("services.repair_agent.app.main:app", host=settings.host, port=settings.port, reload=False)
