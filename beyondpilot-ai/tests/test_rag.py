from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import httpx
import pytest

from beyondpilot.core.contracts import AccessFilter, Chunk, Document, Hit, LlmResult, Message
from beyondpilot.core.errors import AppError, ErrorCode
from beyondpilot.core.registry import Registry
from beyondpilot.core.settings import ResilienceConfig, parse_settings
from beyondpilot.modules.rag import RagPipeline, cache_key, chunk_document, index_documents
from beyondpilot.modules.rag.citations import check_citations, sentences
from beyondpilot.modules.rag.prompts import NO_ANSWER
from beyondpilot.slots.cache import memory as memory_cache
from beyondpilot.slots.cache import valkey
from beyondpilot.slots.embedder import hashing
from beyondpilot.slots.embedder import litellm as litellm_embedder
from beyondpilot.slots.reranker import passthrough, tei
from beyondpilot.slots.retriever.hybrid import Impl as Hybrid
from beyondpilot.slots.retriever.hybrid import fuse, normalise
from beyondpilot.slots.store import memory as memory_store
from beyondpilot.slots.store import pgvector

RES = ResilienceConfig(attempts=2, base_delay=0, timeout=2)

POLICY = """# Chính sách đổi trả
## Hàng hư hỏng
Đại lý được đổi hàng hư hỏng trong vòng 7 ngày kể từ ngày nhận hàng.

Mỗi lần đổi tối đa 50 thùng và cần biên bản có chữ ký của nhân viên giao hàng.

## Hàng cận date
Hàng còn dưới 30 ngày hạn sử dụng được giảm giá 20%.
"""

SECRET = """# Lương ban giám đốc
Lương giám đốc kinh doanh là 900 triệu đồng mỗi năm. Đổi trả hàng hư hỏng không áp dụng.
"""


def c(id_: str, text: str, ws: str = "w1", doc: str = "d1") -> Chunk:
    return Chunk(id_, doc, ws, text)


class ScriptedLlm:
    """Returns the scripted answers in order and records every prompt."""

    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.calls: list[list[Message]] = []

    async def complete(self, messages: Sequence[Message], **options: Any) -> LlmResult:
        self.calls.append(list(messages))
        return LlmResult(self.answers.pop(0), model="scripted")


async def make_pipeline(llm, *, cache=None, rewrite=False, max_retries=2, top_k=3):
    store = memory_store.Impl()
    embedder = hashing.Impl()
    await index_documents([Document("chinh-sach", POLICY)], "w1", embedder, store)
    await index_documents([Document("luong", SECRET)], "w2", embedder, store)
    retriever = Hybrid(embedder=embedder, store=store, alpha=0.5)
    pipeline = RagPipeline(
        llm=llm,
        retriever=retriever,
        reranker=passthrough.Impl(),
        cache=cache,
        top_k=top_k,
        rewrite=rewrite,
        max_retries=max_retries,
    )
    return pipeline, store


# ---------- slots ----------


async def test_memory_store_enforces_access():
    store = memory_store.Impl()
    emb = hashing.Impl()
    chunks = [
        c("a", "đổi trả hàng", "w1", "d1"),
        c("b", "đổi trả hàng", "w1", "d2"),
        c("x", "đổi trả hàng", "w2", "d9"),
    ]
    await store.upsert(chunks, await emb.embed([ch.text for ch in chunks]))
    vec = (await emb.embed(["đổi trả"]))[0]
    for search in (
        store.search_vector(vec, AccessFilter("w1"), 10),
        store.search_text("đổi trả", AccessFilter("w1"), 10),
    ):
        assert {h.chunk.id for h in await search} == {"a", "b"}
    only_d2 = await store.search_text("đổi trả", AccessFilter("w1", ["d2"]), 10)
    assert [h.chunk.id for h in only_d2] == ["b"]


async def test_bm25_prefers_rarer_terms():
    store = memory_store.Impl()
    chunks = [c("1", "hàng hàng hàng"), c("2", "hàng cận date"), c("3", "hàng mới")]
    await store.upsert(chunks, [[1.0]] * 3)
    hits = await store.search_text("cận date", AccessFilter("w1"), 3)
    assert hits[0].chunk.id == "2"


def test_fusion_is_convex_and_normalised():
    a, b, d = c("a", ""), c("b", ""), c("d", "")
    vec = [Hit(a, 0.9), Hit(b, 0.5)]
    txt = [Hit(b, 12.0), Hit(d, 3.0)]
    assert normalise(vec) == {"a": 1.0, "b": 0.0}
    fused = {h.chunk.id: h.score for h in fuse(vec, txt, alpha=0.5)}
    assert fused == {"a": 0.5, "b": 0.5, "d": 0.0}
    assert [h.chunk.id for h in fuse(vec, txt, alpha=1.0)][0] == "a"
    assert [h.chunk.id for h in fuse(vec, txt, alpha=0.0)][0] == "b"


