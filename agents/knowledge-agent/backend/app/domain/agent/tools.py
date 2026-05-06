from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Optional

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
        diagnosis = self.evaluator.classify_gap(arguments.markdown_hits, arguments.rag_hits, arguments.validation_errors)
        return {"gap_diagnosis": diagnosis.model_dump()}


class ProposePatchInput(BaseModel):
    suggestion: str
    knowledge_types: Optional[list[str]] = None


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
                break
        return {
            "duplicate_found": duplicate,
            "candidate_change": {**arguments.candidate_change, "duplicate_checked": True},
        }


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
        return {
            "conflict_found": conflict,
            "candidate_change": {**arguments.candidate_change, "conflict_checked": True},
            "checked_text": text,
        }


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
                path.write_text(
                    path.read_text(encoding="utf-8").rstrip()
                    + f"\n\n[id: {change['target_key']}]\n{change['new_content']}\n",
                    encoding="utf-8",
                )
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
        validation = self.evaluator.evaluate_prompt_delta(arguments.before_prompt, arguments.after_prompt, arguments.expected_terms)
        return {"validation": validation.model_dump()}


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
