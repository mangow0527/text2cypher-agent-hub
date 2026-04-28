# 同步说明

## 目标

本仓库不是五个服务的唯一开发源仓库，而是统一展示与归档仓。

同步机制支持两种触发方式：

- GitHub Actions 自动同步：每 6 小时从源仓 checkout 最新代码，运行同步脚本，并在有变化时提交回本仓库
- GitHub Actions 手动同步：在 Actions 页面手动运行同一套 workflow
- 本地手动同步：在本地执行 `sync/scripts/sync_all.sh` 或单服务同步脚本

同步脚本的职责是：

- 从源目录读取最新代码
- 复制到本仓对应目录
- 排除无意义的运行时文件和本地环境目录
- 生成最近一次同步记录

## GitHub Actions 同步

workflow 文件：

- [.github/workflows/sync-latest.yml](/Users/mangowmac/Desktop/code/text2cypher-agent-hub/.github/workflows/sync-latest.yml)

触发方式：

- 自动触发：每 6 小时运行一次，cron 表达式为 `0 */6 * * *`
- 手动触发：在 GitHub Actions 页面选择 `Sync Latest Source Code` 并点击运行

执行流程：

1. checkout 本展示仓
2. checkout 源仓 `mangow0527/NL2Cypher`
3. 用 Actions 中的源仓路径覆盖 `SOURCE_ROOT_NL2CYPHER`
4. 执行 `./sync/scripts/sync_all.sh`
5. 如果同步后有差异，则提交并 push 回当前运行分支

如果源仓是私有仓库，建议在本仓配置 `SOURCE_REPO_TOKEN` secret，并确保它有读取源仓的权限。

## 同步前检查

本地执行同步前，建议先确认：

1. 源目录已经切到你想同步的最新状态
2. 本仓没有未处理的手工修改

## 默认来源

默认来源配置见 [sync/sources.conf](/Users/mangowmac/Desktop/code/text2cypher-agent-hub/sync/sources.conf)。

如果你的本地路径变化，只需要修改该文件，不需要改同步脚本。

GitHub Actions 会通过环境变量 `SOURCE_ROOT_NL2CYPHER` 覆盖默认本地路径。

## 全量同步

```bash
./sync/scripts/sync_all.sh
```

同步完成后会更新：

- `agents/cypher-generator-agent/`
- `agents/testing-agent/`
- `agents/repair-agent/`
- `console/runtime-console/`
- `contracts/`
- `sync/manifests/latest-sync.json`

## 单服务同步

只同步一个服务时，直接执行对应脚本。例如：

```bash
./sync/scripts/sync_testing.sh
```

`knowledge-agent` 和 `qa-agent` 在展示仓中保留为独立外部代码目录，不参与本仓同步脚本。

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
