# Knowledge Repair Suggestion Service（KRSS）根因分析 V2 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将现有 repair_service 重构为 KRSS：利用强模型（glm-5）+（可选）“类型级最小补丁对照实验”诊断 CGS 在 `id + prompt_snapshot` 下的知识缺口，并以单一请求 `POST /api/knowledge/repairs/apply` 向 Knowledge Ops 提交 `{id, suggestion, knowledge_types}`（成功语义为对方 HTTP 200；非 200 重试直至 200）。

**架构：** 以 LLM 主导的 Prompt Gap Analysis 生成候选 `knowledge_types` 与 suggestion，再按不确定性阈值触发最小补丁对照实验（不从 Knowledge Ops 拉补丁包），用弱模型对“增量类型”响应来收敛类型选择，最后生成对 Knowledge Ops 大模型可执行的 suggestion prompt 并投递。

**技术栈：** FastAPI + Pydantic（contracts/models.py）+ httpx（OpenAI-compatible 调用 glm-5 / CGS prompt snapshot / Knowledge Ops apply）+ pytest。

---

## 文件结构（计划中的新增/修改）

**修改：**
- `services/repair_agent/app/main.py`：接口响应语义改为“投递成功才 200”，移除 `RepairPlanEnvelope` 输出。
- `services/repair_agent/app/service.py`：替换“RepairPlan + 分发”流水线为“Diagnose → Suggest → Apply（重试）”流水线；保留 TuGraph 仅用于对照实验可选项（不用于业务裁决）。
- `services/repair_agent/app/config.py`：增加 Knowledge Ops apply URL 与 CGS prompt snapshot URL 配置；保留 glm-5 配置（已存在字段）。
- `services/repair_agent/app/clients.py`：新增 `CGSPromptSnapshotClient`、`KnowledgeOpsRepairApplyClient`、`OpenAICompatibleKRSSAnalyzer`（glm-5）。
- `contracts/models.py`：增加 KRSS 输出 payload 的模型（严格 3 字段）与 `KnowledgeType` 枚举/字面量约束。

**新增：**
- `services/repair_agent/app/analysis.py`：根因分析与对照实验调度（纯函数/小类），负责把 `IssueTicket + prompt_snapshot` 变成 `{knowledge_types, suggestion}`。
- `tests/test_krss_contract_and_retry.py`：契约与重试语义测试（mock httpx）。
- `tests/test_krss_analysis_llm_first.py`：LLM 主导诊断的单测（mock analyzer 输出）。
- `tests/test_krss_counterfactual_min_patches.py`：最小补丁对照实验触发与类型收敛逻辑测试。

---

## 任务 1：新增 shared 模型（KnowledgeRepairSuggestionRequest + KnowledgeType）

**文件：**
- 修改：`/Users/mangowmac/Desktop/code/NL2Cypher/contracts/models.py`
- 测试：`/Users/mangowmac/Desktop/code/NL2Cypher/tests/test_krss_contract_and_retry.py`

- [ ] **步骤 1：编写失败的测试（payload 严格 3 字段 + knowledge_types 枚举）**

```python
from __future__ import annotations

import pytest
from pydantic import ValidationError

from contracts.models import KnowledgeRepairSuggestionRequest


def test_knowledge_repair_suggestion_request_accepts_only_known_types():
    ok = KnowledgeRepairSuggestionRequest(
        id="q_001",
        suggestion="补充协议版本映射，并新增对应 few-shot",
        knowledge_types=["few-shot", "business_knowledge"],
    )
    assert ok.knowledge_types == ["few-shot", "business_knowledge"]


def test_knowledge_repair_suggestion_request_rejects_unknown_type():
    with pytest.raises(ValidationError):
        KnowledgeRepairSuggestionRequest(
            id="q_001",
            suggestion="x",
            knowledge_types=["unknown_type"],
        )
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
python -m pytest -q tests/test_krss_contract_and_retry.py::test_knowledge_repair_suggestion_request_accepts_only_known_types
```

预期：FAIL，报错 `ImportError` 或 `AttributeError: KnowledgeRepairSuggestionRequest`.

- [ ] **步骤 3：编写最少实现代码（contracts/models.py）**

