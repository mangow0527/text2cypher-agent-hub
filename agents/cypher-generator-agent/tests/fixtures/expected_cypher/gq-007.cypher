MATCH (port:Port)
WHERE port.status = 'down'
RETURN port.id AS port_id
