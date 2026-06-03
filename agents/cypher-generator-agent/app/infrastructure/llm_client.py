from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import httpx


class OpenAICompatibleStructuredLLMClient:
    provider = "openai_compatible"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        temperature: float,
        timeout_seconds: float,
    ) -> None:
        if not base_url.strip():
            raise ValueError("base_url is required")
        if not api_key.strip():
            raise ValueError("api_key is required")
        if not model.strip():
            raise ValueError("model is required")

        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.timeout_seconds = timeout_seconds
        self.last_call_trace: dict[str, Any] | None = None

    def generate_structured(
        self,
        *,
        prompt: str,
        schema_name: str,
        schema: Mapping[str, Any],
        attempt: int,
    ) -> Mapping[str, Any]:
        prompt_markdown = _schema_bound_prompt(
            prompt=prompt,
            schema_name=schema_name,
            schema=schema,
            attempt=attempt,
        )
        self.last_call_trace = None
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "temperature": self.temperature,
                    "enable_thinking": False,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {
                            "role": "user",
                            "content": prompt_markdown,
                        }
                    ],
                },
            )
            response.raise_for_status()

        response_payload = response.json()
        content = _response_content(response_payload)
        self.last_call_trace = {
            "schema_name": schema_name,
            "attempt": attempt,
            "model": self.model,
            "prompt": prompt_markdown,
            "raw_output": content,
            "status": "success",
        }
        token_usage = _token_usage(response_payload)
        if token_usage:
            self.last_call_trace["token_usage"] = token_usage
        payload = json.loads(_strip_json_fence(content))
        if not isinstance(payload, Mapping):
            raise ValueError("structured LLM response must be a JSON object")
        return payload


class TracedStructuredLLMClient:
    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.trace_calls: list[dict[str, Any]] = []

    @property
    def provider(self) -> str:
        return str(getattr(self.inner, "provider", "unknown"))

    def generate_structured(
        self,
        *,
        prompt: str,
        schema_name: str,
        schema: Mapping[str, Any],
        attempt: int,
    ) -> Mapping[str, Any]:
        prompt_markdown = _schema_bound_prompt(
            prompt=prompt,
            schema_name=schema_name,
            schema=schema,
            attempt=attempt,
        )
        call: dict[str, Any] = {
            "call_id": f"{schema_name}-attempt-{attempt}",
            "schema_name": schema_name,
            "attempt": attempt,
            "provider": self.provider,
            "model": getattr(self.inner, "model", None),
            "prompt": prompt_markdown,
            "raw_output": "",
            "parsed_output": None,
            "status": "running",
            "error": None,
        }
        try:
            payload = self.inner.generate_structured(
                prompt=prompt,
                schema_name=schema_name,
                schema=schema,
                attempt=attempt,
            )
        except Exception as exc:
            inner_trace = getattr(self.inner, "last_call_trace", None)
            if isinstance(inner_trace, Mapping):
                call["model"] = inner_trace.get("model") or call["model"]
                call["prompt"] = inner_trace.get("prompt") or call["prompt"]
                call["raw_output"] = inner_trace.get("raw_output") or ""
            call["status"] = "failed"
            call["error"] = {
                "type": exc.__class__.__name__,
                "message": str(exc),
            }
            self.trace_calls.append(call)
            raise

        inner_trace = getattr(self.inner, "last_call_trace", None)
        if isinstance(inner_trace, Mapping):
            call["model"] = inner_trace.get("model") or call["model"]
            call["prompt"] = inner_trace.get("prompt") or call["prompt"]
            call["raw_output"] = inner_trace.get("raw_output") or ""
            if isinstance(inner_trace.get("token_usage"), Mapping):
                call["token_usage"] = dict(inner_trace["token_usage"])
        if not call["raw_output"]:
            call["raw_output"] = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        call["parsed_output"] = dict(payload)
        call["status"] = "success"
        self.trace_calls.append(call)
        return payload


def _schema_bound_prompt(
    *,
    prompt: str,
    schema_name: str,
    schema: Mapping[str, Any],
    attempt: int,
) -> str:
    if _prompt_already_contains_schema_contract(prompt, schema_name=schema_name):
        return prompt
    return "\n".join(
        [
            prompt,
            "",
            "只返回一个 JSON 对象。不要返回 Markdown，不要返回解释性文字。",
            f"Schema 名称：{schema_name}",
            f"第 {attempt} 次尝试。",
            "输出契约（简化版，完整 schema 由工程侧校验）：",
            _schema_output_contract(schema_name=schema_name, schema=schema),
        ]
    )


def _token_usage(response_payload: Mapping[str, Any]) -> dict[str, int]:
    usage = response_payload.get("usage")
    if not isinstance(usage, Mapping):
        return {}
    result: dict[str, int] = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = usage.get(key)
        if isinstance(value, int):
            result[key] = value
    return result


def _prompt_already_contains_schema_contract(prompt: str, *, schema_name: str) -> bool:
    return schema_name in prompt and "JSON Schema:" in prompt and "返回且只返回一个 JSON 对象" in prompt


