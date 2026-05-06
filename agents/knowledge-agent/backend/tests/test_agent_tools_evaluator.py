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
            memory.write_repair_memory(
                {"qa_id": "qa_old", "root_cause_type": "missing_path_rule", "summary": "协议路径"}
            )

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

            gap = ClassifyGapTool(evaluator).execute(
                ClassifyGapInput(markdown_hits=[{"content": "Protocol.version"}], rag_hits=[], validation_errors=[])
            )
            duplicate = CheckDuplicateTool(store).execute(CheckDuplicateInput(candidate_change=candidate))
            conflict = CheckConflictTool().execute(CheckConflictInput(candidate_change=candidate, existing_hits=[]))
            overlay = BuildPromptOverlayTool(store).execute(
                BuildPromptOverlayInput(question="查询协议版本为v2.0的隧道所属网元", candidate_changes=[candidate])
            )
            written = WriteRepairMemoryTool(memory).execute(
                WriteRepairMemoryInput(entry={"qa_id": "qa_001", "root_cause_type": "missing_path_rule"})
            )

            self.assertEqual(gap["gap_diagnosis"]["gap_type"], "retrieval_miss")
            self.assertFalse(duplicate["duplicate_found"])
            self.assertFalse(conflict["conflict_found"])
            self.assertIn("NetworkElement -> Tunnel -> Protocol", overlay["prompt"])
            self.assertIn("memory_id", written["memory"])


if __name__ == "__main__":
    unittest.main()
