# 同步说明

## 目标

本仓库不是五个服务的唯一开发源仓库，而是统一展示与归档仓。

同步脚本的职责是：

- 从本地源目录读取最新代码
- 复制到本仓对应目录
- 排除无意义的运行时文件和本地环境目录
- 生成最近一次同步记录

## 同步前检查

执行同步前，建议先确认：

1. 源目录已经切到你想同步的最新状态
2. 外部两个服务的本地代码也已经更新完成
3. 本仓没有未处理的手工修改

## 默认来源

默认来源配置见 [sync/sources.conf](/Users/mangowmac/Desktop/code/text2cypher-agent-hub/sync/sources.conf)。

如果你的本地路径变化，只需要修改该文件，不需要改同步脚本。

## 全量同步

```bash
./sync/scripts/sync_all.sh
```

同步完成后会更新：

- `agents/cypher-generator-agent/`
- `agents/testing-agent/`
- `agents/repair-agent/`
- `agents/knowledge-agent/`
- `agents/qa-agent/`
- `console/runtime-console/`
- `contracts/`
- `sync/manifests/latest-sync.json`

## 单服务同步

只同步一个服务时，直接执行对应脚本。例如：

```bash
./sync/scripts/sync_knowledge.sh
```

## 排除规则

同步脚本默认排除：

- `.git/`
- `.venv/`
- `node_modules/`
- `__pycache__/`
- `.pytest_cache/`
- `dist/`
- `build/`
- `artifacts/`
- `data/`
- `.DS_Store`

这些目录和文件更偏向本地运行状态，不适合进入整合仓。

## 同步记录

最近一次同步记录写入：

- [sync/manifests/latest-sync.json](/Users/mangowmac/Desktop/code/text2cypher-agent-hub/sync/manifests/latest-sync.json)

它会记录：

- 同步时间
- 各来源目录
- 各目标目录

## 建议流程

推荐每次按这个节奏操作：

1. 在各源仓完成开发
2. 在各源仓确认本地代码状态
3. 回到本仓执行同步脚本
4. 检查差异是否符合预期
5. 提交本仓变更
