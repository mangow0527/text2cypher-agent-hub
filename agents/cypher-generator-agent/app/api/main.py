import asyncio
from typing import Dict

from fastapi import BackgroundTasks, FastAPI
from fastapi.responses import Response
import uvicorn

from .models import IntentRecognitionRequest, QAQuestionRequest, SemanticParseRequest
from .service import get_generator_status, get_workflow_service
from services.cypher_generator_agent.app.core.pipeline import run_pipeline
from services.cypher_generator_agent.app.infrastructure.config import get_settings


app = FastAPI(title="cypher-generator-agent", version="1.0.0")


@app.get("/health")
async def healthcheck() -> Dict[str, str]:
    return {"status": "ok", "service": "cypher-generator-agent"}


@app.get("/api/v1/generator/status")
async def generator_status() -> Dict[str, object]:
    return get_generator_status()


@app.post("/api/v1/qa/questions", status_code=204)
async def ingest_question(request: QAQuestionRequest, background_tasks: BackgroundTasks) -> Response:
    service = get_workflow_service()
    result = await service.accept_question(request)
    background_tasks.add_task(
        service.generate_and_submit_question,
        request,
        generation_run_id=result.generation_run_id,
    )
    return Response(status_code=204)


@app.post("/api/v1/intents/recognize")
async def recognize_intent(request: IntentRecognitionRequest) -> Dict[str, object]:
    return {
        "status": "stubbed",
        "input": {"question": request.question},
        "output": {"intent": {}},
        "internal_flow": {},
    }


@app.post("/api/v1/semantic/parse")
async def parse_semantics(request: SemanticParseRequest) -> Dict[str, object]:
    output = await asyncio.to_thread(
        run_pipeline,
        qa_id=request.id,
        question=request.question,
        generation_run_id=request.generation_run_id or request.id or "api",
    )
    return output.model_dump(exclude_none=True)


if __name__ == "__main__":
    settings = get_settings()
    uvicorn.run("services.cypher_generator_agent.app.api.main:app", host=settings.host, port=settings.port, reload=False)
