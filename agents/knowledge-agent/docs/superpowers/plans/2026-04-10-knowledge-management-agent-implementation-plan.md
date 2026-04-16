# Knowledge Management Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standalone `knowledge-agent` service that exposes prompt retrieval and repair-apply APIs for Text2Cypher, outputs a final RAG-style prompt string, and maintains four editable knowledge documents with automatic minimal-patch updates.

**Architecture:** Create a small FastAPI service under `knowledge-agent` that mirrors the `qa-agent` layout for config and HTTP entrypoints, but owns its own domain modules for prompt assembly, knowledge retrieval, repair routing, and markdown document storage. Keep `schema` read-only from an internal JSON source, and let only `cypher_syntax`, `few_shot`, `system_prompt`, and `business_knowledge` participate in automatic repair writes.

**Tech Stack:** Python, FastAPI, Pydantic, httpx, python-dotenv, unittest

---

### Task 1: Scaffold Standalone Service Layout

**Files:**
- Create: `knowledge-agent/requirements.txt`
- Create: `knowledge-agent/run_api.py`
- Create: `knowledge-agent/app/__init__.py`
- Create: `knowledge-agent/app/config.py`
- Create: `knowledge-agent/app/errors.py`
- Create: `knowledge-agent/app/entrypoints/__init__.py`
- Create: `knowledge-agent/app/entrypoints/api/__init__.py`
- Create: `knowledge-agent/app/entrypoints/api/main.py`
- Create: `knowledge-agent/app/domain/__init__.py`
- Create: `knowledge-agent/tests/test_health.py`

- [ ] **Step 1: Write the failing health/API smoke test**

```python
import unittest
from fastapi.testclient import TestClient

from app.entrypoints.api.main import app


class HealthApiTest(unittest.TestCase):
    def test_health_returns_ok(self) -> None:
        client = TestClient(app)

        response = client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/wangxinhao/muti-agent-offline-system/knowledge-agent && python -m unittest tests.test_health -v`

Expected: FAIL with `ModuleNotFoundError` because the standalone service files do not exist yet.

- [ ] **Step 3: Create the minimal service skeleton modeled after `qa-agent`**

```python
# knowledge-agent/app/config.py
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


ROOT_DIR = Path(__file__).resolve().parents[1]
ARTIFACTS_DIR = ROOT_DIR / "artifacts"
KNOWLEDGE_DIR = ROOT_DIR / "knowledge"
ENV_FILE = ROOT_DIR / ".env"

load_dotenv(ENV_FILE)


@dataclass(frozen=True)
class Settings:
    app_name: str = "knowledge-agent"
    artifacts_dir: Path = ARTIFACTS_DIR
    knowledge_dir: Path = KNOWLEDGE_DIR
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_base_url: str = os.getenv("OPENAI_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")
    openai_model: str = os.getenv("OPENAI_MODEL", "glm-5")
    host: str = os.getenv("APP_HOST", "127.0.0.1")
    port: int = int(os.getenv("APP_PORT", "8010"))


settings = Settings()
```

```python
# knowledge-agent/app/entrypoints/api/main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


app = FastAPI(title="Knowledge Agent")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
```

```python
# knowledge-agent/run_api.py
from app.config import settings
from app.entrypoints.api.main import app


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=settings.host, port=settings.port)
```

- [ ] **Step 4: Run the health test to verify it passes**

Run: `cd /Users/wangxinhao/muti-agent-offline-system/knowledge-agent && python -m unittest tests.test_health -v`

Expected: PASS with one test passing.

- [ ] **Step 5: Record dependency baseline**

Create `knowledge-agent/requirements.txt` with:

```text
fastapi>=0.110,<1.0
uvicorn[standard]>=0.29,<1.0
pydantic>=2.6,<3.0
httpx>=0.27,<1.0
python-dotenv>=1.0,<2.0
```

- [ ] **Step 6: Commit**

Git is not initialized under `/Users/wangxinhao/muti-agent-offline-system`, so skip commit until the workspace is a repository.

### Task 2: Define API Contracts And Domain Models

