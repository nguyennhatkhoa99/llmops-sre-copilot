"""Collects progress events in memory, for local runs and tests."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any


class Impl:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def notify(self, event: str, payload: Mapping[str, Any]) -> None:
        self.events.append({"event": event, **copy.deepcopy(dict(payload))})
