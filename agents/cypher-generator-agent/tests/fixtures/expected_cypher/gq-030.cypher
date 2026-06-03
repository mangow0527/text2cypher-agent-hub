MATCH (port:Port)
WITH port.status AS status, count(port.id) AS port_count
RETURN status AS status, port_count AS port_count
ORDER BY port_count DESC
LIMIT 5