在 `contracts/models.py` 末尾附近增加：

```python
from typing import List, Literal

KnowledgeType = Literal["schema", "cypher_syntax", "few-shot", "system_prompt", "business_knowledge"]


class KnowledgeRepairSuggestionRequest(BaseModel):
    id: str
    suggestion: str
    knowledge_types: List[KnowledgeType]
```

- [ ] **步骤 4：运行测试验证通过**

运行：

```bash
python -m pytest -q tests/test_krss_contract_and_retry.py::test_knowledge_repair_suggestion_request_rejects_unknown_type
```

预期：PASS。

- [ ] **步骤 5：Commit**

```bash
git add contracts/models.py tests/test_krss_contract_and_retry.py
git commit -m "feat(修复建议): 新增知识修复建议 payload 模型"
```

---

## 任务 2：新增 CGS prompt_snapshot 拉取客户端（KRSS 内部调用）

**文件：**
- 修改：`/Users/mangowmac/Desktop/code/NL2Cypher/services/repair_agent/app/clients.py`
- 修改：`/Users/mangowmac/Desktop/code/NL2Cypher/services/repair_agent/app/config.py`
- 测试：`/Users/mangowmac/Desktop/code/NL2Cypher/tests/test_krss_contract_and_retry.py`

- [ ] **步骤 1：编写失败的测试（调用 CGS /api/v1/questions/{id}/prompt）**

```python
from __future__ import annotations

import httpx
import pytest

from services.repair_agent.app.clients import CGSPromptSnapshotClient


class _FakeAsyncClient:
    last_url = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url: str):
        _FakeAsyncClient.last_url = url
        return httpx.Response(
            status_code=200,
            json={"id": "qa-001", "input_prompt_snapshot": "PROMPT"},
        )


@pytest.mark.asyncio
async def test_cgs_prompt_snapshot_client_calls_expected_url(monkeypatch):
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    client = CGSPromptSnapshotClient(base_url="http://127.0.0.1:8000", timeout_seconds=3.0)
    snapshot = await client.fetch_prompt_snapshot("qa-001")
    assert snapshot["id"] == "qa-001"
    assert snapshot["input_prompt_snapshot"] == "PROMPT"
    assert _FakeAsyncClient.last_url == "http://127.0.0.1:8000/api/v1/questions/qa-001/prompt"
```

- [ ] **步骤 2：运行测试验证失败**

```bash
python -m pytest -q tests/test_krss_contract_and_retry.py::test_cgs_prompt_snapshot_client_calls_expected_url
```

预期：FAIL，找不到 `CGSPromptSnapshotClient`。

- [ ] **步骤 3：编写最少实现（clients.py + config.py）**

在 `services/repair_agent/app/clients.py` 增加：

```python
class CGSPromptSnapshotClient:
    def __init__(self, base_url: str, timeout_seconds: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    async def fetch_prompt_snapshot(self, id: str) -> Dict[str, str]:
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(f"{self.base_url}/api/v1/questions/{id}/prompt")
            response.raise_for_status()
            payload = response.json()
        return {"id": payload["id"], "input_prompt_snapshot": payload["input_prompt_snapshot"]}
```

在 `services/repair_agent/app/config.py` 增加配置项（保持 env_prefix 不变）：

```python
    cgs_base_url: str = "http://127.0.0.1:8000"
```

- [ ] **步骤 4：运行测试验证通过**

```bash
python -m pytest -q tests/test_krss_contract_and_retry.py::test_cgs_prompt_snapshot_client_calls_expected_url
```

预期：PASS。

- [ ] **步骤 5：Commit**

```bash
git add services/repair_agent/app/clients.py services/repair_agent/app/config.py tests/test_krss_contract_and_retry.py
git commit -m "feat(修复建议): 新增 CGS prompt snapshot 客户端"
```

---

## 任务 3：新增 Knowledge Ops apply 客户端（严格 200 语义 + 重试直至 200）

**文件：**
- 修改：`/Users/mangowmac/Desktop/code/NL2Cypher/services/repair_agent/app/clients.py`
- 修改：`/Users/mangowmac/Desktop/code/NL2Cypher/services/repair_agent/app/config.py`
- 测试：`/Users/mangowmac/Desktop/code/NL2Cypher/tests/test_krss_contract_and_retry.py`

