"""Allocation as a linear program (OR-Tools GLOP; HiGHS works the same way).

Maximise the smallest fill rate across regions (fairness), then total shipped:
    max  M * t + sum(x)
    s.t. x_k <= min(demand_k, cap_k),  sum(x) <= supply,  x_k >= t * demand_k
Needs ``ortools``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping

from ortools.linear_solver import pywraplp

from beyondpilot.core.errors import AppError, ErrorCode


class Impl:
    def __init__(self, fairness_weight: float = 1e6) -> None:
        self.fairness_weight = fairness_weight

    def _solve(
        self, demand: Mapping[str, float], supply: float, caps: Mapping[str, float]
    ) -> dict[str, float]:
        solver = pywraplp.Solver.CreateSolver("GLOP")
        if solver is None:  # pragma: no cover - depends on the build
            raise AppError(ErrorCode.FEATURE_UNAVAILABLE, "GLOP solver unavailable")
        t = solver.NumVar(0, 1, "t")
        x = {}
        for k, d in demand.items():
            upper = max(0.0, min(float(d), float(caps.get(k, d))))
            x[k] = solver.NumVar(0, upper, f"x_{k}")
            if d > 0:
                solver.Add(x[k] >= t * float(d))
        solver.Add(sum(x.values()) <= max(0.0, float(supply)))
        solver.Maximize(self.fairness_weight * t + sum(x.values()))
        if solver.Solve() not in (pywraplp.Solver.OPTIMAL, pywraplp.Solver.FEASIBLE):
            raise AppError(ErrorCode.UPSTREAM_ERROR, "allocation has no solution")
        return {k: round(v.solution_value(), 6) for k, v in x.items()}

    async def allocate(
        self, demand: Mapping[str, float], supply: float, caps: Mapping[str, float]
    ) -> dict[str, float]:
        return await asyncio.to_thread(self._solve, demand, supply, caps)
