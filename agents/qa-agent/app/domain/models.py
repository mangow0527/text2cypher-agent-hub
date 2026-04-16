from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobMode(str, Enum):
    ONLINE = "online"
    OFFLINE = "offline"


class JobStatus(str, Enum):
    CREATED = "created"
    SCHEMA_READY = "schema_ready"
    SKELETON_READY = "skeleton_ready"
    CYPHER_READY = "cypher_ready"
    VALIDATED = "validated"
    QUESTIONS_READY = "questions_ready"
    ROUNDTRIP_DONE = "roundtrip_done"
    DEDUPED = "deduped"
    PACKAGED = "packaged"
    COMPLETED = "completed"
    FAILED = "failed"


class StageRecord(BaseModel):
    from_status: Optional[JobStatus] = None
    to_status: JobStatus
    started_at: str = Field(default_factory=utc_now)
    finished_at: Optional[str] = None
    duration_ms: Optional[int] = None
    summary: str = ""
    error_code: Optional[str] = None
    error_message: Optional[str] = None


class ModelConfig(BaseModel):
    model: str = "glm-5"
    temperature: float = 0.2
    max_output_tokens: int = 1200


class SchemaSourceConfig(BaseModel):
    type: Literal["inline", "file", "url"] = "inline"
    inline_json: Optional[Any] = None
    file_path: Optional[str] = None
    url: Optional[str] = None
    method: Literal["GET", "POST"] = "GET"
    body: Optional[Dict[str, Any]] = None
    headers: Dict[str, str] = Field(default_factory=dict)


class TuGraphSourceConfig(BaseModel):
    type: Literal["env", "inline"] = "env"


class TuGraphConfig(BaseModel):
    base_url: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    graph: Optional[str] = None
    cypher_endpoint: str = "/cypher"
    timeout_seconds: int = 30


class GenerationLimits(BaseModel):
    max_skeletons: int = 64
    max_candidates_per_skeleton: int = 4
    max_variants_per_question: int = 5


class ValidationConfig(BaseModel):
    require_runtime_validation: bool = True
    allow_empty_results: bool = True
    roundtrip_required: bool = True


class OutputConfig(BaseModel):
    split_seed_limit: int = 10
    split_gold_limit: int = 20
    target_qa_count: int = Field(default=10, ge=1, le=50)


class JobRequest(BaseModel):
    mode: JobMode = JobMode.ONLINE
    schema_input: Any = None
    schema_source: SchemaSourceConfig = Field(default_factory=SchemaSourceConfig)
    taxonomy_version: str = "v1"
    generation_limits: GenerationLimits = Field(default_factory=GenerationLimits)
    validation_config: ValidationConfig = Field(default_factory=ValidationConfig)
    llm_config: ModelConfig = Field(default_factory=ModelConfig)
    tugraph_source: TuGraphSourceConfig = Field(default_factory=TuGraphSourceConfig)
    tugraph_config: TuGraphConfig = Field(default_factory=TuGraphConfig)
    output_config: OutputConfig = Field(default_factory=OutputConfig)


class CanonicalSchemaSpec(BaseModel):
    schema_spec_version: str = "v1"
    node_types: List[str] = Field(default_factory=list)
    edge_types: List[str] = Field(default_factory=list)
    node_properties: Dict[str, Dict[str, str]] = Field(default_factory=dict)
    edge_properties: Dict[str, Dict[str, str]] = Field(default_factory=dict)
    edge_constraints: Dict[str, List[List[str]]] = Field(default_factory=dict)
    primary_keys: Dict[str, str] = Field(default_factory=dict)
    constraints: List[str] = Field(default_factory=list)
    indexes: List[str] = Field(default_factory=list)
    value_catalog: Dict[str, List[str]] = Field(default_factory=dict)
    semantic_alias: Dict[str, List[str]] = Field(default_factory=dict)
    raw_schema: Dict[str, Any] = Field(default_factory=dict)


