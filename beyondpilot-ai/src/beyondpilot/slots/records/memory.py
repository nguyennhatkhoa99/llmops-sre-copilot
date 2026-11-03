"""Record store in memory, for local runs and tests."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any


class Impl:
    def __init__(self) -> None:
        self._data: dict[str, dict[str, dict[str, Any]]] = {}

    async def save(self, collection: str, key: str, data: Mapping[str, Any]) -> None:
        self._data.setdefault(collection, {})[key] = copy.deepcopy(dict(data))

    async def get(self, collection: str, key: str) -> dict[str, Any] | None:
        value = self._data.get(collection, {}).get(key)
        return copy.deepcopy(value) if value is not None else None

    async def list(self, collection: str, prefix: str = "") -> list[dict[str, Any]]:
        items = self._data.get(collection, {})
        return [copy.deepcopy(v) for k, v in sorted(items.items()) if k.startswith(prefix)]
