"""Module G: propose setpoints for a production line (use case advise_setpoint).

Read signals (OPC UA, read only) -> check they are fresh, good and in range ->
soft-sensor predicts the quality target -> simulate candidate setpoints inside
the safe range and the max step -> pick the best -> send to an engineer in
Review UI -> write an audit record.

The service never writes to the plant. ``applied`` is always false; an engineer
decides and changes the setpoint on the DCS.

Lines are declared in config::

    lines:
      line1:
        inputs: {nhiet_do_say: L1.DryerTemp, toc_do_bang_tai: L1.BeltSpeed}
        setpoints:
          nhiet_do_say: {min: 160, max: 190, max_step: 5}
        target: {name: do_am, goal: 3.0, max: 3.5}   # goal: "min", "max" or a number
        ranges: {toc_do_bang_tai: [0.5, 2.0]}
        max_age_s: 60
"""

from __future__ import annotations

import itertools
import math
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from beyondpilot.core.contracts import PlantReader, RecordStore, ReviewQueue, SoftSensor
from beyondpilot.core.diagnostics import log_scope
from beyondpilot.core.errors import AppError, ErrorCode
from beyondpilot.core.registry import Registry
from beyondpilot.core.settings import Settings

MODULE = "ot"
STEPS_PER_SETPOINT = 5
MAX_CANDIDATES = 2000


@dataclass
class LineSpec:
    inputs: Mapping[str, str]
    setpoints: Mapping[str, Mapping[str, float]]
    target: Mapping[str, Any]
    ranges: Mapping[str, tuple[float, float]] = field(default_factory=dict)
    max_age_s: float = 60


@dataclass
class Advice:
    line_id: str
    current: dict[str, float]
    recommended: dict[str, float]
    predicted_now: float
    predicted_after: float
    target: str
    reason: str
    applied: bool = False  # always false: the service never writes to the plant
    review_id: str | None = None
    audit_id: str = ""


def _goal_distance(value: float, goal: Any) -> float:
    if goal == "min":
        return value
    if goal == "max":
        return -value
    return abs(value - float(goal))


def _within(value: float, target: Mapping[str, Any]) -> bool:
    lo, hi = target.get("min"), target.get("max")
    return (lo is None or value >= float(lo)) and (hi is None or value <= float(hi))