class CypherSkeleton(BaseModel):
    skeleton_id: str
    query_types: List[str]
    structure_family: str
    pattern_template: str
    slots: Dict[str, List[str]]
    difficulty_floor: Literal["L1", "L2", "L3", "L4", "L5", "L6", "L7", "L8"] = "L1"


class CypherCandidate(BaseModel):
    candidate_id: str = Field(default_factory=lambda: f"cand_{uuid4().hex[:12]}")
    skeleton_id: str
    cypher: str
    query_types: List[str]
    structure_family: str
    generation_mode: Literal["template", "llm_direct", "llm_refine"] = "template"
    bound_schema_items: Dict[str, List[str]] = Field(default_factory=dict)
    bound_values: Dict[str, Any] = Field(default_factory=dict)
    difficulty: Literal["L1", "L2", "L3", "L4", "L5", "L6", "L7", "L8"] = "L1"


class ValidationResult(BaseModel):
    syntax: bool = False
    schema_valid: bool = Field(default=False, alias="schema")
    type_value: bool = False
    query_type_valid: bool = True
    family_valid: bool = True
    runtime: bool = False
    result_sanity: bool = False
    difficulty_valid: bool = True
    roundtrip_check: Optional[bool] = None

    model_config = {"populate_by_name": True}


class RuntimeMeta(BaseModel):
    latency_ms: int = 0
    warnings: List[str] = Field(default_factory=list)
    planner: str = ""
    error: Optional[str] = None


class ResultSignature(BaseModel):
    columns: List[str] = Field(default_factory=list)
    column_types: List[str] = Field(default_factory=list)
    row_count: int = 0
    result_preview: List[Dict[str, Any]] = Field(default_factory=list)


class ValidatedSample(BaseModel):
    sample_id: str = Field(default_factory=lambda: f"val_{uuid4().hex[:12]}")
    candidate: CypherCandidate
    validation: ValidationResult
    runtime_meta: RuntimeMeta = Field(default_factory=RuntimeMeta)
    result_signature: ResultSignature = Field(default_factory=ResultSignature)
    classified_difficulty: Optional[Literal["L1", "L2", "L3", "L4", "L5", "L6", "L7", "L8"]] = None


class QASample(BaseModel):
    id: str = Field(default_factory=lambda: f"qa_{uuid4().hex[:12]}")
    question_canonical_zh: str
    question_variants_zh: List[str]
    question_variant_styles: List[str] = Field(default_factory=list)
    cypher: str
    cypher_normalized: str
    query_types: List[str]
    difficulty: Literal["L1", "L2", "L3", "L4", "L5", "L6", "L7", "L8"]
    answer: List[Dict[str, Any]] = Field(default_factory=list)
    validation: ValidationResult
    result_signature: ResultSignature
    split: Literal["seed", "silver", "gold"]
    provenance: Dict[str, str] = Field(default_factory=dict)


class ImportStatus(str, Enum):
    CREATED = "created"
    COMPLETED = "completed"
    FAILED = "failed"


class ImportRecord(BaseModel):
    import_id: str = Field(default_factory=lambda: f"imp_{uuid4().hex[:12]}")
    source_type: Literal["inline", "file"] = "inline"
    status: ImportStatus = ImportStatus.CREATED
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)
    sample_count: int = 0
    artifacts: Dict[str, str] = Field(default_factory=dict)
    report: Dict[str, Any] = Field(default_factory=dict)
    errors: List[Dict[str, str]] = Field(default_factory=list)


class JobRecord(BaseModel):
    job_id: str = Field(default_factory=lambda: f"job_{uuid4().hex[:12]}")
    request: JobRequest
    status: JobStatus = JobStatus.CREATED
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)
    stages: List[StageRecord] = Field(default_factory=list)
    artifacts: Dict[str, str] = Field(default_factory=dict)
    metrics: Dict[str, Any] = Field(default_factory=dict)
    errors: List[Dict[str, str]] = Field(default_factory=list)
