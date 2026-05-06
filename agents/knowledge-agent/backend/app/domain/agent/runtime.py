from __future__ import annotations

from app.domain.agent.models import (
    AgentConstraints,
    AgentDecision,
    AgentRun,
    AgentRunStatus,
    AgentTraceEntry,
    CandidateChange,
    GapDiagnosis,
    RootCause,
    ValidationSummary,
)
from app.domain.knowledge.redispatch_result import skipped_redispatch_result
from app.errors import AppError


class RepairAgentRuntime:
    def __init__(
        self,
        run_store,
        controller,
        tool_registry,
        memory_manager,
        policy_guard,
        repair_service=None,
    ) -> None:
        self.run_store = run_store
        self.controller = controller
        self.tool_registry = tool_registry
        self.memory_manager = memory_manager
        self.policy_guard = policy_guard
        self.repair_service = repair_service

    def create_run(self, qa_id: str, goal: str, root_cause: RootCause, constraints: AgentConstraints) -> AgentRun:
        return self.run_store.create(qa_id=qa_id, goal=goal, root_cause=root_cause, constraints=constraints)

    def list_runs(self, status: AgentRunStatus | str | None = None) -> list[AgentRun]:
        return self.run_store.list(status=status)

    def get_run(self, run_id: str) -> AgentRun:
        return self.run_store.get(run_id)

    def step(self, run_id: str) -> AgentRun:
        run = self.run_store.get(run_id)
        if len(run.trace) >= run.constraints.max_steps:
            run.status = AgentRunStatus.FAILED
            run.errors.append("max_steps exceeded")
            return self.run_store.save(run)

        memory = self.memory_manager.search_repair_memory(
            f"{run.root_cause.type} {run.root_cause.summary} {run.root_cause.suggested_fix}"
        )
        action = self.controller.decide_next_action(
            context=run.model_dump(mode="json"),
            memory=memory,
            tools=self.tool_registry.allowed_tool_specs(run),
        )
        if action.action == "request_human_review":
            run.status = AgentRunStatus.NEEDS_REVIEW
            run.decision = AgentDecision(action="human_review", reason=action.reason_summary)
            run.trace.append(AgentTraceEntry(step=len(run.trace) + 1, action=action, observation={}))
            return self.run_store.save(run)
        if action.action == "final":
            run.decision = AgentDecision(
                action="human_review" if action.status == "ready_for_review" else "reject",
                reason=action.reason_summary,
            )
            run.status = AgentRunStatus.NEEDS_REVIEW if action.status == "ready_for_review" else AgentRunStatus.REJECTED
            run.trace.append(AgentTraceEntry(step=len(run.trace) + 1, action=action, observation={"summary": action.summary}))
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

    def approve(self, run_id: str) -> AgentRun:
        run = self.run_store.get(run_id)
        self.policy_guard.assert_can_apply_after_human_approval(run, run.candidate_changes, run.validation)
        return self.apply_and_complete(run)

    def apply_and_complete(self, run: AgentRun) -> AgentRun:
        patches = [change.model_dump(mode="json") for change in run.candidate_changes]
        changes = self.repair_service.apply_candidates(patches, run.root_cause.suggested_fix)
        run.status = AgentRunStatus.APPLIED
        run.evidence.append({"type": "applied_changes", "changes": changes})
        self.run_store.save(run)

        redispatch = skipped_redispatch_result(run.qa_id)
        run.validation.redispatch_status = redispatch.get("status", "unknown")
        self.run_store.save(run)

        memory = self.memory_manager.write_repair_memory(
            {
                "qa_id": run.qa_id,
                "root_cause_type": run.root_cause.type,
                "summary": run.root_cause.summary,
                "candidate_count": len(run.candidate_changes),
                "validation": run.validation.model_dump(mode="json"),
                "redispatch": redispatch,
            }
        )
        run.evidence.append({"type": "repair_memory", "memory": memory})
        run.status = AgentRunStatus.COMPLETED
        run.decision = AgentDecision(action="complete", reason="Repair applied and stored in memory.")
        return self.run_store.save(run)

    def reject(self, run_id: str, reason: str) -> AgentRun:
        run = self.run_store.get(run_id)
        run.status = AgentRunStatus.REJECTED
        run.decision = AgentDecision(action="reject", reason=reason)
        self.memory_manager.write_repair_memory(
            {
                "qa_id": run.qa_id,
                "root_cause_type": run.root_cause.type,
                "summary": run.root_cause.summary,
                "rejected": True,
                "reason": reason,
            }
        )
        return self.run_store.save(run)
