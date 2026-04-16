from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Any, Optional

from app.domain.models import JobRequest, SchemaSourceConfig, TuGraphConfig, TuGraphSourceConfig
from app.domain.importing.service import QAImportService
from app.domain.schema.compatibility_service import SchemaCompatibilityService
from app.domain.schema.service import SchemaService
from app.domain.schema.source_resolver import SourceResolver
from app.integrations.tugraph.graph_executor import GraphExecutor
from app.orchestrator.service import Orchestrator
from app.storage.import_store import ImportStore
from app.storage.job_store import JobStore


app = FastAPI(title="Text2Cypher QA Agent")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

orchestrator = Orchestrator()
job_store = JobStore()
source_resolver = SourceResolver()
schema_service = SchemaService()
graph_executor = GraphExecutor()
schema_compatibility_service = SchemaCompatibilityService(graph_executor=graph_executor)
import_store = ImportStore()
qa_import_service = QAImportService()


class SchemaResolveRequest(BaseModel):
    schema_input: Optional[Any] = None
    schema_source: SchemaSourceConfig


class TuGraphTestRequest(BaseModel):
    tugraph_source: TuGraphSourceConfig
    tugraph_config: TuGraphConfig = TuGraphConfig()


class SchemaCompatibilityRequest(BaseModel):
    schema_input: Optional[Any] = None
    schema_source: SchemaSourceConfig
    tugraph_source: TuGraphSourceConfig
    tugraph_config: TuGraphConfig = TuGraphConfig()


class QAImportRequest(BaseModel):
    source_type: str = "inline"
    payload_text: Optional[str] = None
    file_path: Optional[str] = None


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/jobs")
def create_job(request: JobRequest):
    job = orchestrator.create_job(request)
    return job


@app.post("/jobs/quick-run")
def quick_run_job(request: JobRequest):
    return orchestrator.create_and_run_job(request)


@app.post("/jobs/{job_id}/run")
def run_job(job_id: str):
    return orchestrator.run_job(job_id)


@app.post("/jobs/{job_id}/dispatch")
def redispatch_job(job_id: str):
    try:
        return orchestrator.redispatch_job(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/jobs")
def list_jobs():
    return list(job_store.list())


@app.get("/jobs/{job_id}")
def get_job(job_id: str):
    try:
        return job_store.get(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc


@app.delete("/jobs/{job_id}")
def delete_job(job_id: str):
    try:
        orchestrator.delete_job(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc
    return {"ok": True, "job_id": job_id}


@app.get("/jobs/{job_id}/artifacts/{artifact_name}")
def download_artifact(job_id: str, artifact_name: str):
    try:
        job = job_store.get(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc

    artifact_path = job.artifacts.get(artifact_name)
    if not artifact_path:
        raise HTTPException(status_code=404, detail="Artifact not found")

    return FileResponse(path=artifact_path, filename=f"{job_id}-{artifact_name}")


@app.post("/qa/import")
def import_qa(request: QAImportRequest):
    if request.source_type == "file":
        if not request.file_path:
            raise HTTPException(status_code=400, detail="file_path is required for file imports")
        return qa_import_service.import_file(request.file_path)
    if not request.payload_text:
        raise HTTPException(status_code=400, detail="payload_text is required for inline imports")
    return qa_import_service.import_payload(request.payload_text, source_type="inline")


@app.get("/qa/imports")
def list_imports():
    return list(import_store.list())


@app.get("/qa/imports/{import_id}")
def get_import(import_id: str):
    try:
        return import_store.get(import_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Import not found") from exc


@app.get("/qa/imports/{import_id}/artifacts/{artifact_name}")
def download_import_artifact(import_id: str, artifact_name: str):
    try:
        record = import_store.get(import_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Import not found") from exc

    artifact_path = record.artifacts.get(artifact_name)
    if not artifact_path:
        raise HTTPException(status_code=404, detail="Artifact not found")

    return FileResponse(path=artifact_path, filename=f"{import_id}-{artifact_name}")


@app.post("/helpers/schema/resolve")
def resolve_schema(request: SchemaResolveRequest):
    resolved = source_resolver.resolve_schema(request.schema_source, request.schema_input)
    normalized = schema_service.normalize(resolved)
    return {
        "ok": True,
        "summary": {
            "node_type_count": len(normalized.node_types),
            "edge_type_count": len(normalized.edge_types),
            "node_types": normalized.node_types[:10],
            "edge_types": normalized.edge_types[:10],
        },
    }


@app.post("/helpers/tugraph/test")
def test_tugraph(request: TuGraphTestRequest):
    resolved = source_resolver.resolve_tugraph(request.tugraph_source, request.tugraph_config)
    result = graph_executor.test_connection(resolved)
    result["resolved_config"] = {
        "base_url": resolved.base_url,
        "username": resolved.username,
        "graph": resolved.graph,
    }
    return result


@app.post("/helpers/schema/compatibility")
def check_schema_compatibility(request: SchemaCompatibilityRequest):
    resolved_schema = source_resolver.resolve_schema(request.schema_source, request.schema_input)
    normalized = schema_service.normalize(resolved_schema)
    resolved_tugraph = source_resolver.resolve_tugraph(request.tugraph_source, request.tugraph_config)
    return schema_compatibility_service.validate(normalized, resolved_tugraph)
