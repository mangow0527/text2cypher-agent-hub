MATCH (port:Port)
RETURN port.status AS status, count(port.id) AS port_count
