"""Module B: answer a question from documents, with a citation on every sentence.

Slot chain (see docs/beyondpilot/images/core-flow.png):
0. cache       Valkey; key = question + workspace + allowed docs. Hit -> return.
1. rewriter    optional; the ``llm`` slot rewrites the question for search.
2-4. retriever hybrid search (embedder + store), filtered by access.
5. reranker    keeps the best ``top_k``.
6. synthesizer the ``llm`` slot answers from the sources.
Then the citation check; on failure the answer is regenerated with the errors,
up to ``max_retries`` times, then an extractive fallback is returned.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from beyondpilot.core.contracts import AccessFilter, Cache, Hit, Llm, Reranker, Retriever, allowed
from beyondpilot.core.diagnostics import log_scope, track_tokens
from beyondpilot.core.errors import AppError
from beyondpilot.core.registry import Registry
from beyondpilot.core.settings import Settings
from beyondpilot.modules.rag.citations import CITE_RE, check_citations
from beyondpilot.modules.rag.prompts import NO_ANSWER, answer_messages, rewrite_messages

MODULE = "rag"
CACHE_VERSION = "rag-v1"  # bump when prompts or the answer format change
FALLBACK_INTRO = "Mình chưa tạo được câu trả lời kiểm chứng được. Các đoạn liên quan nhất:"


@dataclass
class Citation:
    index: int
    doc_id: str
    chunk_id: str
    snippet: str


@dataclass
class RagAnswer:
    answer: str
    citations: list[Citation] = field(default_factory=list)
    cached: bool = False
    attempts: int = 0
    used_fallback: bool = False

    def to_cache(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("cached")
        return data

    @classmethod
    def from_cache(cls, data: dict[str, Any]) -> RagAnswer:
        return cls(
            answer=data["answer"],
            citations=[Citation(**c) for c in data.get("citations", [])],
            cached=True,
            attempts=int(data.get("attempts", 0)),
            used_fallback=bool(data.get("used_fallback", False)),
        )


def cache_key(question: str, access: AccessFilter) -> str:
    docs = sorted(set(access.doc_ids)) if access.doc_ids is not None else None
    raw = json.dumps(
        [CACHE_VERSION, " ".join(question.lower().split()), access.workspace_id, docs],
        ensure_ascii=False,
    )
    return "rag:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def snippet(text: str, limit: int = 240) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


class RagPipeline:
    def __init__(
        self,
        *,
        llm: Llm,
        retriever: Retriever,
        reranker: Reranker,
        cache: Cache | None = None,
        top_k: int = 5,
        candidates: int = 20,
        rewrite: bool = False,
        max_retries: int = 2,
        cache_ttl: int = 3600,
        temperature: float = 0.1,
    ) -> None:
        self.llm, self.retriever, self.reranker, self.cache = llm, retriever, reranker, cache
        self.top_k, self.candidates = top_k, candidates
        self.rewrite, self.max_retries = rewrite, max_retries
        self.cache_ttl, self.temperature = cache_ttl, temperature

    @classmethod
    def from_registry(cls, registry: Registry, settings: Settings) -> RagPipeline:
        options = settings.modules[MODULE].options
        return cls(
            llm=registry.get(MODULE, "llm"),
            retriever=registry.get(MODULE, "retriever"),
            reranker=registry.get(MODULE, "reranker"),
            cache=registry.get(MODULE, "cache") if registry.has(MODULE, "cache") else None,
            top_k=int(options.get("top_k", 5)),
            candidates=int(options.get("candidates", 20)),
            rewrite=bool(options.get("rewrite", False)),
            max_retries=settings.validator.max_retries,
            cache_ttl=int(options.get("cache_ttl", 3600)),
        )

    async def _rewrite(self, question: str) -> str:
        try:
            result = await self.llm.complete(rewrite_messages(question), temperature=0.0)
        except AppError:
            return question  # search with the original question rather than fail
        text = result.text.strip().strip('"')
        if not text or len(text) > 3 * len(question) + 50:
            return question
        return text

    async def _sources(self, query: str, question: str, access: AccessFilter) -> list[Hit]:
        hits = await self.retriever.retrieve(query, access, self.candidates)
        # Defence in depth: the store filters by access, and so do we.
        hits = [h for h in hits if allowed(h.chunk, access)]
        if not hits:
            return []
        ranked = await self.reranker.rerank(question, hits, self.top_k)
        return [h for h in ranked if allowed(h.chunk, access)][: self.top_k]

    def _fallback(self, hits: Sequence[Hit]) -> RagAnswer:
        lines = [FALLBACK_INTRO]
        lines += [
            f"- {snippet(CITE_RE.sub('', h.chunk.text))} [{i}]" for i, h in enumerate(hits, 1)
        ]
        citations = [
            Citation(i, h.chunk.doc_id, h.chunk.id, snippet(h.chunk.text))
            for i, h in enumerate(hits, 1)
        ]
        return RagAnswer("\n".join(lines), citations, used_fallback=True)

    async def answer(self, question: str, access: AccessFilter) -> RagAnswer:
        with (
            log_scope("rag.answer", workspace=access.workspace_id) as extra,
            track_tokens() as usage,
        ):
            key = cache_key(question, access)
            if self.cache is not None:
                hit = await self.cache.get(key)
                if hit is not None:
                    extra["cache"] = "hit"
                    return RagAnswer.from_cache(hit)
            extra["cache"] = "miss" if self.cache is not None else "off"

            query = await self._rewrite(question) if self.rewrite else question
            hits = await self._sources(query, question, access)
            extra["sources"] = len(hits)
            if not hits:
                return RagAnswer(NO_ANSWER, attempts=0)

            feedback: Sequence[str] = ()
            result: RagAnswer | None = None
            for attempt in range(1, self.max_retries + 2):
                draft = await self.llm.complete(
                    answer_messages(question, hits, feedback), temperature=self.temperature
                )
                check = check_citations(draft.text, hits)
                if check.ok:
                    citations = [
                        Citation(
                            n,
                            hits[n - 1].chunk.doc_id,
                            hits[n - 1].chunk.id,
                            snippet(hits[n - 1].chunk.text),
                        )
                        for n in check.used
                    ]
                    result = RagAnswer(draft.text.strip(), citations, attempts=attempt)
                    break
                feedback = check.errors
                extra["check_errors"] = len(check.errors)

            if result is None:
                result = self._fallback(hits)
                result.attempts = self.max_retries + 1
            extra.update(
                attempts=result.attempts, fallback=result.used_fallback, tokens=usage.total
            )
            if self.cache is not None and not result.used_fallback:
                await self.cache.set(key, result.to_cache(), self.cache_ttl)
            return result