**Files:**
- Modify: `knowledge-agent/app/domain/__init__.py`
- Create: `knowledge-agent/app/domain/models.py`
- Modify: `knowledge-agent/app/entrypoints/api/main.py`
- Create: `knowledge-agent/tests/test_api_contracts.py`

- [ ] **Step 1: Write failing contract tests for the two POST endpoints**

```python
import unittest
from fastapi.testclient import TestClient

from app.entrypoints.api.main import app


class ApiContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_prompt_package_contract(self) -> None:
        response = self.client.post(
            "/api/knowledge/rag/prompt-package",
            json={"id": "q_001", "question": "查询协议版本为v2.0的隧道所属网元"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        self.assertEqual(response.json()["id"], "q_001")
        self.assertIn("prompt", response.json())

    def test_apply_repair_contract(self) -> None:
        response = self.client.post(
            "/api/knowledge/repairs/apply",
            json={"id": "q_001", "suggestion": "补充协议版本映射"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/wangxinhao/muti-agent-offline-system/knowledge-agent && python -m unittest tests.test_api_contracts -v`

Expected: FAIL with `404` for missing endpoints.

- [ ] **Step 3: Add request/response models and endpoint stubs**

```python
# knowledge-agent/app/domain/models.py
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


KnowledgeType = Literal["cypher_syntax", "few_shot", "system_prompt", "business_knowledge"]


class PromptPackageRequest(BaseModel):
    id: str = Field(min_length=1)
    question: str = Field(min_length=1)


class PromptPackageResponse(BaseModel):
    status: Literal["ok"]
    id: str
    prompt: str


class ApplyRepairRequest(BaseModel):
    id: str = Field(min_length=1)
    suggestion: str = Field(min_length=1)
    knowledge_types: Optional[list[KnowledgeType]] = None


class StatusResponse(BaseModel):
    status: Literal["ok"]
```

```python
# knowledge-agent/app/entrypoints/api/main.py
from app.domain.models import ApplyRepairRequest, PromptPackageRequest, PromptPackageResponse, StatusResponse


@app.post("/api/knowledge/rag/prompt-package", response_model=PromptPackageResponse)
def build_prompt_package(request: PromptPackageRequest) -> PromptPackageResponse:
    return PromptPackageResponse(status="ok", id=request.id, prompt="")


@app.post("/api/knowledge/repairs/apply", response_model=StatusResponse)
def apply_repair(request: ApplyRepairRequest) -> StatusResponse:
    return StatusResponse(status="ok")
```

- [ ] **Step 4: Run tests to verify the contracts pass with stubs**

Run: `cd /Users/wangxinhao/muti-agent-offline-system/knowledge-agent && python -m unittest tests.test_api_contracts tests.test_health -v`

Expected: PASS with three tests passing, even though prompt content is still empty.

- [ ] **Step 5: Commit**

Git is not initialized under `/Users/wangxinhao/muti-agent-offline-system`, so skip commit until the workspace is a repository.

### Task 3: Add Knowledge Sources And Prompt Assembly

**Files:**
- Create: `knowledge-agent/knowledge/schema.json`
- Create: `knowledge-agent/knowledge/cypher_syntax.md`
- Create: `knowledge-agent/knowledge/few_shot.md`
- Create: `knowledge-agent/knowledge/system_prompt.md`
- Create: `knowledge-agent/knowledge/business_knowledge.md`
- Create: `knowledge-agent/app/storage/__init__.py`
- Create: `knowledge-agent/app/storage/knowledge_store.py`
- Create: `knowledge-agent/app/domain/knowledge/__init__.py`
- Create: `knowledge-agent/app/domain/knowledge/schema_formatter.py`
- Create: `knowledge-agent/app/domain/knowledge/retriever.py`
- Create: `knowledge-agent/app/domain/knowledge/prompt_service.py`
- Modify: `knowledge-agent/app/entrypoints/api/main.py`
- Create: `knowledge-agent/tests/test_prompt_service.py`

- [ ] **Step 1: Write failing tests for prompt content and section order**

