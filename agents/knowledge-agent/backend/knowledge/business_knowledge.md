## Terminology Mapping

[id: protocol_version_mapping]
- “协议版本”优先映射到 `Protocol.version`。

[id: network_element_alias]
- “网元”对应 `NetworkElement`。

[id: tunnel_owner_path]
- “隧道所属网元”优先理解为 `(:NetworkElement)-[:FIBER_SRC]->(:Tunnel)` 路径上的上游设备。


[id: add_business_term_mapping_guidance_and_a_few_sho]
- Add business-term mapping guidance and a few-shot example that matches the failed question pattern.

[id: add_business_term_mapping_guidance_and_a_few_sho]
- Add business-term mapping guidance and a few-shot example that matches the failed question pattern.
## protocol_specification

[id: protocol_version_mapping]
{'v1': {'status': 'deprecated', 'features': ['basic_auth', 'rest_api_v1'], 'sunset_date': '2023-12-31'}, 'v2': {'status': 'supported', 'features': ['oauth2', 'rest_api_v2', 'graphql_readonly'], 'sunset_date': None}, 'v3': {'status': 'beta', 'features': ['mtls', 'grpc_api', 'graphql_full'], 'sunset_date': None}}
