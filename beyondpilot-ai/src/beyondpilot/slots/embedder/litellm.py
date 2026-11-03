"""Embeddings through LiteLLM's OpenAI-compatible ``/embeddings`` (bge-m3 on TEI behind it)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from openai import AsyncOpenAI

from beyondpilot.core.resilience import call_with_retry
from beyondpilot.core.settings import ResilienceConfig


class Impl:
    accepts_resilience = True

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str = "bge-m3",
        resilience: ResilienceConfig,
        batch_size: int = 32,
        client: AsyncOpenAI | None = None,
    ) -> None:
        self.model = model
        self.batch_size = batch_size
        self.resilience = resilience
        self.client = client or AsyncOpenAI(base_url=base_url, api_key=api_key, max_retries=0)

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = list(texts[start : start + self.batch_size])

            async def call(batch: list[str] = batch) -> Any:
                return await self.client.embeddings.create(model=self.model, input=batch)

            response = await call_with_retry(call, config=self.resilience, op="embedder.litellm")
            ordered = sorted(response.data, key=lambda d: d.index)
            vectors.extend(list(d.embedding) for d in ordered)
        return vectors
