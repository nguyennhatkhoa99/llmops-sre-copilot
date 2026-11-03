"""Get structured JSON out of an LLM, with the same rewrite loop as the validator.

``complete_json`` asks the model, parses the first JSON object or array in the
reply (code fences allowed), runs the caller's ``check`` on it, and on any error
sends the errors back for another try, up to ``max_retries`` times. It never
raises on bad output: callers decide what a failure means (usually "send to a
person").
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from beyondpilot.core.contracts import Llm, Message

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json(text: str) -> Any:
    """Parse the first JSON value in ``text``. Raises ``ValueError`` if there is none."""
    fenced = _FENCE_RE.search(text)
    candidate = fenced.group(1) if fenced else text
    decoder = json.JSONDecoder()
    for i, ch in enumerate(candidate):
        if ch in "{[":
            try:
                value, _ = decoder.raw_decode(candidate[i:])
                return value
            except json.JSONDecodeError:
                continue
    raise ValueError("no JSON found in the model output")


@dataclass
class JsonResult:
    value: Any = None
    ok: bool = False
    attempts: int = 0
    errors: list[str] = field(default_factory=list)


async def complete_json(
    llm: Llm,
    messages: Sequence[Message],
    check: Callable[[Any], list[str]],
    *,
    max_retries: int = 2,
    temperature: float = 0.0,
) -> JsonResult:
    history = list(messages)
    errors: list[str] = []
    for attempt in range(1, max_retries + 2):
        result = await llm.complete(history, temperature=temperature)
        try:
            value = extract_json(result.text)
        except ValueError as exc:
            errors = [str(exc)]
        else:
            errors = check(value)
            if not errors:
                return JsonResult(value, True, attempt)
        fixes = "\n".join(f"- {e}" for e in errors)
        history = [
            *messages,
            Message("assistant", result.text[:4000]),
            Message("user", f"Kết quả chưa đạt, hãy trả lại đúng JSON và sửa các lỗi:\n{fixes}"),
        ]
    return JsonResult(None, False, max_retries + 1, errors)
