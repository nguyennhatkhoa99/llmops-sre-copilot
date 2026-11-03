"""Every number in generated text must be recomputable from the facts.

``validate`` renders the evidence expressions, then looks at every number the
writer typed directly. A typed number passes only if it equals a fact, or the
value of one of the expressions, within a small relative tolerance. Years are
ignored by default.

``generate_validated`` runs the writer, validates, sends the errors back for a
rewrite up to ``max_retries`` times, and finally falls back to a template.
"""

from __future__ import annotations

import math
import re
from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field

from beyondpilot.core.diagnostics import log_scope
from beyondpilot.core.errors import AppError, ErrorCode
from beyondpilot.core.evidence import EXPR_RE, format_vi, parse_number, render

# A number not glued to letters: skips "Q3", "bge-m3", "v2"; keeps "12,5%" and "1.200".
_NUMBER_RE = re.compile(r"(?<![\w.,])\d+(?:[.,]\d+)*%?(?![\w])")
# Numbers that name a period or position ("quý 3", "tháng 10", "bước 2") are labels, not figures.
_LABEL_BEFORE_RE = re.compile(
    r"(?:quý|tháng|tuần|ngày|năm|kỳ|giai đoạn|bước|mục|phần|chương|điều|khoản|top|số)\s*$",
    re.IGNORECASE,
)


def _is_year(value: float) -> bool:
    return value.is_integer() and 1900 <= value <= 2100


def typed_numbers(
    text: str, ignore: Callable[[float], bool] = _is_year
) -> Iterator[tuple[str, float]]:
    """Numbers written in ``text`` that count as figures: skips years and labels like "quý 3"."""
    for match in _NUMBER_RE.finditer(text):
        raw = match.group(0)
        value = parse_number(raw)
        if value is None or ignore(value):
            continue
        if not raw.endswith("%") and _LABEL_BEFORE_RE.search(text[: match.start()]):
            continue
        yield raw, value


@dataclass
class ValidationResult:
    ok: bool
    text: str
    errors: list[str] = field(default_factory=list)


def validate(
    text: str,
    facts: Mapping[str, float],
    *,
    tolerance: float = 0.005,
    ignore: Callable[[float], bool] = _is_year,
) -> ValidationResult:
    rendered = render(text, facts)
    errors = list(rendered.errors)
    known = [float(v) for v in facts.values()] + [v for _, v in rendered.values]

    def matches_known(value: float) -> bool:
        return any(
            math.isclose(value, k, rel_tol=tolerance, abs_tol=tolerance)
            or math.isclose(value, abs(k), rel_tol=tolerance, abs_tol=tolerance)
            for k in known
        )

    typed_only = EXPR_RE.sub(" ", text)  # numbers inside {{ }} are already checked
    for raw, value in typed_numbers(typed_only, ignore):
        if not matches_known(value):
            errors.append(f"số {raw} không tính được từ facts; hãy dùng {{{{ biểu thức }}}}")
    return ValidationResult(ok=not errors, text=rendered.text, errors=errors)


@dataclass
class ValidatedOutput:
    text: str
    attempts: int
    used_fallback: bool
    last_errors: Sequence[str] = ()


Generate = Callable[[Sequence[str]], Awaitable[str]]  # receives errors from the previous try


async def generate_validated(
    generate: Generate,
    facts: Mapping[str, float],
    *,
    fallback: Callable[[Mapping[str, float]], str],
    max_retries: int = 2,
    tolerance: float = 0.005,
) -> ValidatedOutput:
    errors: Sequence[str] = ()
    with log_scope("validator", max_retries=max_retries) as extra:
        for attempt in range(1, max_retries + 2):
            draft = await generate(errors)
            result = validate(draft, facts, tolerance=tolerance)
            if result.ok:
                extra.update(attempts=attempt, fallback=False)
                return ValidatedOutput(result.text, attempt, False)
            errors = result.errors
            extra["errors"] = len(errors)

        result = validate(fallback(facts), facts, tolerance=tolerance)
        if not result.ok:
            raise AppError(ErrorCode.VALIDATION_FAILED, "fallback template failed validation")
        extra.update(attempts=max_retries + 1, fallback=True)
        return ValidatedOutput(result.text, max_retries + 1, True, errors)


def fact_list_template(labels: Mapping[str, str]) -> Callable[[Mapping[str, float]], str]:
    """A plain fallback: one line per fact, '<label>: <value>'."""

    def build(facts: Mapping[str, float]) -> str:
        return "\n".join(f"- {labels.get(k, k)}: {format_vi(float(v))}" for k, v in facts.items())

    return build
