# System Integration Console Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a bilingual Testing Service-hosted system integration console with `架构总览（Architecture Overview）` and `系统联调（System Integration Console）` tabs, plus a formal runtime architecture document.

**Architecture:** Keep Testing Service as the UI host and add only lightweight orchestration/aggregation endpoints where cross-service data is needed. Reuse existing CGS, Testing Service, and KRSS contracts wherever possible so the console reflects the real running system rather than a mock-only flow.

**Tech Stack:** FastAPI, vanilla HTML/CSS/JavaScript, Pydantic models, pytest

---

## File Map

- Modify: `/Users/mangowmac/Desktop/code/NL2Cypher/services/testing_agent/app/main.py`
  - Add console-support API endpoints for architecture snapshot and system integration runs.
- Modify: `/Users/mangowmac/Desktop/code/NL2Cypher/services/testing_agent/app/service.py`
  - Add orchestration helpers that call CGS and package data for the console.
- Modify: `/Users/mangowmac/Desktop/code/NL2Cypher/services/testing_agent/app/clients.py`
  - Add lightweight clients for CGS and service health/status reads.
- Modify: `/Users/mangowmac/Desktop/code/NL2Cypher/services/testing_agent/app/repository.py`
  - Add helpers to read stored evaluation/submission/issue/KRSS data for console aggregation.
- Modify: `/Users/mangowmac/Desktop/code/NL2Cypher/services/testing_agent/app/ui/index.html`
  - Replace the single-purpose evaluator console with the two-tab bilingual workspace.
- Modify: `/Users/mangowmac/Desktop/code/NL2Cypher/services/testing_agent/app/ui/app.js`
  - Implement tab switching, architecture rendering, and end-to-end integration run interactions.
- Modify: `/Users/mangowmac/Desktop/code/NL2Cypher/services/testing_agent/app/ui/styles.css`
  - Add layout and visual styles for the dual-tab console, cards, diagrams, timeline, and payload panes.
- Create: `/Users/mangowmac/Desktop/code/NL2Cypher/console/runtime_console/docs/System_Runtime_Architecture.md`
  - Formal bilingual runtime architecture reference.
- Test: `/Users/mangowmac/Desktop/code/NL2Cypher/tests/test_testing_service_console_api.py`
  - Cover architecture snapshot endpoint and orchestration endpoint.
- Modify: `/Users/mangowmac/Desktop/code/NL2Cypher/tests/test_testing_service_llm_eval.py`
  - Add or adjust service-level tests for new orchestration helpers if needed.

---

### Task 1: Add Console Runtime APIs And Orchestration

**Files:**
- Modify: `/Users/mangowmac/Desktop/code/NL2Cypher/services/testing_agent/app/clients.py`
- Modify: `/Users/mangowmac/Desktop/code/NL2Cypher/services/testing_agent/app/repository.py`
- Modify: `/Users/mangowmac/Desktop/code/NL2Cypher/services/testing_agent/app/service.py`
- Modify: `/Users/mangowmac/Desktop/code/NL2Cypher/services/testing_agent/app/main.py`
- Test: `/Users/mangowmac/Desktop/code/NL2Cypher/tests/test_testing_service_console_api.py`

- [ ] **Step 1: Write the failing API tests for architecture snapshot and integration run orchestration**

```python
from fastapi.testclient import TestClient

from services.testing_agent.app.main import app


def test_runtime_architecture_endpoint_returns_service_cards(monkeypatch):
    client = TestClient(app)

    response = client.get("/api/v1/runtime/architecture")

    assert response.status_code == 200
    payload = response.json()
    assert payload["title_zh"] == "系统运行架构"
    assert any(service["service_key"] == "cgs" for service in payload["services"])
    assert any(link["source"] == "testing_service" and link["target"] == "krss" for link in payload["links"])


def test_console_run_failure_path_returns_aggregated_trace(monkeypatch):
    client = TestClient(app)

    response = client.post(
        "/api/v1/runtime/console-runs",
        json={"id": "qa-console-fail", "question": "查询失败样例", "run_mode": "failure_path"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["run_mode"] == "failure_path"
    assert payload["stages"]["query_generation"]["status"] in {"success", "failed"}
    assert "knowledge_repair" in payload["artifacts"]
```

- [ ] **Step 2: Run the new tests to verify they fail for the right reason**

Run: `python -m pytest -q tests/test_testing_service_console_api.py`

Expected: FAIL because the new `/api/v1/runtime/architecture` and `/api/v1/runtime/console-runs` endpoints do not exist yet.

- [ ] **Step 3: Add minimal console clients for CGS and service health reads**

