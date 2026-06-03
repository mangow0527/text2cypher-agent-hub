from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator

from services.cypher_generator_agent.app.core.errors import GenerationFinalStatus
from services.cypher_generator_agent.app.core.result import (
    ClarificationRequest,
    GenerationFailure,
    GenerationOutput,
)
from services.cypher_generator_agent.app.observability.stages import StageName, StageStatus


TRACE_SCHEMA_VERSION = "cga_graph_trace_v1"
_FORBIDDEN_TRACE_KEYS = {"db_connection", "execution_result"}


class TraceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["inline", "redacted", "artifact"]
    value: Optional[Any] = None
    reason: Optional[str] = None
    artifact_uri: Optional[str] = None

    @model_validator(mode="after")
    def validate_ref_payload(self) -> "TraceRef":
        if self.type == "inline":
            if self.artifact_uri is not None:
                raise ValueError("inline ref must not include artifact_uri")
            _reject_forbidden_trace_keys(self.value)
            return self
        if self.type == "redacted":
            if not self.reason:
                raise ValueError("redacted ref requires reason")
            if self.value is not None or self.artifact_uri is not None:
                raise ValueError("redacted ref must not include value or artifact_uri")
            return self
        if not self.artifact_uri:
            raise ValueError("artifact ref requires artifact_uri")
        if self.value is not None:
            raise ValueError("artifact ref must not include inline value")
        return self


