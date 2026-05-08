from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .schema_linking import LinkedSemantics


@dataclass(frozen=True)
class SemanticDiagnostic:
    code: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class SemanticValidationResult:
    accepted: bool
    diagnostics: list[SemanticDiagnostic] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "accepted": self.accepted,
            "diagnostics": [diagnostic.to_dict() for diagnostic in self.diagnostics],
        }


class SemanticValidator:
    def __init__(self, semantic_layer: Any) -> None:
        self.semantic_layer = semantic_layer

    def validate(self, linked: LinkedSemantics) -> SemanticValidationResult:
        diagnostics: list[SemanticDiagnostic] = []
        entity_names = {entity.semantic_name for entity in linked.entities}
        if not entity_names:
            diagnostics.append(
                SemanticDiagnostic(
                    code="missing_entity",
                    message="No linked entity is available for semantic query generation.",
                )
            )

        for relationship in linked.relationships:
            if relationship.from_entity not in entity_names or relationship.to_entity not in entity_names:
                diagnostics.append(
                    SemanticDiagnostic(
                        code="relationship_entity_missing",
                        message=(
                            f"Relationship {relationship.semantic_name} requires "
                            f"{relationship.from_entity}->{relationship.to_entity}, but linked entities are "
                            f"{sorted(entity_names)}."
                        ),
                    )
                )
        if len(entity_names) > 1 and not linked.relationships:
            diagnostics.append(
                SemanticDiagnostic(
                    code="relationship_missing",
                    message=f"Multiple entities are linked but no valid relationship connects them: {sorted(entity_names)}.",
                )
            )

        known_properties = self._known_properties()
        for prop in [*linked.return_fields, *linked.group_by]:
            if (prop.owner, prop.property) not in known_properties:
                diagnostics.append(
                    SemanticDiagnostic(
                        code="property_not_found",
                        message=f"Property {prop.owner}.{prop.property} is not defined in semantic layer.",
                    )
                )
        for filter_value in linked.filters:
            if (filter_value.owner, filter_value.property) not in known_properties:
                diagnostics.append(
                    SemanticDiagnostic(
                        code="filter_property_not_found",
                        message=f"Filter property {filter_value.owner}.{filter_value.property} is not defined.",
                    )
                )

        return SemanticValidationResult(accepted=not diagnostics, diagnostics=diagnostics)

    def _known_properties(self) -> set[tuple[str, str]]:
        value = getattr(self.semantic_layer, "properties", [])
        if isinstance(value, dict):
            properties = value.values()
        else:
            properties = value or []
        known: set[tuple[str, str]] = set()
        for prop in properties:
            owner = _value(prop, "owner", "entity", default="")
            name = _value(prop, "property", "physical", default="")
            if owner and name:
                known.add((owner, name))
        return known


def _value(obj: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if isinstance(obj, dict) and name in obj:
            return obj[name]
        if hasattr(obj, name):
            return getattr(obj, name)
    return default
