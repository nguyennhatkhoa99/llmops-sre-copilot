from __future__ import annotations

import json

import httpx
import pytest

from beyondpilot.core.contracts import Message
from beyondpilot.core.errors import AppError
from beyondpilot.core.jsonout import complete_json, extract_json
from beyondpilot.core.registry import Registry
from beyondpilot.core.settings import ResilienceConfig, parse_settings
from beyondpilot.slots.facts import duckdb as duckdb_facts
from beyondpilot.slots.facts import python as python_facts
from beyondpilot.slots.llm.litellm import _message
from beyondpilot.slots.notifier import webhook as notifier_webhook
from beyondpilot.slots.records import memory as records_memory
from beyondpilot.slots.records import postgres as records_pg
from beyondpilot.slots.review import webhook as review_webhook
from beyondpilot.slots.table_source import csv as csv_source
from beyondpilot.slots.table_source import memory as memory_source
from tests.fakes import ScriptedLlm

RES = ResilienceConfig(attempts=2, base_delay=0, timeout=2)


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ('{"a": 1}', {"a": 1}),
        ('Đây là kết quả:\n```json\n{"a": [1, 2]}\n```', {"a": [1, 2]}),
        ('trước {"x": "}"} sau', {"x": "}"}),
        ("[1, 2, 3]", [1, 2, 3]),
    ],
)
def test_extract_json(text, value):
    assert extract_json(text) == value


def test_extract_json_fails():
    with pytest.raises(ValueError):
        extract_json("không có json")


async def test_complete_json_retries_with_errors():
    llm = ScriptedLlm("không phải json", {"n": "x"}, {"n": 3})

    def check(v):
        return [] if isinstance(v.get("n"), int) else ["n phải là số"]

    result = await complete_json(llm, [Message("user", "q")], check, max_retries=2)
    assert result.ok and result.value == {"n": 3} and result.attempts == 3
    assert "n phải là số" in llm.prompt(2)


async def test_complete_json_gives_up_without_raising():
    llm = ScriptedLlm("x", "y")
    result = await complete_json(llm, [Message("user", "q")], lambda v: [], max_retries=1)
    assert not result.ok and result.errors


def test_registry_cross_module_reference():
    settings = parse_settings(
        {
            "auth_disabled": True,
            "modules": {
                "rag": {"slots": {"store": {"type": "memory"}, "embedder": {"type": "hashing"}}},
                "documents": {
                    "slots": {
                        "retriever": {
                            "type": "hybrid",
                            "params": {"embedder": "@rag.embedder", "store": "@rag.store"},
                        }
                    }
                },
            },
        },
        env={},
    )
    registry = Registry(settings)
    assert registry.get("documents", "retriever").store is registry.get("rag", "store")


def test_registry_unknown_cross_module_reference():
    settings = parse_settings(
        {
            "auth_disabled": True,
            "modules": {
                "documents": {
                    "slots": {
                        "retriever": {
                            "type": "hybrid",
                            "params": {"embedder": "@rag.e", "store": "@rag.s"},
                        }
                    }
                }
            },
        },
        env={},
    )
    with pytest.raises(AppError) as info:
        Registry(settings)
    assert "rag.e" in info.value.detail


def test_llm_message_with_images():
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 10
    out = _message(Message("user", "đọc ảnh", images=(png,)))
    assert out["content"][0] == {"type": "text", "text": "đọc ảnh"}
    assert out["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert _message(Message("user", "x")) == {"role": "user", "content": "x"}


async def test_review_webhook_sends_one_idempotency_key_across_retries():
    keys = []

    def handler(request: httpx.Request) -> httpx.Response:
        keys.append(request.headers["Idempotency-Key"])
        assert request.headers["X-API-Key"] == "backend-key"
        if len(keys) == 1:
            return httpx.Response(503)
        return httpx.Response(201, json={"id": "rv-9"})

    review = review_webhook.Impl(
        base_url="http://backend/api",
        api_key="backend-key",
        resilience=RES,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    assert await review.submit("report", {"a": 1}) == "rv-9"
    assert len(keys) == 2 and keys[0] == keys[1]


async def test_notifier_failure_is_swallowed():
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(500)

    notifier = notifier_webhook.Impl(
        base_url="http://backend",
        api_key="k",
        resilience=RES,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    await notifier.notify("read", {"seq": 2, "doc_id": "d"})  # no exception
    assert bodies[0]["seq"] == 2 and bodies[0]["event_id"]


async def test_records_memory_and_prefix():
    records = records_memory.Impl()
    await records.save("c", "s1:a", {"v": 1})
    await records.save("c", "s2:a", {"v": 2})
    assert await records.get("c", "s1:a") == {"v": 1}
    assert [r["v"] for r in await records.list("c", "s1:")] == [1]


async def test_records_postgres_escapes_like_prefix():
    log = []

    class Cur:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, q, p):
            log.append((q, p))

        async def fetchall(self):
            return [('{"v": 1}',)]

    class Pool:
        def connection(self):
            class C:
                async def __aenter__(self):
                    return self

                async def __aexit__(self, *a):
                    return False

                def cursor(self):
                    return Cur()

            return C()

    store = records_pg.Impl(pool=Pool())
    assert await store.list("c", "50%_off") == [{"v": 1}]
    assert log[-1][1]["p"] == "50\\%\\_off%"


async def test_table_sources(tmp_path):
    mem = memory_source.Impl({"sales": [{"q": "Q3", "rev": 10}, {"q": "Q2", "rev": 8}]})
    assert await mem.fetch_table("sales", {"q": "Q3"}) == [{"q": "Q3", "rev": 10}]
    with pytest.raises(AppError):
        await mem.fetch_table("nope", {})
    path = tmp_path / "s.csv"
    path.write_text("q,rev\nQ3,10.5\nQ2,8\n", encoding="utf-8")
    rows = await csv_source.Impl({"sales": str(path)}).fetch_table("sales", {})
    assert rows[0] == {"q": "Q3", "rev": 10.5}


ROWS = [
    {"q": "Q3", "rev": 1250.0, "region": "Bắc"},
    {"q": "Q2", "rev": 1000.0, "region": "Bắc"},
    {"q": "Q3", "rev": 750.0, "region": "Nam"},
]


async def test_python_facts():
    facts = await python_facts.Impl().compute(
        ROWS,
        {
            "rev_q3": {"agg": "sum", "column": "rev", "where": {"q": "Q3"}},
            "n": {"agg": "count"},
            "missing": {"agg": "sum", "column": "rev", "where": {"q": "Q9"}},
        },
    )
    assert facts == {"rev_q3": 2000.0, "n": 3.0}


async def test_duckdb_facts():
    facts = await duckdb_facts.Impl().compute(
        ROWS,
        {
            "rev_q3": "SELECT sum(rev) FROM t WHERE q = 'Q3'",
            "bac": "SELECT avg(rev) FROM t WHERE region = 'Bắc'",
        },
    )
    assert facts == {"rev_q3": 2000.0, "bac": 1125.0}
    with pytest.raises(AppError):
        await duckdb_facts.Impl().compute(ROWS, {"bad": "SELECT nope FROM t"})
