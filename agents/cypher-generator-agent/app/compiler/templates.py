from __future__ import annotations

from services.cypher_generator_agent.app.semantic_model import GraphSemanticRegistry


def get_path_pattern_template(
    registry: GraphSemanticRegistry,
    path_pattern_name: str,
) -> str:
    return registry.get_path_pattern(path_pattern_name).cypher.strip()
