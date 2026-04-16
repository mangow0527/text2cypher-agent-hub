from __future__ import annotations


def format_schema(schema: dict | list[dict]) -> str:
    if isinstance(schema, list):
        return _format_list_schema(schema)

    labels = ["Labels:"]
    for item in schema.get("vertex_labels", []):
        props = ", ".join(item.get("properties", []))
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
            props = ", ".join(prop.get("name", "") for prop in item.get("properties", []))
            labels.append(f"- {item.get('label', 'Unknown')}({props})")
        elif item_type == "EDGE":
            for constraint in item.get("constraints", []):
                if len(constraint) == 2:
                    relationships.append(f"- (:{constraint[0]})-[:{item.get('label', 'UNKNOWN')}]->(:{constraint[1]})")

    return "\n".join(labels + relationships).strip()