```python
import unittest

from app.domain.knowledge.prompt_service import PromptService
from app.storage.knowledge_store import KnowledgeStore


class PromptServiceTest(unittest.TestCase):
    def test_build_prompt_includes_required_sections(self) -> None:
        service = PromptService(KnowledgeStore())

        prompt = service.build_prompt("查询协议版本为v2.0的隧道所属网元")

        self.assertIn("你是一个严格的 TuGraph Text2Cypher 生成器。", prompt)
        self.assertIn("【Schema】", prompt)
        self.assertIn("【TuGraph Cypher 语法约束】", prompt)
        self.assertIn("【业务知识】", prompt)
        self.assertIn("【参考示例】", prompt)
        self.assertIn("【生成要求】", prompt)
        self.assertTrue(prompt.rstrip().endswith("查询协议版本为v2.0的隧道所属网元"))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/wangxinhao/muti-agent-offline-system/knowledge-agent && python -m unittest tests.test_prompt_service -v`

Expected: FAIL with `ModuleNotFoundError` for the knowledge domain modules.

- [ ] **Step 3: Create the read-only schema source and markdown knowledge files**

Use these starter contents:

```json
{
  "vertex_labels": [
    {"name": "NetworkElement", "properties": ["id", "name"]},
    {"name": "Port", "properties": ["id", "name", "speed", "status"]},
    {"name": "Tunnel", "properties": ["id", "name", "latency", "bandwidth"]},
    {"name": "Protocol", "properties": ["version", "name"]}
  ],
  "edge_labels": [
    {"name": "HAS_PORT", "from": "NetworkElement", "to": "Port"},
    {"name": "FIBER_SRC", "from": "NetworkElement", "to": "Tunnel"},
    {"name": "TUNNEL_PROTO", "from": "Tunnel", "to": "Protocol"}
  ]
}
```

```md
## Core Rules

[id: syntax_direction_rule]
- 优先使用 schema 中定义的显式方向，不要依赖双向匹配。
```

```md
## Reference Examples

[id: tunnel_protocol_version]
Question: 查询协议版本为v2.0的隧道
Cypher: MATCH (t:Tunnel)-[:TUNNEL_PROTO]->(p:Protocol) WHERE p.version = 'v2.0' RETURN t.id, t.name
Why: 展示协议版本过滤路径。
```

```md
## Core Rules

[id: role_definition]
- 你是一个严格的 TuGraph Text2Cypher 生成器。
```

```md
## Terminology Mapping

[id: protocol_version_mapping]
- “协议版本”优先映射到 `Protocol.version`。
```

- [ ] **Step 4: Implement minimal file readers, schema formatting, and prompt assembly**

```python
# knowledge-agent/app/storage/knowledge_store.py
from __future__ import annotations

import json
from pathlib import Path

from app.config import settings


class KnowledgeStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or settings.knowledge_dir
        self.root.mkdir(parents=True, exist_ok=True)

    def read_text(self, name: str) -> str:
        return (self.root / name).read_text(encoding="utf-8")

    def read_schema(self) -> dict:
        return json.loads((self.root / "schema.json").read_text(encoding="utf-8"))
```

```python
# knowledge-agent/app/domain/knowledge/schema_formatter.py
def format_schema(schema: dict) -> str:
    labels = ["Labels:"]
    for item in schema.get("vertex_labels", []):
        props = ", ".join(item.get("properties", []))
        labels.append(f"- {item['name']}({props})")

    edges = ["", "Relationships:"]
    for item in schema.get("edge_labels", []):
        edges.append(f"- (:{item['from']})-[:{item['name']}]->(:{item['to']})")
    return "\n".join(labels + edges).strip()
```

