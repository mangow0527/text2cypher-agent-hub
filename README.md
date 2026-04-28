# Text2Cypher Agent Hub

统一汇总 `5` 个 Text2Cypher agent 服务和 `1` 个运行控制台的展示仓。

## 仓库定位

- 本仓库用于统一浏览五个服务的代码与文档。
- 各服务继续在自己的原始开发位置维护。
- GitHub Actions 每 6 小时会自动从源仓同步最新代码并提交回本仓库。
- 你也可以通过 GitHub Actions 手动触发同步，或在本地执行同步脚本。

## 目录结构

- `agents/cypher-generator-agent/`
- `agents/testing-agent/`
- `agents/repair-agent/`
- `agents/knowledge-agent/`
- `agents/qa-agent/`
- `console/runtime-console/`
- `contracts/`
- `sync/`
- `docs/`

## 同步命令

GitHub Actions 同步：

- 自动同步：`.github/workflows/sync-latest.yml` 每 6 小时运行一次。
- 手动同步：在 GitHub Actions 页面运行 `Sync Latest Source Code` workflow。

本地全量同步：

```bash
./sync/scripts/sync_all.sh
```

单服务同步：

```bash
./sync/scripts/sync_cypher_generator.sh
./sync/scripts/sync_testing.sh
./sync/scripts/sync_repair.sh
./sync/scripts/sync_knowledge.sh
./sync/scripts/sync_qa.sh
./sync/scripts/sync_console.sh
./sync/scripts/sync_contracts.sh
```

## 来源说明

默认来源定义在 [sync/sources.conf](/Users/mangowmac/Desktop/code/text2cypher-agent-hub/sync/sources.conf)。

- `cypher-generator-agent`
- `testing-agent`
- `repair-agent`
- `runtime-console`
- `contracts`

都来自本地 [NL2Cypher](/Users/mangowmac/Desktop/code/NL2Cypher) 仓库。

- `knowledge-agent` 来自 `git@github.com:KG-AT-HOME/knowledge-agent.git`
- `qa-agent` 来自 `git@github.com:KG-AT-HOME/qa-agent.git`

## 使用说明

详细同步说明见 [docs/sync-guide.md](/Users/mangowmac/Desktop/code/text2cypher-agent-hub/docs/sync-guide.md)。
