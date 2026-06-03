MATCH (ne:NetworkElement)
WHERE ne.elem_type = 'firewall'
RETURN count(ne.id) AS network_element_count
