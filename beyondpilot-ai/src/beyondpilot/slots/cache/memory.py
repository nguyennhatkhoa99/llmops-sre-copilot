"""In-process cache for local runs and tests. Not shared between replicas."""

from __future__ import annotations

import copy
import time
from collections.abc import Mapping
from typing import Any


class Impl:
    def __init__(self, max_items: int = 1000) -> None:
        self.max_items = max_items
        self._data: dict[str, tuple[float, dict[str, Any]]] = {}

    async def get(self, key: str) -> dict[str, Any] | None:
        item = self._data.get(key)
        if item is None:
            return None
        expires, value = item
        if expires < time.monotonic():
            self._data.pop(key, None)
            return None
        return copy.deepcopy(value)

    async def set(self, key: str, value: Mapping[str, Any], ttl: int) -> None:
        if len(self._data) >= self.max_items:
            self._data.pop(next(iter(self._data)))  # drop the oldest entry
        self._data[key] = (time.monotonic() + ttl, copy.deepcopy(dict(value)))
