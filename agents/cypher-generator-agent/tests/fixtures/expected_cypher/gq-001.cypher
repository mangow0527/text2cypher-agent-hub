MATCH (svc:Service)-[:SERVICE_USES_TUNNEL]->(tun:Tunnel)
WHERE svc.quality_of_service = 'Gold'
RETURN tun.id AS tunnel_id