class OtModule:
    def __init__(
        self,
        *,
        reader: PlantReader,
        soft_sensor: SoftSensor,
        records: RecordStore,
        lines: Mapping[str, LineSpec],
        review: ReviewQueue | None = None,
    ) -> None:
        self.reader, self.soft_sensor, self.records, self.review = (
            reader,
            soft_sensor,
            records,
            review,
        )
        self.lines = dict(lines)

    @classmethod
    def from_registry(cls, registry: Registry, settings: Settings) -> OtModule:
        options = settings.modules[MODULE].options
        lines = {}
        for name, raw in options.get("lines", {}).items():
            raw = dict(raw)
            raw["ranges"] = {k: tuple(v) for k, v in raw.get("ranges", {}).items()}
            lines[name] = LineSpec(**raw)
        return cls(
            reader=registry.get(MODULE, "reader"),
            soft_sensor=registry.get(MODULE, "soft_sensor"),
            records=registry.get(MODULE, "records"),
            review=registry.optional(MODULE, "review"),
            lines=lines,
        )

    async def _signals(self, spec: LineSpec, now: float) -> dict[str, float]:
        tags = dict(spec.inputs)
        values = await self.reader.read_tags(list(tags.values()))
        problems, out = [], {}
        for name, tag in tags.items():
            tv = values.get(tag)
            if tv is None:
                problems.append(f"{name}: không đọc được")
                continue
            if not tv.good:
                problems.append(f"{name}: chất lượng tín hiệu xấu")
            if now - tv.timestamp > spec.max_age_s:
                problems.append(f"{name}: dữ liệu cũ {now - tv.timestamp:.0f} giây")
            if not math.isfinite(tv.value):
                problems.append(f"{name}: giá trị không hợp lệ")
            bounds = spec.ranges.get(name) or (
                (spec.setpoints[name]["min"], spec.setpoints[name]["max"])
                if name in spec.setpoints
                else None
            )
            if bounds and not bounds[0] <= tv.value <= bounds[1]:
                problems.append(f"{name}: {tv.value} ngoài khoảng {bounds[0]}–{bounds[1]}")
            out[name] = tv.value
        if problems:
            raise AppError(ErrorCode.NOT_READY, "; ".join(problems))
        return out

    @staticmethod
    def _candidates(spec: LineSpec, current: Mapping[str, float]) -> list[dict[str, float]]:
        axes = []
        for name, sp in spec.setpoints.items():
            lo = max(float(sp["min"]), current[name] - float(sp["max_step"]))
            hi = min(float(sp["max"]), current[name] + float(sp["max_step"]))
            if hi <= lo:
                axes.append([current[name]])
                continue
            step = (hi - lo) / (STEPS_PER_SETPOINT - 1)
            axes.append(
                sorted(
                    {round(lo + i * step, 4) for i in range(STEPS_PER_SETPOINT)} | {current[name]}
                )
            )
        combos = list(itertools.product(*axes))[:MAX_CANDIDATES]
        return [dict(zip(spec.setpoints, combo, strict=True)) for combo in combos]

    async def advise(self, line_id: str, *, now: float | None = None) -> Advice:
        spec = self.lines.get(line_id)
        if spec is None:
            raise AppError(ErrorCode.INVALID_INPUT, f"unknown line {line_id!r}")
        now = now or time.time()
        with log_scope("ot.advise", line=line_id) as extra:
            current = await self._signals(spec, now)
            current_sp = {k: current[k] for k in spec.setpoints}
            predicted_now = await self.soft_sensor.predict(current)
            goal = spec.target.get("goal", "min")
            best, best_value, best_key = dict(current_sp), predicted_now, None
            for candidate in self._candidates(spec, current_sp):
                value = await self.soft_sensor.predict({**current, **candidate})
                if not _within(value, spec.target):
                    continue
                change = sum(abs(candidate[k] - current_sp[k]) for k in candidate)
                key = (round(_goal_distance(value, goal), 6), change)
                if best_key is None or key < best_key:
                    best, best_value, best_key = candidate, value, key

            target_name = str(spec.target.get("name", "chi_tieu"))
            now_ok = _within(predicted_now, spec.target)
            improves = (
                best_key is not None
                and best != current_sp
                and (
                    not now_ok
                    or _goal_distance(best_value, goal) < _goal_distance(predicted_now, goal) - 1e-9
                )
            )
            if not improves:
                best, best_value = dict(current_sp), predicted_now
                reason = (
                    "Giữ nguyên thông số: không có phương án nào trong giới hạn an toàn "
                    f"làm {target_name} tốt hơn."
                )
            else:
                changes = ", ".join(
                    f"{k} {current_sp[k]:g} → {v:g}" for k, v in best.items() if v != current_sp[k]
                )
                reason = (
                    f"Đề xuất {changes}. Soft-sensor dự đoán {target_name} từ "
                    f"{predicted_now:.3g} thành {best_value:.3g}, vẫn trong giới hạn an toàn "
                    "và bước thay đổi cho phép."
                )
            advice = Advice(
                line_id,
                current_sp,
                best,
                round(predicted_now, 4),
                round(best_value, 4),
                target_name,
                reason,
            )
            if self.review is not None:
                advice.review_id = await self.review.submit("setpoint_advice", vars(advice))
            advice.audit_id = f"{line_id}:{int(now * 1000)}"
            await self.records.save(
                "ot_audit",
                advice.audit_id,
                {**vars(advice), "inputs": current, "decided_by": None, "decision": "pending"},
            )
            extra.update(changed=improves, candidates=best_key is not None)
            return advice
