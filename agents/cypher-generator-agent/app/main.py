from typing import Dict

from fastapi import FastAPI
from fastapi.responses import Response
import uvicorn

from .models import QAQuestionRequest
from .config import get_settings
from .service import get_generator_status, get_workflow_service


app = FastAPI(title="cypher-generator-agent", version="1.0.0")


@app.get("/health")
async def healthcheck() -> Dict[str, str]:
    return {"status": "ok", "service": "cypher-generator-agent"}


@app.get("/api/v1/generator/status")
async def generator_status() -> Dict[str, object]:
    return get_generator_status()


@app.post("/api/v1/qa/questions", status_code=204)
async def ingest_question(request: QAQuestionRequest) -> Response:
    await get_workflow_service().ingest_question(request)
    return Response(status_code=204)


if __name__ == "__main__":
    settings = get_settings()
    uvicorn.run("services.cypher_generator_agent.app.main:app", host=settings.host, port=settings.port, reload=False)
