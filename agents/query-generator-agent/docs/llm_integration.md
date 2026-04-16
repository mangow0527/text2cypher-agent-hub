# LLM Integration

系统中有两处使用 LLM：

1. **查询语句生成服务**：用 LLM 生成 Cypher 查询语句
2. **问题修复服务**：用 LLM 辅助精化修复计划

两处均采用 OpenAI-compatible `/chat/completions` 接入方式，且均有无 LLM 时的回退机制。

---

## 1. 查询语句生成服务

### 两种模式

- `heuristic_fallback`
  - 默认模式
  - 不需要 API Key
  - 使用 `network_schema_v10` 的 schema-aware 启发式规则生成 Cypher
- `llm`
  - 需要配置真实大模型
  - 接入方式：`OpenAI-compatible /chat/completions`

### 配置方式

在项目根目录 `.env` 中填写：

```env
QUERY_GENERATOR_LLM_ENABLED=true
QUERY_GENERATOR_LLM_PROVIDER=openai_compatible
QUERY_GENERATOR_LLM_BASE_URL=https://your-openai-compatible-endpoint/v1
QUERY_GENERATOR_LLM_API_KEY=your_api_key
QUERY_GENERATOR_LLM_MODEL=your_model_name
QUERY_GENERATOR_LLM_TEMPERATURE=0.1
```

### 请求格式

向 `POST {QUERY_GENERATOR_LLM_BASE_URL}/chat/completions` 发送 OpenAI 兼容请求：

```json
{
  "model": "your_model_name",
  "temperature": 0.1,
  "messages": [
    {"role": "system", "content": "..."},
    {"role": "user", "content": "..."}
  ]
}
```

### Prompt 设计

Prompt 中会携带：
- `network_schema_v10` 的完整 schema context
- 当前自然语言问题
- 调用方传入的 schema hint
- 当前 attempt 次数
- 历史反馈摘要

模型被要求：
- 只生成一个 TuGraph 可执行的 Cypher
- 只使用 schema 中真实存在的 label / property / edge
- 返回 JSON：

```json
{
  "cypher": "MATCH ...",
  "notes": "..."
}
```

### 自动回退

如果出现以下任一情况，服务会自动回退到启发式生成：
- `QUERY_GENERATOR_LLM_ENABLED=false`
- 缺少 `base_url`
- 缺少 `api_key`
- 缺少 `model`
- 调用 LLM 接口时报错

---

## 2. 问题修复服务

### 工作方式

修复服务先执行确定性规则分析和对照实验，然后可选地调用 LLM 来精化修复计划。

### 配置方式

在项目根目录 `.env` 中填写：

```env
REPAIR_SERVICE_LLM_ENABLED=true
REPAIR_SERVICE_LLM_BASE_URL=https://your-openai-compatible-endpoint/v1
REPAIR_SERVICE_LLM_API_KEY=your_api_key
REPAIR_SERVICE_LLM_MODEL_NAME=your_model_name
REPAIR_SERVICE_LLM_TEMPERATURE=0.2
```

兼容说明：
- 当前代码同时兼容旧变量名 `REPAIR_SERVICE_LLM_MODEL`
- 推荐统一改为 `REPAIR_SERVICE_LLM_MODEL_NAME`

### Prompt 设计

Prompt 中会携带：
- `IssueTicket` 的完整内容（问题、标准答案、实际结果、评测结果）
- 确定性分析的初步归因和证据
- 对照实验结果（如果执行了）
- `network_schema_v10` 的 schema context

模型被要求：
- 确认或修正根因分析
- 生成具体的修复动作列表
- 返回 JSON：

```json
{
  "root_cause": "generator_logic_issue",
  "confidence": 0.85,
  "reasoning": "...",
  "actions": [
    {
      "target_service": "query_generator",
      "action_type": "prompt_refinement",
      "instruction": "..."
    }
  ]
}
```

### 启动约束

修复服务现在强制要求启用并正确配置 LLM：
- `REPAIR_SERVICE_LLM_ENABLED` 必须为 `true`
- 必须完整提供 `base_url` / `api_key` / `model`
- 缺少任一关键配置时，服务会直接启动失败

---

## 3. 对照实验中的 LLM 使用

修复服务在执行对照实验（A/B/C）时，需要重新调用查询语句生成服务来生成 Cypher。这里使用的 LLM 配置是查询语句生成服务的配置，而不是修复服务的配置。

流程：
1. 修复服务调用查询语句生成服务的 API（如对照实验端点）
2. 查询语句生成服务使用自己的 LLM 配置生成 Cypher
3. 修复服务收集实验结果并进行归因

---

## 4. 状态检查

### 查询生成服务状态

```bash
curl http://127.0.0.1:8000/api/v1/generator/status
```

返回示例：

```json
{
  "llm_enabled": true,
  "llm_provider": "openai_compatible",
  "llm_base_url": "https://your-openai-compatible-endpoint/v1",
  "llm_model": "your_model_name",
  "llm_configured": true,
  "active_mode": "llm"
}
```

### 修复服务状态

修复服务的 LLM 配置可通过其 `/health` 端点间接确认：

```bash
curl http://127.0.0.1:8002/health
```

返回示例：

```json
{
  "status": "healthy",
  "service": "repair_service"
}
```
