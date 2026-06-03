# Network Topology Vocabulary v1

> 日期：2026-05-27
> 状态：设计 v1
> 目的：统一本文档集中的网络拓扑示例命名，避免示例漂移进入实现

## 1. 使用规则

本文档不是完整业务模型，而是 v1 设计文档中的官方示例 vocabulary。所有 DSL、Repair、Observability、Self-Validation 示例如果需要网络拓扑对象，必须优先使用这里的名字。

规则：

- `name` 必须等于 Graph Semantic Model 中的 `name`，也必须等于 Cypher label、edge type 或 property name。
- 不允许同义示例名，例如 `USES_TUNNEL` 和 `SERVICE_USES_TUNNEL` 混用。
- 新示例需要新增 vertex、edge、property、metric 或 path_pattern 时，先改本文档，再引用。
- 本文档只规范示例 vocabulary，不替代真实 graph semantic model 文件。

## 2. Vertices

| vertex | 含义 | id_property |
| --- | --- | --- |
| `NetworkElement` | 网络设备、网元 | `id` |
| `Tunnel` | 隧道实例 | `id` |
| `Service` | 网络服务或业务服务 | `id` |
| `Port` | 设备端口 | `id` |

## 3. Edges

| edge | from | to | 含义 |
| --- | --- | --- | --- |
| `SERVICE_USES_TUNNEL` | `Service` | `Tunnel` | 服务使用隧道 |
| `PATH_THROUGH` | `Tunnel` | `NetworkElement` | 隧道路径经过设备，路径查询必须按 `hop_order` 排序 |
| `TUNNEL_SRC` | `Tunnel` | `NetworkElement` | 隧道源端设备，不用于完整路径推断 |
| `TUNNEL_DST` | `Tunnel` | `NetworkElement` | 隧道宿端设备，不用于完整路径推断 |
| `HAS_PORT` | `NetworkElement` | `Port` | 设备拥有端口 |

## 4. Properties

| owner | property | type | 说明 |
| --- | --- | --- | --- |
| `NetworkElement` | `id` | `string` | 设备唯一标识 |
| `NetworkElement` | `name` | `string` | 设备名 |
| `NetworkElement` | `elem_type` | `string` | 设备类型，常见值包括 `router`、`switch`、`firewall`、`load_balancer` |
| `NetworkElement` | `location` | `string` | 机房或物理位置 |
| `Tunnel` | `id` | `string` | 隧道唯一标识 |
| `Tunnel` | `bandwidth` | `float` | 隧道带宽 |
| `Service` | `id` | `string` | 服务唯一标识 |
| `Service` | `quality_of_service` | `string` | 服务等级，常见值包括 `GOLD`、`SILVER`、`BRONZE` |
| `Service` | `service_type` | `string` | 服务类型 |
| `Port` | `id` | `string` | 端口唯一标识 |
| `Port` | `status` | `string` | 端口状态，常见值包括 `up`、`down` |
| `PATH_THROUGH` | `hop_order` | `int` | 隧道路径 hop 序号 |

## 5. Metrics

| metric | pattern | expression | valid_dimensions |
| --- | --- | --- | --- |
| `device_count` | `(ne:NetworkElement)` | `count(ne)` | `ne.elem_type`、`ne.location` |
| `port_count` | `(ne:NetworkElement)-[:HAS_PORT]->(port:Port)` | `count(port)` | `ne.id`、`port.status` |
| `service_count` | `(svc:Service)` | `count(svc)` | `svc.quality_of_service`、`svc.service_type` |

## 6. Path Patterns

| path_pattern | 参数 | 返回 | 说明 |
| --- | --- | --- | --- |
| `tunnel_full_path` | `tunnel_id: string` | `device`、`hop` | 使用 `PATH_THROUGH` 返回隧道完整路径，按 `PATH_THROUGH.hop_order` 升序 |

## 7. 标准示例表达

| 用户问题 | 推荐对象 |
| --- | --- |
| “Gold 服务使用了哪些隧道” | `Service` --`SERVICE_USES_TUNNEL`--> `Tunnel`，过滤 `Service.quality_of_service=GOLD` |
| “隧道 tun-mpls-001 经过哪些设备” | `path_pattern=tunnel_full_path` |
| “找出所有经过设备 ne-0001 的隧道” | `Tunnel` --`PATH_THROUGH`--> `NetworkElement`，过滤 `NetworkElement.id=ne-0001` |
| “端口最多的 5 台设备” | `NetworkElement` --`HAS_PORT`--> `Port`，聚合 `count(Port.id)`，排序取 5 |
| “全网有多少台防火墙” | `metric=device_count`，过滤 `NetworkElement.elem_type=firewall` |