class TraceStage(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    stage: StageName
    status: StageStatus
    started_at: datetime
    duration_ms: int = Field(..., ge=0)
    input_ref: Optional[TraceRef] = None
    output_ref: Optional[TraceRef] = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    errors: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("metrics", "errors", "warnings")
    @classmethod
    def reject_forbidden_payload_keys(cls, value: Any) -> Any:
        _reject_forbidden_trace_keys(value)
        return value

    @field_serializer("started_at")
    def serialize_started_at(self, value: datetime) -> str:
        return value.isoformat()


class GraphTraceFinalOutputs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dsl: Optional[dict[str, Any]] = None
    cypher: Optional[str] = None
    clarification: Optional[ClarificationRequest] = None
    user_visible_notices: list[str] = Field(default_factory=list)
    failure: Optional[GenerationFailure] = None

    @field_validator("dsl")
    @classmethod
    def reject_forbidden_dsl_keys(cls, value: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
        _reject_forbidden_trace_keys(value)
        return value

    @model_validator(mode="after")
    def reject_forbidden_nested_output_keys(self) -> "GraphTraceFinalOutputs":
        _reject_forbidden_trace_keys(self.clarification)
        _reject_forbidden_trace_keys(self.failure)
        return self


class GraphTraceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    trace_schema_version: Literal["cga_graph_trace_v1"] = TRACE_SCHEMA_VERSION
    trace_id: str
    question_id: str
    generation_run_id: str
    source_question: str
    started_at: datetime
    finished_at: datetime
    final_status: GenerationFinalStatus
    semantic_model: dict[str, Any] = Field(default_factory=dict)
    stages: list[TraceStage] = Field(default_factory=list)
    final_outputs: GraphTraceFinalOutputs

    @field_validator("semantic_model")
    @classmethod
    def reject_forbidden_semantic_model_keys(cls, value: dict[str, Any]) -> dict[str, Any]:
        _reject_forbidden_trace_keys(value)
        return value

    @field_serializer("started_at", "finished_at")
    def serialize_datetime(self, value: datetime) -> str:
        return value.isoformat()

    @model_validator(mode="after")
    def validate_status_outputs(self) -> "GraphTraceRecord":
        outputs = self.final_outputs
        if self.final_status == "generated":
            if not outputs.dsl:
                raise ValueError("generated trace requires final_outputs.dsl")
            if not outputs.cypher or not outputs.cypher.strip():
                raise ValueError("generated trace requires final_outputs.cypher")
            if outputs.failure is not None:
                raise ValueError("generated trace must not include failure")
            if outputs.clarification is not None:
                raise ValueError("generated trace must not include clarification")
            return self

        if outputs.cypher is not None:
            raise ValueError("non-generated trace must not include final_outputs.cypher")
        if outputs.dsl is not None:
            raise ValueError("non-generated trace must not include final_outputs.dsl")

        if self.final_status == "clarification_required":
            if outputs.clarification is None:
                raise ValueError("clarification_required trace requires final_outputs.clarification")
            if outputs.failure is not None:
                raise ValueError("clarification_required trace must not include failure")
            return self

        if outputs.failure is None:
            raise ValueError(f"{self.final_status} trace requires final_outputs.failure")
        return self


class GraphTraceBuilder:
    def __init__(
        self,
        *,
        trace_id: str,
        question_id: str,
        generation_run_id: str,
        source_question: str,
        semantic_model: Optional[dict[str, Any]] = None,
        started_at: Optional[datetime] = None,
    ) -> None:
        self._trace_id = trace_id
        self._question_id = question_id
        self._generation_run_id = generation_run_id
        self._source_question = source_question
        self._semantic_model = semantic_model or {}
        self._started_at = started_at or _now()
        self._stages: list[TraceStage] = []

    def add_stage(
        self,
        *,
        stage: StageName | str,
        status: StageStatus | str,
        started_at: Optional[datetime] = None,
        duration_ms: int,
        input_ref: Optional[TraceRef | dict[str, Any]] = None,
        output_ref: Optional[TraceRef | dict[str, Any]] = None,
        metrics: Optional[dict[str, Any]] = None,
        errors: Optional[list[dict[str, Any]]] = None,
        warnings: Optional[list[dict[str, Any]]] = None,
    ) -> TraceStage:
        trace_stage = TraceStage(
            stage=stage,
            status=status,
            started_at=started_at or _now(),
            duration_ms=duration_ms,
            input_ref=input_ref,
            output_ref=output_ref,
            metrics=metrics or {},
            errors=errors or [],
            warnings=warnings or [],
        )
        self._stages.append(trace_stage)
        return trace_stage

    def finalize_generated(
        self,
        *,
        dsl: dict[str, Any],
        cypher: str,
        user_visible_notices: Optional[list[str]] = None,
        expected_api_status: GenerationFinalStatus = "generated",
        finished_at: Optional[datetime] = None,
    ) -> GenerationOutput:
        trace = self._finalize_trace(
            final_status="generated",
            expected_api_status=expected_api_status,
            finished_at=finished_at,
            final_outputs=GraphTraceFinalOutputs(
                dsl=dsl,
                cypher=cypher,
                user_visible_notices=user_visible_notices or [],
            ),
        )
        return GenerationOutput(
            status="generated",
            dsl=dsl,
            cypher=cypher,
            user_visible_notices=user_visible_notices or [],
            trace=_trace_dump(trace),
        )

    def finalize_clarification(
        self,
        *,
        clarification: ClarificationRequest | dict[str, Any],
        user_visible_notices: Optional[list[str]] = None,
        expected_api_status: GenerationFinalStatus = "clarification_required",
        finished_at: Optional[datetime] = None,
    ) -> GenerationOutput:
        clarification_model = ClarificationRequest.model_validate(clarification)
        trace = self._finalize_trace(
            final_status="clarification_required",
            expected_api_status=expected_api_status,
            finished_at=finished_at,
            final_outputs=GraphTraceFinalOutputs(
                clarification=clarification_model,
                user_visible_notices=user_visible_notices or [],
            ),
        )
        return GenerationOutput(
            status="clarification_required",
            clarification=clarification_model,
            user_visible_notices=user_visible_notices or [],
            trace=_trace_dump(trace),
        )

    def finalize_failure(
        self,
        *,
        status: Literal["unsupported_query_shape", "generation_failed", "service_failed"],
        failure: GenerationFailure | dict[str, Any],
        user_visible_notices: Optional[list[str]] = None,
        expected_api_status: Optional[GenerationFinalStatus] = None,
        finished_at: Optional[datetime] = None,
    ) -> GenerationOutput:
        expected = expected_api_status or status
        failure_model = GenerationFailure.model_validate(failure)
        trace = self._finalize_trace(
            final_status=status,
            expected_api_status=expected,
            finished_at=finished_at,
            final_outputs=GraphTraceFinalOutputs(
                failure=failure_model,
                user_visible_notices=user_visible_notices or [],
            ),
        )
        return GenerationOutput(
            status=status,
            failure=failure_model,
            user_visible_notices=user_visible_notices or [],
            trace=_trace_dump(trace),
        )

    def _finalize_trace(
        self,
        *,
        final_status: GenerationFinalStatus,
        expected_api_status: GenerationFinalStatus,
        finished_at: Optional[datetime],
        final_outputs: GraphTraceFinalOutputs,
    ) -> GraphTraceRecord:
        if final_status != expected_api_status:
            raise ValueError(f"final_status {final_status} does not match expected API status {expected_api_status}")
        return GraphTraceRecord(
            trace_id=self._trace_id,
            question_id=self._question_id,
            generation_run_id=self._generation_run_id,
            source_question=self._source_question,
            started_at=self._started_at,
            finished_at=finished_at or _now(),
            final_status=final_status,
            semantic_model=self._semantic_model,
            stages=list(self._stages),
            final_outputs=final_outputs,
        )


def inline_ref(value: Any) -> TraceRef:
    return TraceRef(type="inline", value=value)


def redacted_ref(*, reason: str) -> TraceRef:
    return TraceRef(type="redacted", reason=reason)


def artifact_ref(*, artifact_uri: str) -> TraceRef:
    return TraceRef(type="artifact", artifact_uri=artifact_uri)


def _trace_dump(trace: GraphTraceRecord) -> dict[str, Any]:
    return trace.model_dump(mode="json", exclude_none=False)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _reject_forbidden_trace_keys(value: Any) -> None:
    if isinstance(value, BaseModel):
        _reject_forbidden_trace_keys(value.model_dump(mode="json"))
        return
    if isinstance(value, dict):
        forbidden = _FORBIDDEN_TRACE_KEYS.intersection(value)
        if forbidden:
            raise ValueError(f"trace payload contains forbidden keys: {', '.join(sorted(forbidden))}")
        for item in value.values():
            _reject_forbidden_trace_keys(item)
        return
    if isinstance(value, list):
        for item in value:
            _reject_forbidden_trace_keys(item)
