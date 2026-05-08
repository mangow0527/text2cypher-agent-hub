from __future__ import annotations

import json
from pathlib import Path
from typing import NamedTuple


class KnowledgeContextUnavailableError(RuntimeError):
    pass


class _KnowledgeFile(NamedTuple):
    filename: str


_REQUIRED_FILES: tuple[_KnowledgeFile, ...] = (
    _KnowledgeFile("system_prompt.md"),
    _KnowledgeFile("schema.json"),
    _KnowledgeFile("cypher_syntax.md"),
    _KnowledgeFile("business_knowledge.md"),
    _KnowledgeFile("few_shot.md"),
)


class KnowledgeDocsValidator:
    def __init__(self, *, knowledge_dir: str | Path) -> None:
        self.knowledge_dir = Path(knowledge_dir)

    def is_available(self) -> bool:
        try:
            self.validate()
        except KnowledgeContextUnavailableError:
            return False
        return True

    def validate(self) -> None:
        if not self.knowledge_dir.is_dir():
            raise KnowledgeContextUnavailableError(f"knowledge context directory does not exist: {self.knowledge_dir}")

        for knowledge_file in _REQUIRED_FILES:
            content = self._read_required_file(knowledge_file.filename)
            if knowledge_file.filename == "schema.json":
                self._validate_schema_json(content)

    def _read_required_file(self, filename: str) -> str:
        path = self.knowledge_dir / filename
        if not path.is_file():
            raise KnowledgeContextUnavailableError(f"missing required file: {filename}")
        content = path.read_text(encoding="utf-8").strip()
        if not content:
            raise KnowledgeContextUnavailableError(f"required file is empty: {filename}")
        return content

    def _validate_schema_json(self, content: str) -> None:
        try:
            json.loads(content)
        except json.JSONDecodeError as exc:
            raise KnowledgeContextUnavailableError(f"schema.json must contain valid JSON: {exc.msg}") from exc