- [ ] **步骤 1：编写失败的测试（非 200 重试，200 停止）**

```python
from __future__ import annotations

import httpx
import pytest

from contracts.models import KnowledgeRepairSuggestionRequest
from services.repair_agent.app.clients import KnowledgeOpsRepairApplyClient


class _FakeAsyncClient:
    call_count = 0

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url: str, json: dict):
        _FakeAsyncClient.call_count += 1
        if _FakeAsyncClient.call_count < 3:
            return httpx.Response(status_code=503, json={"detail": "busy"})
        return httpx.Response(status_code=200, json={"ok": True})


@pytest.mark.asyncio
async def test_knowledge_ops_apply_retries_until_http_200(monkeypatch):
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    client = KnowledgeOpsRepairApplyClient(apply_url="http://ko/api/knowledge/repairs/apply", timeout_seconds=3.0)
    payload = KnowledgeRepairSuggestionRequest(
        id="q_001",
        suggestion="PROMPT",
        knowledge_types=["system_prompt"],
    )
    await client.apply(payload)
    assert _FakeAsyncClient.call_count == 3
```

- [ ] **步骤 2：运行测试验证失败**

```bash
python -m pytest -q tests/test_krss_contract_and_retry.py::test_knowledge_ops_apply_retries_until_http_200
```

预期：FAIL，找不到 `KnowledgeOpsRepairApplyClient`。

- [ ] **步骤 3：编写最少实现（clients.py + config.py）**

在 `services/repair_agent/app/clients.py` 增加：

```python
import asyncio

from contracts.models import KnowledgeRepairSuggestionRequest


class KnowledgeOpsRepairApplyClient:
    def __init__(self, apply_url: str, timeout_seconds: float) -> None:
        self.apply_url = apply_url
        self.timeout_seconds = timeout_seconds

    async def apply(self, payload: KnowledgeRepairSuggestionRequest) -> None:
        attempt = 0
        while True:
            attempt += 1
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(self.apply_url, json=payload.model_dump())
            if response.status_code == 200:
                return
            await asyncio.sleep(min(2 ** min(attempt, 6), 30))
```

在 `services/repair_agent/app/config.py` 增加：

```python
    knowledge_ops_repairs_apply_url: str = "http://127.0.0.1:8003/api/knowledge/repairs/apply"
```

- [ ] **步骤 4：运行测试验证通过**

```bash
python -m pytest -q tests/test_krss_contract_and_retry.py::test_knowledge_ops_apply_retries_until_http_200
```

预期：PASS。

- [ ] **步骤 5：Commit**

```bash
git add services/repair_agent/app/clients.py services/repair_agent/app/config.py tests/test_krss_contract_and_retry.py
git commit -m "feat(修复建议): 新增 Knowledge Ops apply 重试客户端"
```

---

## 任务 4：实现 LLM 主导的 Prompt Gap Analysis（glm-5）

**文件：**
- 创建：`/Users/mangowmac/Desktop/code/NL2Cypher/services/repair_agent/app/analysis.py`
- 修改：`/Users/mangowmac/Desktop/code/NL2Cypher/services/repair_agent/app/clients.py`
- 测试：`/Users/mangowmac/Desktop/code/NL2Cypher/tests/test_krss_analysis_llm_first.py`

- [ ] **步骤 1：编写失败的测试（LLM 输出结构化 knowledge_types + suggestion_outline）**

