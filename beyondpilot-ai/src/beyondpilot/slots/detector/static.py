"""Returns fixed detections from config. For local runs and tests only."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from beyondpilot.core.contracts import Detection


class Impl:
    def __init__(self, detections: Sequence[Mapping[str, Any]] = ()) -> None:
        self.detections = [
            Detection(str(d["label"]), float(d.get("score", 1.0))) for d in detections
        ]

    async def detect(self, image: bytes) -> list[Detection]:
        return list(self.detections)