```python
# knowledge-agent/app/domain/knowledge/prompt_service.py
from __future__ import annotations

from app.domain.knowledge.schema_formatter import format_schema
from app.storage.knowledge_store import KnowledgeStore


class PromptService:
    def __init__(self, store: KnowledgeStore) -> None:
        self.store = store

    def build_prompt(self, question: str) -> str:
        schema_context = format_schema(self.store.read_schema())
        syntax_context = self.store.read_text("cypher_syntax.md").strip()
        few_shot = self.store.read_text("few_shot.md").strip()
        system_prompt = self.store.read_text("system_prompt.md").strip()
        business_context = self.store.read_text("business_knowledge.md").strip()
        return (
            f"{system_prompt}\n\n"
            "【Schema】\n"
            f"{schema_context}\n\n"
            "【TuGraph Cypher 语法约束】\n"
            f"{syntax_context}\n\n"
            "【业务知识】\n"
            f"{business_context}\n\n"
            "【参考示例】\n"
            f"{few_shot}\n\n"
            "【生成要求】\n"
            "- 输出必须是单条 Cypher\n"
            "- 不要输出解释\n"
            "- 不要输出 Markdown\n"
            "- 确保方向、属性、过滤条件、聚合语义正确\n\n"
            "【用户问题】\n"
            f"{question}"
        )
```

- [ ] **Step 5: Wire the prompt endpoint to `PromptService`**

```python
from app.domain.knowledge.prompt_service import PromptService
from app.storage.knowledge_store import KnowledgeStore


knowledge_store = KnowledgeStore()
prompt_service = PromptService(knowledge_store)


@app.post("/api/knowledge/rag/prompt-package", response_model=PromptPackageResponse)
def build_prompt_package(request: PromptPackageRequest) -> PromptPackageResponse:
    return PromptPackageResponse(
        status="ok",
        id=request.id,
        prompt=prompt_service.build_prompt(request.question),
    )
```

- [ ] **Step 6: Run prompt tests and API contract tests**

Run: `cd /Users/wangxinhao/muti-agent-offline-system/knowledge-agent && python -m unittest tests.test_prompt_service tests.test_api_contracts tests.test_health -v`

Expected: PASS with prompt content populated and endpoint contracts still valid.

- [ ] **Step 7: Commit**

Git is not initialized under `/Users/wangxinhao/muti-agent-offline-system`, so skip commit until the workspace is a repository.

### Task 4: Add Internal Retrieval Heuristics For Syntax, Business Notes, And Few-Shot

**Files:**
- Modify: `knowledge-agent/app/domain/knowledge/retriever.py`
- Modify: `knowledge-agent/app/domain/knowledge/prompt_service.py`
- Create: `knowledge-agent/tests/test_retriever.py`

- [ ] **Step 1: Write failing tests for question-aware retrieval**

```python
import unittest

from app.domain.knowledge.retriever import KnowledgeRetriever
from app.storage.knowledge_store import KnowledgeStore


class KnowledgeRetrieverTest(unittest.TestCase):
    def test_retrieves_protocol_related_notes(self) -> None:
        retriever = KnowledgeRetriever(KnowledgeStore())

        bundle = retriever.retrieve("查询协议版本为v2.0的隧道所属网元")

        self.assertIn("Protocol.version", bundle["business_context"])
        self.assertIn("TUNNEL_PROTO", bundle["few_shot_examples"])
```

- [ ] **Step 2: Run the retrieval test to verify it fails**

Run: `cd /Users/wangxinhao/muti-agent-offline-system/knowledge-agent && python -m unittest tests.test_retriever -v`

Expected: FAIL because retrieval is still returning full raw files or no retriever exists.

- [ ] **Step 3: Implement a minimal deterministic retriever before any embedding work**

```python
# knowledge-agent/app/domain/knowledge/retriever.py
from __future__ import annotations

from app.domain.knowledge.schema_formatter import format_schema
from app.storage.knowledge_store import KnowledgeStore


class KnowledgeRetriever:
    def __init__(self, store: KnowledgeStore) -> None:
        self.store = store

    def retrieve(self, question: str) -> dict[str, str]:
        lowered = question.lower()
        syntax_text = self.store.read_text("cypher_syntax.md")
        business_text = self.store.read_text("business_knowledge.md")
        few_shot_text = self.store.read_text("few_shot.md")

        if "协议" in question or "protocol" in lowered:
            business_slice = "\n".join(line for line in business_text.splitlines() if "协议" in line or "Protocol" in line)
            few_shot_slice = "\n".join(line for line in few_shot_text.splitlines() if "协议" in line or "TUNNEL_PROTO" in line or line.startswith("Question:") or line.startswith("Cypher:"))
        else:
            business_slice = business_text
            few_shot_slice = few_shot_text

        return {
            "schema_context": format_schema(self.store.read_schema()),
            "syntax_context": syntax_text.strip(),
            "business_context": business_slice.strip() or business_text.strip(),
            "few_shot_examples": few_shot_slice.strip() or few_shot_text.strip(),
            "system_prompt": self.store.read_text("system_prompt.md").strip(),
        }
```

