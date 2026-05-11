from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from . import resource_paths
from .semantic_layer import SemanticLayer, SemanticLayerConfigError, load_semantic_layer


@dataclass(frozen=True)
class SemanticAlignmentDiagnostic:
    code: str
    message: str
    source: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class SemanticAlignmentReport:
    accepted: bool
    diagnostics: list[SemanticAlignmentDiagnostic]
    checked_sources: list[str]

    def to_dict(self) -> dict[str, object]:
        return {
            "accepted": self.accepted,
            "diagnostics": [diagnostic.to_dict() for diagnostic in self.diagnostics],
            "checked_sources": self.checked_sources,
        }


@dataclass(frozen=True)
class _PhysicalSchema:
    vertex_properties: dict[str, set[str]]
    edge_constraints: dict[str, set[tuple[str, str]]]


_KNOWLEDGE_REQUIRED_FILES = (
    "system_prompt.md",
    "schema.json",
    "cypher_syntax.md",
    "business_knowledge.md",
    "few_shot.md",
)


def validate_default_semantic_alignment(*, knowledge_dir: str | Path | None = None) -> SemanticAlignmentReport:
    return validate_semantic_alignment(
        semantic_layer_path=_default_semantic_layer_path(),
        tugraph_schema_path=_default_tugraph_schema_path(),
        knowledge_dir=knowledge_dir,
    )


def validate_semantic_alignment(
    *,
    semantic_layer_path: str | Path,
    tugraph_schema_path: str | Path,
    knowledge_dir: str | Path | None = None,
) -> SemanticAlignmentReport:
    semantic_layer_path = Path(semantic_layer_path)
    tugraph_schema_path = Path(tugraph_schema_path)
    diagnostics: list[SemanticAlignmentDiagnostic] = []
    checked_sources = [semantic_layer_path.name, tugraph_schema_path.name]

    try:
        semantic_layer = load_semantic_layer(semantic_layer_path, schema_path=tugraph_schema_path)
    except SemanticLayerConfigError as exc:
        diagnostics.append(
            SemanticAlignmentDiagnostic(
                code="semantic_layer_not_aligned_to_tugraph_schema",
                message=str(exc),
                source=semantic_layer_path.name,
            )
        )
        return SemanticAlignmentReport(accepted=False, diagnostics=diagnostics, checked_sources=checked_sources)

    tugraph_schema = _load_physical_schema(tugraph_schema_path)
    if knowledge_dir is not None:
        knowledge_path = Path(knowledge_dir)
        _append_knowledge_alignment(
            semantic_layer=semantic_layer,
            tugraph_schema=tugraph_schema,
            knowledge_dir=knowledge_path,
            diagnostics=diagnostics,
            checked_sources=checked_sources,
        )

    return SemanticAlignmentReport(
        accepted=not diagnostics,
        diagnostics=diagnostics,
        checked_sources=checked_sources,
    )


def _append_knowledge_alignment(
    *,
    semantic_layer: SemanticLayer,
    tugraph_schema: _PhysicalSchema,
    knowledge_dir: Path,
    diagnostics: list[SemanticAlignmentDiagnostic],
    checked_sources: list[str],
) -> None:
    if not knowledge_dir.is_dir():
        diagnostics.append(
            SemanticAlignmentDiagnostic(
                code="knowledge_context_unavailable",
                message=f"knowledge context directory does not exist: {knowledge_dir}",
                source="knowledge",
            )
        )
        return

    for filename in _KNOWLEDGE_REQUIRED_FILES:
        path = knowledge_dir / filename
        checked_sources.append(f"knowledge/{filename}")
        if not path.is_file():
            diagnostics.append(
                SemanticAlignmentDiagnostic(
                    code="knowledge_context_unavailable",
                    message=f"missing required knowledge file: {filename}",
                    source=f"knowledge/{filename}",
                )
            )
            return
        if not path.read_text(encoding="utf-8").strip():
            diagnostics.append(
                SemanticAlignmentDiagnostic(
                    code="knowledge_context_unavailable",
                    message=f"required knowledge file is empty: {filename}",
                    source=f"knowledge/{filename}",
                )
            )
            return

    try:
        knowledge_schema = _load_physical_schema(knowledge_dir / "schema.json")
    except (json.JSONDecodeError, SemanticLayerConfigError) as exc:
        diagnostics.append(
            SemanticAlignmentDiagnostic(
                code="knowledge_schema_invalid",
                message=str(exc),
                source="knowledge/schema.json",
            )
        )
        return

    _append_schema_drift_diagnostics(
        tugraph_schema=tugraph_schema,
        knowledge_schema=knowledge_schema,
        diagnostics=diagnostics,
    )
    _append_semantic_layer_against_knowledge_schema_diagnostics(
        semantic_layer=semantic_layer,
        knowledge_schema=knowledge_schema,
        diagnostics=diagnostics,
    )
    for filename in ("business_knowledge.md", "few_shot.md"):
        _append_knowledge_reference_diagnostics(
            semantic_layer=semantic_layer,
            tugraph_schema=tugraph_schema,
            source=f"knowledge/{filename}",
            text=(knowledge_dir / filename).read_text(encoding="utf-8"),
            diagnostics=diagnostics,
        )


