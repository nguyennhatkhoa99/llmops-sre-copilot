"""Keeps the retriever's order. For local runs and for turning reranking off."""

from __future__ import annotations

from collections.abc import Sequence

from beyondpilot.core.contracts import Hit


class Impl:
    async def rerank(self, query: str, hits: Sequence[Hit], top_k: int) -> list[Hit]:
        return list(hits)[:top_k]
