"""Evidence DSL: numbers in generated text written as expressions over facts.

The writer never types a number. It writes ``{{ expr }}`` where ``expr`` uses
fact ids, numbers, ``+ - * /`` and a few functions:

- ``pct(a, b)``    percent change from b to a: (a - b) / b * 100
- ``share(a, b)``  a as a percent of b: a / b * 100
- ``round(x, n)``, ``abs(x)``, ``min(...)``, ``max(...)``, ``sum(...)``

``render`` evaluates each expression against the facts and writes the number in
Vietnamese format (``1.234,5``). Anything else (attributes, other names, other
calls) is rejected, so expressions cannot run code.
"""

from __future__ import annotations

import ast
import math
import operator
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from beyondpilot.core.errors import AppError, ErrorCode

EXPR_RE = re.compile(r"\{\{\s*(.+?)\s*\}\}")
_MAX_EXPR_LEN = 200


def _pct(a: float, b: float) -> float:
    return (a - b) / b * 100


def _share(a: float, b: float) -> float:
    return a / b * 100


FUNCTIONS: dict[str, Callable[..., float]] = {
    "pct": _pct,
    "share": _share,
    "round": lambda x, n=0: round(x, int(n)),
    "abs": abs,
    "min": min,
    "max": max,
    "sum": lambda *xs: sum(xs),
}

_BINOPS: dict[type, Callable[[float, float], float]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}


class EvidenceError(ValueError):
    """An expression is malformed, unsafe or references an unknown fact."""


def evaluate(expr: str, facts: Mapping[str, float]) -> float:
    if len(expr) > _MAX_EXPR_LEN:
        raise EvidenceError(f"expression too long: {expr[:40]}…")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise EvidenceError(f"cannot parse {expr!r}") from exc

    def ev(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, int | float):
            if isinstance(node.value, bool):
                raise EvidenceError("booleans are not numbers")
            return float(node.value)
        if isinstance(node, ast.Name):
            if node.id not in facts:
                raise EvidenceError(f"unknown fact {node.id!r}")
            return float(facts[node.id])
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub | ast.UAdd):
            value = ev(node.operand)
            return -value if isinstance(node.op, ast.USub) else value
        if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
            return _BINOPS[type(node.op)](ev(node.left), ev(node.right))
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in FUNCTIONS
            and not node.keywords
        ):
            return float(FUNCTIONS[node.func.id](*[ev(a) for a in node.args]))
        raise EvidenceError(f"not allowed in expressions: {ast.dump(node)[:60]}")

    try:
        value = ev(tree)
    except ZeroDivisionError as exc:
        raise EvidenceError(f"division by zero in {expr!r}") from exc
    except TypeError as exc:  # wrong arity for a function
        raise EvidenceError(f"bad arguments in {expr!r}") from exc
    if math.isnan(value) or math.isinf(value):
        raise EvidenceError(f"{expr!r} is not a finite number")
    return value


def format_vi(value: float, decimals: int | None = None) -> str:
    """1234567.891 -> '1.234.567,89'. Integers keep no decimals."""
    if decimals is None:
        decimals = 0 if float(value).is_integer() else (1 if abs(value) >= 10 else 2)
    text = f"{value:,.{decimals}f}"
    return text.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def parse_number(text: str) -> float | None:
    """Parse a number written in Vietnamese ('1.234,5') or plain ('1234.5', '12')."""
    raw = text.rstrip("%").strip()
    if not raw:
        return None
    if "," in raw:  # Vietnamese decimal comma
        raw = raw.replace(".", "").replace(",", ".")
    elif raw.count(".") > 1 or re.fullmatch(r"\d{1,3}(\.\d{3})+", raw):
        raw = raw.replace(".", "")  # thousands separators only
    try:
        return float(raw)
    except ValueError:
        return None


@dataclass
class Rendered:
    text: str
    values: list[tuple[str, float]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def render(text: str, facts: Mapping[str, float]) -> Rendered:
    """Replace every ``{{ expr }}`` with its formatted value. Errors are collected, not raised."""
    result = Rendered(text="")
    parts: list[str] = []
    last = 0
    for match in EXPR_RE.finditer(text):
        parts.append(text[last : match.start()])
        expr = match.group(1)
        try:
            value = evaluate(expr, facts)
        except EvidenceError as exc:
            result.errors.append(str(exc))
            parts.append(match.group(0))
        else:
            result.values.append((expr, value))
            parts.append(format_vi(value))
        last = match.end()
    parts.append(text[last:])
    result.text = "".join(parts)
    return result


def require(expr: str, facts: Mapping[str, float]) -> float:
    """Evaluate or raise ``AppError`` (for callers outside the validator loop)."""
    try:
        return evaluate(expr, facts)
    except EvidenceError as exc:
        raise AppError(ErrorCode.VALIDATION_FAILED, str(exc)) from exc
