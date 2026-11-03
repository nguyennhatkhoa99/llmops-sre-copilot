"""Reranker on Hugging Face TEI (bge-reranker-v2-m3), ``POST /rerank``.

TEI answers ``[{"index": i, "score": s}, ...]``. Only hits TEI scored are kept,
in TEI's order, so the reranker can drop but never add a chunk.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import httpx

from beyondpilot.core.contracts import Hit
from beyondpilot.core.resilience import call_with_retry
from beyondpilot.core.settings import ResilienceConfig


class Impl:
    accepts_resilience = True

    def __init__(
        self,
        *,
        base_url: str,
        resilience: ResilienceConfig,
        max_chars: int = 2000,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.url = base_url.rstrip("/") + "/rerank"
        self.max_chars = max_chars
        self.resilience = resilience
        self.client = client or httpx.AsyncClient()

    async def rerank(self, query: str, hits: Sequence[Hit], top_k: int) -> list[Hit]:
        if not hits:
            return []
        payload = {
            "query": query,
            "texts": [h.chunk.text[: self.max_chars] for h in hits],
            "raw_scores": False,
        }

        async def call() -> Any:
            response = await self.client.post(self.url, json=payload)
            response.raise_for_status()
            return response.json()

        ranked = await call_with_retry(call, config=self.resilience, op="reranker.tei")
        out: list[Hit] = []
        for item in sorted(ranked, key=lambda r: r["score"], reverse=True):
            index = item.get("index")
            if isinstance(index, int) and 0 <= index < len(hits):
                out.append(Hit(hits[index].chunk, float(item["score"])))
        return out[:top_k]