- [ ] **Step 4: Update `PromptService` to consume the retrieval bundle**

```python
from app.domain.knowledge.retriever import KnowledgeRetriever


class PromptService:
    def __init__(self, store: KnowledgeStore) -> None:
        self.retriever = KnowledgeRetriever(store)

    def build_prompt(self, question: str) -> str:
        bundle = self.retriever.retrieve(question)
        return (
            f"{bundle['system_prompt']}\n\n"
            "【Schema】\n"
            f"{bundle['schema_context']}\n\n"
            "【TuGraph Cypher 语法约束】\n"
            f"{bundle['syntax_context']}\n\n"
            "【业务知识】\n"
            f"{bundle['business_context']}\n\n"
            "【参考示例】\n"
            f"{bundle['few_shot_examples']}\n\n"
            "【生成要求】\n"
            "- 输出必须是单条 Cypher\n"
            "- 不要输出解释\n"
            "- 不要输出 Markdown\n"
            "- 确保方向、属性、过滤条件、聚合语义正确\n\n"
            "【用户问题】\n"
            f"{question}"
        )
```

- [ ] **Step 5: Run retrieval and prompt tests**

Run: `cd /Users/wangxinhao/muti-agent-offline-system/knowledge-agent && python -m unittest tests.test_retriever tests.test_prompt_service -v`

Expected: PASS with question-aware slices present in the prompt.

- [ ] **Step 6: Commit**

Git is not initialized under `/Users/wangxinhao/muti-agent-offline-system`, so skip commit until the workspace is a repository.

### Task 5: Implement Repair Routing And Minimal Markdown Patch Application

**Files:**
- Create: `knowledge-agent/app/integrations/__init__.py`
- Create: `knowledge-agent/app/integrations/openai/__init__.py`
- Create: `knowledge-agent/app/integrations/openai/model_gateway.py`
- Create: `knowledge-agent/app/domain/knowledge/repair_service.py`
- Create: `knowledge-agent/app/domain/knowledge/patcher.py`
- Modify: `knowledge-agent/app/storage/knowledge_store.py`
- Modify: `knowledge-agent/app/entrypoints/api/main.py`
- Create: `knowledge-agent/tests/test_repair_service.py`

- [ ] **Step 1: Write failing tests for explicit and inferred repair routing**

```python
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.domain.knowledge.repair_service import RepairService
from app.integrations.openai.model_gateway import ModelGateway
from app.storage.knowledge_store import KnowledgeStore


class FakeGateway(ModelGateway):
    def __init__(self) -> None:
        pass

    def generate_text(self, prompt_name, model_config, **kwargs):
        return """{
  "doc_type": "business_knowledge",
  "section": "Terminology Mapping",
  "action": "add_section_item",
  "target_key": "protocol_version_mapping_2",
  "new_content": "- “协议版本”优先映射到 `Protocol.version`。",
  "reason": "补充业务语义映射"
}"""


class RepairServiceTest(unittest.TestCase):
    def test_apply_repair_updates_target_document(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            store = KnowledgeStore(Path(tmp_dir))
            store.bootstrap_defaults()
            service = RepairService(store, FakeGateway())

            service.apply("补充协议版本映射", ["business_knowledge"])

            content = store.read_text("business_knowledge.md")
            self.assertIn("protocol_version_mapping_2", content)
```

- [ ] **Step 2: Run the repair tests to verify they fail**

Run: `cd /Users/wangxinhao/muti-agent-offline-system/knowledge-agent && python -m unittest tests.test_repair_service -v`