def _append_schema_drift_diagnostics(
    *,
    tugraph_schema: _PhysicalSchema,
    knowledge_schema: _PhysicalSchema,
    diagnostics: list[SemanticAlignmentDiagnostic],
) -> None:
    for label, knowledge_properties in sorted(knowledge_schema.vertex_properties.items()):
        tugraph_properties = tugraph_schema.vertex_properties.get(label)
        if tugraph_properties is None:
            diagnostics.append(
                SemanticAlignmentDiagnostic(
                    code="knowledge_schema_mismatch",
                    message=f"knowledge schema references unknown TuGraph label {label!r}",
                    source="knowledge/schema.json",
                )
            )
            continue
        for property_name in sorted(knowledge_properties - tugraph_properties):
            diagnostics.append(
                SemanticAlignmentDiagnostic(
                    code="knowledge_schema_mismatch",
                    message=f"knowledge schema references unknown TuGraph property {label}.{property_name}",
                    source="knowledge/schema.json",
                )
            )

    for edge, constraints in sorted(knowledge_schema.edge_constraints.items()):
        tugraph_constraints = tugraph_schema.edge_constraints.get(edge)
        if tugraph_constraints is None:
            diagnostics.append(
                SemanticAlignmentDiagnostic(
                    code="knowledge_schema_mismatch",
                    message=f"knowledge schema references unknown TuGraph edge {edge!r}",
                    source="knowledge/schema.json",
                )
            )
            continue
        for from_label, to_label in sorted(constraints - tugraph_constraints):
            diagnostics.append(
                SemanticAlignmentDiagnostic(
                    code="knowledge_schema_mismatch",
                    message=f"knowledge schema edge {edge!r} does not allow {from_label!r} -> {to_label!r}",
                    source="knowledge/schema.json",
                )
            )


def _append_semantic_layer_against_knowledge_schema_diagnostics(
    *,
    semantic_layer: SemanticLayer,
    knowledge_schema: _PhysicalSchema,
    diagnostics: list[SemanticAlignmentDiagnostic],
) -> None:
    for entity in semantic_layer.entities.values():
        if entity.label not in knowledge_schema.vertex_properties:
            diagnostics.append(
                SemanticAlignmentDiagnostic(
                    code="semantic_layer_not_aligned_to_knowledge_schema",
                    message=f"semantic entity {entity.name!r} references label {entity.label!r} missing from knowledge schema",
                    source="semantic_layer.yaml",
                )
            )

    for prop in semantic_layer.properties.values():
        label = semantic_layer.entities[prop.owner].label
        if prop.property not in knowledge_schema.vertex_properties.get(label, set()):
            diagnostics.append(
                SemanticAlignmentDiagnostic(
                    code="semantic_layer_not_aligned_to_knowledge_schema",
                    message=f"semantic property {prop.name!r} references {label}.{prop.property} missing from knowledge schema",
                    source="semantic_layer.yaml",
                )
            )

    for metric in semantic_layer.metrics.values():
        if metric.property is None:
            continue
        label = semantic_layer.entities[metric.owner].label
        if metric.property not in knowledge_schema.vertex_properties.get(label, set()):
            diagnostics.append(
                SemanticAlignmentDiagnostic(
                    code="semantic_layer_not_aligned_to_knowledge_schema",
                    message=f"semantic metric {metric.name!r} references {label}.{metric.property} missing from knowledge schema",
                    source="semantic_layer.yaml",
                )
            )

    for relationship in semantic_layer.relationships.values():
        from_label = semantic_layer.entities[relationship.from_entity].label
        to_label = semantic_layer.entities[relationship.to_entity].label
        expected = (from_label, to_label)
        if expected not in knowledge_schema.edge_constraints.get(relationship.edge, set()):
            diagnostics.append(
                SemanticAlignmentDiagnostic(
                    code="semantic_layer_not_aligned_to_knowledge_schema",
                    message=(
                        f"semantic relationship {relationship.name!r} references "
                        f"{from_label}-[:{relationship.edge}]->{to_label} missing from knowledge schema"
                    ),
                    source="semantic_layer.yaml",
                )
            )


