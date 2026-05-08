import asyncio
from contextlib import asynccontextmanager
from typing import Dict

from fastapi import FastAPI
from fastapi.responses import Response
import uvicorn

from .intent_recognition import get_hybrid_intent_recognizer
from .models import IntentRecognitionRequest, QAQuestionRequest, SemanticParseRequest
from .config import get_settings
from .service import get_generator_status, get_workflow_service


@asynccontextmanager
async def lifespan(app: FastAPI):
    asyncio.create_task(get_workflow_service().retry_pending_deliveries())
    yield


app = FastAPI(title="cypher-generator-agent", version="1.0.0", lifespan=lifespan)


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


@app.post("/api/v1/intents/recognize")
async def recognize_intent(request: IntentRecognitionRequest) -> Dict[str, object]:
    result = get_hybrid_intent_recognizer().recognize(request.question)
    return result.to_dict()


@app.post("/api/v1/semantic/parse")
async def parse_semantics(request: SemanticParseRequest) -> Dict[str, object]:
    result = await get_workflow_service().semantic_pipeline.parse_with_fallback(
        id=request.id,
        question=request.question,
        generation_run_id=request.generation_run_id,
    )
    return result.to_dict()


if __name__ == "__main__":
    settings = get_settings()
    uvicorn.run("services.cypher_generator_agent.app.main:app", host=settings.host, port=settings.port, reload=False)