```python
class QueryGeneratorConsoleClient:
    def __init__(self, base_url: str, timeout_seconds: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    async def submit_question(self, *, id: str, question: str) -> Dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                f"{self.base_url}/api/v1/qa/questions",
                json={"id": id, "question": question},
            )
            response.raise_for_status()
            return response.json()

    async def get_question_run(self, id: str) -> Dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(f"{self.base_url}/api/v1/questions/{id}")
            response.raise_for_status()
            return response.json()


class ServiceHealthClient:
    async def read_health(self, base_url: str, timeout_seconds: float) -> Dict[str, Any]:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.get(f"{base_url.rstrip('/')}/health")
            response.raise_for_status()
            return response.json()
```

- [ ] **Step 4: Add repository read helpers for stored submission, issue, and KRSS response aggregation**

```python
    def get_submission_snapshot(self, id: str) -> Optional[Dict[str, Any]]:
        return self.get_submission(id)

    def get_issue_snapshot_by_submission_id(self, id: str) -> Optional[Dict[str, Any]]:
        submission = self.get_submission(id)
        if submission is None or not submission.get("issue_ticket_id"):
            return None
        ticket = self.get_issue_ticket(submission["issue_ticket_id"])
        return None if ticket is None else ticket.model_dump(mode="json")
```

- [ ] **Step 5: Add orchestration helpers to EvaluationService**

```python
    async def get_runtime_architecture(self) -> Dict[str, object]:
        return {
            "title_zh": "系统运行架构",
            "title_en": "System Runtime Architecture",
            "services": [...],
            "links": [...],
            "data_objects": [...],
        }

    async def run_console_flow(self, *, id: str, question: str, run_mode: str) -> Dict[str, object]:
        if run_mode == "success_path":
            self.repository.save_golden(
                QAGoldenRequest(
                    id=id,
                    cypher="MATCH (n:NetworkElement) RETURN n.id AS id, n.name AS name LIMIT 20",
                    answer=[{"id": "ne-1", "name": "edge-router-1"}],
                    difficulty="L3",
                )
            )
        else:
            self.repository.save_golden(
                QAGoldenRequest(
                    id=id,
                    cypher="MATCH (n:NetworkElement) RETURN n.id AS id, n.name AS name LIMIT 20",
                    answer=[{"id": "non-matching-id", "name": "golden-only-device"}],
                    difficulty="L3",
                )
            )

        generation = await self.console_query_client.submit_question(id=id, question=question)
        submission = self.repository.get_submission_snapshot(id)
        issue = self.repository.get_issue_snapshot_by_submission_id(id)

        return {
            "id": id,
            "question": question,
            "run_mode": run_mode,
            "generation": generation,
            "submission": submission,
            "issue_ticket": issue,
        }
```

- [ ] **Step 6: Wire the new FastAPI endpoints**

```python
@app.get("/api/v1/runtime/architecture")
async def runtime_architecture() -> Dict[str, object]:
    return await validation_service.get_runtime_architecture()


@app.post("/api/v1/runtime/console-runs")
async def run_console_flow(request: Dict[str, str]) -> Dict[str, object]:
    return await validation_service.run_console_flow(
        id=request["id"],
        question=request["question"],
        run_mode=request["run_mode"],
    )
```

- [ ] **Step 7: Run the console API tests to verify they pass**

Run: `python -m pytest -q tests/test_testing_service_console_api.py`

Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add services/testing_agent/app/clients.py services/testing_agent/app/repository.py services/testing_agent/app/service.py services/testing_agent/app/main.py tests/test_testing_service_console_api.py
git commit -m "feat: add testing service console runtime APIs"
```

---

### Task 2: Build The Dual-Tab Testing Service Console UI

**Files:**
- Modify: `/Users/mangowmac/Desktop/code/NL2Cypher/services/testing_agent/app/ui/index.html`
- Modify: `/Users/mangowmac/Desktop/code/NL2Cypher/services/testing_agent/app/ui/app.js`
- Modify: `/Users/mangowmac/Desktop/code/NL2Cypher/services/testing_agent/app/ui/styles.css`

- [ ] **Step 1: Write a failing UI smoke test by asserting the served HTML contains the two new tabs**

```python
def test_console_html_exposes_dual_tab_workspace():
    client = TestClient(app)

    response = client.get("/console")

    assert response.status_code == 200
    assert "架构总览" in response.text
    assert "系统联调" in response.text
```

- [ ] **Step 2: Run the single smoke test to verify it fails**

Run: `python -m pytest -q tests/test_testing_service_console_api.py::test_console_html_exposes_dual_tab_workspace`

Expected: FAIL because the current UI still renders only the old golden/evaluation form.

- [ ] **Step 3: Replace the HTML shell with a bilingual two-tab workspace**

```html
<nav class="workspace-tabs" aria-label="Console Tabs">
  <button class="workspace-tab is-active" data-tab="architecture">架构总览 Architecture Overview</button>
  <button class="workspace-tab" data-tab="integration">系统联调 System Integration Console</button>
</nav>

