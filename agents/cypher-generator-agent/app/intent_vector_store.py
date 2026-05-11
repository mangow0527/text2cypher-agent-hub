from __future__ import annotations

from typing import Any, Protocol

import httpx

from .intent_recognition import IntentEmbeddingSample


class RagIntentSearchError(RuntimeError):
    pass


class EmbeddingStoreLike(Protocol):
    def search(
        self,
        query_vector: tuple[float, ...],
        *,
        top_k: int,
        query_text: str | None = None,
    ) -> list[tuple[IntentEmbeddingSample, float]]:
        ...


class RagIntentEmbeddingStore:
    def __init__(
        self,
        *,
        base_url: str,
        collection: str,
        endpoint_path: str = "/api/v1/intent/search",
        taxonomy_version: str | None = None,
        timeout_seconds: float = 60.0,
        include_query_vector: bool = False,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.collection = collection
        self.endpoint_path = endpoint_path if endpoint_path.startswith("/") else f"/{endpoint_path}"
        self.taxonomy_version = taxonomy_version
        self.timeout_seconds = timeout_seconds
        self.include_query_vector = include_query_vector
        self.transport = transport

    def search(
        self,
        query_vector: tuple[float, ...],
        *,
        top_k: int,
        query_text: str | None = None,
    ) -> list[tuple[IntentEmbeddingSample, float]]:
        if not query_text or not query_text.strip():
            raise RagIntentSearchError("RAG intent search requires query_text")

        request_payload: dict[str, object] = {
            "question": query_text,
            "top_k": top_k,
            "collection": self.collection,
            "filters": self._filters(),
        }
        if self.include_query_vector:
            request_payload["query_vector"] = list(query_vector)

        try:
            with httpx.Client(timeout=self.timeout_seconds, transport=self.transport) as client:
                response = client.post(f"{self.base_url}{self.endpoint_path}", json=request_payload)
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise RagIntentSearchError(f"RAG intent search failed: {exc}") from exc

        return self._parse_matches(payload)

    def _filters(self) -> dict[str, object]:
        filters: dict[str, object] = {"enabled": True}
        if self.taxonomy_version:
            filters["taxonomy_version"] = self.taxonomy_version
        return filters

    def _parse_matches(self, payload: Any) -> list[tuple[IntentEmbeddingSample, float]]:
        if not isinstance(payload, dict):
            raise RagIntentSearchError("RAG intent search response must be a JSON object")

        hits = payload.get("hits")
        if hits is None:
            hits = payload.get("matches")
        if hits is None:
            hits = payload.get("results")
        if not isinstance(hits, list):
            raise RagIntentSearchError("RAG intent search response missing hits list")

        matches: list[tuple[IntentEmbeddingSample, float]] = []
        for hit in hits:
            if not isinstance(hit, dict):
                raise RagIntentSearchError("RAG intent search hit must be a JSON object")
            matches.append((_sample_from_hit(hit), _score_from_hit(hit)))
        return matches


class FallbackEmbeddingStore:
    def __init__(
        self,
        *,
        primary: EmbeddingStoreLike,
        fallback: EmbeddingStoreLike,
    ) -> None:
        self.primary = primary
        self.fallback = fallback

    def search(
        self,
        query_vector: tuple[float, ...],
        *,
        top_k: int,
        query_text: str | None = None,
    ) -> list[tuple[IntentEmbeddingSample, float]]:
        try:
            return self.primary.search(query_vector, top_k=top_k, query_text=query_text)
        except RagIntentSearchError:
            return self.fallback.search(query_vector, top_k=top_k, query_text=query_text)


def _sample_from_hit(hit: dict[str, Any]) -> IntentEmbeddingSample:
    sample_id = _required_hit_str(hit, "id", "sample_id")
    text = _required_hit_str(hit, "text", "sample_text", "question")
    primary_intent = _hit_str(hit, "primary_intent")
    secondary_intent = _hit_str(hit, "secondary_intent")
    intent_key = _hit_str(hit, "intent", "intent_key")
    if (not primary_intent or not secondary_intent) and intent_key and "." in intent_key:
        primary_intent, secondary_intent = intent_key.split(".", 1)
    if not primary_intent or not secondary_intent:
        raise RagIntentSearchError(f"RAG intent search hit {sample_id!r} missing intent fields")
    return IntentEmbeddingSample(
        id=sample_id,
        primary_intent=primary_intent,
        secondary_intent=secondary_intent,
        text=text,
    )


def _score_from_hit(hit: dict[str, Any]) -> float:
    value = _hit_value(hit, "score", "similarity")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise RagIntentSearchError("RAG intent search hit missing numeric score") from exc


def _required_hit_str(hit: dict[str, Any], *keys: str) -> str:
    value = _hit_str(hit, *keys)
    if not value:
        raise RagIntentSearchError(f"RAG intent search hit missing {'/'.join(keys)}")
    return value


def _hit_str(hit: dict[str, Any], *keys: str) -> str | None:
    value = _hit_value(hit, *keys)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _hit_value(hit: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in hit:
            return hit[key]
    for nested_key in ("payload", "metadata"):
        nested_value = hit.get(nested_key)
        if not isinstance(nested_value, dict):
            continue
        for key in keys:
            if key in nested_value:
                return nested_value[key]
    return None
