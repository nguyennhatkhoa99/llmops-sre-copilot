"""Object detector served over HTTP (RT-DETR on the GPU node).

``POST {url}`` with the image as ``multipart/form-data`` field ``image``; the
server answers ``[{"label": str, "score": float, "box": [x1, y1, x2, y2]}]``.
"""

from __future__ import annotations

from typing import Any

import httpx

from beyondpilot.core.contracts import Detection
from beyondpilot.core.resilience import call_with_retry
from beyondpilot.core.settings import ResilienceConfig


class Impl:
    accepts_resilience = True

    def __init__(
        self,
        *,
        url: str,
        resilience: ResilienceConfig,
        min_score: float = 0.4,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.url, self.min_score, self.resilience = url, min_score, resilience
        self.client = client or httpx.AsyncClient()

    async def detect(self, image: bytes) -> list[Detection]:
        async def call() -> Any:
            response = await self.client.post(self.url, files={"image": ("image", image)})
            response.raise_for_status()
            return response.json()

        raw = await call_with_retry(call, config=self.resilience, op="detector.http")
        out = []
        for item in raw:
            score = float(item.get("score", 0))
            if score >= self.min_score:
                box = tuple(float(v) for v in item.get("box", (0, 0, 0, 0)))[:4]
                out.append(Detection(str(item["label"]), score, box))  # type: ignore[arg-type]
        return out
