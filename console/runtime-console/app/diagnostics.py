from __future__ import annotations

import json
import re
from typing import Any, Protocol

import httpx


CGA_DIAGNOSTIC_SCHEMA_VERSION = "runtime_cga_diagnostic_v1"
FACT_SCHEMA_VERSION = "runtime_cga_diagnostic_fact_v1"


class CgaDiagnosticClient(Protocol):
    async def generate(self, *, facts: dict[str, Any]) -> dict[str, Any]:
        ...


class RuntimeCgaDiagnosticLLMClient:
    def __init__(
        self,
        *,
        base_url: str | None,
        api_key: str | None,
        model: str | None,
        timeout_seconds: float,
        temperature: float,
    ) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key or ""
        self.model = model or ""
        self.timeout_seconds = timeout_seconds
        self.temperature = temperature

    async def generate(self, *, facts: dict[str, Any]) -> dict[str, Any]:
        missing = [
            name
            for name, value in (
                ("diagnostic_llm_base_url", self.base_url),
                ("diagnostic_llm_api_key", self.api_key),
                ("diagnostic_llm_model", self.model),
            )
            if not value
        ]
        if missing:
            raise RuntimeError(f"诊断 LLM 配置缺失: {', '.join(missing)}")

        prompt = build_cga_diagnostic_prompt(facts)
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "temperature": self.temperature,
                    "enable_thinking": False,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            response.raise_for_status()
        raw_text = str(response.json()["choices"][0]["message"]["content"]).strip()
        payload = json.loads(_strip_code_fence(raw_text))
        return normalize_cga_diagnostic_payload(payload)


def build_cga_diagnostic_facts(
    *,
    user_query_id: str,
    question: str,
    status: str,
    generation_status: str | None,
    cga_generation: dict[str, Any] | None,
    cga_error: str | None,
    tugraph_response: dict[str, Any] | None,
    generated_cypher: str | None,
) -> dict[str, Any]:
    trace = _as_dict((cga_generation or {}).get("trace"))
    final_outputs = _as_dict(trace.get("final_outputs"))
    top_failure = _as_dict((cga_generation or {}).get("failure"))
    failure = top_failure or _as_dict(final_outputs.get("failure"))
    top_clarification = _as_dict((cga_generation or {}).get("clarification"))
    clarification = top_clarification or _as_dict(final_outputs.get("clarification"))
    details = _as_dict(failure.get("details"))
    grounded = _as_dict(details.get("grounded_understanding"))
    unsupported = _as_dict(grounded.get("unsupported"))
    coverage = _as_dict(grounded.get("coverage"))
    unrecognized_terms = _extract_unrecognized_terms(coverage)
    stage_evidence = _notable_stage_evidence(trace.get("stages"))
    tugraph_error = _raw_tugraph_error(tugraph_response)

    return {
        "schema_version": FACT_SCHEMA_VERSION,
        "task": "请面向业务用户解释这次自然语言图查询为什么没有完成，并给出可执行的改问建议。",
        "domain": "网络资源图谱查询，常见对象包括服务、隧道、设备、端口、链路等。",
        "user_query_id": user_query_id,
        "user_question": question,
        "query_status": status,
        "generation_status": generation_status,
        "generated_cypher_present": bool(generated_cypher),
        "business_status": _business_status(status=status, generation_status=generation_status),
        "primary_failure": {
            "source": _failure_source(status=status, cga_error=cga_error, tugraph_error=tugraph_error),
            "reason_code": failure.get("reason") or unsupported.get("reason_code") or status,
            "message": failure.get("message") or unsupported.get("message") or cga_error or tugraph_error or "",
        },
        "clarification_question": clarification.get("question") or clarification.get("question_zh"),
        "question_understanding": {
            "unrecognized_terms": unrecognized_terms,
            "recognized_terms": _extract_recognized_terms(coverage),
            "missing_information": _extract_missing_information(clarification),
            "unsupported_reason": unsupported.get("reason_code"),
            "unsupported_message": unsupported.get("message"),
        },
        "cga_suggested_rewrites": _unique_strings(
            [
                *(_as_string_list(failure.get("suggested_rewrites"))),
                *(_as_string_list(unsupported.get("suggested_rewrites"))),
            ]
        ),
        "user_visible_notices": _as_string_list((cga_generation or {}).get("user_visible_notices"))
        or _as_string_list(final_outputs.get("user_visible_notices")),
        "tugraph_error": tugraph_error,
        "cga_error": cga_error,
        "notable_stage_evidence": stage_evidence,
        "constraints": [
            "不要提到 Cypher、trace、schema、错误码、阶段名。",
            "不要编造事实包中没有提供的原因、对象或数据。",
            "如果证据不足，请说明需要补充问题信息，不要假装知道数据库里有什么。",
            "建议改问必须贴合原问题和网络资源图谱场景。",
        ],
        "output_schema": {
            "title": "string",
            "summary": "string",
            "main_reason": "string",
            "suggested_questions": ["string"],
        },
    }


def build_cga_diagnostic_prompt(facts: dict[str, Any]) -> str:
    return (
        "你是网络资源图谱查询产品的客户反馈文案助手。"
        "你只能根据给定事实材料，面向业务用户解释本次查询为什么没有完成，并给出改问建议。\n"
        "严格要求：\n"
        "1. 只返回 JSON，不要返回 Markdown。\n"
        "2. JSON 字段必须是 title、summary、main_reason、suggested_questions。\n"
        "3. suggested_questions 必须是 1 到 3 条中文字符串。\n"
        "4. 不要提到 Cypher、trace、schema、错误码、阶段名或内部服务名。\n"
        "5. 不要编造事实材料中没有的对象、数据或失败原因。\n\n"
        f"事实材料：\n{json.dumps(facts, ensure_ascii=False, indent=2)}"
    )


