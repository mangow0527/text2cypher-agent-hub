# Guarded Knowledge Repair Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a true guarded Knowledge Repair Agent where the LLM decides which tool to call next, the agent can retrieve knowledge and memory, and the system safely applies verified repairs.

**Architecture:** Add `app/domain/agent/` as a small custom agent runtime. The runtime executes one LLM-selected tool per step, records every action/observation, writes observations into structured `AgentRun` fields, and uses policy gates before formal knowledge writes or redispatch. The implementation preserves existing `/api/knowledge/repairs/apply` and `/api/knowledge/rag/prompt-package` behavior.

**Tech Stack:** Python 3, FastAPI, Pydantic, unittest, JSONL/JSON artifact stores, existing OpenAI-compatible `ModelGateway`, existing markdown `KnowledgeStore`, future RAG adapter stub.

---

## UML Review Reference

Review the UML diagrams in:

`knowledge-agent/docs/superpowers/specs/2026-05-06-guarded-knowledge-repair-agent-design.md`

The design includes component, class, state, and sequence UML diagrams in Mermaid format. This plan is aligned to those diagrams after review fixes.

## Review Fixes Incorporated

- V1 registers the minimum real agent tool set: `inspect_qa_case`, `retrieve_knowledge`, `rag_retrieve`, `read_repair_memory`, `classify_gap`, `propose_patch`, `check_duplicate`, `check_conflict`, `build_prompt_overlay`, `evaluate_before_after`, `redispatch_qa`, and `write_repair_memory`.
- `request_human_review` is a structured action, not a tool.
- `RepairAgentRuntime.apply_observation_to_run()` maps observations into `AgentRun.evidence`, `memory_hits`, `gap_diagnosis`, `candidate_changes`, and `validation`.
- `PolicyGuard` separates `assert_can_auto_apply()` from `assert_can_apply_after_human_approval()`.
- `approve()` applies the approved patch, calls redispatch, writes repair memory, and sets the final status to `completed`.
- The LLM `final` action cannot directly mark a run completed; runtime completion requires side effects to finish.
- `RepairService` split must preserve existing module logs and legacy tests.
- The LLM sees tool descriptions and Pydantic argument schemas, not only tool names.
- Side-effect tools are excluded from the default LLM allowlist and run only through guarded completion paths.

## File Structure

- Create: `knowledge-agent/backend/app/domain/agent/__init__.py`
- Create: `knowledge-agent/backend/app/domain/agent/models.py`
- Create: `knowledge-agent/backend/app/domain/agent/run_store.py`
- Create: `knowledge-agent/backend/app/domain/agent/controller.py`
- Create: `knowledge-agent/backend/app/domain/agent/tool_registry.py`
- Create: `knowledge-agent/backend/app/domain/agent/tools.py`
- Create: `knowledge-agent/backend/app/domain/agent/memory.py`
- Create: `knowledge-agent/backend/app/domain/agent/policy.py`
- Create: `knowledge-agent/backend/app/domain/agent/evaluator.py`
- Create: `knowledge-agent/backend/app/domain/agent/runtime.py`
- Modify: `knowledge-agent/backend/app/domain/knowledge/repair_service.py`
- Modify: `knowledge-agent/backend/app/integrations/qa_agent/redispatch_gateway.py`
- Modify: `knowledge-agent/backend/app/domain/models.py`
- Modify: `knowledge-agent/backend/app/entrypoints/api/main.py`
- Add tests under `knowledge-agent/backend/tests/test_agent_*.py`

---

### Task 1: Agent Models And API Contracts

**Files:**
- Create: `knowledge-agent/backend/app/domain/agent/__init__.py`
- Create: `knowledge-agent/backend/app/domain/agent/models.py`
- Modify: `knowledge-agent/backend/app/domain/models.py`
- Test: `knowledge-agent/backend/tests/test_agent_models.py`

- [ ] **Step 1: Write failing tests**

Create `knowledge-agent/backend/tests/test_agent_models.py`:

```python
import unittest
from pydantic import ValidationError

from app.domain.agent.models import (
    AgentAction,
    AgentConstraints,
    AgentDecision,
    AgentRun,
    AgentRunStatus,
    CandidateChange,
    RootCause,
)


class AgentModelsTest(unittest.TestCase):
    def test_agent_action_requires_tool_for_tool_call(self) -> None:
        action = AgentAction(
            action="tool_call",
            tool_name="retrieve_knowledge",
            arguments={"query": "协议版本 所属网元"},
            reason_summary="先检索相关知识",
        )
        self.assertEqual(action.tool_name, "retrieve_knowledge")

        with self.assertRaises(ValidationError):
            AgentAction(action="tool_call", reason_summary="missing tool")

    def test_final_action_cannot_claim_completed(self) -> None:
        with self.assertRaises(ValidationError):
            AgentAction(action="final", status="completed", reason_summary="unsafe")

    def test_candidate_change_doc_type_is_constrained(self) -> None:
        CandidateChange(
            doc_type="few_shot",
            section="Reference Examples",
            target_key="k",
            new_content="Question: q\nCypher: MATCH (n) RETURN n",
        )
        with self.assertRaises(ValidationError):
            CandidateChange(doc_type="not_a_doc", section="s", target_key="k", new_content="x")

    def test_agent_run_holds_structured_outputs(self) -> None:
        run = AgentRun(
            qa_id="qa_001",
            goal="repair",
            root_cause=RootCause(type="missing_path_rule", summary="s", suggested_fix="f"),
            constraints=AgentConstraints(max_steps=8),
            status=AgentRunStatus.CREATED,
            decision=AgentDecision(action="continue", reason="new run"),
        )
        self.assertEqual(run.qa_id, "qa_001")
        self.assertEqual(run.status, AgentRunStatus.CREATED)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run failing tests**

Run:

```bash
cd knowledge-agent/backend
python3 -m unittest tests/test_agent_models.py -v
```

Expected: FAIL because `app.domain.agent.models` does not exist.

- [ ] **Step 3: Implement models**

Create `knowledge-agent/backend/app/domain/agent/__init__.py`:

```python
"""Guarded knowledge repair agent package."""
```

Create `knowledge-agent/backend/app/domain/agent/models.py` with these public types:

```python
from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

AgentKnowledgeType = Literal["cypher_syntax", "few_shot", "system_prompt", "business_knowledge"]
AgentActionKind = Literal["tool_call", "request_human_review", "final"]
FinalStatus = Literal["ready_for_review", "rejected"]


