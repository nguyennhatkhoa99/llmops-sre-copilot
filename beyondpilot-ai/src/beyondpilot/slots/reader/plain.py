"""Reader for text files: plain text, Markdown, CSV."""

from __future__ import annotations

from beyondpilot.core.contracts import Document
from beyondpilot.core.errors import AppError, ErrorCode

TEXT_MIMES = {"text/plain", "text/markdown", "text/csv"}


class Impl:
    async def read(self, data: bytes, *, mime: str) -> Document:
        if mime not in TEXT_MIMES:
            raise AppError(ErrorCode.INVALID_INPUT, f"plain reader cannot read {mime}")
        return Document(
            id="", text=data.decode("utf-8-sig", errors="replace"), metadata={"reader": "plain"}
        )
