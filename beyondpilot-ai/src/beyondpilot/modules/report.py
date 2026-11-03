"""Module A: facts + narrative (use case build_report).

Source -> facts builder -> fact sheet -> writer (evidence DSL) -> validator ->
renderer -> Review UI. Reports are declared in config under
``modules.report.options.reports``; callers pick one by name and pass filter
parameters. No raw SQL or free-form query ever comes from the API.

Only numbers and their labels reach the model, so customer names or other
personal data in the source rows never leave the service.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from beyondpilot.core.contracts import FactsBuilder, Llm, ReviewQueue, TableSource
from beyondpilot.core.diagnostics import log_scope
from beyondpilot.core.errors import AppError, ErrorCode
from beyondpilot.core.evidence import format_vi
from beyondpilot.core.registry import Registry
from beyondpilot.core.renderer import Renderer
from beyondpilot.core.settings import Settings
from beyondpilot.modules.writer import write_narrative

MODULE = "report"


@dataclass
class ReportSpec:
    title: str
    source: str
    metrics: Mapping[str, Any]
    labels: Mapping[str, str] = field(default_factory=dict)
    instructions: str = "Tóm tắt kết quả chính và điểm cần chú ý."
    allowed_params: tuple[str, ...] = ()


@dataclass
class ReportResult:
    report: str
    title: str
    text: str
    html: str
    facts: dict[str, float]
    attempts: int
    used_fallback: bool
    review_id: str | None = None


class ReportModule:
    def __init__(
        self,
        *,
        source: TableSource,
        facts: FactsBuilder,
        llm: Llm,
        reports: Mapping[str, ReportSpec],
        review: ReviewQueue | None = None,
        renderer: Renderer | None = None,
        max_retries: int = 2,
    ) -> None:
        self.source, self.facts, self.llm, self.review = source, facts, llm, review
        self.reports = dict(reports)
        self.renderer = renderer or Renderer()
        self.max_retries = max_retries

    @classmethod
    def from_registry(cls, registry: Registry, settings: Settings) -> ReportModule:
        options = settings.modules[MODULE].options
        reports = {}
        for name, raw in options.get("reports", {}).items():
            raw = dict(raw)
            raw["allowed_params"] = tuple(raw.get("allowed_params", ()))
            reports[name] = ReportSpec(**raw)
        return cls(
            source=registry.get(MODULE, "source"),
            facts=registry.get(MODULE, "facts"),
            llm=registry.get(MODULE, "llm"),
            review=registry.optional(MODULE, "review"),
            reports=reports,
            max_retries=settings.validator.max_retries,
        )

    async def build(self, report: str, params: Mapping[str, Any]) -> ReportResult:
        spec = self.reports.get(report)
        if spec is None:
            raise AppError(ErrorCode.INVALID_INPUT, f"unknown report {report!r}")
        extra_params = set(params) - set(spec.allowed_params)
        if extra_params:
            raise AppError(ErrorCode.INVALID_INPUT, f"params not allowed: {sorted(extra_params)}")
        with log_scope("report.build", report=report) as extra:
            rows = await self.source.fetch_table(spec.source, params)
            facts = await self.facts.compute(rows, spec.metrics)
            extra.update(rows=len(rows), facts=len(facts))
            if not facts:
                raise AppError(ErrorCode.INVALID_INPUT, "no data for the requested report")
            written = await write_narrative(
                self.llm, facts, spec.labels, spec.instructions, max_retries=self.max_retries
            )
            subtitle = ", ".join(f"{k}: {v}" for k, v in sorted(params.items()))
            html = self.renderer.html(
                "report.html",
                title=spec.title,
                subtitle=subtitle,
                body=written.text,
                facts=[(spec.labels.get(k, k), format_vi(v)) for k, v in facts.items()],
            )
            review_id = None
            if self.review is not None:
                review_id = await self.review.submit(
                    "report",
                    {
                        "report": report,
                        "params": dict(params),
                        "text": written.text,
                        "facts": facts,
                        "used_fallback": written.used_fallback,
                    },
                )
            extra.update(attempts=written.attempts, fallback=written.used_fallback)
            return ReportResult(
                report,
                spec.title,
                written.text,
                html,
                facts,
                written.attempts,
                written.used_fallback,
                review_id,
            )
