from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Awaitable, Callable

import httpx


SleepFn = Callable[[float], Awaitable[None]]


@dataclass(frozen=True)
class RetryDecision:
    should_retry: bool
    reason: str
    status_code: int | None = None
    body_preview: str | None = None
    retry_after_seconds: float | None = None


def extract_request_id(headers: object) -> str | None:
    if not headers:
        return None
    for key in ("x-request-id", "request-id", "x-trace-id"):
        value = getattr(headers, "get", lambda _key, _default=None: None)(key, None)
        if value:
            return str(value)
    return None


def response_body_preview(response: httpx.Response | None, limit: int = 240) -> str | None:
    if response is None:
        return None
    text = (response.text or "").strip()
    if not text:
        return None
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def parse_retry_after_seconds(response: httpx.Response | None) -> float | None:
    if response is None:
        return None
    header_value = response.headers.get("Retry-After")
    if not header_value:
        return None
    try:
        parsed_seconds = float(header_value)
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(header_value)
        except (TypeError, ValueError, IndexError, OverflowError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        parsed_seconds = (retry_at - datetime.now(timezone.utc)).total_seconds()
    return max(0.0, parsed_seconds)


def classify_retryable_error(exc: Exception) -> RetryDecision:
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        if status_code == 429:
            return RetryDecision(
                should_retry=True,
                reason="rate_limited",
                status_code=status_code,
                body_preview=response_body_preview(exc.response),
                retry_after_seconds=parse_retry_after_seconds(exc.response),
            )
        if 500 <= status_code < 600:
            return RetryDecision(
                should_retry=True,
                reason="server_error",
                status_code=status_code,
                body_preview=response_body_preview(exc.response),
            )
        return RetryDecision(
            should_retry=False,
            reason="non_retryable_http_status",
            status_code=status_code,
            body_preview=response_body_preview(exc.response),
        )

    if isinstance(exc, httpx.TimeoutException):
        return RetryDecision(should_retry=True, reason="timeout")
    if isinstance(exc, httpx.TransportError):
        return RetryDecision(should_retry=True, reason="transport_error")

    return RetryDecision(should_retry=False, reason="non_retryable_exception")


async def sleep_with_backoff(
    *,
    sleep_fn: SleepFn = asyncio.sleep,
    base_delay_seconds: float,
    attempt_index: int,
    retry_after_seconds: float | None = None,
) -> float:
    if retry_after_seconds is not None:
        delay_seconds = retry_after_seconds
    else:
        delay_seconds = base_delay_seconds * (2 ** max(0, attempt_index))
    await sleep_fn(delay_seconds)
    return delay_seconds