Expected: FAIL because repair modules and bootstrap helpers do not exist yet.

- [ ] **Step 3: Port a minimal model gateway from `qa-agent` for patch generation**

```python
# knowledge-agent/app/integrations/openai/model_gateway.py
from __future__ import annotations

import time

import httpx

from app.config import settings
from app.errors import AppError


class ModelGateway:
    def __init__(self) -> None:
        self._client = httpx.Client(timeout=60)

    def generate_text(self, prompt_name: str, model_config: dict, **kwargs) -> str:
        if not settings.openai_api_key:
            raise AppError("OPENAI_NOT_CONFIGURED", "Set OPENAI_API_KEY before applying repair suggestions.")
        prompt = kwargs["prompt"]
        response = self._client.post(
            f"{settings.openai_base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {settings.openai_api_key}", "Content-Type": "application/json"},
            json={
                "model": model_config.get("model", settings.openai_model),
                "messages": [{"role": "user", "content": prompt}],
                "thinking": {"type": "disabled"},
                "temperature": model_config.get("temperature", 0.1),
                "max_tokens": model_config.get("max_output_tokens", 800),
            },
        )
        try:
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise AppError("OPENAI_REQUEST_ERROR", str(exc)) from exc
        return response.json()["choices"][0]["message"]["content"].strip()
```

- [ ] **Step 4: Implement `KnowledgeStore.bootstrap_defaults`, rollback snapshots, and a minimal markdown patcher**

```python
# knowledge-agent/app/storage/knowledge_store.py
def bootstrap_defaults(self) -> None:
    self.root.mkdir(parents=True, exist_ok=True)
    defaults = {
        "cypher_syntax.md": "## Core Rules\n\n[id: syntax_direction_rule]\n- 优先使用 schema 中定义的显式方向，不要依赖双向匹配。\n",
        "few_shot.md": "## Reference Examples\n\n[id: tunnel_protocol_version]\nQuestion: 查询协议版本为v2.0的隧道\nCypher: MATCH (t:Tunnel)-[:TUNNEL_PROTO]->(p:Protocol) WHERE p.version = 'v2.0' RETURN t.id, t.name\nWhy: 展示协议版本过滤路径。\n",
        "system_prompt.md": "## Core Rules\n\n[id: role_definition]\n- 你是一个严格的 TuGraph Text2Cypher 生成器。\n",
        "business_knowledge.md": "## Terminology Mapping\n\n[id: protocol_version_mapping]\n- “协议版本”优先映射到 `Protocol.version`。\n",
    }
    for name, content in defaults.items():
        path = self.root / name
        if not path.exists():
            path.write_text(content, encoding="utf-8")
```

```python
# knowledge-agent/app/domain/knowledge/patcher.py
def append_item_under_section(document: str, section: str, item: str) -> str:
    marker = f"## {section}"
    if marker not in document:
        suffix = f"\n\n## {section}\n\n{item}\n"
        return f"{document.rstrip()}{suffix}"
    head, tail = document.split(marker, 1)
    section_block = f"{marker}{tail}"
    return f"{head}{section_block.rstrip()}\n\n{item}\n"
```

- [ ] **Step 5: Implement repair routing and endpoint wiring**

```python
# knowledge-agent/app/domain/knowledge/repair_service.py
from __future__ import annotations

import json

from app.domain.knowledge.patcher import append_item_under_section


DOC_FILE_MAP = {
    "cypher_syntax": "cypher_syntax.md",
    "few_shot": "few_shot.md",
    "system_prompt": "system_prompt.md",
    "business_knowledge": "business_knowledge.md",
}


class RepairService:
    def __init__(self, store, model_gateway) -> None:
        self.store = store
        self.model_gateway = model_gateway

    def apply(self, suggestion: str, knowledge_types: list[str] | None) -> None:
        target_types = knowledge_types or self._infer_types(suggestion)
        target_type = target_types[0]
        patch = json.loads(
            self.model_gateway.generate_text(
                "repair_patch",
                {"model": "glm-5", "temperature": 0.1, "max_output_tokens": 800},
                prompt=f"根据建议生成最小 patch JSON。\n建议：{suggestion}\n目标知识类型：{target_type}",
            )
        )
        filename = DOC_FILE_MAP[target_type]
        before = self.store.read_text(filename)
        after = append_item_under_section(before, patch["section"], f"[id: {patch['target_key']}]\n{patch['new_content']}")
        self.store.write_versioned(filename, before, after, suggestion, target_type)

    def _infer_types(self, suggestion: str) -> list[str]:
        if "术语" in suggestion or "映射" in suggestion or "语义" in suggestion:
            return ["business_knowledge"]
        if "示例" in suggestion or "few-shot" in suggestion:
            return ["few_shot"]
        if "提示词" in suggestion or "规则" in suggestion:
            return ["system_prompt"]
        return ["cypher_syntax"]
```

