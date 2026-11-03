"""LightGBM on lag features, forecasting step by step (recursive).

Features for each point: the previous ``lags`` values plus the value one season
ago. Trained per call on the series given; small series fall back to seasonal
naive. Needs ``lightgbm`` (and ``numpy``).
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

import lightgbm as lgb
import numpy as np

from beyondpilot.slots.forecaster.seasonal_naive import Impl as SeasonalNaive


class Impl:
    def __init__(
        self, season_length: int = 7, lags: int = 7, n_estimators: int = 200, min_points: int = 30
    ) -> None:
        self.season_length, self.lags = season_length, lags
        self.n_estimators, self.min_points = n_estimators, min_points
        self._naive = SeasonalNaive(season_length)

    def _features(self, series: list[float], t: int) -> list[float]:
        lags = [series[t - k] for k in range(1, self.lags + 1)]
        return [*lags, series[t - self.season_length]]

    def _fit_predict(self, history: list[float], horizon: int) -> list[float]:
        start = max(self.lags, self.season_length)
        x = np.array([self._features(history, t) for t in range(start, len(history))])
        y = np.array(history[start:])
        params = {
            "objective": "regression",
            "learning_rate": 0.05,
            "num_leaves": 15,
            "min_data_in_leaf": 5,
            "verbose": -1,
        }
        model = lgb.train(params, lgb.Dataset(x, y), num_boost_round=self.n_estimators)
        series = list(history)
        for _ in range(horizon):
            series.append(float(model.predict(np.array([self._features(series, len(series))]))[0]))
        return [max(0.0, v) for v in series[len(history) :]]

    async def forecast(self, history: Sequence[float], horizon: int) -> list[float]:
        if len(history) < max(self.min_points, self.lags + self.season_length + 5):
            return await self._naive.forecast(history, horizon)
        return await asyncio.to_thread(self._fit_predict, [float(v) for v in history], horizon)
