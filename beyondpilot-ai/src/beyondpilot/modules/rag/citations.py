"""Check a generated answer against the sources it was given.

An answer passes when:
- every sentence carries at least one citation ``[n]``;
- every citation points to a source that was actually provided;
- every figure in a sentence appears in one of the sources that sentence cites.

The fixed "not enough information" answer passes without citations.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from beyondpilot.core.contracts import Hit
from beyondpilot.core.validator import typed_numbers
from beyondpilot.modules.rag.prompts import NO_ANSWER

CITE_RE = re.compile(r"\[(\d{1,3})\]")
# Sentence ends: . ! ? followed by space, or a line break (lists).
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")


@dataclass
class CitationCheck:
    ok: bool
    errors: list[str] = field(default_factory=list)
    used: list[int] = field(default_factory=list)  # 1-based source numbers cited


def sentences(text: str) -> list[str]:
    parts = [p.strip(" -*•\t") for p in _SENTENCE_SPLIT_RE.split(text.strip())]
    # A bare citation after the period ("... đổi trả. [2]") belongs to the previous sentence.
    out: list[str] = []
    for part in parts:
        if not part:
            continue
        if out and CITE_RE.sub("", part).strip() == "":
            out[-1] += " " + part
        else:
            out.append(part)
    return out


def _numbers_in(text: str) -> list[float]:
    return [v for _, v in typed_numbers(text, ignore=lambda v: False)]


def check_citations(answer: str, sources: Sequence[Hit]) -> CitationCheck:
    text = answer.strip()
    if not text:
        return CitationCheck(False, ["câu trả lời rỗng"])
    if CITE_RE.sub("", text).strip() == NO_ANSWER:
        return CitationCheck(True)

    errors: list[str] = []
    used: set[int] = set()
    source_numbers = [_numbers_in(h.chunk.text) for h in sources]
    for sentence in sentences(text):
        cites = [int(n) for n in CITE_RE.findall(sentence)]
        preview = CITE_RE.sub("", sentence).strip()[:60]
        if not cites:
            errors.append(f'câu thiếu trích dẫn: "{preview}"')
            continue
        bad = [n for n in cites if not 1 <= n <= len(sources)]
        if bad:
            errors.append(f"trích dẫn không tồn tại {bad}; chỉ có nguồn 1 đến {len(sources)}")
        good = [n for n in cites if 1 <= n <= len(sources)]
        used.update(good)
        allowed = [v for n in good for v in source_numbers[n - 1]]
        for raw, value in typed_numbers(CITE_RE.sub(" ", sentence), ignore=lambda v: False):
            if not any(math.isclose(value, a, rel_tol=1e-9, abs_tol=1e-9) for a in allowed):
                errors.append(f'số {raw} không có trong nguồn được trích ở câu "{preview}"')
    return CitationCheck(not errors, errors, sorted(used))
