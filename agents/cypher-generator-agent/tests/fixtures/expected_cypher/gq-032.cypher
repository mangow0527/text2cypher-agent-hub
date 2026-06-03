MATCH (svc:Service)-[:SERVICE_USES_TUNNEL]->(tun:Tunnel)
RETURN tun.id AS tunnel_id, tun.name AS tunnel_name, tun.bandwidth AS tunnel_bandwidth