<section id="tab-architecture" class="tab-panel is-active">
  <section class="service-grid" id="service-grid"></section>
  <section class="diagram-grid">
    <article class="panel"><h2>系统结构图 System Structure</h2><div id="structure-diagram"></div></article>
    <article class="panel"><h2>正式数据流 Data Flow</h2><div id="data-flow-diagram"></div></article>
  </section>
</section>

<section id="tab-integration" class="tab-panel">
  <form id="console-run-form" class="panel panel-form">...</form>
  <section class="panel panel-output">...</section>
</section>
```

- [ ] **Step 4: Implement frontend state, tab switching, architecture rendering, and integration run fetching**

```javascript
async function loadArchitecture() {
  const response = await fetch("/api/v1/runtime/architecture");
  const payload = await response.json();
  renderServiceCards(payload.services);
  renderStructureDiagram(payload.links);
  renderDataObjects(payload.data_objects);
}

async function runConsoleFlow(payload) {
  const response = await fetch("/api/v1/runtime/console-runs", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  renderTimeline(data.timeline);
  renderArtifacts(data.artifacts);
}
```

- [ ] **Step 5: Add styles for tabs, cards, diagrams, timeline, and artifact panels**

```css
.workspace-tabs {
  display: inline-flex;
  gap: 10px;
  padding: 8px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.05);
}

.workspace-tab.is-active {
  background: linear-gradient(135deg, var(--secondary), #0ea5e9);
  color: #081018;
}

.tab-panel {
  display: none;
}

.tab-panel.is-active {
  display: block;
}
```

- [ ] **Step 6: Re-run the console smoke test**

Run: `python -m pytest -q tests/test_testing_service_console_api.py::test_console_html_exposes_dual_tab_workspace`

Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add services/testing_agent/app/ui/index.html services/testing_agent/app/ui/app.js services/testing_agent/app/ui/styles.css tests/test_testing_service_console_api.py
git commit -m "feat: add bilingual system integration console ui"
```

---

### Task 3: Publish The Formal Runtime Architecture Document

**Files:**
- Create: `/Users/mangowmac/Desktop/code/NL2Cypher/console/runtime_console/docs/System_Runtime_Architecture.md`

- [ ] **Step 1: Write the runtime architecture document with bilingual service names and formal data flow**

```markdown
# 系统运行架构（System Runtime Architecture）

## 系统概述（System Overview）

当前运行中的服务包括：
- Cypher 生成服务（Cypher Generation Service, CGS） `8000`
- 测试服务（Testing Service） `8001`
- 知识修复建议服务（Knowledge Repair Suggestion Service, KRSS） `8002`
- 知识运营服务（Knowledge Ops / knowledge-agent） `8010`
- QA 生成器（QA Generator / qa-agent） `8020`

## 成功路径（Success Path）

`问题请求 -> CGS -> Testing Service -> 通过`

## 失败闭环路径（Failure Closed Loop）

`问题请求 -> CGS -> Testing Service -> KRSS -> Knowledge Ops`
```

- [ ] **Step 2: Verify the document includes service boundaries, key objects, and interface quick reference**

Run: `rg -n "Success Path|Failure Closed Loop|Issue Ticket|Knowledge Repair Suggestion|QA Generator" docs/System_Runtime_Architecture.md`

Expected: matching lines for each required section and term.

- [ ] **Step 3: Commit**

```bash
git add docs/System_Runtime_Architecture.md
git commit -m "docs: add formal system runtime architecture reference"
```

---

### Task 4: Full Verification And Cleanup

**Files:**
- Modify as needed: `/Users/mangowmac/Desktop/code/NL2Cypher/tests/test_testing_service_console_api.py`
- Modify as needed: `/Users/mangowmac/Desktop/code/NL2Cypher/tests/test_testing_service_llm_eval.py`

- [ ] **Step 1: Run the focused console and Testing Service test suite**

Run: `python -m pytest -q tests/test_testing_service_console_api.py tests/test_testing_service_llm_eval.py`

Expected: PASS

- [ ] **Step 2: Run the broader regression suite for touched services**

Run: `python -m pytest -q tests/test_query_generation_service_api.py tests/test_query_generation_service_workflow.py tests/test_krss_service_flow.py tests/test_testing_service_console_api.py tests/test_testing_service_llm_eval.py`

Expected: PASS

- [ ] **Step 3: Manual smoke-check the console**

Run:

```bash
python -m uvicorn services.testing_agent.app.main:app --host 127.0.0.1 --port 8001
```

Then open: `http://127.0.0.1:8001/console`

Expected:
- `架构总览（Architecture Overview）` tab shows service cards and diagrams
- `系统联调（System Integration Console）` tab can run both success and failure paths
- artifact panels show prompt, generated cypher, evaluation, issue ticket, and knowledge repair suggestion data

- [ ] **Step 4: Commit final verification fixes**

```bash
git add services/testing_agent/app tests/test_testing_service_console_api.py tests/test_testing_service_llm_eval.py docs/System_Runtime_Architecture.md
git commit -m "test: verify system integration console end to end"
```
