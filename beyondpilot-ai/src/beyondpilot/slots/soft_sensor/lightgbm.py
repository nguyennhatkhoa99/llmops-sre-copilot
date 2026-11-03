"""LightGBM soft-sensor loaded from a model file trained offline (module F's ML jobs).

``features`` lists the input names in the order the model was trained with.
Needs ``lightgbm`` and ``numpy``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence

import lightgbm as lgb
import numpy as np

from beyondpilot.core.errors import AppError, ErrorCode


class Impl:
    def __init__(self, model_path: str, features: Sequence[str]) -> None:
        self.booster = lgb.Booster(model_file=model_path)
        self.features = list(features)

    async def predict(self, inputs: Mapping[str, float]) -> float:
        missing = [f for f in self.features if f not in inputs]
        if missing:
            raise AppError(ErrorCode.INVALID_INPUT, f"soft-sensor inputs missing: {missing}")
        row = np.array([[float(inputs[f]) for f in self.features]])
        return float((await asyncio.to_thread(self.booster.predict, row))[0])