```python
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from contracts.models import IssueTicket
from services.repair_agent.app.analysis import KRSSAnalyzer


@pytest.mark.asyncio
async def test_llm_first_analysis_returns_types_and_suggestion_prompt():
    llm = AsyncMock()
    llm.diagnose.return_value = {
        "knowledge_types": ["business_knowledge", "few-shot"],
        "rationale": "protocol version mapping missing; weak model needs examples",
        "suggestion_points": [
            {"type": "business_knowledge", "items": ["补充协议版本映射表：v1->xxx，v2->yyy"]},
            {"type": "few-shot", "items": ["新增 2 条协议相关查询 few-shot，覆盖过滤与返回字段"]},
        ],
    }

    analyzer = KRSSAnalyzer(llm_analyzer=llm)
    ticket = IssueTicket(
        ticket_id="t1",
        id="q_001",
        difficulty="L1",
        question="查询协议版本对应的隧道",
        expected={"cypher": "MATCH (n) RETURN n", "answer": {"rows": []}},
        actual={"generated_cypher": "MATCH (n) RETURN n", "execution": {"rows": []}},
        evaluation={
            "dimensions": {
                "syntax_validity": "pass",
                "schema_alignment": "pass",
                "question_alignment": "fail",
                "result_correctness": "fail",
            },
            "symptom": "wrong mapping",
            "evidence": ["Protocol version mapping missing"],
        },
        input_prompt_snapshot="PROMPT",
    )
    result = await analyzer.analyze(ticket=ticket, prompt_snapshot="PROMPT")
    assert result.knowledge_types == ["business_knowledge", "few-shot"]
    assert "Protocol version mapping missing" in result.suggestion
```

- [ ] **步骤 2：运行测试验证失败**

```bash
python -m pytest -q tests/test_krss_analysis_llm_first.py
```

预期：FAIL，找不到 `services.repair_agent.app.analysis` 或 `KRSSAnalyzer`。

- [ ] **步骤 3：编写最少实现（analysis.py + clients.py）**

在 `services/repair_agent/app/analysis.py` 创建：

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Protocol

from contracts.models import IssueTicket, KnowledgeRepairSuggestionRequest, KnowledgeType


@dataclass(frozen=True)
class KRSSAnalysisResult:
    knowledge_types: List[KnowledgeType]
    suggestion: str


class LLMAnalyzer(Protocol):
    async def diagnose(self, ticket: IssueTicket, prompt_snapshot: str) -> dict: ...


class KRSSAnalyzer:
    def __init__(self, llm_analyzer: LLMAnalyzer) -> None:
        self.llm_analyzer = llm_analyzer

    async def analyze(self, ticket: IssueTicket, prompt_snapshot: str) -> KRSSAnalysisResult:
        diagnosis = await self.llm_analyzer.diagnose(ticket=ticket, prompt_snapshot=prompt_snapshot)
        knowledge_types = diagnosis["knowledge_types"]
        evidence = list(ticket.evaluation.evidence)
        suggestion = (
            "你是知识运营服务（Knowledge Ops）的执行模型。请根据以下失败信息产出可落地的知识资产修复产物。\n\n"
            f"id: {ticket.id}\n"
            f"question: {ticket.question}\n"
            f"input_prompt_snapshot: {prompt_snapshot}\n"
            f"generated_cypher: {ticket.actual.generated_cypher}\n"
            f"evidence: {evidence}\n\n"
            f"knowledge_types: {knowledge_types}\n"
            f"suggestion_points: {diagnosis.get('suggestion_points', [])}\n"
            "请按 knowledge_types 分组输出需要新增/修改的具体内容。"
        )
        return KRSSAnalysisResult(knowledge_types=knowledge_types, suggestion=suggestion)
```

在 `services/repair_agent/app/clients.py` 增加一个 LLM 适配器（glm-5，OpenAI-compatible）：

```python
class OpenAICompatibleKRSSAnalyzer:
    def __init__(self, base_url: str, api_key: str, model: str, timeout_seconds: float, temperature: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.temperature = temperature

    async def diagnose(self, ticket: IssueTicket, prompt_snapshot: str) -> Dict[str, Any]:
        system_prompt = (
            "You are diagnosing a weak Text2Cypher generator. "
            "Given the original prompt snapshot and failure evidence, "
            "decide which knowledge_types are missing and propose concrete suggestion points. "
            'Return JSON only: {"knowledge_types": [...], "rationale": "...", "suggestion_points": [...], "need_experiments": true|false}'
        )
        user_prompt = f"IssueTicket: {ticket.model_dump_json()}\nPromptSnapshot: {prompt_snapshot}\n"
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json={
                    "model": self.model,
                    "temperature": self.temperature,
                    "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
                },
            )
            response.raise_for_status()
            payload = response.json()
        content = payload["choices"][0]["message"]["content"].strip()
        if content.startswith("```"):
            content = content.strip("`")
            if content.startswith("json"):
                content = content[4:].strip()
        return json.loads(content)