def _append_knowledge_reference_diagnostics(
    *,
    semantic_layer: SemanticLayer,
    tugraph_schema: _PhysicalSchema,
    source: str,
    text: str,
    diagnostics: list[SemanticAlignmentDiagnostic],
) -> None:
    reference_text = _positive_reference_text(text)
    for label, property_name in sorted(_property_refs(reference_text)):
        if property_name not in tugraph_schema.vertex_properties.get(label, set()):
            diagnostics.append(
                SemanticAlignmentDiagnostic(
                    code="knowledge_reference_not_in_tugraph_schema",
                    message=f"knowledge text references {label}.{property_name}, but TuGraph schema does not expose it",
                    source=source,
                )
            )
            continue
        if not _semantic_layer_exposes_property(semantic_layer, label, property_name):
            diagnostics.append(
                SemanticAlignmentDiagnostic(
                    code="knowledge_reference_not_in_semantic_layer",
                    message=f"knowledge text references {label}.{property_name}, but semantic layer does not expose it",
                    source=source,
                )
            )

    for from_label, edge, to_label in sorted(_relationship_pattern_refs(reference_text)):
        if (from_label, to_label) not in tugraph_schema.edge_constraints.get(edge, set()):
            diagnostics.append(
                SemanticAlignmentDiagnostic(
                    code="knowledge_relationship_not_in_tugraph_schema",
                    message=f"knowledge text references {from_label}-[:{edge}]->{to_label}, but TuGraph schema does not expose it",
                    source=source,
                )
            )
            continue
        if not _semantic_layer_exposes_relationship(semantic_layer, from_label, edge, to_label):
            diagnostics.append(
                SemanticAlignmentDiagnostic(
                    code="knowledge_relationship_not_in_semantic_layer",
                    message=f"knowledge text references {from_label}-[:{edge}]->{to_label}, but semantic layer does not expose it",
                    source=source,
                )
            )


def _semantic_layer_exposes_property(semantic_layer: SemanticLayer, label: str, property_name: str) -> bool:
    owners = {entity.name for entity in semantic_layer.entities.values() if entity.label == label}
    for prop in semantic_layer.properties.values():
        if prop.owner in owners and prop.property == property_name:
            return True
    for metric in semantic_layer.metrics.values():
        if metric.owner in owners and metric.property == property_name:
            return True
    for mapping in semantic_layer.value_mappings.values():
        if mapping.owner in owners and mapping.property == property_name:
            return True
    return False


def _semantic_layer_exposes_relationship(
    semantic_layer: SemanticLayer,
    from_label: str,
    edge: str,
    to_label: str,
) -> bool:
    for relationship in semantic_layer.relationships.values():
        from_entity = semantic_layer.entities[relationship.from_entity]
        to_entity = semantic_layer.entities[relationship.to_entity]
        if from_entity.label == from_label and relationship.edge == edge and to_entity.label == to_label:
            return True
    return False


def _label_property_refs(text: str) -> set[tuple[str, str]]:
    return {
        (match.group(1), match.group(2))
        for match in re.finditer(r"\b([A-Z][A-Za-z0-9_]*)\.([a-zA-Z_][A-Za-z0-9_]*)\b", text)
    }


def _property_refs(text: str) -> set[tuple[str, str]]:
    return _label_property_refs(text) | _alias_property_refs(text)


def _alias_property_refs(text: str) -> set[tuple[str, str]]:
    refs: set[tuple[str, str]] = set()
    for block in _reference_blocks(text):
        alias_to_label = {
            match.group(1): match.group(2)
            for match in re.finditer(r"\(([A-Za-z_][A-Za-z0-9_]*)\s*:\s*([A-Z][A-Za-z0-9_]*)\)", block)
        }
        for match in re.finditer(r"\b([A-Za-z_][A-Za-z0-9_]*)\.([a-zA-Z_][A-Za-z0-9_]*)\b", block):
            label = alias_to_label.get(match.group(1))
            if label is not None:
                refs.add((label, match.group(2)))
    return refs


