"""Module F: forecast demand and allocate supply (use case forecast / optimise).

Data (named query) -> series per group -> anomalies (robust z-score) ->
backtest the model against seasonal naive on the last ``horizon`` points ->
metric gate -> forecast -> allocate supply -> explanation through the shared
writer (every number checked) -> log the run.

When the model misses the error threshold, or does worse than the baseline, its
forecast is not used: the baseline forecast is returned with ``published=False``
and the reason, so nobody plans on a bad model.

Series are declared in config::

    series:
      milo_theo_vung:
        query: ban_hang_ngay
        date_column: ngay
        value_column: so_thung
        group_column: vung
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from beyondpilot.core.contracts import Allocator, Forecaster, Llm, ModelRegistry, TableSource
from beyondpilot.core.diagnostics import log_scope
from beyondpilot.core.errors import AppError, ErrorCode
from beyondpilot.core.registry import Registry
from beyondpilot.core.settings import Settings
from beyondpilot.modules.writer import write_narrative
from beyondpilot.slots.forecaster.seasonal_naive import Impl as SeasonalNaive

MODULE = "forecast"


@dataclass
class SeriesSpec:
    query: str
    date_column: str
    value_column: str
    group_column: str | None = None
    allowed_params: Sequence[str] = ()


@dataclass
class GroupForecast:
    group: str
    forecast: list[float]
    demand: float
    allocation: float | None
    anomalies: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ForecastResult:
    series: str
    horizon: int
    groups: list[GroupForecast]
    model_mape: float
    baseline_mape: float
    published: bool
    reason: str
    explanation: str
    run_version: str


def mape(actual: Sequence[float], predicted: Sequence[float]) -> float:
    pairs = [(a, p) for a, p in zip(actual, predicted, strict=False) if a != 0]
    if not pairs:
        return 0.0 if all(p == 0 for p in predicted) else 100.0
    return 100 * sum(abs(a - p) / abs(a) for a, p in pairs) / len(pairs)


def anomalies(
    dates: Sequence[str], values: Sequence[float], season_length: int = 7, threshold: float = 3.5
) -> list[dict]:
    """Robust z-score on seasonal residuals.

    Each value is compared with the median of the same position in the season
    (same weekday), so a normal weekend peak is not an anomaly. The spread uses
    MAD; when MAD is 0 (a perfectly regular series) it falls back to the mean
    absolute deviation so a single spike is still caught.
    """
    if len(values) < max(5, 2 * season_length):
        return []
    by_position: dict[int, list[float]] = defaultdict(list)
    for i, v in enumerate(values):
        by_position[i % season_length].append(v)
    seasonal = {k: statistics.median(vs) for k, vs in by_position.items()}
    residuals = [v - seasonal[i % season_length] for i, v in enumerate(values)]
    center = statistics.median(residuals)
    mad = statistics.median(abs(r - center) for r in residuals)
    if mad > 0:
        scale = mad / 0.6745
    else:
        scale = statistics.fmean(abs(r - center) for r in residuals) * 1.2533
    if scale == 0:
        return []
    out = []
    for d, v, r in zip(dates, values, residuals, strict=True):
        z = (r - center) / scale
        if abs(z) > threshold:
            out.append({"date": d, "value": v, "z": round(z, 2)})
    return out


class ForecastModule:
    def __init__(
        self,
        *,
        source: TableSource,
        forecaster: Forecaster,
        allocator: Allocator,
        llm: Llm,
        series: Mapping[str, SeriesSpec],
        model_registry: ModelRegistry | None = None,
        season_length: int = 7,
        mape_threshold: float = 30.0,
        max_horizon: int = 90,
        max_retries: int = 2,
    ) -> None:
        self.source, self.forecaster, self.allocator, self.llm = source, forecaster, allocator, llm
        self.series = dict(series)
        self.model_registry = model_registry
        self.season_length, self.mape_threshold = season_length, mape_threshold
        self.max_horizon, self.max_retries = max_horizon, max_retries
        self.baseline = SeasonalNaive(season_length)

    @classmethod
    def from_registry(cls, registry: Registry, settings: Settings) -> ForecastModule:
        options = settings.modules[MODULE].options
        return cls(
            source=registry.get(MODULE, "source"),
            forecaster=registry.get(MODULE, "forecaster"),
            allocator=registry.get(MODULE, "allocator"),
            llm=registry.get(MODULE, "llm"),
            model_registry=registry.optional(MODULE, "model_registry"),
            series={k: SeriesSpec(**v) for k, v in options.get("series", {}).items()},
            season_length=int(options.get("season_length", 7)),
            mape_threshold=float(options.get("mape_threshold", 30)),
            max_horizon=int(options.get("max_horizon", 90)),
            max_retries=settings.validator.max_retries,
        )

    def _groups(self, spec: SeriesSpec, rows: Sequence[Mapping[str, Any]]) -> dict[str, list]:
        grouped: dict[str, dict[str, float]] = defaultdict(dict)
        for row in rows:
            group = str(row.get(spec.group_column, "tong")) if spec.group_column else "tong"
            try:
                value = float(row[spec.value_column])
            except (KeyError, TypeError, ValueError) as exc:
                raise AppError(ErrorCode.INVALID_INPUT, "bad value in source data") from exc
            date = str(row[spec.date_column])
            grouped[group][date] = grouped[group].get(date, 0.0) + value
        return {g: sorted(series.items()) for g, series in grouped.items()}

    async def run(
        self,
        series: str,
        *,
        horizon: int,
        supply: float | None = None,
        caps: Mapping[str, float] | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> ForecastResult:
        spec = self.series.get(series)
        if spec is None:
            raise AppError(ErrorCode.INVALID_INPUT, f"unknown series {series!r}")
        if not 1 <= horizon <= self.max_horizon:
            raise AppError(ErrorCode.INVALID_INPUT, f"horizon must be 1..{self.max_horizon}")
        params = dict(params or {})
        if set(params) - set(spec.allowed_params):
            raise AppError(ErrorCode.INVALID_INPUT, "params not allowed for this series")

        with log_scope("forecast.run", series=series, horizon=horizon) as extra:
            rows = await self.source.fetch_table(spec.query, params)
            groups = self._groups(spec, rows)
            needed = 2 * self.season_length + horizon
            short = [g for g, pts in groups.items() if len(pts) < needed]
            if not groups or short:
                raise AppError(
                    ErrorCode.INVALID_INPUT,
                    f"not enough history (need {needed} points) for {short or 'any'}",
                )

            actual, model_pred, base_pred = [], [], []
            for points in groups.values():
                values = [v for _, v in points]
                train, test = values[:-horizon], values[-horizon:]
                actual += test
                model_pred += await self.forecaster.forecast(train, horizon)
                base_pred += await self.baseline.forecast(train, horizon)
            model_mape, base_mape = mape(actual, model_pred), mape(actual, base_pred)
            published = model_mape <= self.mape_threshold and model_mape <= base_mape
            if published:
                reason = "model đạt ngưỡng và tốt hơn mô hình nền"
            elif model_mape > self.mape_threshold:
                reason = f"MAPE {model_mape:.1f}% vượt ngưỡng {self.mape_threshold:g}%"
            else:
                reason = "model không tốt hơn mô hình nền (seasonal naive)"
            chosen = self.forecaster if published else self.baseline

            results: dict[str, GroupForecast] = {}
            for group, points in groups.items():
                dates, values = [d for d, _ in points], [v for _, v in points]
                fc = [round(v, 3) for v in await chosen.forecast(values, horizon)]
                results[group] = GroupForecast(
                    group, fc, round(sum(fc), 3), None, anomalies(dates, values, self.season_length)
                )
            if supply is not None:
                alloc = await self.allocator.allocate(
                    {g: r.demand for g, r in results.items()}, supply, dict(caps or {})
                )
                for g, r in results.items():
                    r.allocation = alloc.get(g)

            facts: dict[str, float] = {
                "mape": round(model_mape, 2),
                "mape_nen": round(base_mape, 2),
            }
            labels = {"mape": "Sai số MAPE của model (%)", "mape_nen": "MAPE mô hình nền (%)"}
            for g, r in results.items():
                key = "".join(ch if ch.isalnum() else "_" for ch in g.lower())
                facts[f"nhu_cau_{key}"] = r.demand
                labels[f"nhu_cau_{key}"] = f"Nhu cầu dự báo {g}"
                if r.allocation is not None:
                    facts[f"phan_bo_{key}"] = r.allocation
                    labels[f"phan_bo_{key}"] = f"Phân bổ cho {g}"
            if supply is not None:
                facts["nguon_cung"], labels["nguon_cung"] = float(supply), "Tổng nguồn cung"
            written = await write_narrative(
                self.llm,
                facts,
                labels,
                f"Giải thích dự báo {horizon} kỳ tới cho {series}"
                + ("" if published else f"; nói rõ đang dùng mô hình nền vì {reason}")
                + ". Nêu vùng thiếu hàng nếu có.",
                max_retries=self.max_retries,
            )
            version = ""
            if self.model_registry is not None:
                version = await self.model_registry.log(
                    series,
                    {"mape": model_mape, "baseline_mape": base_mape},
                    {"horizon": horizon, "published": published, "groups": len(results)},
                )
            extra.update(mape=round(model_mape, 2), published=published, groups=len(results))
            return ForecastResult(
                series,
                horizon,
                list(results.values()),
                round(model_mape, 2),
                round(base_mape, 2),
                published,
                reason,
                written.text,
                version,
            )
