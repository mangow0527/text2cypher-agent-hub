from __future__ import annotations

import json
import re

from app.domain.models import CanonicalSchemaSpec, ModelConfig, QASample, ValidatedSample
from app.errors import AppError
from app.integrations.openai.model_gateway import ModelGateway

QUESTION_VARIANT_STYLES = [
    "natural_short",
    "spoken_query",
    "business_term",
    "ellipsis_query",
    "task_oriented",
]


def normalize_cypher(cypher: str) -> str:
    return " ".join(cypher.split()).strip().lower()


def normalize_question(question: str) -> str:
    return " ".join(question.split()).strip().lower()


CYTHER_LIKE_PATTERNS = [
    re.compile(r"```"),
    re.compile(r"\bmatch\b", re.IGNORECASE),
    re.compile(r"\breturn\b", re.IGNORECASE),
    re.compile(r"\bwhere\b", re.IGNORECASE),
    re.compile(r"\bwith\b", re.IGNORECASE),
    re.compile(r"\border\s+by\b", re.IGNORECASE),
    re.compile(r"\blimit\b", re.IGNORECASE),
    re.compile(r"\bdistinct\b", re.IGNORECASE),
    re.compile(r"\b(count|sum|avg|min|max)\s*\(", re.IGNORECASE),
    re.compile(r"\b[a-zA-Z_]\w*\.[a-zA-Z_]\w*\b"),
    re.compile(r"-\s*\[:?[A-Za-z_]\w*"),
    re.compile(r"->|<-"),
    re.compile(r"\(\s*[a-zA-Z]\s*:\s*[A-Za-z_]\w*\s*\)"),
]


def is_natural_language_question(text: str) -> bool:
    normalized = text.strip()
    if not normalized:
        return False
    for pattern in CYTHER_LIKE_PATTERNS:
        if pattern.search(normalized):
            return False
    return True


def build_schema_summary(schema: CanonicalSchemaSpec, sample: ValidatedSample) -> str:
    lines: list[str] = []
    candidate_nodes = sample.candidate.bound_schema_items.get("nodes", [])
    candidate_edges = sample.candidate.bound_schema_items.get("edges", [])
    for node in candidate_nodes:
        props = schema.node_properties.get(node, {})
        if isinstance(props, dict):
            lines.append(f"节点 {node}: 属性 {', '.join(props.keys())}")
    for edge in candidate_edges:
        constraints = schema.edge_constraints.get(edge, [])
        props = schema.edge_properties.get(edge, {})
        route = " | ".join([f"{src}->{dst}" for src, dst in constraints]) if constraints else ""
        prop_text = ", ".join(props.keys()) if isinstance(props, dict) else ""
        lines.append(f"关系 {edge}: 约束 {route}; 属性 {prop_text}")
    return "\n".join(lines) if lines else "无摘要"


def build_result_summary(sample: ValidatedSample) -> str:
    preview_rows = []
    for row in sample.result_signature.result_preview[:2]:
        compact_row = {}
        for key, value in row.items():
            text = str(value).replace("\n", " ").strip()
            if len(text) > 120:
                text = f"{text[:117]}..."
            compact_row[key] = text
        preview_rows.append(compact_row)
    payload = {
        "columns": sample.result_signature.columns,
        "row_count": sample.result_signature.row_count,
        "preview": preview_rows,
    }
    return json.dumps(payload, ensure_ascii=False)


class QuestionService:
    def __init__(self, model_gateway: ModelGateway | None = None) -> None:
        self.model_gateway = model_gateway or ModelGateway()

    def generate(
        self,
        sample: ValidatedSample,
        schema: CanonicalSchemaSpec,
        model_config: ModelConfig,
        max_variants: int,
    ) -> QASample:
        schema_summary = build_schema_summary(schema, sample)
        result_summary = build_result_summary(sample)
        bundle_text = self.model_gateway.generate_text(
            "question_bundle",
            model_config,
            schema_summary=schema_summary,
            cypher=sample.candidate.cypher,
            query_types=",".join(sample.candidate.query_types),
            return_semantics=",".join(sample.result_signature.columns or ["result"]),
            result_summary=result_summary,
            requested_styles=", ".join(QUESTION_VARIANT_STYLES[:max_variants]),
        )
        canonical, parsed_variants = self._parse_bundle(bundle_text)
        canonical = self._sanitize_question_text(canonical)
        if not is_natural_language_question(canonical):
            raise AppError("QUESTION_GENERATION_ERROR", "Canonical question contains Cypher-like or non-natural-language content.")
        variants = []
        variant_styles = []
        seen = set()
        for style, line in parsed_variants:
            text = self._sanitize_question_text(line)
            if not text or text in seen or not is_natural_language_question(text):
                continue
            seen.add(text)
            variants.append(text)
            variant_styles.append(style)

        return QASample(
            question_canonical_zh=canonical,
            question_variants_zh=variants[:max_variants],
            question_variant_styles=variant_styles[:max_variants],
            cypher=sample.candidate.cypher,
            cypher_normalized=normalize_cypher(sample.candidate.cypher),
            query_types=sample.candidate.query_types,
            difficulty=sample.classified_difficulty or sample.candidate.difficulty,
            answer=sample.result_signature.result_preview,
            validation=sample.validation,
            result_signature=sample.result_signature,
            split="silver",
            provenance={
                "skeleton_id": sample.candidate.skeleton_id,
                "structure_family": sample.candidate.structure_family,
                "generation_mode": sample.candidate.generation_mode,
                "schema_summary": schema_summary,
                "return_semantics": ",".join(sample.result_signature.columns or ["result"]),
                "result_summary": result_summary,
            },
        )

    def _parse_bundle(self, bundle_text: str) -> tuple[str, list[tuple[str, str]]]:
        try:
            payload = json.loads(bundle_text)
            canonical = str(payload.get("canonical_question", "")).strip()
            variants = payload.get("variants", [])
            parsed_variants: list[tuple[str, str]] = []
            if not isinstance(variants, list):
                variants = []
            for index, item in enumerate(variants):
                if isinstance(item, dict):
                    style = str(item.get("style", "")).strip() or QUESTION_VARIANT_STYLES[index]
                    question = str(item.get("question", "")).strip()
                else:
                    style = QUESTION_VARIANT_STYLES[index] if index < len(QUESTION_VARIANT_STYLES) else f"variant_{index + 1}"
                    question = str(item).strip()
                if question:
                    parsed_variants.append((style, question))
            if canonical:
                return canonical, parsed_variants
        except json.JSONDecodeError:
            pass

        lines = [line.strip().lstrip("-").strip() for line in bundle_text.splitlines() if line.strip()]
        canonical = lines[0] if lines else "请列出符合条件的数据。"
        variants = []
        for index, line in enumerate(lines[1:]):
            style = QUESTION_VARIANT_STYLES[index] if index < len(QUESTION_VARIANT_STYLES) else f"variant_{index + 1}"
            variants.append((style, line))
        return canonical, variants

    def _sanitize_question_text(self, text: str) -> str:
        cleaned = text.strip().strip('"').strip("'").strip()
        cleaned = re.sub(r"\s+", " ", cleaned)
        return cleaned
