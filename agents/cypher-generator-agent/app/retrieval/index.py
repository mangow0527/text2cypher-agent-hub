from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from services.cypher_generator_agent.app.semantic_model import GraphSemanticRegistry
from services.cypher_generator_agent.app.semantic_model.model import (
    EdgeDefinition,
    MetricDefinition,
    PathPatternDefinition,
    PropertyDefinition,
    VertexDefinition,
)

from .models import SemanticType


@dataclass(frozen=True)
class IndexedText:
    field: str
    text: str


@dataclass(frozen=True)
class SemanticSearchDocument:
    semantic_type: SemanticType
    semantic_id: str
    semantic_name: str
    owner: str | None
    exact_names: tuple[IndexedText, ...]
    synonyms: tuple[IndexedText, ...]
    text_fields: tuple[IndexedText, ...]
    metadata: Mapping[str, Any]


def build_semantic_index(registry: GraphSemanticRegistry) -> list[SemanticSearchDocument]:
    documents: list[SemanticSearchDocument] = []

    for vertex in registry.model.vertices:
        documents.append(_vertex_document(vertex))
        for prop in vertex.properties:
            documents.append(_property_document(prop, owner=vertex.name))

    for edge in registry.model.edges:
        documents.append(_edge_document(edge))
        for prop in edge.properties:
            documents.append(_property_document(prop, owner=edge.name))

    documents.extend(_metric_document(metric) for metric in registry.model.metrics)
    documents.extend(_path_pattern_document(path_pattern) for path_pattern in registry.model.path_patterns)
    return documents


def _vertex_document(vertex: VertexDefinition) -> SemanticSearchDocument:
    return SemanticSearchDocument(
        semantic_type="vertex",
        semantic_id=vertex.name,
        semantic_name=vertex.name,
        owner=None,
        exact_names=_exact_names(vertex.name),
        synonyms=_ai_context_texts(vertex.ai_context, "synonyms"),
        text_fields=(
            *_optional_text("description", vertex.description),
            *_ai_context_texts(vertex.ai_context, "examples"),
        ),
        metadata={
            "id_property": vertex.id_property,
            "property_names": [prop.name for prop in vertex.properties],
        },
    )


def _edge_document(edge: EdgeDefinition) -> SemanticSearchDocument:
    return SemanticSearchDocument(
        semantic_type="edge",
        semantic_id=edge.name,
        semantic_name=edge.name,
        owner=None,
        exact_names=_exact_names(edge.name),
        synonyms=_ai_context_texts(edge.ai_context, "synonyms"),
        text_fields=(
            *_optional_text("description", edge.description),
            *_optional_text("direction_semantics", edge.direction_semantics),
            *_text_list("anti_patterns", edge.anti_patterns),
            *_ai_context_texts(edge.ai_context, "examples"),
        ),
        metadata={
            "from_vertex": edge.from_vertex,
            "to_vertex": edge.to_vertex,
            "cardinality": edge.cardinality,
            "direction_semantics": edge.direction_semantics,
            "anti_patterns": edge.anti_patterns,
            "property_names": [prop.name for prop in edge.properties],
        },
    )


def _property_document(prop: PropertyDefinition, owner: str) -> SemanticSearchDocument:
    semantic_id = f"{owner}.{prop.name}"
    return SemanticSearchDocument(
        semantic_type="property",
        semantic_id=semantic_id,
        semantic_name=prop.name,
        owner=owner,
        exact_names=(
            *_exact_names(prop.name),
            IndexedText("qualified_name", semantic_id),
        ),
        synonyms=_ai_context_texts(prop.ai_context, "synonyms"),
        text_fields=(
            *_optional_text("description", prop.description),
            *_text_list("valid_values", prop.valid_values),
            *_value_synonym_texts(prop.value_synonyms),
        ),
        metadata={
            "property_type": prop.type,
            "required": prop.required,
            "valid_values": prop.valid_values,
        },
    )


def _metric_document(metric: MetricDefinition) -> SemanticSearchDocument:
    return SemanticSearchDocument(
        semantic_type="metric",
        semantic_id=metric.name,
        semantic_name=metric.name,
        owner=None,
        exact_names=_exact_names(metric.name),
        synonyms=_ai_context_texts(metric.ai_context, "synonyms"),
        text_fields=(
            *_optional_text("description", metric.description),
            *_optional_text("pattern", metric.pattern),
            *_optional_text("expression", metric.expression),
            *_optional_text("full_cypher", metric.full_cypher),
            *_text_list("valid_dimensions", metric.valid_dimensions),
            *_ai_context_texts(metric.ai_context, "examples"),
        ),
        metadata={
            "pattern": metric.pattern,
            "expression": metric.expression,
            "valid_dimensions": metric.valid_dimensions,
        },
    )


def _path_pattern_document(path_pattern: PathPatternDefinition) -> SemanticSearchDocument:
    return SemanticSearchDocument(
        semantic_type="path_pattern",
        semantic_id=path_pattern.name,
        semantic_name=path_pattern.name,
        owner=None,
        exact_names=_exact_names(path_pattern.name),
        synonyms=_ai_context_texts(path_pattern.ai_context, "synonyms"),
        text_fields=(
            *_optional_text("description", path_pattern.description),
            *_optional_text("cypher", path_pattern.cypher),
            *_ai_context_texts(path_pattern.ai_context, "examples"),
            *(
                IndexedText(f"parameters.{parameter.name}.description", parameter.description)
                for parameter in path_pattern.parameters
                if parameter.description
            ),
        ),
        metadata={
            "parameters": [
                {"name": parameter.name, "type": parameter.type, "description": parameter.description}
                for parameter in path_pattern.parameters
            ],
        },
    )


def _exact_names(name: str) -> tuple[IndexedText, ...]:
    return (IndexedText("name", name),)


def _optional_text(field: str, value: str | None) -> tuple[IndexedText, ...]:
    if not value:
        return ()
    return (IndexedText(field, value),)


def _text_list(field: str, values: Iterable[str]) -> tuple[IndexedText, ...]:
    return tuple(IndexedText(field, value) for value in values if value)


def _ai_context_texts(ai_context: dict[str, Any], key: str) -> tuple[IndexedText, ...]:
    value = ai_context.get(key)
    if isinstance(value, str):
        return (IndexedText(f"ai_context.{key}", value),)
    if not isinstance(value, list):
        return ()
    return tuple(IndexedText(f"ai_context.{key}", item) for item in value if isinstance(item, str) and item)


def _value_synonym_texts(value_synonyms: dict[str, list[str]]) -> tuple[IndexedText, ...]:
    texts: list[IndexedText] = []
    for canonical_value, synonyms in value_synonyms.items():
        texts.append(IndexedText("value_synonyms", canonical_value))
        texts.extend(IndexedText("value_synonyms", synonym) for synonym in synonyms if synonym)
    return tuple(texts)
