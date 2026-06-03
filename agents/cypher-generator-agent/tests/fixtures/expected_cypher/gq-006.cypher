MATCH (ne:NetworkElement)-[:HAS_PORT]->(port:Port)
WHERE ne.id = 'ne-0001'
RETURN port.id AS port_id
