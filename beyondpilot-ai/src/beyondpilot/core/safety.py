"""Input safety: mask PII, block prompt injection.

Uses Presidio (PII) and LLM Guard (prompt injection) when they are installed
(``pip install beyondpilot-ai[safety]``). Without them it falls back to the
regex heuristics ported from the pilot's ``guardrails/`` package, extended for
Vietnamese phone numbers, citizen IDs and injection phrases.

Policy: injection always blocks. PII is masked and the request continues,
unless ``block_on_pii`` is set.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from beyondpilot.core.errors import AppError, ErrorCode

MASK = "[đã che]"

_PII_PATTERNS = [
    re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9.-]+"),  # email
    re.compile(r"\b(?:sk|pk|rk)[-_][A-Za-z0-9]{16,}\b"),  # API keys
    re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"),  # IPv4
    re.compile(r"(?<!\d)(?:\+84|0)(?:3|5|7|8|9)\d{8}(?!\d)"),  # VN mobile
    re.compile(r"(?<!\d)\d{12}(?!\d)"),  # CCCD (12 digits)
    re.compile(r"\+\d[\d\s-]{7,}\d"),  # other international numbers
]

_INJECTION_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"ignore\s+(all\s+)?previous",
        r"disregard\s+(the\s+)?above",
        r"system\s+prompt",
        r"you\s+are\s+now\s+.*?model",
        r"drop\s+all\s+rules",
        r"delete\s+your\s+instructions",
        r"bỏ\s+qua\s+(mọi|tất\s+cả|các)?\s*(hướng\s+dẫn|chỉ\s+dẫn|quy\s+tắc)",
        r"quên\s+(hết|mọi|tất\s+cả)\s+(hướng\s+dẫn|chỉ\s+dẫn|quy\s+tắc)",
        r"tiết\s+lộ\s+(system\s+prompt|lời\s+nhắc\s+hệ\s+thống)",
    ]
]


@dataclass(frozen=True)
class GuardrailResult:
    """Same shape as the pilot's result, so callers ported from it keep working."""

    sanitized_text: str
    pii_matches: list[str] = field(default_factory=list)
    prompt_injection: bool = False
    blocked: bool = False
    reason: str | None = None


def find_pii(text: str) -> list[str]:
    found: list[str] = []
    for pattern in _PII_PATTERNS:
        found.extend(m.group(0) for m in pattern.finditer(text))
    return list(dict.fromkeys(found))


def mask(text: str, matches: Iterable[str]) -> str:
    for match in sorted(set(matches), key=len, reverse=True):
        text = text.replace(match, MASK)
    return text


def detect_injection(text: str) -> bool:
    return any(p.search(text) for p in _INJECTION_PATTERNS)


class SafetyEngine:
    def __init__(self, *, block_on_pii: bool = False, use_backends: bool = True) -> None:
        self.block_on_pii = block_on_pii
        self._presidio: tuple[Any, Any] | None = None
        self._llm_guard: Any | None = None
        if use_backends:
            self._presidio = self._load_presidio()
            self._llm_guard = self._load_llm_guard()

    @property
    def backends(self) -> dict[str, str]:
        return {
            "pii": "presidio" if self._presidio else "regex",
            "injection": "llm_guard" if self._llm_guard else "regex",
        }

    @staticmethod
    def _load_presidio() -> tuple[Any, Any] | None:
        try:
            from presidio_analyzer import AnalyzerEngine
            from presidio_anonymizer import AnonymizerEngine
        except ImportError:
            return None
        return AnalyzerEngine(), AnonymizerEngine()

    @staticmethod
    def _load_llm_guard() -> Any | None:
        try:
            from llm_guard.input_scanners import PromptInjection
        except ImportError:
            return None
        return PromptInjection()

    def _pii(self, text: str) -> tuple[str, list[str]]:
        matches = find_pii(text)
        if self._presidio:
            analyzer, anonymizer = self._presidio
            results = analyzer.analyze(text=text, language="en")
            matches = list(dict.fromkeys(matches + [text[r.start : r.end] for r in results]))
        return mask(text, matches), matches

    def _injection(self, text: str) -> bool:
        if detect_injection(text):
            return True
        if self._llm_guard:
            _, is_valid, _ = self._llm_guard.scan(text)
            return not is_valid
        return False

    def _check_sync(self, text: str) -> GuardrailResult:
        sanitized, matches = self._pii(text)
        injection = self._injection(text)
        if injection:
            return GuardrailResult(sanitized, matches, True, True, "prompt injection")
        if matches and self.block_on_pii:
            return GuardrailResult(sanitized, matches, False, True, "PII")
        return GuardrailResult(sanitized, matches, False, False, None)

    async def check(self, text: str) -> GuardrailResult:
        # Presidio and LLM Guard are synchronous and CPU-bound; keep them off the event loop.
        if self._presidio or self._llm_guard:
            return await asyncio.to_thread(self._check_sync, text)
        return self._check_sync(text)

    async def ensure_safe(self, text: str) -> str:
        """Return the masked text, or raise ``UNSAFE_INPUT``."""
        result = await self.check(text)
        if result.blocked:
            raise AppError(ErrorCode.UNSAFE_INPUT, result.reason)
        return result.sanitized_text