def test_hybrid_rejects_bad_alpha():
    with pytest.raises(TypeError):
        Hybrid(embedder=hashing.Impl(), store=memory_store.Impl(), alpha=1.5)


async def test_tei_reranker_keeps_only_scored_hits():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(
            200,
            json=[
                {"index": 1, "score": 0.9},
                {"index": 0, "score": 0.2},
                {"index": 7, "score": 0.99},
            ],
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    rr = tei.Impl(base_url="http://tei/", resilience=RES, client=client)
    hits = [Hit(c("a", "A"), 1.0), Hit(c("b", "B"), 0.5)]
    out = await rr.rerank("q", hits, 5)
    assert [h.chunk.id for h in out] == ["b", "a"]  # index 7 does not exist and is dropped
    assert seen["texts"] == ["A", "B"]


async def test_tei_reranker_retries_on_503():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503)
        return httpx.Response(200, json=[{"index": 0, "score": 0.5}])

    rr = tei.Impl(
        base_url="http://tei",
        resilience=RES,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    assert len(await rr.rerank("q", [Hit(c("a", "A"), 1.0)], 1)) == 1
    assert calls["n"] == 2


async def test_litellm_embedder_batches_and_orders():
    class Data:
        def __init__(self, index, embedding):
            self.index, self.embedding = index, embedding

    class Embeddings:
        def __init__(self):
            self.inputs = []

        async def create(self, model, input):
            self.inputs.append(list(input))
            return type(
                "R",
                (),
                {"data": [Data(i, [float(len(t))]) for i, t in reversed(list(enumerate(input)))]},
            )()

    client = type("C", (), {"embeddings": Embeddings()})()
    emb = litellm_embedder.Impl(
        base_url="x", api_key="k", resilience=RES, batch_size=2, client=client
    )
    out = await emb.embed(["a", "bb", "ccc"])
    assert out == [[1.0], [2.0], [3.0]]
    assert client.embeddings.inputs == [["a", "bb"], ["ccc"]]


async def test_memory_cache_expiry_and_copy():
    cache = memory_cache.Impl()
    await cache.set("k", {"a": [1]}, ttl=60)
    got = await cache.get("k")
    got["a"].append(2)
    assert await cache.get("k") == {"a": [1]}
    await cache.set("old", {"a": 1}, ttl=-1)
    assert await cache.get("old") is None


async def test_valkey_cache_failures_are_misses():
    class Broken:
        async def get(self, key):
            raise ConnectionError("down")

        async def set(self, key, value, ex):
            raise ConnectionError("down")

    class Fake:
        def __init__(self):
            self.data = {}

        async def get(self, key):
            return self.data.get(key)

        async def set(self, key, value, ex):
            self.data[key] = value

    broken = valkey.Impl(client=Broken())
    assert await broken.get("k") is None
    await broken.set("k", {"a": 1}, 10)  # no exception
    fake = Fake()
    ok = valkey.Impl(client=fake, prefix="bp:")
    await ok.set("k", {"answer": "Đổi trong 7 ngày [1]"}, 10)
    assert "bp:k" in fake.data
    assert await ok.get("k") == {"answer": "Đổi trong 7 ngày [1]"}


class FakeCursor:
    def __init__(self, log, rows):
        self.log, self.rows = log, rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, query, params):
        self.log.append((query, params))

    async def executemany(self, query, rows):
        self.log.append((query, rows))

    async def fetchall(self):
        return self.rows


class FakePool:
    def __init__(self, rows=()):
        self.log, self.rows = [], list(rows)

    def connection(self):
        pool = self

        class Conn:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            def cursor(self):
                return FakeCursor(pool.log, pool.rows)

        return Conn()


async def test_pgvector_filters_by_access_in_sql():
    pool = FakePool(rows=[("c1", "d1", "w1", "đổi trả", {"section": "x"}, 0.8)])
    store = pgvector.Impl(pool=pool, table="rag_chunks")
    hits = await store.search_vector([0.1, 0.2], AccessFilter("w1", ["d1"]), 5)
    query, params = pool.log[-1]
    assert "workspace_id = %(ws)s" in query and "ANY(%(docs)s)" in query
    assert params["ws"] == "w1" and params["docs"] == ["d1"] and params["vec"] == "[0.1,0.2]"
    assert hits[0].chunk.workspace_id == "w1"

    await store.search_text("đổi trả'; DROP TABLE x;--", AccessFilter("w1"), 5)
    query, params = pool.log[-1]
    assert "DROP" not in query  # user text is only ever a bound parameter
    assert params["docs"] is None


