import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from services.cypher_generator_agent.app.knowledge_context import (
    FileKnowledgeContextProvider,
    KnowledgeContextUnavailableError,
)


class FileKnowledgeContextProviderTest(unittest.TestCase):
    def test_fetch_context_reads_required_files_in_fixed_order_and_ignores_unknown_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            knowledge_dir = Path(temp_dir)
            self._write_required_files(
                knowledge_dir,
                system_prompt="System rules",
                schema={"nodes": [{"label": "Protocol"}]},
                cypher_syntax="MATCH syntax",
                business_knowledge="Domain glossary",
                few_shot="Q: list protocols\nA: MATCH (p:Protocol) RETURN p",
            )
            (knowledge_dir / "_history").mkdir()
            (knowledge_dir / "_history" / "old.md").write_text("stale history", encoding="utf-8")
            (knowledge_dir / "backup").mkdir()
            (knowledge_dir / "backup" / "schema.json").write_text('{"backup": true}', encoding="utf-8")
            (knowledge_dir / "notes.tmp").write_text("temporary draft", encoding="utf-8")
            (knowledge_dir / "unknown.md").write_text("unknown knowledge", encoding="utf-8")
            provider = FileKnowledgeContextProvider(knowledge_dir=knowledge_dir)

            ko_context = asyncio.run(provider.fetch_context(id="first-id", question="first question"))

        self.assertLess(ko_context.index("## System Prompt"), ko_context.index("## Schema"))
        self.assertLess(ko_context.index("## Schema"), ko_context.index("## Cypher Syntax"))
        self.assertLess(ko_context.index("## Cypher Syntax"), ko_context.index("## Business Knowledge"))
        self.assertLess(ko_context.index("## Business Knowledge"), ko_context.index("## Few-shot Examples"))
        self.assertIn("System rules", ko_context)
        self.assertIn("MATCH syntax", ko_context)
        self.assertIn("Domain glossary", ko_context)
        self.assertIn("Q: list protocols", ko_context)
        self.assertIn("```json", ko_context)
        self.assertIn('"label": "Protocol"', ko_context)
        self.assertNotIn("stale history", ko_context)
        self.assertNotIn("backup", ko_context)
        self.assertNotIn("temporary draft", ko_context)
        self.assertNotIn("unknown knowledge", ko_context)

    def test_fetch_context_reads_files_from_disk_each_time(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            knowledge_dir = Path(temp_dir)
            self._write_required_files(knowledge_dir, business_knowledge="first version")
            provider = FileKnowledgeContextProvider(knowledge_dir=knowledge_dir)

            first_context = asyncio.run(provider.fetch_context(id="qa-1", question="original"))
            (knowledge_dir / "business_knowledge.md").write_text("second version", encoding="utf-8")
            second_context = asyncio.run(provider.fetch_context(id="qa-1", question="original"))

        self.assertIn("first version", first_context)
        self.assertNotIn("second version", first_context)
        self.assertIn("second version", second_context)
        self.assertNotIn("first version", second_context)

    def test_fetch_context_raises_clear_error_when_directory_is_missing(self) -> None:
        provider = FileKnowledgeContextProvider(knowledge_dir=Path("/definitely/missing/knowledge-context"))

        with self.assertRaisesRegex(KnowledgeContextUnavailableError, "directory does not exist"):
            asyncio.run(provider.fetch_context(id="qa-1", question="question"))

    def test_fetch_context_raises_clear_error_when_required_file_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            knowledge_dir = Path(temp_dir)
            self._write_required_files(knowledge_dir)
            (knowledge_dir / "few_shot.md").unlink()
            provider = FileKnowledgeContextProvider(knowledge_dir=knowledge_dir)

            with self.assertRaisesRegex(KnowledgeContextUnavailableError, "missing required file: few_shot.md"):
                asyncio.run(provider.fetch_context(id="qa-1", question="question"))

    def test_fetch_context_raises_clear_error_when_required_file_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            knowledge_dir = Path(temp_dir)
            self._write_required_files(knowledge_dir)
            (knowledge_dir / "cypher_syntax.md").write_text("  \n", encoding="utf-8")
            provider = FileKnowledgeContextProvider(knowledge_dir=knowledge_dir)

            with self.assertRaisesRegex(KnowledgeContextUnavailableError, "required file is empty: cypher_syntax.md"):
                asyncio.run(provider.fetch_context(id="qa-1", question="question"))

    def test_fetch_context_raises_clear_error_when_schema_is_invalid_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            knowledge_dir = Path(temp_dir)
            self._write_required_files(knowledge_dir)
            (knowledge_dir / "schema.json").write_text("{not-json", encoding="utf-8")
            provider = FileKnowledgeContextProvider(knowledge_dir=knowledge_dir)

            with self.assertRaisesRegex(KnowledgeContextUnavailableError, "schema.json must contain valid JSON"):
                asyncio.run(provider.fetch_context(id="qa-1", question="question"))

    def _write_required_files(
        self,
        knowledge_dir: Path,
        *,
        system_prompt: str = "System prompt",
        schema: object | None = None,
        cypher_syntax: str = "Cypher syntax",
        business_knowledge: str = "Business knowledge",
        few_shot: str = "Few-shot examples",
    ) -> None:
        schema_value = {"nodes": [{"label": "Default"}]} if schema is None else schema
        (knowledge_dir / "system_prompt.md").write_text(system_prompt, encoding="utf-8")
        (knowledge_dir / "schema.json").write_text(json.dumps(schema_value), encoding="utf-8")
        (knowledge_dir / "cypher_syntax.md").write_text(cypher_syntax, encoding="utf-8")
        (knowledge_dir / "business_knowledge.md").write_text(business_knowledge, encoding="utf-8")
        (knowledge_dir / "few_shot.md").write_text(few_shot, encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
