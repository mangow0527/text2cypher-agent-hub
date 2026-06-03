MATCH path = (tun:Tunnel)-[:PATH_THROUGH*1..8]->(ne:NetworkElement)
WHERE ne.id = 'ne-0001'
RETURN tun.id AS tunnel_id
