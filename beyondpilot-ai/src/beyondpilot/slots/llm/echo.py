"""LLM slot for local runs and tests: answers with the last user message.

Never configure this in production; it exists so the service and its tests can
start without a model server.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from beyondpilot.core.contracts import LlmResult, Message


class Impl:
    def __init__(self, prefix: str = "") -> None:
        self.prefix = prefix

    async def complete(self, messages: Sequence[Message], **options: Any) -> LlmResult:
        last = next((m.content for m in reversed(messages) if m.role == "user"), "")
        return LlmResult(text=f"{self.prefix}{last}", model="echo")
