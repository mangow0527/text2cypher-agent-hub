from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from services.cypher_generator_agent.app.infrastructure.llm_client import (
    OpenAICompatibleStructuredLLMClient,
    TracedStructuredLLMClient,
)


def test_openai_compatible_client_posts_schema_bound_json_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[dict[str, Any]] = []

    class FakeClient:
        def __init__(self, *, timeout: float) -> None:
            self.timeout = timeout

        def __enter__(self) -> "FakeClient":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def post(
            self,
            url: str,
            *,
            headers: dict[str, str],
            json: dict[str, Any],
        ) -> httpx.Response:
            requests.append({"url": url, "headers": headers, "json": json, "timeout": self.timeout})
            return httpx.Response(
                200,
                headers={"x-request-id": "req-123"},
                json={
                    "choices": [
                        {
                            "message": {
                                "content": '{"schema_version":"question_decomposition_v1","intent_type":"list"}'
                            }
                        }
                    ]
                },
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr(httpx, "Client", FakeClient)

    client = OpenAICompatibleStructuredLLMClient(
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1/",
        api_key="test-key",
        model="qwen3-32b",
        temperature=0.1,
        timeout_seconds=12.0,
    )

    result = client.generate_structured(
        prompt="Decompose this question.",
        schema_name="question_decomposition_v1",
        schema={"type": "object", "required": ["schema_version"]},
        attempt=2,
    )

    assert result == {"schema_version": "question_decomposition_v1", "intent_type": "list"}
    request = requests[0]
    assert request["url"] == "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
    assert request["headers"]["Authorization"] == "Bearer test-key"
    assert request["json"]["model"] == "qwen3-32b"
    assert request["json"]["temperature"] == 0.1
    assert request["json"]["enable_thinking"] is False
    assert request["json"]["response_format"] == {"type": "json_object"}
    assert request["json"]["messages"][0]["role"] == "user"
    assert "Decompose this question." in request["json"]["messages"][0]["content"]
    assert "question_decomposition_v1" in request["json"]["messages"][0]["content"]
    assert "只返回一个 JSON 对象" in request["json"]["messages"][0]["content"]
    assert "不要返回 Markdown" in request["json"]["messages"][0]["content"]
    assert "Schema 名称" in request["json"]["messages"][0]["content"]
    assert "第 2 次尝试" in request["json"]["messages"][0]["content"]
    assert "输出契约（简化版，完整 schema 由工程侧校验）" in request["json"]["messages"][0]["content"]
    assert "正常拆解时返回" in request["json"]["messages"][0]["content"]
    assert "literal_candidates" in request["json"]["messages"][0]["content"]
    assert "Return exactly one JSON object" not in request["json"]["messages"][0]["content"]
    assert "JSON Schema:" not in request["json"]["messages"][0]["content"]
    assert '"$defs"' not in request["json"]["messages"][0]["content"]
    assert '"required": ["schema_version"]' not in request["json"]["messages"][0]["content"]
    assert request["timeout"] == 12.0


def test_grounded_understanding_schema_bound_contract_uses_compact_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[dict[str, Any]] = []

    class FakeClient:
        def __init__(self, *, timeout: float) -> None:
            self.timeout = timeout

        def __enter__(self) -> "FakeClient":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def post(
            self,
            url: str,
            *,
            headers: dict[str, str],
            json: dict[str, Any],
        ) -> httpx.Response:
            requests.append({"url": url, "headers": headers, "json": json, "timeout": self.timeout})
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": '{"schema_version":"grounded_understanding_v1","status":"grounded","query_shape":"lookup","selected_bindings":[]}'
                            }
                        }
                    ]
                },
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr(httpx, "Client", FakeClient)

    client = OpenAICompatibleStructuredLLMClient(
        base_url="https://example.test/v1",
        api_key="test-key",
        model="qwen3-32b",
        temperature=0.1,
        timeout_seconds=12.0,
    )

    client.generate_structured(
        prompt="Ground this question.",
        schema_name="grounded_understanding_v1",
        schema={"type": "object", "required": ["schema_version"]},
        attempt=1,
    )

    content = requests[0]["json"]["messages"][0]["content"]
    assert "compact selection contract" in content
    assert "selected_literal_ids" in content
    assert "candidate_id" in content
    assert "不要输出 semantic_id" in content
    assert "projection/group_by/measures/sort/assumptions 必须是对象数组" in content
    assert '"selected_literals"' not in content
    assert '"coverage"' not in content
    assert '"rationale"' not in content
    assert '"confidence"' not in content


def test_openai_compatible_client_strips_markdown_json_fences(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeClient:
        def __init__(self, *, timeout: float) -> None:
            pass

        def __enter__(self) -> "FakeClient":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def post(self, url: str, **_: Any) -> httpx.Response:
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "```json\n{\"ok\": true}\n```"}}]},
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr(httpx, "Client", FakeClient)

    client = OpenAICompatibleStructuredLLMClient(
        base_url="https://example.test/v1",
        api_key="test-key",
        model="qwen3-32b",
        temperature=0.1,
        timeout_seconds=12.0,
    )

    assert client.generate_structured(prompt="p", schema_name="s", schema={}, attempt=1) == {"ok": True}


def test_openai_compatible_client_records_provider_token_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeClient:
        def __init__(self, *, timeout: float) -> None:
            pass

        def __enter__(self) -> "FakeClient":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def post(self, url: str, **_: Any) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": "{\"ok\": true}"}}],
                    "usage": {
                        "prompt_tokens": 123,
                        "completion_tokens": 45,
                        "total_tokens": 168,
                    },
                },
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr(httpx, "Client", FakeClient)
    client = TracedStructuredLLMClient(
        OpenAICompatibleStructuredLLMClient(
            base_url="https://example.test/v1",
            api_key="test-key",
            model="qwen3-32b",
            temperature=0.1,
            timeout_seconds=12.0,
        )
    )

    assert client.generate_structured(prompt="p", schema_name="s", schema={}, attempt=1) == {"ok": True}
    assert client.trace_calls[0]["token_usage"] == {
        "prompt_tokens": 123,
        "completion_tokens": 45,
        "total_tokens": 168,
    }


def test_openai_compatible_client_rejects_non_object_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeClient:
        def __init__(self, *, timeout: float) -> None:
            pass

        def __enter__(self) -> "FakeClient":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def post(self, url: str, **_: Any) -> httpx.Response:
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": json.dumps(["not", "object"])}}]},
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr(httpx, "Client", FakeClient)
    client = OpenAICompatibleStructuredLLMClient(
        base_url="https://example.test/v1",
        api_key="test-key",
        model="qwen3-32b",
        temperature=0.1,
        timeout_seconds=12.0,
    )

    with pytest.raises(ValueError, match="structured LLM response must be a JSON object"):
        client.generate_structured(prompt="p", schema_name="s", schema={}, attempt=1)