def test_pgvector_rejects_unsafe_table_name():
    with pytest.raises(AppError):
        pgvector.Impl(pool=FakePool(), table="chunks; drop table users")


# ---------- chunking and citations ----------


def test_chunks_keep_heading_path():
    chunks = chunk_document(Document("cs", POLICY), "w1")
    assert chunks[0].text.startswith("Chính sách đổi trả > Hàng hư hỏng\n")
    assert all(ch.workspace_id == "w1" and ch.doc_id == "cs" for ch in chunks)
    assert any("Hàng cận date" in ch.text for ch in chunks)


def test_long_sections_split_on_paragraphs():
    text = "# A\n" + "\n\n".join("đoạn " + str(i) + " " + "x" * 300 for i in range(6))
    chunks = chunk_document(Document("d", text), "w1", max_chars=700)
    assert len(chunks) >= 3 and all(len(ch.text) <= 720 for ch in chunks)


def test_sentence_split_attaches_trailing_citation():
    assert sentences("Câu một [1]. Câu hai.\n[2]\n- Ý ba [3]") == [
        "Câu một [1].",
        "Câu hai. [2]",
        "Ý ba [3]",
    ]


SOURCES = [
    Hit(c("a", "Đổi hàng hư hỏng trong vòng 7 ngày, tối đa 50 thùng."), 1),
    Hit(c("b", "Hàng còn dưới 30 ngày hạn được giảm giá 20%."), 1),
]


def test_citation_check_passes_good_answer():
    check = check_citations(
        "Đại lý được đổi trong 7 ngày [1]. Hàng cận date giảm 20% [2].", SOURCES
    )
    assert check.ok, check.errors
    assert check.used == [1, 2]


@pytest.mark.parametrize(
    ("answer", "needle"),
    [
        ("Đại lý được đổi trong 7 ngày.", "thiếu trích dẫn"),
        ("Đại lý được đổi trong 7 ngày [3].", "không tồn tại"),
        ("Đại lý được đổi trong 10 ngày [1].", "số 10"),
        ("Hàng cận date giảm 20% [1].", "số 20%"),  # 20% is in source 2, not the cited source 1
    ],
)
def test_citation_check_errors(answer, needle):
    check = check_citations(answer, SOURCES)
    assert not check.ok and any(needle in e for e in check.errors), check.errors


def test_no_answer_needs_no_citation():
    assert check_citations(NO_ANSWER, SOURCES).ok


# ---------- pipeline ----------


async def test_answer_with_citations():
    llm = ScriptedLlm("Đại lý được đổi hàng hư hỏng trong vòng 7 ngày [1].")
    pipeline, _ = await make_pipeline(llm)
    result = await pipeline.answer("Hàng hư hỏng được đổi trong bao lâu?", AccessFilter("w1"))
    assert result.attempts == 1 and not result.used_fallback
    assert result.citations[0].index == 1 and result.citations[0].doc_id == "chinh-sach"
    prompt = llm.calls[0][1].content
    assert '<nguon so="1"' in prompt and "7 ngày" in prompt


async def test_other_workspace_never_reaches_the_model():
    llm = ScriptedLlm(NO_ANSWER)
    pipeline, _ = await make_pipeline(llm)
    await pipeline.answer("Lương giám đốc kinh doanh là bao nhiêu?", AccessFilter("w1"))
    prompt = llm.calls[0][1].content
    assert "900 triệu" not in prompt and "Lương ban giám đốc" not in prompt


async def test_doc_filter_limits_sources():
    llm = ScriptedLlm(NO_ANSWER)
    pipeline, _ = await make_pipeline(llm)
    result = await pipeline.answer("đổi trả", AccessFilter("w1", ["khong-co"]))
    assert result.answer == NO_ANSWER and llm.calls == []  # nothing visible, model not called


async def test_retry_with_feedback_then_success():
    llm = ScriptedLlm("Đổi trong 10 ngày [1].", "Đại lý được đổi trong vòng 7 ngày [1].")
    pipeline, _ = await make_pipeline(llm)
    result = await pipeline.answer("Hàng hư hỏng đổi trong bao lâu?", AccessFilter("w1"))
    assert result.attempts == 2 and not result.used_fallback
    assert "số 10" in llm.calls[1][1].content  # the errors were sent back


