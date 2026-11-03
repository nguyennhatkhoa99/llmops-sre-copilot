"""Facts from rows with simple aggregations, no extra packages.

Metric spec: ``{"agg": "sum|avg|count|min|max", "column": "revenue",
"where": {"quarter": "Q3"}}``. ``count`` needs no column.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from beyondpilot.core.errors import AppError, ErrorCode

_AGGS = {
    "sum": sum,
    "avg": lambda xs: sum(xs) / len(xs),
    "min": min,
    "max": max,
    "count": len,
}


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


class Impl:
    async def compute(
        self, rows: Sequence[Mapping[str, Any]], metrics: Mapping[str, Any]
    ) -> dict[str, float]:
        facts: dict[str, float] = {}
        for fact_id, spec in metrics.items():
            agg = spec.get("agg", "sum")
            if agg not in _AGGS:
                raise AppError(ErrorCode.CONFIG_INVALID, f"unknown aggregation {agg!r}")
            where = spec.get("where", {})
            selected = [r for r in rows if all(str(r.get(k)) == str(v) for k, v in where.items())]
            if agg == "count":
                facts[fact_id] = float(len(selected))
                continue
            values = [v for r in selected if (v := _num(r.get(spec["column"]))) is not None]
            if values:  # a metric with no data is left out rather than reported as 0
                facts[fact_id] = float(_AGGS[agg](values))
        return facts
