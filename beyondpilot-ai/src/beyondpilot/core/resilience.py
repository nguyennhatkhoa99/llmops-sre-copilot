"""The one retry layer for calls that leave the service.

Rules:
- Retry happens here and only here. A call made inside another retried call is
  not retried again, so a failing upstream never gets attempts x attempts calls.
- Slot clients turn off their own retries (for example ``max_retries=0`` on the
  OpenAI client) and wrap their calls with ``call_with_retry``.
- ``Retry-After`` from the upstream wins over the backoff, capped at ``max_delay``.
- When retries run out the caller gets ``AppError``, never a raw exception.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Any, TypeVar

import httpx

from beyondpilot.core.diagnostics import log_scope
from beyondpilot.core.errors import AppError, ErrorCode
from beyondpilot.core.settings import ResilienceConfig

T = TypeVar("T")

RETRY_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
# Retryable OpenAI SDK errors, matched by name so the SDK stays an optional import here.
_RETRY_EXC_NAMES = frozenset(
    {"APITimeoutError", "APIConnectionError", "RateLimitError", "InternalServerError"}
)

_in_retry: ContextVar[bool] = ContextVar("beyondpilot_in_retry", default=False)


class RetryableError(Exception):
    """Raise from a slot to ask for a retry, optionally with a server-given delay."""

    def __init__(self, message: str = "", retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


def _status(exc: BaseException) -> int | None:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code
    status = getattr(exc, "status_code", None)
    return status if isinstance(status, int) else None


def is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, RetryableError | asyncio.TimeoutError | httpx.TransportError):
        return True
    if type(exc).__name__ in _RETRY_EXC_NAMES:
        return True
    status = _status(exc)
    return status in RETRY_STATUS if status is not None else False


def retry_after(exc: BaseException) -> float | None:
    value = getattr(exc, "retry_after", None)
    if isinstance(value, int | float):
        return float(value)
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is not None:
        raw = headers.get("retry-after")
        try:
            return float(raw) if raw is not None else None
        except ValueError:  # HTTP-date form is rare for these upstreams; fall back to backoff
            return None
    return None


def backoff(attempt: int, cfg: ResilienceConfig) -> float:
    return min(cfg.max_delay, cfg.base_delay * (2 ** (attempt - 1)))


async def call_with_retry(
    fn: Callable[..., Awaitable[T]],
    *args: Any,
    config: ResilienceConfig,
    op: str,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    **kwargs: Any,
) -> T:
    """Run ``fn`` with timeout and retry. ``op`` names the call in logs and traces."""
    if _in_retry.get():
        return await asyncio.wait_for(fn(*args, **kwargs), timeout=config.timeout)

    token = _in_retry.set(True)
    try:
        for attempt in range(1, config.attempts + 1):
            with log_scope(op, attempt=attempt) as extra:
                try:
                    return await asyncio.wait_for(fn(*args, **kwargs), timeout=config.timeout)
                except AppError:
                    raise
                except Exception as exc:  # noqa: BLE001 - classified below
                    extra["error"] = type(exc).__name__
                    if not is_retryable(exc):
                        raise AppError(
                            ErrorCode.UPSTREAM_ERROR, f"{op}: {type(exc).__name__}"
                        ) from exc
                    if attempt == config.attempts:
                        code = (
                            ErrorCode.UPSTREAM_TIMEOUT
                            if isinstance(exc, asyncio.TimeoutError | httpx.TimeoutException)
                            else ErrorCode.UPSTREAM_UNAVAILABLE
                        )
                        raise AppError(code, f"{op}: gave up after {attempt} attempts") from exc
                    hinted = retry_after(exc)
                    delay = (
                        min(hinted, config.max_delay)
                        if hinted is not None
                        else backoff(attempt, config)
                    )
                    extra["retry_in_s"] = delay
            await sleep(delay)
        raise AssertionError("unreachable")  # pragma: no cover
    finally:
        _in_retry.reset(token)
