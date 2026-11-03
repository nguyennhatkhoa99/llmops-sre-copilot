"""Deterministic bag-of-words embedder for local runs and tests.

Words are hashed into ``dimensions`` buckets and the vector is L2-normalised, so
texts that share words get a high cosine score. It has no idea of meaning; use
``litellm`` (bge-m3) for anything real.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence

_WORD_RE = re.compile(r"\w+", re.UNICODE)


class Impl:
    def __init__(self, dimensions: int = 256) -> None:
        self.dimensions = dimensions

    def _vector(self, text: str) -> list[float]:
        vec = [0.0] * self.dimensions
        for word in _WORD_RE.findall(text.lower()):
            digest = hashlib.sha1(word.encode("utf-8")).digest()
            vec[int.from_bytes(digest[:4], "big") % self.dimensions] += 1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]
