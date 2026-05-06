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

    def list(self, status: AgentRunStatus | str | None = None) -> list[AgentRun]:
        runs = []
        expected_status = status.value if isinstance(status, AgentRunStatus) else status
        for path in sorted(self.root.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
            run = AgentRun.model_validate_json(path.read_text(encoding="utf-8"))
            if expected_status and run.status.value != expected_status:
                continue
            runs.append(run)
        return runs

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