class AgentRunStatus(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    NEEDS_REVIEW = "needs_review"
    APPROVED = "approved"
    APPLIED = "applied"
    REDISPATCHED = "redispatched"
    COMPLETED = "completed"
    REJECTED = "rejected"
    FAILED = "failed"


class RootCause(BaseModel):
    type: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    suggested_fix: str = Field(min_length=1)
    evidence: list[str] = Field(default_factory=list)


class AgentConstraints(BaseModel):
    auto_apply: bool = False
    max_steps: int = Field(default=12, ge=1, le=50)
    allowed_tools: list[str] = Field(
        default_factory=lambda: [
            "inspect_qa_case",
            "retrieve_knowledge",
            "rag_retrieve",
            "read_repair_memory",
            "classify_gap",
            "propose_patch",
            "check_duplicate",
            "check_conflict",
            "build_prompt_overlay",
            "evaluate_before_after",
        ]
    )
    verification_required: bool = True


class AgentAction(BaseModel):
    action: AgentActionKind
    tool_name: Optional[str] = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    status: Optional[FinalStatus] = None
    reason_summary: str = Field(min_length=1)
    summary: str = ""

    @model_validator(mode="after")
    def validate_action_shape(self) -> "AgentAction":
        if self.action == "tool_call" and not self.tool_name:
            raise ValueError("tool_name is required for tool_call actions")
        if self.action != "tool_call" and self.tool_name:
            raise ValueError("tool_name is only allowed for tool_call actions")
        return self


class AgentTraceEntry(BaseModel):
    step: int = Field(ge=1)
    action: AgentAction
    observation: dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None


class CandidateChange(BaseModel):
    operation: Literal["add", "modify", "delete"] = "add"
    doc_type: AgentKnowledgeType
    section: str = Field(min_length=1)
    target_key: str = Field(min_length=1)
    new_content: str = Field(min_length=1)
    rationale: str = ""
    risk: Literal["low", "medium", "high"] = "medium"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    duplicate_checked: bool = False
    conflict_checked: bool = False


class GapDiagnosis(BaseModel):
    gap_type: Literal[
        "knowledge_missing",
        "retrieval_miss",
        "prompt_orchestration_gap",
        "generator_noncompliance",
        "knowledge_conflict",
        "unknown",
    ] = "unknown"
    reason: str = ""
    suggested_action: str = ""


class ValidationSummary(BaseModel):
    prompt_package_built: bool = False
    before_after_improved: bool = False
    redispatch_status: str = ""
    remaining_risks: list[str] = Field(default_factory=list)


class AgentDecision(BaseModel):
    action: Literal["continue", "human_review", "apply", "reject", "complete"] = "continue"
    reason: str = ""


class CreateRepairAgentRunRequest(BaseModel):
    qa_id: str = Field(min_length=1)
    goal: str = Field(min_length=1)
    root_cause: RootCause
    constraints: AgentConstraints = Field(default_factory=AgentConstraints)


class AgentRun(BaseModel):
    run_id: str = Field(default_factory=lambda: f"krun_{uuid4().hex[:12]}")
    qa_id: str
    goal: str
    root_cause: RootCause
    constraints: AgentConstraints = Field(default_factory=AgentConstraints)
    status: AgentRunStatus = AgentRunStatus.CREATED
    trace: list[AgentTraceEntry] = Field(default_factory=list)
    memory_hits: list[dict[str, Any]] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    gap_diagnosis: GapDiagnosis = Field(default_factory=GapDiagnosis)
    candidate_changes: list[CandidateChange] = Field(default_factory=list)
    validation: ValidationSummary = Field(default_factory=ValidationSummary)
    decision: Optional[AgentDecision] = None
    errors: list[str] = Field(default_factory=list)
```

Modify `knowledge-agent/backend/app/domain/models.py`:

```python
from app.domain.agent.models import AgentRun, CreateRepairAgentRunRequest
```

Add:

```python
class RepairAgentRunResponse(StatusResponse):
    run: AgentRun


class RejectRepairAgentRunRequest(BaseModel):
    reason: str = Field(min_length=1)
```

- [ ] **Step 4: Run tests**

Run:

```bash
cd knowledge-agent/backend
python3 -m unittest tests/test_agent_models.py -v
```

Expected: PASS.

---

### Task 2: Run Store And Memory

**Files:**
- Create: `knowledge-agent/backend/app/domain/agent/run_store.py`
- Create: `knowledge-agent/backend/app/domain/agent/memory.py`
- Test: `knowledge-agent/backend/tests/test_agent_storage_memory.py`

- [ ] **Step 1: Write failing tests**

Create `knowledge-agent/backend/tests/test_agent_storage_memory.py`:

```python
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.domain.agent.memory import MemoryManager
from app.domain.agent.models import AgentAction, AgentConstraints, AgentRunStatus, RootCause
from app.domain.agent.run_store import AgentRunStore


class AgentStorageMemoryTest(unittest.TestCase):
    def test_run_store_create_trace_status_roundtrip(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            store = AgentRunStore(Path(tmp_dir))
            run = store.create("qa_001", "repair", RootCause(type="missing_path_rule", summary="s", suggested_fix="f"), AgentConstraints(max_steps=3))
            store.append_trace(
                run.run_id,
                AgentAction(action="tool_call", tool_name="inspect_qa_case", arguments={"qa_id": "qa_001"}, reason_summary="读取失败样本"),
                {"question": "q"},
            )
            updated = store.update_status(run.run_id, AgentRunStatus.RUNNING)

            loaded = store.get(run.run_id)
            self.assertEqual(updated.status, AgentRunStatus.RUNNING)
            self.assertEqual(loaded.trace[0].observation["question"], "q")

    def test_memory_searches_historical_repairs(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            memory = MemoryManager(Path(tmp_dir))
            memory.write_repair_memory({"qa_id": "qa_001", "root_cause_type": "missing_path_rule", "summary": "协议路径修复"})

            hits = memory.search_repair_memory("missing_path_rule 协议")

            self.assertEqual(hits[0]["qa_id"], "qa_001")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run failing tests**

Run:

```bash
cd knowledge-agent/backend
python3 -m unittest tests/test_agent_storage_memory.py -v
```

Expected: FAIL because storage and memory modules do not exist.

- [ ] **Step 3: Implement run store**

Create `knowledge-agent/backend/app/domain/agent/run_store.py`:

```python
from __future__ import annotations

from pathlib import Path

from app.config import settings
from app.domain.agent.models import AgentAction, AgentConstraints, AgentRun, AgentRunStatus, AgentTraceEntry, RootCause


class AgentRunStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or settings.artifacts_dir / "agent_runs"
        self.root.mkdir(parents=True, exist_ok=True)

    def create(self, qa_id: str, goal: str, root_cause: RootCause, constraints: AgentConstraints) -> AgentRun:
        return self.save(AgentRun(qa_id=qa_id, goal=goal, root_cause=root_cause, constraints=constraints))

    def get(self, run_id: str) -> AgentRun:
        path = self._path(run_id)
        if not path.exists():
            raise FileNotFoundError(f"Agent run not found: {run_id}")
        return AgentRun.model_validate_json(path.read_text(encoding="utf-8"))

    def save(self, run: AgentRun) -> AgentRun:
        self._path(run.run_id).write_text(run.model_dump_json(indent=2), encoding="utf-8")
        return run

    def append_trace(self, run_id: str, action: AgentAction, observation: dict, error: str | None = None) -> AgentRun:
        run = self.get(run_id)
        run.trace.append(AgentTraceEntry(step=len(run.trace) + 1, action=action, observation=observation, error=error))
        return self.save(run)

    def update_status(self, run_id: str, status: AgentRunStatus) -> AgentRun:
        run = self.get(run_id)
        run.status = status
        return self.save(run)

    def _path(self, run_id: str) -> Path:
        return self.root / f"{run_id}.json"
```

- [ ] **Step 4: Implement memory manager**

Create `knowledge-agent/backend/app/domain/agent/memory.py`:

```python
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.config import settings


class MemoryManager:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or settings.artifacts_dir / "agent_memory"
        self.root.mkdir(parents=True, exist_ok=True)
        self.repair_memory_path = self.root / "repair_memory.jsonl"

    def search_repair_memory(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        if not self.repair_memory_path.exists():
            return []
        terms = {term.lower() for term in query.split() if term.strip()}
        scored: list[tuple[int, dict[str, Any]]] = []
        for line in self.repair_memory_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            haystack = json.dumps(item, ensure_ascii=False).lower()
            score = sum(1 for term in terms if term in haystack)
            if score > 0:
                scored.append((score, item))
        return [item for score, item in sorted(scored, key=lambda row: row[0], reverse=True)[:limit]]

    def write_repair_memory(self, entry: dict[str, Any]) -> dict[str, Any]:
        payload = {"memory_id": f"mem_{uuid4().hex[:12]}", "created_at": datetime.now(timezone.utc).isoformat(), **entry}
        with self.repair_memory_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
        return payload
```

- [ ] **Step 5: Run tests**

Run:

```bash
cd knowledge-agent/backend
python3 -m unittest tests/test_agent_storage_memory.py -v
```

Expected: PASS.

---

### Task 3: Controller And Tool Registry

**Files:**
- Create: `knowledge-agent/backend/app/domain/agent/controller.py`
- Create: `knowledge-agent/backend/app/domain/agent/tool_registry.py`
- Test: `knowledge-agent/backend/tests/test_agent_controller_registry.py`

- [ ] **Step 1: Write failing tests**

Create `knowledge-agent/backend/tests/test_agent_controller_registry.py`:

```python
import unittest
from pydantic import BaseModel

from app.domain.agent.controller import LLMController
from app.domain.agent.models import AgentAction, AgentConstraints, AgentRun, RootCause
from app.domain.agent.tool_registry import AgentTool, ToolRegistry
from app.errors import AppError


class FakeGateway:
    def __init__(self, response: str) -> None:
        self.response = response

    def generate_text(self, prompt_name: str, model_config: dict, **kwargs) -> str:
        return self.response


class EchoInput(BaseModel):
    value: str


class EchoTool(AgentTool):
    name = "echo"
    description = "Echo a string value for testing."
    input_model = EchoInput

    def execute(self, arguments: EchoInput) -> dict:
        return {"value": arguments.value}


class SideEffectTool(AgentTool):
    name = "side_effect"
    description = "Dangerous side-effect tool used for allowlist tests."
    input_model = EchoInput
    side_effect = True

    def execute(self, arguments: EchoInput) -> dict:
        return {"value": arguments.value}


class ControllerRegistryTest(unittest.TestCase):
    def test_controller_parses_tool_call_json(self) -> None:
        controller = LLMController(FakeGateway('{"action":"tool_call","tool_name":"echo","arguments":{"value":"ok"},"reason_summary":"test"}'))
        action = controller.decide_next_action(
            context={"qa_id": "qa_001"},
            memory=[],
            tools=[{"name": "echo", "description": "Echo a string value for testing.", "input_schema": EchoInput.model_json_schema()}],
        )
        self.assertEqual(action.tool_name, "echo")

    def test_controller_rejects_free_text(self) -> None:
        controller = LLMController(FakeGateway("我想查知识"))
        with self.assertRaises(AppError):
            controller.decide_next_action(context={}, memory=[], tools=[])

    def test_registry_exposes_tool_specs_with_argument_schema(self) -> None:
        registry = ToolRegistry()
        registry.register(EchoTool())
        run = AgentRun(qa_id="qa_001", goal="repair", root_cause=RootCause(type="x", summary="s", suggested_fix="f"), constraints=AgentConstraints(allowed_tools=["echo"]))

        specs = registry.allowed_tool_specs(run)

        self.assertEqual(specs[0]["name"], "echo")
        self.assertIn("value", specs[0]["input_schema"]["properties"])

    def test_default_allowlist_does_not_expose_side_effect_tools(self) -> None:
        registry = ToolRegistry()
        registry.register(EchoTool())
        registry.register(SideEffectTool())
        run = AgentRun(qa_id="qa_001", goal="repair", root_cause=RootCause(type="x", summary="s", suggested_fix="f"), constraints=AgentConstraints())

        self.assertEqual(registry.allowed_tool_names(run), ["echo"])

    def test_registry_executes_only_allowed_tools(self) -> None:
        registry = ToolRegistry()
        registry.register(EchoTool())
        run = AgentRun(qa_id="qa_001", goal="repair", root_cause=RootCause(type="x", summary="s", suggested_fix="f"), constraints=AgentConstraints(allowed_tools=["echo"]))
        observation = registry.execute(run, AgentAction(action="tool_call", tool_name="echo", arguments={"value": "ok"}, reason_summary="test"))
        self.assertEqual(observation["value"], "ok")

        blocked_run = run.model_copy(update={"constraints": AgentConstraints(allowed_tools=["other"])})
        with self.assertRaises(AppError):
            registry.execute(blocked_run, AgentAction(action="tool_call", tool_name="echo", arguments={"value": "ok"}, reason_summary="test"))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run failing tests**

Run:

```bash
cd knowledge-agent/backend
python3 -m unittest tests/test_agent_controller_registry.py -v
```

Expected: FAIL because controller and registry modules do not exist.

- [ ] **Step 3: Implement controller**

Create `knowledge-agent/backend/app/domain/agent/controller.py`:

```python
from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from app.domain.agent.models import AgentAction
from app.errors import AppError


class LLMController:
    def __init__(self, model_gateway, model_config: dict[str, Any] | None = None) -> None:
        self.model_gateway = model_gateway
        self.model_config = model_config or {"model": "glm-5", "temperature": 0.1, "max_output_tokens": 800}

    def decide_next_action(self, context: dict[str, Any], memory: list[dict[str, Any]], tools: list[dict[str, Any]]) -> AgentAction:
        raw = self.model_gateway.generate_text(
            "agent_next_action",
            self.model_config,
            prompt=self._build_prompt(context=context, memory=memory, tools=tools),
        )
        try:
            return AgentAction.model_validate(json.loads(raw))
        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
            raise AppError("AGENT_CONTROLLER_INVALID_ACTION", f"Agent controller returned invalid action: {raw}") from exc

    def _build_prompt(self, context: dict[str, Any], memory: list[dict[str, Any]], tools: list[dict[str, Any]]) -> str:
        return (
            "你是 Guarded Knowledge Repair Agent 的控制器。"
            "你必须只输出合法 JSON，不能输出解释文本。"
            "你可以选择 tool_call、request_human_review 或 final。"
            "final 不能声明 completed，只能声明 ready_for_review 或 rejected。"
            f"\n可用工具：{json.dumps(tools, ensure_ascii=False)}"
            f"\n当前状态：{json.dumps(context, ensure_ascii=False)}"
            f"\n相关记忆：{json.dumps(memory, ensure_ascii=False)}"
        )
```

- [ ] **Step 4: Implement tool registry**

Create `knowledge-agent/backend/app/domain/agent/tool_registry.py`:

```python
from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ValidationError

from app.domain.agent.models import AgentAction, AgentRun
from app.errors import AppError


class AgentTool:
    name: ClassVar[str]
    description: ClassVar[str] = ""
    input_model: ClassVar[type[BaseModel]]
    side_effect: ClassVar[bool] = False

    def execute(self, arguments: BaseModel) -> dict:
        raise NotImplementedError


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, AgentTool] = {}

    def register(self, tool: AgentTool) -> None:
        self._tools[tool.name] = tool

    def allowed_tool_names(self, run: AgentRun) -> list[str]:
        configured = run.constraints.allowed_tools
        return [name for name in configured if name in self._tools and not self._tools[name].side_effect]

    def allowed_tool_specs(self, run: AgentRun) -> list[dict]:
        return [
            {
                "name": name,
                "description": self._tools[name].description,
                "input_schema": self._tools[name].input_model.model_json_schema(),
            }
            for name in self.allowed_tool_names(run)
        ]

    def execute(self, run: AgentRun, action: AgentAction) -> dict:
        if action.action != "tool_call" or not action.tool_name:
            raise AppError("AGENT_ACTION_NOT_TOOL_CALL", "Only tool_call actions can be executed.")
        if action.tool_name not in self._tools:
            raise AppError("AGENT_TOOL_NOT_FOUND", f"Agent tool not found: {action.tool_name}")
        if run.constraints.allowed_tools and action.tool_name not in run.constraints.allowed_tools:
            raise AppError("AGENT_TOOL_NOT_ALLOWED", f"Agent tool is not allowed for this run: {action.tool_name}")
        tool = self._tools[action.tool_name]
        try:
            parsed = tool.input_model.model_validate(action.arguments)
        except ValidationError as exc:
            raise AppError("AGENT_TOOL_ARGUMENT_INVALID", str(exc)) from exc
        return tool.execute(parsed)
```

- [ ] **Step 5: Run tests**

Run:

```bash
cd knowledge-agent/backend
python3 -m unittest tests/test_agent_controller_registry.py -v
```

Expected: PASS.

---

### Task 4: Repair Service Candidate Patch Split

**Files:**
- Modify: `knowledge-agent/backend/app/domain/knowledge/repair_service.py`
- Test: existing `knowledge-agent/backend/tests/test_repair_service.py`

- [ ] **Step 1: Add candidate-only behavior assertions**

Extend `knowledge-agent/backend/tests/test_repair_service.py` with:

```python
    def test_propose_does_not_write_knowledge_documents(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            store = KnowledgeStore(Path(tmp_dir))
            store.bootstrap_defaults()
            gateway = FakeGateway()
            service = RepairService(store, gateway)
            before = store.read_text("business_knowledge.md")

            patches = service.propose("补充协议版本映射", ["business_knowledge"])

            self.assertTrue(patches)
            self.assertEqual(store.read_text("business_knowledge.md"), before)
```

- [ ] **Step 2: Run failing focused tests**

Run:

```bash
cd knowledge-agent/backend
python3 -m unittest tests/test_repair_service.py -v
```

Expected: FAIL because `RepairService.propose` does not exist.

- [ ] **Step 3: Split `RepairService` while preserving logs**

Modify `RepairService.apply()` into:

```python
    def apply(self, suggestion: str, knowledge_types: list[str] | None) -> list[dict[str, str]]:
        patches = self.propose(suggestion, knowledge_types)
        return self.apply_candidates(patches, suggestion)
```

Add `propose()` that keeps existing `repair_analysis_requested`, `repair_analysis_generated`, `repair_analysis_parsed`, and fallback logs:

```python
    def propose(self, suggestion: str, knowledge_types: list[str] | None) -> list[dict[str, str]]:
        target_types = knowledge_types or self._infer_types(suggestion)
        if self.module_logs is not None:
            self.module_logs.append(
                module="repair",
                level="info",
                operation="repair_analysis_requested",
                status="started",
                request_body={"suggestion": suggestion, "knowledge_types": target_types},
            )
        raw_response = self.model_gateway.generate_text(
            "repair_analysis",
            {"model": "glm-5", "temperature": 0.1, "max_output_tokens": 800},
            prompt=self._repair_analysis_prompt(suggestion, target_types),
        )
        if self.module_logs is not None:
            self.module_logs.append(
                module="repair",
                level="info",
                operation="repair_analysis_generated",
                status="success",
                response_body={"requested_types": target_types, "raw_response": raw_response},
            )
        analysis = self._parse_analysis(raw_response, suggestion, target_types)
        return self._build_patches_from_analysis(analysis, target_types)
```

Add `apply_candidates()` that keeps `knowledge_document_skipped_duplicate` and `knowledge_document_updated` logs:

```python
    def apply_candidates(self, patches: list[dict[str, str]], suggestion: str) -> list[dict[str, str]]:
        changes: list[dict[str, str]] = []
        for patch in patches:
            target_type = patch["doc_type"]
            filename = DOC_FILE_MAP[target_type]
            before = self.store.read_text(filename)
            if self._patch_already_present(before, patch["section"], patch["new_content"]):
                if self.module_logs is not None:
                    self.module_logs.append(
                        module="repair",
                        level="info",
                        operation="knowledge_document_skipped_duplicate",
                        status="skipped",
                        request_body={"filename": filename, "target_type": target_type, "section": patch["section"], "target_key": patch["target_key"]},
                    )
                continue
            item = f"[id: {patch['target_key']}]\n{patch['new_content']}"
            after = append_item_under_section(before, patch["section"], item)
            self.store.write_versioned(filename, before, after, suggestion, target_type)
            if self.module_logs is not None:
                self.module_logs.append(
                    module="repair",
                    level="info",
                    operation="knowledge_document_updated",
                    status="success",
                    request_body={"filename": filename, "target_type": target_type, "section": patch["section"], "target_key": patch["target_key"]},
                    response_body={"before": before, "after": after},
                )
            changes.append({"doc_type": target_type, "section": patch["section"], "before": before, "after": after})
        return changes
```

Add `_repair_analysis_prompt()` containing the prompt string currently embedded in `apply()`.

- [ ] **Step 4: Run legacy repair tests**

Run:

```bash
cd knowledge-agent/backend
python3 -m unittest tests/test_repair_service.py tests/test_prompt_service.py tests/test_api_contracts.py -v
```

Expected: PASS.

---

### Task 5: Full V1 Tool Set

**Files:**
- Create: `knowledge-agent/backend/app/domain/agent/tools.py`
- Create: `knowledge-agent/backend/app/domain/agent/evaluator.py`
- Test: `knowledge-agent/backend/tests/test_agent_tools_evaluator.py`

- [ ] **Step 1: Write failing tests for the full minimum tool set**

Create `knowledge-agent/backend/tests/test_agent_tools_evaluator.py`:

```python
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.domain.agent.evaluator import RepairEvaluator
from app.domain.agent.memory import MemoryManager
from app.domain.agent.tools import (
    BuildPromptOverlayInput,
    BuildPromptOverlayTool,
    CheckConflictInput,
    CheckConflictTool,
    CheckDuplicateInput,
    CheckDuplicateTool,
    ClassifyGapInput,
    ClassifyGapTool,
    InspectQACaseInput,
    InspectQACaseTool,
    RagRetrieveInput,
    RagRetrieveTool,
    ReadRepairMemoryInput,
    ReadRepairMemoryTool,
    RetrieveKnowledgeInput,
    RetrieveKnowledgeTool,
    WriteRepairMemoryInput,
    WriteRepairMemoryTool,
)
from app.storage.knowledge_store import KnowledgeStore


class FakeQAGateway:
    def get_detail(self, qa_id: str) -> dict:
        return {"id": qa_id, "question": "查询协议版本为v2.0的隧道所属网元", "cypher": "MATCH (t:Tunnel) RETURN t.id"}


class AgentToolsEvaluatorTest(unittest.TestCase):
    def test_inspect_retrieve_memory_and_rag_tools(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            store = KnowledgeStore(Path(tmp_dir))
            store.bootstrap_defaults()
            memory = MemoryManager(Path(tmp_dir) / "memory")
            memory.write_repair_memory({"qa_id": "qa_old", "root_cause_type": "missing_path_rule", "summary": "协议路径"})

            qa = InspectQACaseTool(FakeQAGateway()).execute(InspectQACaseInput(qa_id="qa_001"))
            markdown = RetrieveKnowledgeTool(store).execute(RetrieveKnowledgeInput(query="协议版本 所属网元"))
            rag = RagRetrieveTool().execute(RagRetrieveInput(query="协议版本", filters={}))
            mem = ReadRepairMemoryTool(memory).execute(ReadRepairMemoryInput(query="missing_path_rule 协议"))

            self.assertEqual(qa["qa_id"], "qa_001")
            self.assertTrue(markdown["hits"])
            self.assertEqual(rag["hits"], [])
            self.assertEqual(mem["memory_hits"][0]["qa_id"], "qa_old")

    def test_gap_duplicate_conflict_overlay_and_memory_tools(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            store = KnowledgeStore(Path(tmp_dir))
            store.bootstrap_defaults()
            evaluator = RepairEvaluator()
            memory = MemoryManager(Path(tmp_dir) / "memory")
            candidate = {
                "operation": "add",
                "doc_type": "business_knowledge",
                "section": "Terminology Mapping",
                "target_key": "protocol_owner_path",
                "new_content": "- 协议版本所属网元应走 NetworkElement -> Tunnel -> Protocol。",
                "risk": "medium",
                "confidence": 0.9,
            }

            gap = ClassifyGapTool(evaluator).execute(ClassifyGapInput(markdown_hits=[{"content": "Protocol.version"}], rag_hits=[], validation_errors=[]))
            duplicate = CheckDuplicateTool(store).execute(CheckDuplicateInput(candidate_change=candidate))
            conflict = CheckConflictTool().execute(CheckConflictInput(candidate_change=candidate, existing_hits=[]))
            overlay = BuildPromptOverlayTool(store).execute(BuildPromptOverlayInput(question="查询协议版本为v2.0的隧道所属网元", candidate_changes=[candidate]))
            written = WriteRepairMemoryTool(memory).execute(WriteRepairMemoryInput(entry={"qa_id": "qa_001", "root_cause_type": "missing_path_rule"}))

            self.assertEqual(gap["gap_diagnosis"]["gap_type"], "retrieval_miss")
            self.assertFalse(duplicate["duplicate_found"])
            self.assertFalse(conflict["conflict_found"])
            self.assertIn("NetworkElement -> Tunnel -> Protocol", overlay["prompt"])
            self.assertIn("memory_id", written["memory"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run failing tests**

Run:

```bash
cd knowledge-agent/backend
python3 -m unittest tests/test_agent_tools_evaluator.py -v
```

Expected: FAIL because tools and evaluator do not exist.

- [ ] **Step 3: Implement evaluator**

Create `knowledge-agent/backend/app/domain/agent/evaluator.py`:

```python
from __future__ import annotations

from app.domain.agent.models import GapDiagnosis, ValidationSummary


class RepairEvaluator:
    def classify_gap(self, markdown_hits: list[dict], rag_hits: list[dict], validation_errors: list[str]) -> GapDiagnosis:
        if markdown_hits and not rag_hits:
            return GapDiagnosis(gap_type="retrieval_miss", reason="Markdown knowledge exists but RAG returned no hits.", suggested_action="Improve metadata/index/rerank.")
        if not markdown_hits and not rag_hits:
            return GapDiagnosis(gap_type="knowledge_missing", reason="No markdown or RAG knowledge covers the failure.", suggested_action="Propose a knowledge patch.")
        if "conflict" in validation_errors:
            return GapDiagnosis(gap_type="knowledge_conflict", reason="Validation reported conflict.", suggested_action="Request human review.")
        return GapDiagnosis(gap_type="generator_noncompliance", reason="Knowledge appears available but generation did not comply.", suggested_action="Strengthen few-shot, anti-pattern, or validator.")

    def evaluate_prompt_delta(self, before_prompt: str, after_prompt: str, expected_terms: list[str]) -> ValidationSummary:
        improved = bool(after_prompt) and after_prompt != before_prompt and all(term in after_prompt for term in expected_terms)
        return ValidationSummary(prompt_package_built=bool(after_prompt), before_after_improved=improved, remaining_risks=[] if improved else ["expected terms missing"])
```

- [ ] **Step 4: Implement full V1 tools**

Create `knowledge-agent/backend/app/domain/agent/tools.py` with all tool classes named in the tests. Each tool returns a dict with stable keys:

```python
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from pydantic import BaseModel, Field

from app.domain.agent.tool_registry import AgentTool
from app.domain.knowledge.prompt_service import PromptService
from app.domain.knowledge.retriever import KnowledgeRetriever
from app.storage.knowledge_store import DOCUMENTS, KnowledgeStore


class InspectQACaseInput(BaseModel):
    qa_id: str


class InspectQACaseTool(AgentTool):
    name = "inspect_qa_case"
    description = "Load the failed QA case by qa_id from qa-agent, including question, Cypher, answer, and validation context when available."
    input_model = InspectQACaseInput

    def __init__(self, qa_gateway) -> None:
        self.qa_gateway = qa_gateway

    def execute(self, arguments: InspectQACaseInput) -> dict:
        detail = self.qa_gateway.get_detail(arguments.qa_id)
        return {"qa_id": arguments.qa_id, "qa_case": detail}


class RetrieveKnowledgeInput(BaseModel):
    query: str = Field(min_length=1)
    filters: dict[str, Any] = Field(default_factory=dict)


class RetrieveKnowledgeTool(AgentTool):
    name = "retrieve_knowledge"
    description = "Retrieve related markdown knowledge blocks from schema, syntax, business knowledge, and few-shot documents."
    input_model = RetrieveKnowledgeInput

    def __init__(self, store: KnowledgeStore) -> None:
        self.store = store

    def execute(self, arguments: RetrieveKnowledgeInput) -> dict:
        bundle = KnowledgeRetriever(self.store).retrieve(arguments.query)
        hits = [
            {"doc_type": "schema", "content": bundle["schema_context"]},
            {"doc_type": "cypher_syntax", "content": bundle["syntax_context"]},
            {"doc_type": "business_knowledge", "content": bundle["business_context"]},
            {"doc_type": "few_shot", "content": bundle["few_shot_examples"]},
        ]
        requested = set(arguments.filters.get("knowledge_types", []))
        if requested:
            hits = [hit for hit in hits if hit["doc_type"] in requested]
        return {"hits": hits}


class RagRetrieveInput(BaseModel):
    query: str
    filters: dict[str, Any] = Field(default_factory=dict)


class RagRetrieveTool(AgentTool):
    name = "rag_retrieve"
    description = "Retrieve related chunks from the future RAG index; V1 returns an empty result when the adapter is not configured."
    input_model = RagRetrieveInput

    def execute(self, arguments: RagRetrieveInput) -> dict:
        return {"hits": [], "adapter": "not_configured"}


class ReadRepairMemoryInput(BaseModel):
    query: str


class ReadRepairMemoryTool(AgentTool):
    name = "read_repair_memory"
    description = "Search historical repair memory for similar root causes, patches, decisions, and outcomes."
    input_model = ReadRepairMemoryInput

    def __init__(self, memory_manager) -> None:
        self.memory_manager = memory_manager

    def execute(self, arguments: ReadRepairMemoryInput) -> dict:
        return {"memory_hits": self.memory_manager.search_repair_memory(arguments.query)}


class ClassifyGapInput(BaseModel):
    markdown_hits: list[dict] = Field(default_factory=list)
    rag_hits: list[dict] = Field(default_factory=list)
    validation_errors: list[str] = Field(default_factory=list)


class ClassifyGapTool(AgentTool):
    name = "classify_gap"
    description = "Classify whether the failure is caused by missing knowledge, RAG retrieval miss, prompt orchestration gap, generator noncompliance, or conflict."
    input_model = ClassifyGapInput

    def __init__(self, evaluator) -> None:
        self.evaluator = evaluator

    def execute(self, arguments: ClassifyGapInput) -> dict:
        return {"gap_diagnosis": self.evaluator.classify_gap(arguments.markdown_hits, arguments.rag_hits, arguments.validation_errors).model_dump()}


class ProposePatchInput(BaseModel):
    suggestion: str
    knowledge_types: list[str] | None = None


class ProposePatchTool(AgentTool):
    name = "propose_patch"
    description = "Generate candidate knowledge changes from the root-cause suggestion without writing formal knowledge files."
    input_model = ProposePatchInput

    def __init__(self, repair_service) -> None:
        self.repair_service = repair_service

    def execute(self, arguments: ProposePatchInput) -> dict:
        return {"candidate_changes": self.repair_service.propose(arguments.suggestion, arguments.knowledge_types)}


class CheckDuplicateInput(BaseModel):
    candidate_change: dict[str, Any]


class CheckDuplicateTool(AgentTool):
    name = "check_duplicate"
    description = "Check whether a candidate change duplicates existing knowledge content."
    input_model = CheckDuplicateInput

    def __init__(self, store: KnowledgeStore) -> None:
        self.store = store

    def execute(self, arguments: CheckDuplicateInput) -> dict:
        content = arguments.candidate_change.get("new_content", "").strip()
        duplicate = False
        for info in DOCUMENTS.values():
            path = self.store.root / str(info["filename"])
            if path.exists() and content and content in path.read_text(encoding="utf-8"):
                duplicate = True
        return {"duplicate_found": duplicate, "candidate_change": {**arguments.candidate_change, "duplicate_checked": True}}


class CheckConflictInput(BaseModel):
    candidate_change: dict[str, Any]
    existing_hits: list[dict] = Field(default_factory=list)


class CheckConflictTool(AgentTool):
    name = "check_conflict"
    description = "Check whether a candidate change conflicts with retrieved knowledge or known constraints."
    input_model = CheckConflictInput

    def execute(self, arguments: CheckConflictInput) -> dict:
        text = arguments.candidate_change.get("new_content", "")
        conflict = any("冲突" in hit.get("content", "") or "conflict" in hit.get("content", "").lower() for hit in arguments.existing_hits)
        return {"conflict_found": conflict, "candidate_change": {**arguments.candidate_change, "conflict_checked": True}, "checked_text": text}


class BuildPromptOverlayInput(BaseModel):
    question: str
    candidate_changes: list[dict[str, Any]] = Field(default_factory=list)


class BuildPromptOverlayTool(AgentTool):
    name = "build_prompt_overlay"
    description = "Build a temporary prompt package with candidate changes overlaid, without modifying formal knowledge files."
    input_model = BuildPromptOverlayInput

    def __init__(self, store: KnowledgeStore) -> None:
        self.store = store

    def execute(self, arguments: BuildPromptOverlayInput) -> dict:
        before_prompt = PromptService(self.store).build_prompt(arguments.question)
        with TemporaryDirectory() as tmp_dir:
            overlay = KnowledgeStore(Path(tmp_dir))
            overlay.bootstrap_defaults()
            for info in DOCUMENTS.values():
                source = self.store.root / str(info["filename"])
                target = overlay.root / str(info["filename"])
                target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
            for change in arguments.candidate_changes:
                filename = DOCUMENTS[change["doc_type"]]["filename"]
                path = overlay.root / str(filename)
                path.write_text(path.read_text(encoding="utf-8").rstrip() + f"\n\n[id: {change['target_key']}]\n{change['new_content']}\n", encoding="utf-8")
            after_prompt = PromptService(overlay).build_prompt(arguments.question)
        return {"before_prompt": before_prompt, "prompt": after_prompt, "prompt_length": len(after_prompt)}


class EvaluateBeforeAfterInput(BaseModel):
    before_prompt: str
    after_prompt: str
    expected_terms: list[str] = Field(default_factory=list)


class EvaluateBeforeAfterTool(AgentTool):
    name = "evaluate_before_after"
    description = "Evaluate whether the overlay prompt improves expected repair signals compared with the original prompt."
    input_model = EvaluateBeforeAfterInput

    def __init__(self, evaluator) -> None:
        self.evaluator = evaluator

    def execute(self, arguments: EvaluateBeforeAfterInput) -> dict:
        return {"validation": self.evaluator.evaluate_prompt_delta(arguments.before_prompt, arguments.after_prompt, arguments.expected_terms).model_dump()}


class RedispatchQAInput(BaseModel):
    qa_id: str


class RedispatchQATool(AgentTool):
    name = "redispatch_qa"
    description = "Trigger QA redispatch. This is side-effecting and is not exposed to the LLM default tool allowlist."
    input_model = RedispatchQAInput
    side_effect = True

    def __init__(self, qa_redispatch_gateway) -> None:
        self.qa_redispatch_gateway = qa_redispatch_gateway

    def execute(self, arguments: RedispatchQAInput) -> dict:
        return {"redispatch": self.qa_redispatch_gateway.redispatch(arguments.qa_id)}


class WriteRepairMemoryInput(BaseModel):
    entry: dict[str, Any]


class WriteRepairMemoryTool(AgentTool):
    name = "write_repair_memory"
    description = "Write a repair memory entry. This is side-effecting and is used by the runtime completion path."
    input_model = WriteRepairMemoryInput
    side_effect = True

    def __init__(self, memory_manager) -> None:
        self.memory_manager = memory_manager

    def execute(self, arguments: WriteRepairMemoryInput) -> dict:
        return {"memory": self.memory_manager.write_repair_memory(arguments.entry)}
```

- [ ] **Step 5: Run tests**

Run:

```bash
cd knowledge-agent/backend
python3 -m unittest tests/test_agent_tools_evaluator.py -v
```

Expected: PASS.

---

### Task 6: Policy Guard And Runtime Observation Mapping

**Files:**
- Create: `knowledge-agent/backend/app/domain/agent/policy.py`
- Create: `knowledge-agent/backend/app/domain/agent/runtime.py`
- Test: `knowledge-agent/backend/tests/test_agent_policy_runtime.py`

- [ ] **Step 1: Write failing tests**

Create `knowledge-agent/backend/tests/test_agent_policy_runtime.py`:

```python
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from pydantic import BaseModel

from app.domain.agent.memory import MemoryManager
from app.domain.agent.models import AgentAction, AgentConstraints, AgentRunStatus, CandidateChange, RootCause, ValidationSummary
from app.domain.agent.policy import PolicyGuard
from app.domain.agent.run_store import AgentRunStore
from app.domain.agent.runtime import RepairAgentRuntime
from app.domain.agent.tool_registry import AgentTool, ToolRegistry
from app.errors import AppError


class FakeController:
    def __init__(self, action: AgentAction) -> None:
        self.action = action

    def decide_next_action(self, context, memory, tools):
        return self.action


class PatchInput(BaseModel):
    suggestion: str


class PatchTool(AgentTool):
    name = "propose_patch"
    input_model = PatchInput

    def execute(self, arguments: PatchInput) -> dict:
        return {
            "candidate_changes": [
                {
                    "doc_type": "few_shot",
                    "section": "Reference Examples",
                    "target_key": "k",
                    "new_content": "Question: q\nCypher: MATCH (n) RETURN n",
                    "risk": "low",
                    "confidence": 0.95,
                    "duplicate_checked": True,
                    "conflict_checked": True,
                }
            ]
        }


class PolicyRuntimeTest(unittest.TestCase):
    def test_auto_apply_guard_differs_from_human_approval_guard(self) -> None:
        guard = PolicyGuard()
        run = self._run(auto_apply=False)
        change = CandidateChange(
            doc_type="few_shot",
            section="Reference Examples",
            target_key="k",
            new_content="Question: q\nCypher: MATCH (n) RETURN n",
            risk="low",
            confidence=0.95,
            duplicate_checked=True,
            conflict_checked=True,
        )
        validation = ValidationSummary(before_after_improved=True)

        with self.assertRaises(AppError):
            guard.assert_can_auto_apply(run, [change], validation)
        guard.assert_can_apply_after_human_approval(run, [change], validation)

    def test_runtime_maps_candidate_observation_into_run(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            registry = ToolRegistry()
            registry.register(PatchTool())
            runtime = RepairAgentRuntime(
                AgentRunStore(Path(tmp_dir) / "runs"),
                FakeController(AgentAction(action="tool_call", tool_name="propose_patch", arguments={"suggestion": "fix"}, reason_summary="生成候选 patch")),
                registry,
                MemoryManager(Path(tmp_dir) / "memory"),
                PolicyGuard(),
            )
            run = runtime.create_run("qa_001", "repair", RootCause(type="missing_few_shot", summary="s", suggested_fix="f"), AgentConstraints(allowed_tools=["propose_patch"]))

            stepped = runtime.step(run.run_id)

            self.assertEqual(stepped.status, AgentRunStatus.RUNNING)
            self.assertEqual(stepped.candidate_changes[0].target_key, "k")

    def _run(self, auto_apply: bool):
        from app.domain.agent.models import AgentRun
        return AgentRun(qa_id="qa_001", goal="repair", root_cause=RootCause(type="missing_few_shot", summary="s", suggested_fix="f"), constraints=AgentConstraints(auto_apply=auto_apply))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run failing tests**

Run:

```bash
cd knowledge-agent/backend
python3 -m unittest tests/test_agent_policy_runtime.py -v
```

Expected: FAIL because policy and runtime do not exist.

- [ ] **Step 3: Implement policy**

Create `knowledge-agent/backend/app/domain/agent/policy.py`:

```python
from __future__ import annotations

from app.domain.agent.models import AgentRun, CandidateChange, ValidationSummary
from app.errors import AppError


class PolicyGuard:
    def assert_can_auto_apply(self, run: AgentRun, changes: list[CandidateChange], validation: ValidationSummary) -> None:
        if not run.constraints.auto_apply:
            raise AppError("AGENT_HUMAN_REVIEW_REQUIRED", "auto_apply is disabled for this run.")
        self._assert_common_apply_requirements(changes, validation)
        for change in changes:
            if self.requires_review(change):
                raise AppError("AGENT_HUMAN_REVIEW_REQUIRED", f"{change.doc_type} change requires human review.")

    def assert_can_apply_after_human_approval(self, run: AgentRun, changes: list[CandidateChange], validation: ValidationSummary) -> None:
        self._assert_common_apply_requirements(changes, validation)

    def requires_review(self, change: CandidateChange) -> bool:
        return change.doc_type == "system_prompt" or (change.doc_type == "cypher_syntax" and change.risk in {"medium", "high"})

    def _assert_common_apply_requirements(self, changes: list[CandidateChange], validation: ValidationSummary) -> None:
        if not changes:
            raise AppError("AGENT_NO_CANDIDATE_PATCH", "No candidate changes are available to apply.")
        if not validation.before_after_improved:
            raise AppError("AGENT_VALIDATION_NOT_IMPROVED", "Candidate patch did not improve validation signals.")
        for change in changes:
            if not change.duplicate_checked:
                raise AppError("AGENT_DUPLICATE_CHECK_REQUIRED", "Candidate patch must pass duplicate check.")
            if not change.conflict_checked:
                raise AppError("AGENT_CONFLICT_CHECK_REQUIRED", "Candidate patch must pass conflict check.")
            if change.confidence < 0.8:
                raise AppError("AGENT_CONFIDENCE_TOO_LOW", "Candidate patch confidence is below threshold.")
```

- [ ] **Step 4: Implement runtime with observation mapping**

Create `knowledge-agent/backend/app/domain/agent/runtime.py`:

```python
from __future__ import annotations

from app.domain.agent.models import (
    AgentConstraints,
    AgentDecision,
    AgentRun,
    AgentRunStatus,
    CandidateChange,
    GapDiagnosis,
    RootCause,
    ValidationSummary,
)
from app.errors import AppError


class RepairAgentRuntime:
    def __init__(self, run_store, controller, tool_registry, memory_manager, policy_guard, repair_service=None, qa_redispatch_gateway=None) -> None:
        self.run_store = run_store
        self.controller = controller
        self.tool_registry = tool_registry
        self.memory_manager = memory_manager
        self.policy_guard = policy_guard
        self.repair_service = repair_service
        self.qa_redispatch_gateway = qa_redispatch_gateway

    def create_run(self, qa_id: str, goal: str, root_cause: RootCause, constraints: AgentConstraints) -> AgentRun:
        return self.run_store.create(qa_id=qa_id, goal=goal, root_cause=root_cause, constraints=constraints)

    def step(self, run_id: str) -> AgentRun:
        run = self.run_store.get(run_id)
        if len(run.trace) >= run.constraints.max_steps:
            run.status = AgentRunStatus.FAILED
            run.errors.append("max_steps exceeded")
            return self.run_store.save(run)
        memory = self.memory_manager.search_repair_memory(f"{run.root_cause.type} {run.root_cause.summary} {run.root_cause.suggested_fix}")
        action = self.controller.decide_next_action(context=run.model_dump(), memory=memory, tools=self.tool_registry.allowed_tool_specs(run))
        if action.action == "request_human_review":
            run.status = AgentRunStatus.NEEDS_REVIEW
            run.decision = AgentDecision(action="human_review", reason=action.reason_summary)
            run.trace.append({"step": len(run.trace) + 1, "action": action, "observation": {}})
            return self.run_store.save(run)
        if action.action == "final":
            run.decision = AgentDecision(action="human_review" if action.status == "ready_for_review" else "reject", reason=action.reason_summary)
            run.status = AgentRunStatus.NEEDS_REVIEW if action.status == "ready_for_review" else AgentRunStatus.REJECTED
            run.trace.append({"step": len(run.trace) + 1, "action": action, "observation": {"summary": action.summary}})
            return self.run_store.save(run)
        try:
            observation = self.tool_registry.execute(run, action)
            run = self.run_store.append_trace(run.run_id, action, observation)
            run = self.apply_observation_to_run(run, action.tool_name or "", observation)
            run.status = AgentRunStatus.RUNNING
            run = self.maybe_auto_apply(run)
            return self.run_store.save(run)
        except Exception as exc:
            run = self.run_store.append_trace(run.run_id, action, {}, error=str(exc))
            run.status = AgentRunStatus.FAILED
            run.errors.append(str(exc))
            return self.run_store.save(run)

    def apply_observation_to_run(self, run: AgentRun, tool_name: str, observation: dict) -> AgentRun:
        if tool_name == "inspect_qa_case":
            run.evidence.append({"type": "qa_case", **observation})
        elif tool_name in {"retrieve_knowledge", "rag_retrieve"}:
            run.evidence.append({"type": tool_name, "hits": observation.get("hits", [])})
        elif tool_name == "read_repair_memory":
            run.memory_hits = observation.get("memory_hits", [])
        elif tool_name == "classify_gap":
            run.gap_diagnosis = GapDiagnosis.model_validate(observation.get("gap_diagnosis", {}))
        elif tool_name == "propose_patch":
            run.candidate_changes = [CandidateChange.model_validate(item) for item in observation.get("candidate_changes", [])]
        elif tool_name in {"check_duplicate", "check_conflict"} and observation.get("candidate_change"):
            checked = CandidateChange.model_validate(observation["candidate_change"])
            run.candidate_changes = [checked if item.target_key == checked.target_key else item for item in run.candidate_changes]
        elif tool_name == "build_prompt_overlay":
            run.evidence.append({"type": "prompt_overlay", "prompt_length": observation.get("prompt_length", 0)})
        elif tool_name == "evaluate_before_after":
            run.validation = ValidationSummary.model_validate(observation.get("validation", {}))
        elif tool_name == "redispatch_qa":
            run.validation.redispatch_status = observation.get("redispatch", {}).get("status", "unknown")
        elif tool_name == "write_repair_memory":
            run.evidence.append({"type": "repair_memory", "memory": observation.get("memory", {})})
        return run

    def maybe_auto_apply(self, run: AgentRun) -> AgentRun:
        if not run.constraints.auto_apply:
            return run
        try:
            self.policy_guard.assert_can_auto_apply(run, run.candidate_changes, run.validation)
        except AppError:
            return run
        return self.apply_and_complete(run)
```

- [ ] **Step 5: Run tests**

Run:

```bash
cd knowledge-agent/backend
python3 -m unittest tests/test_agent_policy_runtime.py -v
```

Expected: PASS.

---

### Task 7: Approval, Redispatch, Completion

**Files:**
- Modify: `knowledge-agent/backend/app/domain/agent/runtime.py`
- Test: `knowledge-agent/backend/tests/test_agent_approval_lifecycle.py`

- [ ] **Step 1: Write failing lifecycle tests**

Create `knowledge-agent/backend/tests/test_agent_approval_lifecycle.py`:

```python
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.domain.agent.memory import MemoryManager
from app.domain.agent.models import AgentConstraints, AgentRunStatus, CandidateChange, RootCause, ValidationSummary
from app.domain.agent.policy import PolicyGuard
from app.domain.agent.run_store import AgentRunStore
from app.domain.agent.runtime import RepairAgentRuntime


class FakeRepairService:
    def __init__(self) -> None:
        self.applied = []

    def apply_candidates(self, patches, suggestion):
        self.applied.append((patches, suggestion))
        return [{"doc_type": patches[0]["doc_type"], "section": patches[0]["section"], "before": "old", "after": "new"}]


class FakeRedispatchGateway:
    def __init__(self) -> None:
        self.calls = []

    def redispatch(self, qa_id: str):
        self.calls.append(qa_id)
        return {"qa_id": qa_id, "status": "success", "attempt": 1, "max_attempts": 3, "dispatch": {"status": "success"}}


class ApprovalLifecycleTest(unittest.TestCase):
    def test_approve_applies_redispatches_writes_memory_and_completes(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            memory = MemoryManager(Path(tmp_dir) / "memory")
            repair = FakeRepairService()
            redispatch = FakeRedispatchGateway()
            runtime = RepairAgentRuntime(AgentRunStore(Path(tmp_dir) / "runs"), None, None, memory, PolicyGuard(), repair, redispatch)
            run = runtime.create_run("qa_001", "repair", RootCause(type="missing_few_shot", summary="s", suggested_fix="f"), AgentConstraints(auto_apply=False))
            run.candidate_changes = [
                CandidateChange(
                    doc_type="few_shot",
                    section="Reference Examples",
                    target_key="k",
                    new_content="Question: q\nCypher: MATCH (n) RETURN n",
                    risk="low",
                    confidence=0.95,
                    duplicate_checked=True,
                    conflict_checked=True,
                )
            ]
            run.validation = ValidationSummary(before_after_improved=True)
            runtime.run_store.save(run)

            completed = runtime.approve(run.run_id)

            self.assertEqual(completed.status, AgentRunStatus.COMPLETED)
            self.assertEqual(repair.applied[0][0][0]["target_key"], "k")
            self.assertEqual(redispatch.calls, ["qa_001"])
            self.assertTrue(memory.search_repair_memory("missing_few_shot"))

    def test_auto_apply_uses_same_apply_redispatch_memory_completion_path(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            memory = MemoryManager(Path(tmp_dir) / "memory")
            repair = FakeRepairService()
            redispatch = FakeRedispatchGateway()
            runtime = RepairAgentRuntime(AgentRunStore(Path(tmp_dir) / "runs"), None, None, memory, PolicyGuard(), repair, redispatch)
            run = runtime.create_run("qa_001", "repair", RootCause(type="missing_few_shot", summary="s", suggested_fix="f"), AgentConstraints(auto_apply=True))
            run.candidate_changes = [
                CandidateChange(
                    doc_type="few_shot",
                    section="Reference Examples",
                    target_key="k",
                    new_content="Question: q\nCypher: MATCH (n) RETURN n",
                    risk="low",
                    confidence=0.95,
                    duplicate_checked=True,
                    conflict_checked=True,
                )
            ]
            run.validation = ValidationSummary(before_after_improved=True)

            completed = runtime.maybe_auto_apply(run)

            self.assertEqual(completed.status, AgentRunStatus.COMPLETED)
            self.assertEqual(redispatch.calls, ["qa_001"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run failing test**

Run:

```bash
cd knowledge-agent/backend
python3 -m unittest tests/test_agent_approval_lifecycle.py -v
```

Expected: FAIL because `approve()` does not exist.

- [ ] **Step 3: Implement approve and reject**

Add to `RepairAgentRuntime`:

```python
    def approve(self, run_id: str) -> AgentRun:
        run = self.run_store.get(run_id)
        self.policy_guard.assert_can_apply_after_human_approval(run, run.candidate_changes, run.validation)
        return self.apply_and_complete(run)

    def apply_and_complete(self, run: AgentRun) -> AgentRun:
        patches = [change.model_dump() for change in run.candidate_changes]
        changes = self.repair_service.apply_candidates(patches, run.root_cause.suggested_fix)
        run.status = AgentRunStatus.APPLIED
        run.evidence.append({"type": "applied_changes", "changes": changes})
        self.run_store.save(run)
        redispatch = self.qa_redispatch_gateway.redispatch(run.qa_id)
        run.status = AgentRunStatus.REDISPATCHED
        run.validation.redispatch_status = redispatch.get("status", "unknown")
        self.run_store.save(run)
        memory = self.memory_manager.write_repair_memory(
            {
                "qa_id": run.qa_id,
                "root_cause_type": run.root_cause.type,
                "summary": run.root_cause.summary,
                "candidate_count": len(run.candidate_changes),
                "validation": run.validation.model_dump(),
                "redispatch": redispatch,
            }
        )
        run.evidence.append({"type": "repair_memory", "memory": memory})
        run.status = AgentRunStatus.COMPLETED
        return self.run_store.save(run)

    def reject(self, run_id: str, reason: str) -> AgentRun:
        run = self.run_store.get(run_id)
        run.status = AgentRunStatus.REJECTED
        run.decision = AgentDecision(action="reject", reason=reason)
        self.memory_manager.write_repair_memory({"qa_id": run.qa_id, "root_cause_type": run.root_cause.type, "summary": run.root_cause.summary, "rejected": True, "reason": reason})
        return self.run_store.save(run)
```

- [ ] **Step 4: Run lifecycle tests**

Run:

```bash
cd knowledge-agent/backend
python3 -m unittest tests/test_agent_approval_lifecycle.py tests/test_agent_policy_runtime.py -v
```

Expected: PASS.

---

### Task 8: FastAPI Agent Endpoints

**Files:**
- Modify: `knowledge-agent/backend/app/integrations/qa_agent/redispatch_gateway.py`
- Modify: `knowledge-agent/backend/app/entrypoints/api/main.py`
- Test: `knowledge-agent/backend/tests/test_agent_api_contracts.py`

- [ ] **Step 1: Write failing API contract tests**

Create `knowledge-agent/backend/tests/test_agent_api_contracts.py`:

```python
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.domain.agent.models import AgentConstraints, AgentRun, AgentRunStatus, RootCause
from app.entrypoints.api.main import app
from app.integrations.qa_agent.redispatch_gateway import QARedispatchGateway


class FakeHTTPResponse:
    is_success = True
    status_code = 200

    def json(self):
        return {"id": "qa_001", "question": "q", "cypher": "MATCH (n) RETURN n"}

    def raise_for_status(self):
        return None


class FakeHTTPClient:
    def __init__(self) -> None:
        self.urls = []

    def get(self, url: str):
        self.urls.append(url)
        return FakeHTTPResponse()


class FakeRuntime:
    def create_run(self, qa_id, goal, root_cause, constraints):
        return AgentRun(run_id="krun_001", qa_id=qa_id, goal=goal, root_cause=root_cause, constraints=constraints, status=AgentRunStatus.CREATED)

    def step(self, run_id):
        return AgentRun(run_id=run_id, qa_id="qa_001", goal="repair", root_cause=RootCause(type="missing_path_rule", summary="s", suggested_fix="f"), constraints=AgentConstraints(), status=AgentRunStatus.RUNNING)

    def approve(self, run_id):
        return AgentRun(run_id=run_id, qa_id="qa_001", goal="repair", root_cause=RootCause(type="missing_path_rule", summary="s", suggested_fix="f"), constraints=AgentConstraints(), status=AgentRunStatus.COMPLETED)

    def reject(self, run_id, reason):
        return AgentRun(run_id=run_id, qa_id="qa_001", goal="repair", root_cause=RootCause(type="missing_path_rule", summary="s", suggested_fix="f"), constraints=AgentConstraints(), status=AgentRunStatus.REJECTED)


class AgentApiContractsTest(unittest.TestCase):
    def test_qa_redispatch_gateway_get_detail_calls_qa_agent_detail_endpoint(self) -> None:
        client = FakeHTTPClient()
        gateway = QARedispatchGateway(client=client)

        detail = gateway.get_detail("qa_001")

        self.assertEqual(detail["id"], "qa_001")
        self.assertTrue(client.urls[0].endswith("/qa/qa_001"))

    def test_create_step_approve_reject_contracts(self) -> None:
        client = TestClient(app)
        with patch("app.entrypoints.api.main.repair_agent_runtime", FakeRuntime()):
            create_response = client.post(
                "/api/knowledge/agent/repair-runs",
                json={
                    "qa_id": "qa_001",
                    "goal": "根据已知根因修复知识，并验证是否改善",
                    "root_cause": {"type": "missing_path_rule", "summary": "s", "suggested_fix": "f"},
                    "constraints": {"auto_apply": False, "max_steps": 12},
                },
            )
            step_response = client.post("/api/knowledge/agent/repair-runs/krun_001/step")
            approve_response = client.post("/api/knowledge/agent/repair-runs/krun_001/approve")
            reject_response = client.post("/api/knowledge/agent/repair-runs/krun_001/reject", json={"reason": "manual"})

        self.assertEqual(create_response.json()["run"]["status"], "created")
        self.assertEqual(step_response.json()["run"]["status"], "running")
        self.assertEqual(approve_response.json()["run"]["status"], "completed")
        self.assertEqual(reject_response.json()["run"]["status"], "rejected")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run failing tests**

Run:

```bash
cd knowledge-agent/backend
python3 -m unittest tests/test_agent_api_contracts.py -v
```

Expected: FAIL because endpoints are not registered.

- [ ] **Step 3: Wire dependencies and endpoints**

Modify `knowledge-agent/backend/app/entrypoints/api/main.py` imports:

```python
from app.domain.agent.controller import LLMController
from app.domain.agent.evaluator import RepairEvaluator
from app.domain.agent.memory import MemoryManager
from app.domain.agent.policy import PolicyGuard
from app.domain.agent.run_store import AgentRunStore
from app.domain.agent.runtime import RepairAgentRuntime
from app.domain.agent.tool_registry import ToolRegistry
from app.domain.agent.tools import (
    BuildPromptOverlayTool,
    CheckConflictTool,
    CheckDuplicateTool,
    ClassifyGapTool,
    EvaluateBeforeAfterTool,
    InspectQACaseTool,
    ProposePatchTool,
    RagRetrieveTool,
    ReadRepairMemoryTool,
    RedispatchQATool,
    RetrieveKnowledgeTool,
    WriteRepairMemoryTool,
)
from app.domain.models import CreateRepairAgentRunRequest, RejectRepairAgentRunRequest, RepairAgentRunResponse
```

Add QA detail lookup to `knowledge-agent/backend/app/integrations/qa_agent/redispatch_gateway.py` so `inspect_qa_case` can call the existing `qa-agent` detail endpoint:

```python
    def get_detail(self, qa_id: str) -> dict:
        url = f"{settings.qa_agent_base_url.rstrip('/')}/qa/{qa_id}"
        if self.module_logs is not None:
            self.module_logs.append(
                module="qa_case",
                level="info",
                operation="qa_agent_detail_requested",
                trace_id=qa_id,
                status="started",
                request_body={"url": url, "qa_id": qa_id},
            )
        response = self.client.get(url)
        payload = response.json()
        if self.module_logs is not None:
            self.module_logs.append(
                module="qa_case",
                level="info" if response.is_success else "error",
                operation="qa_agent_detail_completed",
                trace_id=qa_id,
                status="success" if response.is_success else "error",
                request_body={"url": url, "qa_id": qa_id},
                response_body=payload,
                http_status=response.status_code,
            )
        response.raise_for_status()
        return payload
```

Initialize runtime:

```python
agent_memory_manager = MemoryManager()
agent_evaluator = RepairEvaluator()
agent_tool_registry = ToolRegistry()
agent_tool_registry.register(InspectQACaseTool(qa_redispatch_gateway))
agent_tool_registry.register(RetrieveKnowledgeTool(knowledge_store))
agent_tool_registry.register(RagRetrieveTool())
agent_tool_registry.register(ReadRepairMemoryTool(agent_memory_manager))
agent_tool_registry.register(ClassifyGapTool(agent_evaluator))
agent_tool_registry.register(ProposePatchTool(repair_service))
agent_tool_registry.register(CheckDuplicateTool(knowledge_store))
agent_tool_registry.register(CheckConflictTool())
agent_tool_registry.register(BuildPromptOverlayTool(knowledge_store))
agent_tool_registry.register(EvaluateBeforeAfterTool(agent_evaluator))
agent_tool_registry.register(RedispatchQATool(qa_redispatch_gateway))
agent_tool_registry.register(WriteRepairMemoryTool(agent_memory_manager))
repair_agent_runtime = RepairAgentRuntime(
    AgentRunStore(),
    LLMController(ModelGateway()),
    agent_tool_registry,
    agent_memory_manager,
    PolicyGuard(),
    repair_service,
    qa_redispatch_gateway,
)
```

Add endpoints:

```python
@app.post("/api/knowledge/agent/repair-runs", response_model=RepairAgentRunResponse)
def create_repair_agent_run(request: CreateRepairAgentRunRequest) -> RepairAgentRunResponse:
    run = repair_agent_runtime.create_run(request.qa_id, request.goal, request.root_cause, request.constraints)
    return RepairAgentRunResponse(status="ok", run=run)


@app.post("/api/knowledge/agent/repair-runs/{run_id}/step", response_model=RepairAgentRunResponse)
def step_repair_agent_run(run_id: str) -> RepairAgentRunResponse:
    return RepairAgentRunResponse(status="ok", run=repair_agent_runtime.step(run_id))


@app.post("/api/knowledge/agent/repair-runs/{run_id}/approve", response_model=RepairAgentRunResponse)
def approve_repair_agent_run(run_id: str) -> RepairAgentRunResponse:
    return RepairAgentRunResponse(status="ok", run=repair_agent_runtime.approve(run_id))


@app.post("/api/knowledge/agent/repair-runs/{run_id}/reject", response_model=RepairAgentRunResponse)
def reject_repair_agent_run(run_id: str, request: RejectRepairAgentRunRequest) -> RepairAgentRunResponse:
    return RepairAgentRunResponse(status="ok", run=repair_agent_runtime.reject(run_id, request.reason))
```

- [ ] **Step 4: Run API and legacy tests**

Run:

```bash
cd knowledge-agent/backend
python3 -m unittest tests/test_agent_api_contracts.py tests/test_api_contracts.py -v
```

Expected: PASS.

---

### Task 9: End-To-End Agent Loop Tests

**Files:**
- Test: `knowledge-agent/backend/tests/test_agent_end_to_end.py`

- [ ] **Step 1: Write end-to-end fake LLM test**

Create `knowledge-agent/backend/tests/test_agent_end_to_end.py`:

```python
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.domain.agent.controller import LLMController
from app.domain.agent.memory import MemoryManager
from app.domain.agent.models import AgentConstraints, AgentRunStatus, RootCause, ValidationSummary
from app.domain.agent.policy import PolicyGuard
from app.domain.agent.run_store import AgentRunStore
from app.domain.agent.runtime import RepairAgentRuntime
from app.domain.agent.tool_registry import ToolRegistry
from app.domain.agent.tools import ProposePatchTool


class SequenceGateway:
    def __init__(self) -> None:
        self.responses = [
            '{"action":"tool_call","tool_name":"propose_patch","arguments":{"suggestion":"补充 few-shot","knowledge_types":["few_shot"]},"reason_summary":"生成候选 patch"}',
            '{"action":"request_human_review","reason_summary":"候选 patch 已生成，需要人工审核"}',
        ]

    def generate_text(self, prompt_name, model_config, **kwargs):
        return self.responses.pop(0)


class FakeRepairService:
    def propose(self, suggestion, knowledge_types):
        return [
            {
                "doc_type": "few_shot",
                "section": "Reference Examples",
                "target_key": "k",
                "new_content": "Question: q\nCypher: MATCH (n) RETURN n",
                "risk": "low",
                "confidence": 0.95,
                "duplicate_checked": True,
                "conflict_checked": True,
            }
        ]


class AgentEndToEndTest(unittest.TestCase):
    def test_fake_llm_chooses_tool_then_requests_review(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            registry = ToolRegistry()
            registry.register(ProposePatchTool(FakeRepairService()))
            runtime = RepairAgentRuntime(
                AgentRunStore(Path(tmp_dir) / "runs"),
                LLMController(SequenceGateway()),
                registry,
                MemoryManager(Path(tmp_dir) / "memory"),
                PolicyGuard(),
            )
            run = runtime.create_run("qa_001", "repair", RootCause(type="missing_few_shot", summary="s", suggested_fix="f"), AgentConstraints(allowed_tools=["propose_patch"]))

            first = runtime.step(run.run_id)
            first.validation = ValidationSummary(before_after_improved=True)
            runtime.run_store.save(first)
            second = runtime.step(run.run_id)

            self.assertEqual(first.candidate_changes[0].target_key, "k")
            self.assertEqual(second.status, AgentRunStatus.NEEDS_REVIEW)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run end-to-end test**

Run:

```bash
cd knowledge-agent/backend
python3 -m unittest tests/test_agent_end_to_end.py -v
```

Expected: PASS.

---

## Final Verification

Run all knowledge-agent backend tests:

```bash
cd knowledge-agent/backend
python3 -m unittest discover tests -v
```

Expected: PASS.

Run QA redispatch contract tests:

```bash
cd qa-agent
python3 -m unittest tests/test_redispatch_service.py -v
```

Expected: PASS.

Search for unresolved plan placeholders:

```bash
rg -n "TBD|TODO|implement later|fill in|Similar to|placeholder" knowledge-agent/docs/superpowers/plans/2026-05-06-guarded-knowledge-repair-agent.md
```

Expected: no matches.

## Plan Self-Review

- Spec coverage: This plan covers LLM-driven tool selection, full V1 tool registration, markdown/RAG retrieval hooks, repair memory, structured observation mapping, candidate-only patches, guarded apply, redispatch, and final memory write.
- Review fixes: The P1 issues from the independent review are addressed by Tasks 5, 6, 7, and 8.
- Type consistency: The plan uses `AgentKnowledgeType`, typed `AgentDecision`, typed `AgentTraceEntry`, and separates `final` from runtime completion.
- Legacy compatibility: Task 4 explicitly preserves `RepairService.apply()` behavior and logging.

## Execution Options

After review, choose one:

1. **Subagent-Driven (recommended)** - dispatch a fresh worker per task and review after each task.
2. **Inline Execution** - execute tasks in this session using checkpoints.
