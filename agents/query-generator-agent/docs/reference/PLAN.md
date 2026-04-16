# Text2Cypher 架构规划说明（已归档）

## 说明

这份文档保留为历史规划入口，但其中旧版“查询生成服务执行 TuGraph、再把 execution 提交给测试服务”的方案已经**不再生效**。

当前实现已经切换为：

- `Cypher Generation Service`（Cypher 生成服务）负责：
  - 接收 `id + question`
  - 主动获取 `prompt`
  - 调用模型生成 Cypher
  - 保留 `input_prompt_snapshot`
  - 向测试服务提交生成结果
- `Testing Service`（测试服务）负责：
  - 执行 TuGraph
  - 完成评测
  - 生成 `IssueTicket`

## 当前权威文档

请以后续设计和实现以下文档为准：

- [Cypher_Generation_Service_Design.md](/Users/mangowmac/Desktop/code/NL2Cypher/services/query_generator_agent/docs/Cypher_Generation_Service_Design.md)
- [workflow.md](/Users/mangowmac/Desktop/code/NL2Cypher/console/runtime_console/docs/reference/workflow.md)

## 保留这份文档的原因

- 保留早期架构思路，方便回看设计演进
- 避免误删后失去历史上下文

## 已失效的旧假设

以下假设已全部失效，请勿再据此开发：

1. 查询生成服务负责执行 TuGraph
2. 测试服务接收 `execution + knowledge_context`
3. 查询生成服务主链路依赖知识标签加载和 schema hint 组装
4. 查询生成服务输出的是“业务成功/失败”结论
5. 主存储方案是 SQLite

当前代码以 JSON 文件持久化为主，且按新的服务边界执行。
