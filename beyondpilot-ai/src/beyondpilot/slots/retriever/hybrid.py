"""Hybrid retriever: vector search + full-text search, merged by convex fusion.

Both result lists are min-max normalised to 0..1, then combined as
``alpha * vector + (1 - alpha) * text``. A chunk found by only one search gets 0
for the other. ``alpha`` = 1 means vector only, 0 means keywords only.

Config: ``embedder: "@embedder"``, ``store: "@store"`` refer to sibling slots.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from beyondpilot.core.contracts import AccessFilter, Embedder, Hit, Store
from beyondpilot.core.diagnostics import log_scope


def normalise(hits: Sequence[Hit]) -> dict[str, float]:
    if not hits:
        return {}
    scores = [h.score for h in hits]
    low, high = min(scores), max(scores)
    if high == low:
        return {h.chunk.id: 1.0 for h in hits}
    return {h.chunk.id: (h.score - low) / (high - low) for h in hits}


def fuse(vector_hits: Sequence[Hit], text_hits: Sequence[Hit], alpha: float) -> list[Hit]:
    v, t = normalise(vector_hits), normalise(text_hits)
    chunks = {h.chunk.id: h.chunk for h in [*vector_hits, *text_hits]}
    fused = [
        Hit(chunk, alpha * v.get(cid, 0.0) + (1 - alpha) * t.get(cid, 0.0))
        for cid, chunk in chunks.items()
    ]
    return sorted(fused, key=lambda h: (-h.score, h.chunk.id))


class Impl:
    def __init__(
        self, *, embedder: Embedder, store: Store, alpha: float = 0.5, candidates: int = 20
    ) -> None:
        if not 0 <= alpha <= 1:
            raise TypeError("alpha must be between 0 and 1")
        self.embedder, self.store = embedder, store
        self.alpha, self.candidates = alpha, candidates

    async def retrieve(self, query: str, access: AccessFilter, top_k: int) -> list[Hit]:
        with log_scope("retriever.hybrid", top_k=top_k, alpha=self.alpha) as extra:
            [vector] = await self.embedder.embed([query])
            n = max(top_k, self.candidates)
            vector_hits, text_hits = await asyncio.gather(
                self.store.search_vector(vector, access, n),
                self.store.search_text(query, access, n),
            )
            extra.update(vector_hits=len(vector_hits), text_hits=len(text_hits))
            return fuse(vector_hits, text_hits, self.alpha)[:top_k]
