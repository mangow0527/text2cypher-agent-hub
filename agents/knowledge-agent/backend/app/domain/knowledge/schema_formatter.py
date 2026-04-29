from __future__ import annotations


def format_schema(schema: dict | list[dict]) -> str:
    if isinstance(schema, list):
        return _format_list_schema(schema)

    labels = ["Labels:"]
    for item in schema.get("vertex_labels", []):
        props = ", ".join(_format_compact_property(prop) for prop in item.get("properties", []))
        labels.append(f"- {item['name']}({props})")

    edges = ["", "Relationships:"]
    for item in schema.get("edge_labels", []):
        edges.append(f"- (:{item['from']})-[:{item['name']}]->(:{item['to']})")

    return "\n".join(labels + edges).strip()


def _format_list_schema(schema: list[dict]) -> str:
    labels = ["Labels:"]
    relationships = ["", "Relationships:"]

    for item in schema:
        item_type = item.get("type")
        if item_type == "VERTEX":
            labels.extend(_format_vertex(item))
        elif item_type == "EDGE":
            relationships.extend(_format_edge(item))

    return "\n".join(labels + relationships).strip()


def _format_vertex(item: dict) -> list[str]:
    label = item.get("label", "Unknown")
    output = [f"- {label}"]
    output.append(f"  type: {item.get('type', 'VERTEX')}")
    if item.get("description"):
        output.append(f"  description: {item['description']}")
    if item.get("primary"):
        output.append(f"  primary: {item['primary']}")
    output.append("  properties:")
    properties = item.get("properties", [])
    if not properties:
        output.append("    - <none>")
    for prop in properties:
        output.append(f"    - {_format_property(prop)}")
    return output


def _format_edge(item: dict) -> list[str]:
    label = item.get("label", "UNKNOWN")
    output = [f"- {label}"]
    output.append(f"  type: {item.get('type', 'EDGE')}")
    if item.get("description"):
        output.append(f"  description: {item['description']}")
    constraints = [constraint for constraint in item.get("constraints", []) if len(constraint) == 2]
    output.append("  constraints:")
    if constraints:
        for source, target in constraints:
            output.append(f"    - (:{source})-[:{label}]->(:{target})")
    else:
        output.append("    - <none>")
    properties = item.get("properties", [])
    if properties:
        output.append("  properties:")
        for prop in properties:
            output.append(f"    - {_format_property(prop)}")
    return output


def _format_property(prop: dict) -> str:
    name = prop.get("name", "unknown")
    prop_type = prop.get("type", "UNKNOWN")
    flags = []
    for key in ("optional", "unique", "index"):
        if key in prop:
            flags.append(f"{key}={str(prop[key]).lower()}")
    description = prop.get("description")
    suffix = f"; {'; '.join(flags)}" if flags else ""
    if description:
        suffix = f"{suffix}; description: {description}"
    return f"{name}: {prop_type}{suffix}"


def _format_compact_property(prop: str | dict) -> str:
    if isinstance(prop, dict):
        return _format_property(prop)
    return str(prop)
