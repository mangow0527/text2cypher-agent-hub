MATCH (t:Tunnel {id: 'tun-mpls-001'})-[p:PATH_THROUGH]->(ne:NetworkElement)
RETURN ne AS device, p.hop_order AS hop
ORDER BY p.hop_order ASC
