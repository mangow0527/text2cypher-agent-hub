MATCH (ne:NetworkElement)-[:HAS_PORT]->(port:Port)
RETURN ne.id AS device, count(port) AS port_count
ORDER BY port_count DESC
LIMIT 5
