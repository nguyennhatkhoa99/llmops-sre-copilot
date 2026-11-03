"""Linear soft-sensor: ``intercept + sum(coef * input)``, coefficients from config.

A placeholder until the LightGBM soft-sensor is trained on plant data; also
useful to test module G with a model whose answer is easy to check by hand.
"""

from __future__ import annotations

from collections.abc import Mapping

from beyondpilot.core.errors import AppError, ErrorCode


class Impl:
    def __init__(self, coefficients: Mapping[str, float], intercept: float = 0.0) -> None:
        self.coefficients = {k: float(v) for k, v in coefficients.items()}
        self.intercept = float(intercept)

    async def predict(self, inputs: Mapping[str, float]) -> float:
        missing = [k for k in self.coefficients if k not in inputs]
        if missing:
            raise AppError(ErrorCode.INVALID_INPUT, f"soft-sensor inputs missing: {missing}")
        return self.intercept + sum(c * float(inputs[k]) for k, c in self.coefficients.items())