```python
# knowledge-agent/app/entrypoints/api/main.py
from app.domain.knowledge.repair_service import RepairService
from app.integrations.openai.model_gateway import ModelGateway


repair_service = RepairService(knowledge_store, ModelGateway())


@app.post("/api/knowledge/repairs/apply", response_model=StatusResponse)
def apply_repair(request: ApplyRepairRequest) -> StatusResponse:
    repair_service.apply(request.suggestion, request.knowledge_types)
    return StatusResponse(status="ok")
```

- [ ] **Step 6: Run repair tests and the full suite**

Run: `cd /Users/wangxinhao/muti-agent-offline-system/knowledge-agent && python -m unittest tests.test_repair_service tests.test_retriever tests.test_prompt_service tests.test_api_contracts tests.test_health -v`

Expected: PASS with repair writes updating only the target markdown file and both APIs still succeeding.

- [ ] **Step 7: Commit**

Git is not initialized under `/Users/wangxinhao/muti-agent-offline-system`, so skip commit until the workspace is a repository.

### Task 6: Add Write History And Bad-Update Recovery Evidence

**Files:**
- Modify: `knowledge-agent/app/storage/knowledge_store.py`
- Create: `knowledge-agent/tests/test_knowledge_store.py`

- [ ] **Step 1: Write failing tests for version snapshots**

```python
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.storage.knowledge_store import KnowledgeStore


class KnowledgeStoreTest(unittest.TestCase):
    def test_write_versioned_saves_snapshot_record(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            store = KnowledgeStore(Path(tmp_dir))
            store.bootstrap_defaults()
            before = store.read_text("business_knowledge.md")
            after = before + "\n[id: extra]\n- extra\n"

            store.write_versioned("business_knowledge.md", before, after, "补充一条规则", "business_knowledge")

            history = list((Path(tmp_dir) / "_history").glob("*.json"))
            self.assertTrue(history)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/wangxinhao/muti-agent-offline-system/knowledge-agent && python -m unittest tests.test_knowledge_store -v`

Expected: FAIL because `_history` support does not exist yet.

- [ ] **Step 3: Implement versioned writes with JSON snapshot metadata**

```python
# knowledge-agent/app/storage/knowledge_store.py
from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_versioned(self, filename: str, before: str, after: str, suggestion: str, target_type: str) -> None:
    history_dir = self.root / "_history"
    history_dir.mkdir(parents=True, exist_ok=True)
    (self.root / filename).write_text(after, encoding="utf-8")
    snapshot = {
        "id": f"chg_{uuid4().hex[:12]}",
        "filename": filename,
        "target_type": target_type,
        "suggestion": suggestion,
        "created_at": _utc_now(),
        "before": before,
        "after": after,
    }
    (history_dir / f"{snapshot['id']}.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
```

- [ ] **Step 4: Run storage and full tests**

Run: `cd /Users/wangxinhao/muti-agent-offline-system/knowledge-agent && python -m unittest tests.test_knowledge_store tests.test_repair_service tests.test_retriever tests.test_prompt_service tests.test_api_contracts tests.test_health -v`

Expected: PASS with snapshot files created under `knowledge/_history` or the temporary test directory.

- [ ] **Step 5: Commit**

Git is not initialized under `/Users/wangxinhao/muti-agent-offline-system`, so skip commit until the workspace is a repository.

