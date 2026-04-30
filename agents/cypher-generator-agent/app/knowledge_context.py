from __future__ import annotations

import json
from pathlib import Path
from typing import NamedTuple


class KnowledgeContextUnavailableError(RuntimeError):
    pass


class _KnowledgeFile(NamedTuple):
    filename: str
    title: str
    fenced_language: str | None = None


_REQUIRED_FILES = (
    _KnowledgeFile("system_prompt.md", "System Prompt"),
    _KnowledgeFile("schema.json", "Schema", "json"),
    _KnowledgeFile("cypher_syntax.md", "Cypher Syntax"),
    _KnowledgeFile("business_knowledge.md", "Business Knowledge"),
    _KnowledgeFile("few_shot.md", "Few-shot Examples"),
)


class FileKnowledgeContextProvider:
    def __init__(self, *, knowledge_dir: str | Path) -> None:
        self.knowledge_dir = Path(knowledge_dir)

    def is_available(self) -> bool:
        try:
            self._build_context()
        except KnowledgeContextUnavailableError:
            return False
        return True

    async def fetch_context(self, id: str, question: str) -> str:
        del id, question
        return self._build_context()

    def _build_context(self) -> str:
        if not self.knowledge_dir.is_dir():
            raise KnowledgeContextUnavailableError(f"knowledge context directory does not exist: {self.knowledge_dir}")

        sections: list[str] = []
        for knowledge_file in _REQUIRED_FILES:
            content = self._read_required_file(knowledge_file.filename)
            if knowledge_file.filename == "schema.json":
                content = self._format_schema_json(content)
            sections.append(self._render_section(knowledge_file.title, content, knowledge_file.fenced_language))
        return "\n\n".join(sections)

    def _read_required_file(self, filename: str) -> str:
        path = self.knowledge_dir / filename
        if not path.is_file():
            raise KnowledgeContextUnavailableError(f"missing required file: {filename}")
        content = path.read_text(encoding="utf-8").strip()
        if not content:
            raise KnowledgeContextUnavailableError(f"required file is empty: {filename}")
        return content

    def _format_schema_json(self, content: str) -> str:
        try:
            parsed_schema = json.loads(content)
        except json.JSONDecodeError as exc:
            raise KnowledgeContextUnavailableError(f"schema.json must contain valid JSON: {exc.msg}") from exc
        return json.dumps(parsed_schema, ensure_ascii=False, indent=2)

    def _render_section(self, title: str, content: str, fenced_language: str | None) -> str:
        if fenced_language is None:
            return f"## {title}\n{content}"
        return f"## {title}\n```{fenced_language}\n{content}\n```"
