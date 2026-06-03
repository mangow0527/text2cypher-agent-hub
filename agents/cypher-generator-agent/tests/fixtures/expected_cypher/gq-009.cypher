MATCH (ne:NetworkElement)
RETURN ne.elem_type AS elem_type, count(ne) AS device_count
