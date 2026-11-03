"""Model run log kept in memory, for local runs and tests."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class Impl:
    def __init__(self) -> None:
        self.runs: dict[str, list[dict[str, Any]]] = {}

    async def log(self, name: str, metrics: Mapping[str, float], params: Mapping[str, Any]) -> str:
        runs = self.runs.setdefault(name, [])
        version = str(len(runs) + 1)
        runs.append({"version": version, "metrics": dict(metrics), "params": dict(params)})
        return version

    async def latest(self, name: str) -> dict[str, Any] | None:
        runs = self.runs.get(name)
        return dict(runs[-1]) if runs else None
