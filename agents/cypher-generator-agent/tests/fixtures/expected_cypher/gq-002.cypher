MATCH (svc:Service)-[:SERVICE_USES_TUNNEL]->(tun:Tunnel)
WHERE svc.id = 'svc-gold-001'
RETURN tun.id AS tunnel_id
