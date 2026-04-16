# Text2Cypher Agent Hub

统一汇总 `5` 个 Text2Cypher agent 服务和 `1` 个运行控制台的展示仓。

## 仓库定位

- 本仓库用于统一浏览五个服务的代码与文档。
- 各服务继续在自己的原始开发位置维护。
- 只有在你主动执行同步脚本时，本仓库才会更新到本地最新代码。

## 目录结构

- `agents/query-generator-agent/`
- `agents/testing-agent/`
- `agents/repair-agent/`
- `agents/knowledge-agent/`
- `agents/qa-agent/`
- `console/runtime-console/`
- `contracts/`
- `sync/`
- `docs/`

## 同步命令

全量同步：

```bash
./sync/scripts/sync_all.sh
```

单服务同步：

```bash
./sync/scripts/sync_query_generator.sh
./sync/scripts/sync_testing.sh
./sync/scripts/sync_repair.sh
./sync/scripts/sync_knowledge.sh
./sync/scripts/sync_qa.sh
./sync/scripts/sync_console.sh
./sync/scripts/sync_contracts.sh
```

## 来源说明

默认来源定义在 [sync/sources.conf](/Users/mangowmac/Desktop/code/text2cypher-agent-hub/sync/sources.conf)。

- `query-generator-agent`
- `testing-agent`
- `repair-agent`
- `runtime-console`
- `contracts`

都来自本地 [NL2Cypher](/Users/mangowmac/Desktop/code/NL2Cypher) 仓库。

- `knowledge-agent`
- `qa-agent`

默认来自本地 [third_party](/Users/mangowmac/Desktop/code/NL2Cypher/third_party) 目录中的最新代码副本。

## 使用说明

详细同步说明见 [docs/sync-guide.md](/Users/mangowmac/Desktop/code/text2cypher-agent-hub/docs/sync-guide.md)。

