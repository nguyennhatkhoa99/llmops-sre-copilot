"""Timing, scoped logs and token counts, without logging content.

Prompts, answers and document text never reach the log. ``safe_fields`` drops
known content keys and keeps only short scalar values such as ids and counts.
If OpenTelemetry is installed, every ``log_scope`` is also a span.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("beyondpilot")

CONTENT_KEYS = frozenset(
    {
        "prompt",
        "messages",
        "content",
        "text",
        "answer",
        "query",
        "question",
        "input",
        "output",
        "document",
        "body",
        "completion",
    }
)
MAX_STR = 80

try:  # optional
    from opentelemetry import trace as _otel_trace

    _tracer = _otel_trace.get_tracer("beyondpilot")
except ImportError:  # pragma: no cover - depends on extras
    _tracer = None


def safe_fields(fields: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in fields.items():
        if key.lower() in CONTENT_KEYS:
            continue
        if isinstance(value, bool | int | float):
            out[key] = value
        elif isinstance(value, str) and len(value) <= MAX_STR and "\n" not in value:
            out[key] = value
    return out


class Timer:
    def __init__(self) -> None:
        self.start = time.perf_counter()

    @property
    def ms(self) -> float:
        return round((time.perf_counter() - self.start) * 1000, 1)


@dataclass
class TokenUsage:
    prompt: int = 0
    completion: int = 0
    by_model: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return self.prompt + self.completion


_scope: ContextVar[tuple[str, ...]] = ContextVar("beyondpilot_scope", default=())
_usage: ContextVar[TokenUsage | None] = ContextVar("beyondpilot_usage", default=None)


def current_scope() -> str:
    return ".".join(_scope.get())


def record_tokens(prompt: int, completion: int, model: str = "") -> None:
    usage = _usage.get()
    if usage is None:
        return
    usage.prompt += prompt
    usage.completion += completion
    if model:
        usage.by_model[model] = usage.by_model.get(model, 0) + prompt + completion


@contextmanager
def track_tokens() -> Iterator[TokenUsage]:
    """Collect token counts of every LLM call made inside the block."""
    usage = TokenUsage()
    token = _usage.set(usage)
    try:
        yield usage
    finally:
        _usage.reset(token)


@contextmanager
def log_scope(name: str, **fields: Any) -> Iterator[dict[str, Any]]:
    """Time a block, log one line when it ends and open a span if OTel is present.

    The yielded dict can receive more fields; they go through ``safe_fields`` too.
    """
    token = _scope.set(_scope.get() + (name,))
    extra: dict[str, Any] = {}
    timer = Timer()
    span_cm = _tracer.start_as_current_span(name) if _tracer else None
    span = span_cm.__enter__() if span_cm else None
    status = "ok"
    try:
        yield extra
    except BaseException as exc:
        status = "error"
        extra["error"] = type(exc).__name__
        code = getattr(exc, "code", None)
        if code is not None:
            extra["error_code"] = getattr(code, "value", str(code))
        raise
    finally:
        record = safe_fields({**fields, **extra})
        record.update(scope=current_scope(), status=status, duration_ms=timer.ms)
        if span is not None:
            for key, value in record.items():
                span.set_attribute(f"beyondpilot.{key}", value)
        logger.info("%s %s", name, " ".join(f"{k}={v}" for k, v in sorted(record.items())))
        if span_cm:
            span_cm.__exit__(None, None, None)
        _scope.reset(token)
