"""In-memory store with cosine search and BM25, for local runs and tests.

Applies the access filter before scoring, the same way the pgvector store does
in SQL, so tests of the access rules mean something.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Sequence

from beyondpilot.core.contracts import AccessFilter, Chunk, Hit, allowed

_WORD_RE = re.compile(r"\w+", re.UNICODE)


def tokenize(text: str) -> list[str]:
    return _WORD_RE.findall(text.lower())


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


class Impl:
    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self._rows: dict[str, tuple[Chunk, list[float], list[str]]] = {}

    async def upsert(self, chunks: Sequence[Chunk], vectors: Sequence[Sequence[float]]) -> None:
        if len(chunks) != len(vectors):
            raise ValueError("chunks and vectors must have the same length")
        for chunk, vector in zip(chunks, vectors, strict=True):
            self._rows[chunk.id] = (chunk, list(vector), tokenize(chunk.text))

    def _visible(self, access: AccessFilter) -> list[tuple[Chunk, list[float], list[str]]]:
        return [row for row in self._rows.values() if allowed(row[0], access)]

    async def search_vector(
        self, vector: Sequence[float], access: AccessFilter, top_k: int
    ) -> list[Hit]:
        hits = [Hit(c, cosine(vector, v)) for c, v, _ in self._visible(access)]
        return sorted(hits, key=lambda h: h.score, reverse=True)[:top_k]

    async def search_text(self, query: str, access: AccessFilter, top_k: int) -> list[Hit]:
        rows = self._visible(access)
        terms = set(tokenize(query))
        if not rows or not terms:
            return []
        avg_len = sum(len(t) for _, _, t in rows) / len(rows)
        df = Counter(term for _, _, tokens in rows for term in set(tokens) if term in terms)
        hits = []
        for chunk, _, tokens in rows:
            tf = Counter(tokens)
            score = 0.0
            for term in terms:
                if not tf[term]:
                    continue
                idf = math.log(1 + (len(rows) - df[term] + 0.5) / (df[term] + 0.5))
                norm = tf[term] + self.k1 * (1 - self.b + self.b * len(tokens) / avg_len)
                score += idf * tf[term] * (self.k1 + 1) / norm
            if score > 0:
                hits.append(Hit(chunk, score))
        return sorted(hits, key=lambda h: h.score, reverse=True)[:top_k]
