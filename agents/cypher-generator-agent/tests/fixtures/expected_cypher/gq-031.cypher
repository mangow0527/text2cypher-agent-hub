MATCH (svc:Service)
RETURN svc.id AS service_id, svc.name AS service_name, svc.elem_type AS service_elem_type, svc.quality_of_service AS service_quality_of_service, svc.bandwidth AS service_bandwidth, svc.latency AS service_latency