```

- [ ] **步骤 4：运行测试验证通过**

```bash
python -m pytest -q tests/test_krss_analysis_llm_first.py
```

预期：PASS。

- [ ] **步骤 5：Commit**

```bash
git add services/repair_agent/app/analysis.py services/repair_agent/app/clients.py tests/test_krss_analysis_llm_first.py
git commit -m "feat(修复建议): 引入 LLM 主导的缺口诊断"
```

---

## 任务 5：加入“类型级最小补丁对照实验”（不从 Knowledge Ops 拉包）

**文件：**
- 修改：`/Users/mangowmac/Desktop/code/NL2Cypher/services/repair_agent/app/analysis.py`
- 测试：`/Users/mangowmac/Desktop/code/NL2Cypher/tests/test_krss_counterfactual_min_patches.py`

- [ ] **步骤 1：编写失败的测试（当 need_experiments=true 时触发实验并收敛类型）**

```python
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from contracts.models import IssueTicket
from services.repair_agent.app.analysis import KRSSAnalyzer


@pytest.mark.asyncio
async def test_counterfactual_min_patches_adjusts_types_based_on_weak_model_response():
    llm = AsyncMock()
    llm.diagnose.return_value = {
        "knowledge_types": ["business_knowledge", "system_prompt"],
        "need_experiments": True,
        "suggestion_points": [],
    }
    weak = AsyncMock()
    weak.generate_with_patch.side_effect = [
        {"patch_type": "system_prompt", "improved": False},
        {"patch_type": "business_knowledge", "improved": True},
    ]

    analyzer = KRSSAnalyzer(llm_analyzer=llm)
    analyzer._weak_model = weak

    ticket = IssueTicket(
        ticket_id="t1",
        id="q_001",
        difficulty="L1",
        question="查询协议版本对应的隧道",
        expected={"cypher": "MATCH (n) RETURN n", "answer": {"rows": []}},
        actual={"generated_cypher": "MATCH (n) RETURN n", "execution": {"rows": []}},
        evaluation={
            "dimensions": {
                "syntax_validity": "pass",
                "schema_alignment": "pass",
                "question_alignment": "fail",
                "result_correctness": "fail",
            },
            "symptom": "wrong mapping",
            "evidence": ["Protocol version mapping missing"],
        },
        input_prompt_snapshot="PROMPT",
    )

    result = await analyzer.analyze(ticket=ticket, prompt_snapshot="PROMPT")
    assert result.knowledge_types == ["business_knowledge"]
```

- [ ] **步骤 2：运行测试验证失败**

```bash
python -m pytest -q tests/test_krss_counterfactual_min_patches.py
```

预期：FAIL，KRSSAnalyzer 尚未支持实验收敛逻辑。

- [ ] **步骤 3：编写最少实现（analysis.py）**

在 `KRSSAnalyzer.analyze()` 中加入：
- 如果 `diagnosis.get("need_experiments") is True`：
  - 让 LLM 生成每个候选类型的最小补丁片段（system_prompt/few-shot/business_knowledge/schema/cypher_syntax）
  - 用弱模型客户端对每个补丁生成一次（只看错误模式是否改善，改善定义由 LLM 给出判据）
  - 让 LLM 再读实验摘要，输出收敛后的 `knowledge_types`

实现最小可测版本（先不跑真实 TuGraph）：
- 在 analyzer 内部定义 `_run_min_patch_experiments()`，返回 `[{patch_type, improved, note}]`
- 再定义 `_refine_types_with_experiment_results()` 调用 LLM 一次做收敛

- [ ] **步骤 4：运行测试验证通过**

```bash
python -m pytest -q tests/test_krss_counterfactual_min_patches.py
```

预期：PASS。

- [ ] **步骤 5：Commit**

```bash
git add services/repair_agent/app/analysis.py tests/test_krss_counterfactual_min_patches.py
git commit -m "feat(修复建议): 支持类型级最小补丁对照实验"
```

---

## 任务 6：改造 repair_service 主流程为 KRSS（只投递 Knowledge Ops）

**文件：**
- 修改：`/Users/mangowmac/Desktop/code/NL2Cypher/services/repair_agent/app/service.py`
- 修改：`/Users/mangowmac/Desktop/code/NL2Cypher/services/repair_agent/app/main.py`
- 修改：`/Users/mangowmac/Desktop/code/NL2Cypher/services/repair_agent/app/schemas.py`
- 测试：`/Users/mangowmac/Desktop/code/NL2Cypher/tests/test_verify_communication_contract.py`（或新增 `tests/test_krss_api.py`）

- [ ] **步骤 1：编写失败的测试（API 语义：仅当 Knowledge Ops 返回 200 才返回 200）**

新增 `tests/test_krss_api.py`：

```python
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from services.repair_agent.app.main import app