async def test_fallback_after_max_retries():
    llm = ScriptedLlm("bịa 1", "bịa 2", "bịa 3")
    pipeline, _ = await make_pipeline(llm, max_retries=2)
    result = await pipeline.answer("Hàng hư hỏng đổi trong bao lâu?", AccessFilter("w1"))
    assert result.used_fallback and result.attempts == 3 and len(llm.calls) == 3
    assert result.answer.startswith("Mình chưa tạo được")
    assert result.citations and all(ci.doc_id == "chinh-sach" for ci in result.citations)


async def test_cache_hit_skips_model_and_fallback_not_cached():
    cache = memory_cache.Impl()
    llm = ScriptedLlm("Đại lý được đổi trong vòng 7 ngày [1].")
    pipeline, _ = await make_pipeline(llm, cache=cache)
    q = "Hàng hư hỏng đổi trong bao lâu?"
    first = await pipeline.answer(q, AccessFilter("w1"))
    second = await pipeline.answer("  hàng hư hỏng ĐỔI trong bao lâu? ", AccessFilter("w1"))
    assert not first.cached and second.cached and second.answer == first.answer
    assert len(llm.calls) == 1

    llm2 = ScriptedLlm("x", "y", "z")
    pipeline2, _ = await make_pipeline(llm2, cache=cache)
    await pipeline2.answer("Cận date giảm bao nhiêu?", AccessFilter("w1"))
    assert await cache.get(cache_key("Cận date giảm bao nhiêu?", AccessFilter("w1"))) is None


def test_cache_key_separates_access():
    q = "đổi trả"
    keys = {
        cache_key(q, AccessFilter("w1")),
        cache_key(q, AccessFilter("w2")),
        cache_key(q, AccessFilter("w1", ["d1"])),
        cache_key(q, AccessFilter("w1", ["d2"])),
    }
    assert len(keys) == 4
    assert cache_key(q, AccessFilter("w1", ["a", "b"])) == cache_key(
        q, AccessFilter("w1", ["b", "a"])
    )


async def test_rewrite_used_for_search_only():
    llm = ScriptedLlm("chính sách đổi hàng hư hỏng", "Đại lý được đổi trong vòng 7 ngày [1].")
    pipeline, _ = await make_pipeline(llm, rewrite=True)
    await pipeline.answer("ê cho hỏi cái vụ đổi hàng hư á", AccessFilter("w1"))
    assert "ê cho hỏi" in llm.calls[1][1].content  # the answer prompt keeps the user's words


async def test_rewrite_failure_falls_back_to_question():
    class FailingRewrite(ScriptedLlm):
        async def complete(self, messages, **options):
            if len(self.calls) == 0:
                self.calls.append(list(messages))
                raise AppError(ErrorCode.UPSTREAM_TIMEOUT)
            return await super().complete(messages, **options)

    llm = FailingRewrite("Đại lý được đổi trong vòng 7 ngày [1].")
    pipeline, _ = await make_pipeline(llm, rewrite=True)
    result = await pipeline.answer("Hàng hư hỏng đổi trong bao lâu?", AccessFilter("w1"))
    assert not result.used_fallback


async def test_registry_wires_slot_references():
    settings = parse_settings(
        {
            "auth_disabled": True,
            "modules": {
                "rag": {
                    "options": {"top_k": 2},
                    "slots": {
                        "retriever": {
                            "type": "hybrid",
                            "params": {"embedder": "@embedder", "store": "@store"},
                        },
                        "embedder": {"type": "hashing"},
                        "store": {"type": "memory"},
                        "reranker": {"type": "passthrough"},
                        "llm": {"type": "echo"},
                        "cache": {"type": "memory"},
                    },
                }
            },
        },
        env={},
    )
    registry = Registry(settings)
    retriever = registry.get("rag", "retriever")
    assert retriever.store is registry.get("rag", "store")
    pipeline = RagPipeline.from_registry(registry, settings)
    assert pipeline.top_k == 2 and pipeline.cache is not None


@pytest.mark.parametrize(
    ("slots", "needle"),
    [
        (
            {"retriever": {"type": "hybrid", "params": {"embedder": "@nope", "store": "@nope"}}},
            "unknown slot",
        ),
        (
            {
                "a": {
                    "type": "hybrid",
                    "kind": "retriever",
                    "params": {"embedder": "@b", "store": "@b"},
                },
                "b": {
                    "type": "hybrid",
                    "kind": "retriever",
                    "params": {"embedder": "@a", "store": "@a"},
                },
            },
            "circular",
        ),
    ],
)
def test_registry_reference_errors(slots, needle):
    settings = parse_settings({"auth_disabled": True, "modules": {"rag": {"slots": slots}}}, env={})
    with pytest.raises(AppError) as info:
        Registry(settings)
    assert needle in info.value.detail
