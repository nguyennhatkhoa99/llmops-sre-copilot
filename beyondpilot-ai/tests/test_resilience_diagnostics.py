from __future__ import annotations

import asyncio
import logging

import httpx
import pytest

from beyondpilot.core.diagnostics import log_scope, record_tokens, safe_fields, track_tokens
from beyondpilot.core.errors import AppError, ErrorCode
from beyondpilot.core.resilience import RetryableError, call_with_retry
from beyondpilot.core.settings import ResilienceConfig

CFG = ResilienceConfig(attempts=3, base_delay=0.5, max_delay=4, timeout=1)


class Sleeps:
    def __init__(self):
        self.calls: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.calls.append(delay)


def flaky(failures: list[BaseException], result="ok"):
    calls = {"n": 0}

    async def fn():
        calls["n"] += 1
        if failures:
            raise failures.pop(0)
        return result

    return fn, calls


async def test_retries_then_succeeds_with_backoff():
    fn, calls = flaky([RetryableError(), RetryableError()])
    sleeps = Sleeps()
    assert await call_with_retry(fn, config=CFG, op="t", sleep=sleeps) == "ok"
    assert calls["n"] == 3
    assert sleeps.calls == [0.5, 1.0]


async def test_retry_after_wins_and_is_capped():
    fn, _ = flaky([RetryableError(retry_after=2), RetryableError(retry_after=60)])
    sleeps = Sleeps()
    await call_with_retry(fn, config=CFG, op="t", sleep=sleeps)
    assert sleeps.calls == [2.0, 4.0]


async def test_retry_after_from_http_header():
    request = httpx.Request("POST", "http://litellm/v1/chat/completions")
    response = httpx.Response(429, headers={"Retry-After": "3"}, request=request)
    fn, _ = flaky([httpx.HTTPStatusError("429", request=request, response=response)])
    sleeps = Sleeps()
    await call_with_retry(fn, config=CFG, op="t", sleep=sleeps)
    assert sleeps.calls == [3.0]


async def test_gives_up_with_app_error():
    fn, calls = flaky([RetryableError()] * 5)
    with pytest.raises(AppError) as info:
        await call_with_retry(fn, config=CFG, op="t", sleep=Sleeps())
    assert info.value.code is ErrorCode.UPSTREAM_UNAVAILABLE
    assert calls["n"] == 3


async def test_timeout_maps_to_upstream_timeout():
    async def slow():
        await asyncio.sleep(5)

    cfg = ResilienceConfig(attempts=2, base_delay=0, timeout=0.01)
    with pytest.raises(AppError) as info:
        await call_with_retry(slow, config=cfg, op="t", sleep=Sleeps())
    assert info.value.code is ErrorCode.UPSTREAM_TIMEOUT


async def test_non_retryable_fails_fast():
    request = httpx.Request("GET", "http://x")
    response = httpx.Response(400, request=request)
    fn, calls = flaky([httpx.HTTPStatusError("400", request=request, response=response)])
    with pytest.raises(AppError) as info:
        await call_with_retry(fn, config=CFG, op="t", sleep=Sleeps())
    assert info.value.code is ErrorCode.UPSTREAM_ERROR
    assert calls["n"] == 1


async def test_only_one_retry_layer():
    inner_fn, inner_calls = flaky([RetryableError()] * 10)

    async def outer():
        return await call_with_retry(inner_fn, config=CFG, op="inner", sleep=Sleeps())

    with pytest.raises(AppError):
        await call_with_retry(outer, config=CFG, op="outer", sleep=Sleeps())
    # 3 outer attempts, each calling the inner function once: 3, not 9.
    assert inner_calls["n"] == 3


async def test_app_error_is_not_retried():
    fn, calls = flaky([AppError(ErrorCode.UNSAFE_INPUT)])
    with pytest.raises(AppError):
        await call_with_retry(fn, config=CFG, op="t", sleep=Sleeps())
    assert calls["n"] == 1


def test_safe_fields_drops_content():
    out = safe_fields(
        {
            "prompt": "bí mật",
            "answer": "x",
            "doc_id": "d1",
            "top_k": 5,
            "long": "a" * 500,
            "multi": "a\nb",
        }
    )
    assert out == {"doc_id": "d1", "top_k": 5}


def test_log_scope_logs_no_content(caplog):
    caplog.set_level(logging.INFO, logger="beyondpilot")
    with log_scope(
        "rag.answer", question="lương của giám đốc là bao nhiêu", workspace="w1"
    ) as extra:
        extra["hits"] = 3
    line = caplog.records[-1].getMessage()
    assert "lương" not in line
    assert "workspace=w1" in line and "hits=3" in line and "status=ok" in line


def test_log_scope_records_error_code(caplog):
    caplog.set_level(logging.INFO, logger="beyondpilot")
    with pytest.raises(AppError):
        with log_scope("x"):
            raise AppError(ErrorCode.UPSTREAM_TIMEOUT)
    line = caplog.records[-1].getMessage()
    assert "status=error" in line and "error_code=UPSTREAM_TIMEOUT" in line


def test_token_tracking():
    record_tokens(5, 5)  # outside a tracker: ignored
    with track_tokens() as usage:
        record_tokens(10, 20, "qwen3")
        record_tokens(1, 2, "qwen3")
    assert usage.total == 33
    assert usage.by_model == {"qwen3": 33}
