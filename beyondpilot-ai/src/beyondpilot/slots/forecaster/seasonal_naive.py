"""Seasonal naive forecast: next value = value one season ago. Also the baseline every
model in module F must beat."""

from __future__ import annotations

from collections.abc import Sequence


class Impl:
    def __init__(self, season_length: int = 7) -> None:
        self.season_length = season_length

    async def forecast(self, history: Sequence[float], horizon: int) -> list[float]:
        if not history:
            return [0.0] * horizon
        season = min(self.season_length, len(history))
        last = list(history[-season:])
        return [float(last[i % season]) for i in range(horizon)]
