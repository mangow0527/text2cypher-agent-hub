# Text2Cypher QA Agent

本项目是一个本地运行的 `Text2Cypher QA Agent`，严格按照 [text2cypher_qa_generation_scheme.md](/Users/wangxinhao/muti-agent-offline-system/text2cypher_qa_generation_scheme.md) 中定义的规则链执行：

- Schema 标准化
- Cypher 骨架生成
- Cypher 实例化
- 五级验证
- 中文问题生成
- round-trip 复验
- 去重与分层产出

## 目录

- `app/` Python 后端
- `frontend/` React 前端
- `prompts/` Prompt 模板
- `artifacts/` 中间与最终产物
- `docs/superpowers/specs/` 设计与实施文档

## 后端启动

1. 安装依赖

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2. 配置环境变量

```bash
cp .env.example .env
```

然后直接编辑项目根目录下的 `.env`。

3. 启动 API

```bash
python3 run_api.py
```

4. 运行 CLI

```bash
python3 -m app.entrypoints.cli.main list-jobs
```

## 前端启动

```bash
cd frontend
npm install
npm run dev
```

默认前端访问 `http://127.0.0.1:5173`，默认后端访问 `http://127.0.0.1:8000`。

## Schema 与 TuGraph 输入方式

当前系统支持三种 schema 输入方式：

- 前台界面输入 JSON
- 本地文件读取
- 接口读取返回 JSON

当前系统支持两种 TuGraph 配置方式：

- 环境变量读取
- 前台或 API 直接输入

## CLI 示例

```bash
python3 -m app.entrypoints.cli.main create-job --schema-file schema.json --mode offline
python3 -m app.entrypoints.cli.main run-job <job_id>
```

```bash
python3 -m app.entrypoints.cli.main create-job --schema-url http://127.0.0.1:9000/schema --mode online
```

```bash
python3 -m app.entrypoints.cli.main create-job \
  --schema-file schema.json \
  --mode online \
  --tugraph-base-url http://127.0.0.1:7070 \
  --tugraph-user admin \
  --tugraph-password your_password \
  --tugraph-graph network_schema_v10
```

## TuGraph 配置

当前实现优先复用 [tugraph_http_ops.py](/Users/wangxinhao/muti-agent-offline-system/tugraph_http_ops.py) 中的登录和 `/cypher` 调用逻辑。配置项可来自环境变量：

- `TUGRAPH_BASE_URL`
- `TUGRAPH_USER`
- `TUGRAPH_PASSWORD`
- `TUGRAPH_GRAPH`

也可以在任务请求中的 `tugraph_config` 里显式传入：

- `base_url`
- `username`
- `password`
- `graph`

当 `base_url` 为空时，系统仍使用本地 mock 运行时校验，方便先调通整体流程。

## `.env` 填写示例

请在项目根目录新建并填写 [`.env`](/Users/wangxinhao/muti-agent-offline-system/.env)，可以直接参考 [`.env.example`](/Users/wangxinhao/muti-agent-offline-system/.env.example)。

推荐至少填写：

```bash
OPENAI_API_KEY=你的智谱 API Key
OPENAI_BASE_URL=https://open.bigmodel.cn/api/paas/v4
OPENAI_MODEL=glm-5

TUGRAPH_BASE_URL=http://118.196.92.128:7070
TUGRAPH_USER=admin
TUGRAPH_PASSWORD=你的 TuGraph 密码
TUGRAPH_GRAPH=network_schema_v10
```

填写完成后，重启后端服务即可生效。