def test_issue_ticket_endpoint_returns_200_only_when_apply_succeeds(monkeypatch):
    from services.repair_agent.app import service as service_module

    async def _fake_create_suggestion(_ticket):
        return {"status": "ok"}

    service_module.krss_service.create_suggestion_and_apply = AsyncMock(side_effect=_fake_create_suggestion)
    client = TestClient(app)
    resp = client.post("/api/v1/issue-tickets", json={"ticket_id": "t", "id": "q", "difficulty": "L1", "question": "x",
                                                      "expected": {"cypher": "MATCH (n) RETURN n", "answer": {"rows": []}},
                                                      "actual": {"generated_cypher": "MATCH (n) RETURN n", "execution": {"rows": []}},
                                                      "evaluation": {"dimensions": {"syntax_validity": "pass", "schema_alignment": "pass", "question_alignment": "fail", "result_correctness": "fail"},
                                                                     "symptom": "x", "evidence": []},
                                                      "input_prompt_snapshot": "PROMPT"})
    assert resp.status_code == 200
```

- [ ] **步骤 2：运行测试验证失败**

```bash
python -m pytest -q tests/test_krss_api.py
```

预期：FAIL，当前 repair_service 仍返回 RepairPlanEnvelope。

- [ ] **步骤 3：编写最少实现（service.py + main.py）**

改造要点：
- 新增 `create_suggestion_and_apply(issue_ticket)`：
  - 调用 `CGSPromptSnapshotClient.fetch_prompt_snapshot(id)`（以 CGS 为事实来源）
  - 调用 `KRSSAnalyzer.analyze(ticket, prompt_snapshot)`
  - 组装 `KnowledgeRepairSuggestionRequest(id, suggestion, knowledge_types)`
  - 调用 `KnowledgeOpsRepairApplyClient.apply(payload)`（重试直至 200）
- `POST /api/v1/issue-tickets` 的 response_model 改为最小对象（如 `{status: "ok"}`）

- [ ] **步骤 4：运行测试验证通过**

```bash
python -m pytest -q tests/test_krss_api.py
```

预期：PASS。

- [ ] **步骤 5：运行全量测试**

```bash
python -m pytest -q
```

预期：全绿。

- [ ] **步骤 6：Commit**

```bash
git add services/repair_agent/app/main.py services/repair_agent/app/service.py services/repair_agent/app/schemas.py tests/test_krss_api.py
git commit -m "refactor(修复建议): 将 repair_service 主流程重构为 KRSS 单一投递"
```

---

## 自检清单（执行本计划前/后都要过）

1. `knowledge_types` 的字符串是否全仓库一致：`schema/cypher_syntax/few-shot/system_prompt/business_knowledge`
2. payload 是否严格 3 字段（不包含 evidence/confidence/root_cause）
3. 成功语义是否严格以 Knowledge Ops HTTP 200 判定，且 200 后不落库
4. 重试是否只在投递层发生，不会重复调用 LLM 导致成本爆炸
5. KRSS 仅使用 CGS 的 `prompt_snapshot` 作为“当时输入事实”，不从 Knowledge Ops 拉“补丁包”进行实验

---

## 执行方式选择

计划已完成并保存到 `docs/superpowers/plans/2026-04-13-krss-analysis-v2.md`。两种执行方式：

1. **子代理驱动（推荐）**：使用 superpowers:subagent-driven-development，每个任务派一个子代理实现 + 两阶段审查。
2. **内联执行**：使用 superpowers:executing-plans，在当前会话逐任务实现并设检查点。

你希望选哪种？