def normalize_cga_diagnostic_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("诊断 LLM 返回不是 JSON 对象")
    title = _required_text(payload, "title")
    summary = _required_text(payload, "summary")
    main_reason = _required_text(payload, "main_reason")
    suggested_questions = _as_string_list(payload.get("suggested_questions"))
    if not suggested_questions:
        raise ValueError("诊断 LLM 返回缺少 suggested_questions")
    return {
        "title": title,
        "summary": summary,
        "main_reason": main_reason,
        "suggested_questions": suggested_questions[:3],
    }


def make_generated_diagnostic(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_cga_diagnostic_payload(payload)
    return {
        "schema_version": CGA_DIAGNOSTIC_SCHEMA_VERSION,
        "status": "generated",
        **normalized,
    }


def make_failed_diagnostic(error: Exception | str) -> dict[str, Any]:
    return {
        "schema_version": CGA_DIAGNOSTIC_SCHEMA_VERSION,
        "status": "failed",
        "title": "诊断生成失败",
        "error_message": str(error),
    }


def make_pending_diagnostic() -> dict[str, Any]:
    return {
        "schema_version": CGA_DIAGNOSTIC_SCHEMA_VERSION,
        "status": "pending",
        "title": "诊断生成中",
        "summary": "查询结果已返回，正在生成面向业务用户的诊断说明。",
    }


def make_not_required_diagnostic() -> dict[str, Any]:
    return {
        "schema_version": CGA_DIAGNOSTIC_SCHEMA_VERSION,
        "status": "not_required",
        "title": "查询已完成",
        "summary": "本次查询已生成并执行完成。",
    }


def _business_status(*, status: str, generation_status: str | None) -> str:
    if status == "completed":
        return "查询已完成"
    if status == "query_failed":
        return "查询语句已生成，但数据库执行失败"
    if generation_status == "clarification_required":
        return "需要补充问题信息后才能继续查询"
    if generation_status == "unsupported_query_shape":
        return "系统暂时无法理解这个查询意图"
    if generation_status == "service_failed" or status == "service_failed":
        return "查询服务暂时异常"
    return "系统暂时无法完成这次查询"


def _failure_source(*, status: str, cga_error: str | None, tugraph_error: str | None) -> str:
    if cga_error:
        return "runtime_cga_call"
    if status == "query_failed" or tugraph_error:
        return "tugraph"
    return "cga"


def _extract_unrecognized_terms(coverage: dict[str, Any]) -> list[str]:
    terms: list[str] = []
    for path in (
        ("substantive_terms", "uncovered"),
        ("projection_terms", "uncovered"),
        ("time_terms", "unresolved"),
        ("unparsed_terms", "unresolved"),
    ):
        current: Any = coverage
        for key in path:
            current = _as_dict(current).get(key)
        terms.extend(_as_string_list(current))
    return _unique_strings(terms)


def _extract_recognized_terms(coverage: dict[str, Any]) -> list[str]:
    terms: list[str] = []
    for path in (
        ("substantive_terms", "covered"),
        ("projection_terms", "covered"),
        ("time_terms", "covered"),
    ):
        current: Any = coverage
        for key in path:
            current = _as_dict(current).get(key)
        terms.extend(_as_string_list(current))
    return _unique_strings(terms)


def _extract_missing_information(clarification: dict[str, Any]) -> list[str]:
    return _unique_strings(
        [
            *(_as_string_list(clarification.get("missing_information"))),
            *([str(clarification.get("reason"))] if clarification.get("reason") else []),
        ]
    )


def _notable_stage_evidence(stages: Any) -> list[dict[str, Any]]:
    if not isinstance(stages, list):
        return []
    notable: list[dict[str, Any]] = []
    for stage in stages:
        if not isinstance(stage, dict):
            continue
        errors = _as_dict_list(stage.get("errors"))
        warnings = _as_dict_list(stage.get("warnings"))
        if stage.get("status") not in {"failed", "warning"} and not errors and not warnings:
            continue
        notable.append(
            {
                "stage": stage.get("stage"),
                "status": stage.get("status"),
                "errors": [_compact_issue(issue) for issue in errors],
                "warnings": [_compact_issue(issue) for issue in warnings],
            }
        )
    return notable[-5:]


def _compact_issue(issue: dict[str, Any]) -> dict[str, Any]:
    return {
        key: issue.get(key)
        for key in ("code", "type", "message", "reason")
        if issue.get(key) is not None
    }


def _raw_tugraph_error(tugraph_response: dict[str, Any] | None) -> str | None:
    if not isinstance(tugraph_response, dict):
        return None
    raw_error = tugraph_response.get("error_message") or tugraph_response.get("error") or tugraph_response.get("errors")
    if raw_error is None:
        return None
    if isinstance(raw_error, str):
        return raw_error
    return json.dumps(raw_error, ensure_ascii=False, default=str)


def _required_text(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"诊断 LLM 返回缺少 {key}")
    return value.strip()


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _as_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _unique_strings(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def _strip_code_fence(text: str) -> str:
    stripped = text.strip()
    match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", stripped, flags=re.DOTALL)
    return match.group(1).strip() if match else stripped
