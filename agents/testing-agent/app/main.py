from typing import Dict

from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse
import uvicorn

from .config import get_settings
from .models import EvaluationStatusResponse, IssueTicket, QAGoldenResponse, SubmissionReceipt
from .schemas import GeneratedCypherSubmissionRequest, QAGoldenRequest
from .service import get_testing_service

app = FastAPI(title="testing-agent", version="2.0.0")


@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse(url="/health")


@app.get("/health")
async def healthcheck() -> Dict[str, str]:
    return {"status": "ok", "service": "testing-agent"}


@app.get("/api/v1/status")
async def service_status() -> Dict[str, object]:
    return get_testing_service().get_service_status()


@app.post("/api/v1/qa/goldens", response_model=QAGoldenResponse)
async def ingest_golden(request: QAGoldenRequest) -> QAGoldenResponse:
    try:
        return await get_testing_service().ingest_golden(request)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/v1/evaluations/submissions", response_model=SubmissionReceipt)
async def submit_evaluation(request: GeneratedCypherSubmissionRequest) -> SubmissionReceipt:
    try:
        return await get_testing_service().ingest_submission(request)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/v1/evaluations/{id}", response_model=EvaluationStatusResponse)
async def get_evaluation(id: str) -> EvaluationStatusResponse:
    return get_testing_service().get_evaluation_status(id)


@app.get("/api/v1/issues/{ticket_id}", response_model=IssueTicket)
async def get_issue_ticket(ticket_id: str) -> IssueTicket:
    ticket = get_testing_service().get_issue_ticket(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail=f"No issue ticket found for ticket_id={ticket_id}")
    return ticket


if __name__ == "__main__":
    settings = get_settings()
    uvicorn.run("services.testing_agent.app.main:app", host=settings.host, port=settings.port, reload=False)