def _positive_reference_text(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not _is_negative_reference_line(line))


def _is_negative_reference_line(line: str) -> bool:
    normalized = line.casefold()
    return any(
        marker in normalized
        for marker in (
            "anti-pattern",
            "antipattern",
            "bad example",
            "wrong example",
            "invalid example",
            "不要",
            "不得",
            "禁止",
            "错误",
            "反例",
        )
    )


def _reference_blocks(text: str) -> list[str]:
    blocks: list[str] = []
    for paragraph in re.split(r"\n\s*\n", text):
        blocks.extend(
            block
            for block in re.split(r"(?m)(?=^\s*(?:Question|Q|问题)\s*:)", paragraph)
            if block.strip()
        )
    return blocks or [text]


def _relationship_pattern_refs(text: str) -> set[tuple[str, str, str]]:
    node = r"\((?:(?:[A-Za-z_][A-Za-z0-9_]*)?:)?([A-Z][A-Za-z0-9_]*)\)"
    pattern = rf"{node}\s*-\s*\[:([A-Z][A-Z0-9_]*)\]\s*->\s*{node}"
    return {
        (match.group(1), match.group(2), match.group(3))
        for match in re.finditer(pattern, text)
    }


def _load_physical_schema(path: Path) -> _PhysicalSchema:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return _schema_from_reference_list(payload)
    if isinstance(payload, dict):
        return _schema_from_mapping(payload)
    raise SemanticLayerConfigError(f"{path} must contain a JSON list or mapping")


def _schema_from_reference_list(payload: list[Any]) -> _PhysicalSchema:
    vertex_properties: dict[str, set[str]] = {}
    edge_constraints: dict[str, set[tuple[str, str]]] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        label = item.get("label")
        schema_type = item.get("type")
        if not isinstance(label, str):
            continue
        if schema_type == "VERTEX":
            vertex_properties[label] = _property_names(item.get("properties", []))
        elif schema_type == "EDGE":
            edge_constraints[label] = _constraint_pairs(item.get("constraints", []))
    return _PhysicalSchema(vertex_properties=vertex_properties, edge_constraints=edge_constraints)


def _schema_from_mapping(payload: dict[str, Any]) -> _PhysicalSchema:
    vertex_properties: dict[str, set[str]] = {}
    edge_constraints: dict[str, set[tuple[str, str]]] = {}
    for item in _first_list(payload, "nodes", "vertices", "vertex_labels"):
        if not isinstance(item, dict):
            continue
        label = _first_str(item, "label", "name")
        if label:
            vertex_properties[label] = _property_names(item.get("properties", item.get("columns", [])))

    for item in _first_list(payload, "edges", "relationships", "edge_labels"):
        if not isinstance(item, dict):
            continue
        label = _first_str(item, "label", "name", "type")
        if not label:
            continue
        constraints = _constraint_pairs(item.get("constraints", []))
        from_label = _first_str(item, "from", "source", "start")
        to_label = _first_str(item, "to", "target", "end")
        if from_label and to_label:
            constraints.add((from_label, to_label))
        edge_constraints[label] = constraints
    return _PhysicalSchema(vertex_properties=vertex_properties, edge_constraints=edge_constraints)


def _property_names(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    names: set[str] = set()
    for item in value:
        if isinstance(item, str):
            names.add(item)
        elif isinstance(item, dict) and isinstance(item.get("name"), str):
            names.add(item["name"])
    return names


def _constraint_pairs(value: Any) -> set[tuple[str, str]]:
    if not isinstance(value, list):
        return set()
    pairs: set[tuple[str, str]] = set()
    for item in value:
        if isinstance(item, list) and len(item) == 2 and isinstance(item[0], str) and isinstance(item[1], str):
            pairs.add((item[0], item[1]))
        elif isinstance(item, dict):
            from_label = _first_str(item, "from", "source", "start")
            to_label = _first_str(item, "to", "target", "end")
            if from_label and to_label:
                pairs.add((from_label, to_label))
    return pairs


def _first_list(payload: dict[str, Any], *keys: str) -> list[Any]:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            return value
    return []


def _first_str(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _default_semantic_layer_path() -> Path:
    return resource_paths.semantic_layer_path()


def _default_tugraph_schema_path() -> Path:
    return Path(__file__).resolve().parents[3] / "services" / "testing_agent" / "docs" / "reference" / "schema.json"
