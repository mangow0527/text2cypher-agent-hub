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
            run = store.create(
                "qa_001",
                "repair",
                RootCause(type="missing_path_rule", summary="s", suggested_fix="f"),
                AgentConstraints(max_steps=3),
            )
            store.append_trace(
                run.run_id,
                AgentAction(
                    action="tool_call",
                    tool_name="inspect_qa_case",
                    arguments={"qa_id": "qa_001"},
                    reason_summary="读取失败样本",
                ),
                {"question": "q"},
            )
            updated = store.update_status(run.run_id, AgentRunStatus.RUNNING)

            loaded = store.get(run.run_id)
            self.assertEqual(updated.status, AgentRunStatus.RUNNING)
            self.assertEqual(loaded.trace[0].observation["question"], "q")

    def test_run_store_lists_runs_by_status(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            store = AgentRunStore(Path(tmp_dir))
            first = store.create(
                "qa_001",
                "repair",
                RootCause(type="missing_path_rule", summary="s1", suggested_fix="f1"),
                AgentConstraints(max_steps=3),
            )
            second = store.create(
                "qa_002",
                "repair",
                RootCause(type="missing_few_shot", summary="s2", suggested_fix="f2"),
                AgentConstraints(max_steps=3),
            )
            store.update_status(first.run_id, AgentRunStatus.NEEDS_REVIEW)
            store.update_status(second.run_id, AgentRunStatus.COMPLETED)

            pending = store.list(status=AgentRunStatus.NEEDS_REVIEW)

            self.assertEqual([run.run_id for run in pending], [first.run_id])

    def test_memory_searches_historical_repairs(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            memory = MemoryManager(Path(tmp_dir))
            memory.write_repair_memory(
                {"qa_id": "qa_001", "root_cause_type": "missing_path_rule", "summary": "协议路径修复"}
            )

            hits = memory.search_repair_memory("missing_path_rule 协议")

            self.assertEqual(hits[0]["qa_id"], "qa_001")


if __name__ == "__main__":
    unittest.main()
