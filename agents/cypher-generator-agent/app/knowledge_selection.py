from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any, Protocol

import httpx

from .intent_recognition import IntentRecognitionResult
from .semantic_query import SemanticQuerySpec


@dataclass(frozen=True)
class SelectedKnowledgeContext:
    fragments: list[dict[str, Any]] = field(default_factory=list)
    prompt_context: str = ""
    selection_trace: list[str] = field(default_factory=list)
    size_estimate: int = 0
    missing_knowledge_signals: list[str] = field(default_factory=list)
    source: str = "none"

    def to_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "fragments": self.fragments,
            "prompt_context": self.prompt_context,
            "selection_trace": self.selection_trace,
            "size_estimate": self.size_estimate,
            "missing_knowledge_signals": self.missing_knowledge_signals,
        }


class KnowledgeSelector(Protocol):
    async def select(
        self,
        *,
        question: str,
        intent_result: IntentRecognitionResult,
        semantic_query: SemanticQuerySpec,
    ) -> SelectedKnowledgeContext:
        ...


class RagKnowledgeSelector:
    def __init__(
        self,
        *,
        base_url: str,
        limit: int = 12,
        timeout_seconds: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.limit = limit
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    async def select(
        self,
        *,
        question: str,
        intent_result: IntentRecognitionResult,
        semantic_query: SemanticQuerySpec,
    ) -> SelectedKnowledgeContext:
        semantic_payload = semantic_query_to_rag_payload(semantic_query)
        if not semantic_payload.get("intent"):
            semantic_payload["intent"] = _intent_name(intent_result)
        request_payload = {
            "question": question,
            "semantic_query": semantic_payload,
            "limit": self.limit,
        }
        async with httpx.AsyncClient(timeout=self.timeout_seconds, transport=self.transport) as client:
            response = await client.post(f"{self.base_url}/api/v1/retrieve", json=request_payload)
            response.raise_for_status()
            payload = response.json()
        return SelectedKnowledgeContext(
            fragments=_dict_list(payload.get("fragments")),
            prompt_context=str(payload.get("prompt_context") or ""),
            selection_trace=_string_list(payload.get("selection_trace")),
            size_estimate=_int_value(payload.get("size_estimate"), default=len(str(payload.get("prompt_context") or ""))),
            missing_knowledge_signals=_string_list(payload.get("missing_knowledge_signals")),
            source="rag",
        )


def semantic_query_to_rag_payload(semantic_query: SemanticQuerySpec | dict[str, Any]) -> dict[str, object]:
    semantic_dict = _to_plain_dict(semantic_query)
    kind = semantic_dict.get("kind") or semantic_dict.get("query_kind")
    return {
        "kind": kind,
        "query_kind": kind,
        "intent": semantic_dict.get("intent"),
        "schema_id": semantic_dict.get("schema_id"),
        "scenario_id": semantic_dict.get("scenario_id"),
        "entities": _unique(
            _symbolic_value(entity, preferred_keys=("label", "name"))
            for entity in _list_value(semantic_dict.get("entities"))
        ),
        "relationships": _unique(
            _symbolic_value(relationship, preferred_keys=("edge", "name"))
            for relationship in _list_value(semantic_dict.get("relationships"))
        ),
        "properties": _unique(_iter_semantic_properties(semantic_dict)),
        "metrics": _unique(
            _symbolic_value(metric, preferred_keys=("name", "output_alias"))
            for metric in _list_value(semantic_dict.get("metrics"))
        ),
    }


def _iter_semantic_properties(semantic_dict: dict[str, Any]):
    for section_name in ("projections", "dimensions", "filters", "metrics"):
        for item in _list_value(semantic_dict.get(section_name)):
            value = _symbolic_value(item, preferred_keys=("property",))
            if value:
                yield value
    for order_by in _list_value(semantic_dict.get("order_by")):
        expression = _symbolic_value(order_by, preferred_keys=("expression",))
        if expression and "." in expression:
            yield expression.rsplit(".", 1)[-1]


def _to_plain_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return dict(to_dict())
    if is_dataclass(value):
        return asdict(value)
    raise TypeError(f"unsupported semantic query payload: {type(value)!r}")


def _list_value(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _symbolic_value(value: Any, *, preferred_keys: tuple[str, ...]) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, dict):
        for key in preferred_keys:
            candidate = value.get(key)
            if candidate is not None and str(candidate).strip():
                return str(candidate)
    return None


def _unique(values: Any) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _dict_list(value: Any) -> list[dict[str, Any]]:
    return [item for item in _list_value(value) if isinstance(item, dict)]


def _string_list(value: Any) -> list[str]:
    return [str(item) for item in _list_value(value) if item is not None]


def _int_value(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _intent_name(intent_result: IntentRecognitionResult) -> str | None:
    if intent_result.primary_intent is None:
        return None
    if intent_result.secondary_intent is None:
        return intent_result.primary_intent
    return f"{intent_result.primary_intent}.{intent_result.secondary_intent}"
