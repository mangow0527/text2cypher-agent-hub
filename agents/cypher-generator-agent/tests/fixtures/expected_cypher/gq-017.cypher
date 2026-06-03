MATCH (ne:NetworkElement)-[:HAS_PORT]->(port:Port)
WHERE ne.id = 'ne-9999'
RETURN port.id AS port_id
