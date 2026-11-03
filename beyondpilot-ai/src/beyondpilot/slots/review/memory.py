"""Review queue kept in memory, for local runs and tests."""

from __future__ import annotations

import copy
import uuid
from collections.abc import Mapping
from typing import Any


class Impl:
    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    async def submit(self, kind: str, payload: Mapping[str, Any]) -> str:
        review_id = f"rv-{uuid.uuid4().hex[:12]}"
        self.items.append({"id": review_id, "kind": kind, "payload": copy.deepcopy(dict(payload))})
        return review_id