def _schema_output_contract(*, schema_name: str, schema: Mapping[str, Any]) -> str:
    if schema_name == "question_decomposition_v1":
        return _question_decomposition_contract()
    if schema_name == "grounded_understanding_v1":
        return _grounded_understanding_contract()
    return _generic_schema_contract(schema)


def _question_decomposition_contract() -> str:
    return "\n".join(
        [
            "正常拆解时返回以下字段；没有内容的数组也返回 []，不要省略字段：",
            "{",
            '  "schema_version": "question_decomposition_v1",',
            '  "result_type": "decomposition",',
            '  "intent_type": "lookup|list|count|aggregate|top_n|path|compare|unknown",',
            '  "original_question": "原始用户问题",',
            '  "substantive_terms": [',
            '    {"text": "会影响查询语义的实质词", "slot": "projection|filter|group_by|order_by|limit|path|unknown", "attached_to": "可选，仅消歧需要时填写"}',
            "  ],",
            '  "literal_candidates": [',
            '    {"text": "表层字面值", "kind_hint": "enum_or_name|id|number|datetime|unknown", "attached_to": "附着对象词"}',
            "  ],",
            '  "modality_terms": ["大概、可能、应该这类软约束词"],',
            '  "time_terms": ["最近、2024 年、过去 7 天这类时间词"],',
            '  "unparsed_terms": ["无法可靠分类但可能影响语义的词"],',
            '  "output_shape": "rows|scalar|grouped_rows|path|unknown"',
            "}",
            "如果代词或指示词缺少明确指代对象，改为返回：",
            "{",
            '  "schema_version": "question_decomposition_v1",',
            '  "result_type": "clarification_required",',
            '  "original_question": "原始用户问题",',
            '  "clarification_question": "需要向用户确认的问题",',
            '  "missing_referents": ["缺少指代对象的词，例如 它、这个"]',
            "}",
        ]
    )


def _grounded_understanding_contract() -> str:
    return "\n".join(
        [
            "这是 single-shot fallback 的 compact selection contract。只选择候选，不复述候选详情，不生成 Cypher。",
            "只能从 top_candidates 复制 candidate_id；不要输出 semantic_id、semantic_name、semantic_type、owner、confidence、rationale 或 coverage。",
            "返回以下字段；没有内容的数组也返回 []，没有值的可空字段返回 null：",
            "{",
            '  "schema_version": "grounded_understanding_v1",',
            '  "status": "grounded|clarification_required|unsupported_query_shape|failed",',
            '  "query_shape": "vertex_lookup|single_hop|single_hop_traversal|named_path_pattern|variable_path|variable_path_traversal|metric_aggregate|ad_hoc_aggregate|top_n|two_step_aggregate|lookup|unsupported",',
            '  "selected_bindings": [',
            "    {",
            '      "role": "source|target|relation|filter_property|projection|metric|path_pattern",',
            '      "candidate_id": "从候选中原样复制",',
            '      "direction": "forward|backward|null"',
            "    }",
            "  ],",
            '  "selected_literal_ids": ["literal:0 这类 literal_resolver_results 中的 id"],',
            '  "filters": [{"owner": "Service", "property": "quality_of_service", "operator": "=", "raw_literal": "Gold"}],',
            '  "projection": [{"semantic_type": "property", "owner": "Tunnel", "name": "id", "alias": "tunnel_id"}],',
            '  "group_by": [{"alias": "network_element_location", "target": "network_element", "property": {"owner": "NetworkElement", "name": "location"}}],',
            '  "measures": [{"alias": "cnt", "function": "count", "target": "service", "property": {"owner": "Service", "name": "id"}}],',
            '  "sort": [{"source": "measure.cnt", "direction": "desc"}],',
            '  "limit": 50,',
            '  "assumptions": [{"type": "llm_assumption", "message": "高置信但非精确匹配的假设"}],',
            '  "ambiguities": [{"role": "歧义角色", "reason": "歧义原因", "candidate_ids": ["候选 id 1", "候选 id 2"]}],',
            '  "unsupported": null,',
            "}",
            "projection/group_by/measures/sort/assumptions 必须是对象数组，不要返回字符串数组。",
            '当 status 为 "unsupported_query_shape" 时，query_shape 必须是 "unsupported"，unsupported 必须包含 reason_code、message 和 suggested_rewrites。',
        ]
    )


def _generic_schema_contract(schema: Mapping[str, Any]) -> str:
    title = schema.get("title") or schema.get("$id") or "unknown_schema"
    required = schema.get("required")
    properties = schema.get("properties")
    lines = [f"返回一个符合 {title} 的 JSON 对象。"]
    if isinstance(required, list) and required:
        lines.append("必须包含字段：" + "、".join(str(item) for item in required))
    if isinstance(properties, Mapping) and properties:
        lines.append("允许字段：" + "、".join(str(key) for key in properties))
    lines.append("完整 schema 由工程侧校验；如果校验失败，系统会带错误原因重试。")
    return "\n".join(lines)


def _response_content(payload: Mapping[str, Any]) -> str:
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("LLM response missing choices[0].message.content") from exc
    if not isinstance(content, str) or not content.strip():
        raise ValueError("LLM response content must be non-empty text")
    return content.strip()


def _strip_json_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped

    lines = stripped.splitlines()
    if not lines:
        return stripped
    if lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()
