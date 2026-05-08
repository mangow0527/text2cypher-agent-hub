import json
import tempfile
import unittest
from pathlib import Path

from services.cypher_generator_agent.app.knowledge_context import (
    KnowledgeDocsValidator,
    KnowledgeContextUnavailableError,
)


class KnowledgeDocsValidatorTest(unittest.TestCase):
    def test_validate_accepts_required_files_and_ignores_unknown_files(self) -> None:
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
            validator = KnowledgeDocsValidator(knowledge_dir=knowledge_dir)

            validator.validate()
            self.assertTrue(validator.is_available())

    def test_validate_reads_files_from_disk_each_time(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            knowledge_dir = Path(temp_dir)
            self._write_required_files(knowledge_dir, business_knowledge="first version")
            validator = KnowledgeDocsValidator(knowledge_dir=knowledge_dir)

            validator.validate()
            (knowledge_dir / "business_knowledge.md").write_text("  \n", encoding="utf-8")

            with self.assertRaisesRegex(KnowledgeContextUnavailableError, "required file is empty: business_knowledge.md"):
                validator.validate()

    def test_validate_raises_clear_error_when_directory_is_missing(self) -> None:
        validator = KnowledgeDocsValidator(knowledge_dir=Path("/definitely/missing/knowledge-context"))

        with self.assertRaisesRegex(KnowledgeContextUnavailableError, "directory does not exist"):
            validator.validate()

    def test_validate_raises_clear_error_when_required_file_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            knowledge_dir = Path(temp_dir)
            self._write_required_files(knowledge_dir)
            (knowledge_dir / "few_shot.md").unlink()
            validator = KnowledgeDocsValidator(knowledge_dir=knowledge_dir)

            with self.assertRaisesRegex(KnowledgeContextUnavailableError, "missing required file: few_shot.md"):
                validator.validate()

    def test_validate_raises_clear_error_when_required_file_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            knowledge_dir = Path(temp_dir)
            self._write_required_files(knowledge_dir)
            (knowledge_dir / "cypher_syntax.md").write_text("  \n", encoding="utf-8")
            validator = KnowledgeDocsValidator(knowledge_dir=knowledge_dir)

            with self.assertRaisesRegex(KnowledgeContextUnavailableError, "required file is empty: cypher_syntax.md"):
                validator.validate()

    def test_validate_raises_clear_error_when_schema_is_invalid_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            knowledge_dir = Path(temp_dir)
            self._write_required_files(knowledge_dir)
            (knowledge_dir / "schema.json").write_text("{not-json", encoding="utf-8")
            validator = KnowledgeDocsValidator(knowledge_dir=knowledge_dir)

            with self.assertRaisesRegex(KnowledgeContextUnavailableError, "schema.json must contain valid JSON"):
                validator.validate()

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
