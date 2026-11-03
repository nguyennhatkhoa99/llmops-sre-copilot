"""Pages from config instead of the Internet. For local runs and tests only."""

from __future__ import annotations

from collections.abc import Mapping

from beyondpilot.core.contracts import Page
from beyondpilot.core.errors import AppError, ErrorCode


class Impl:
    def __init__(self, pages: Mapping[str, str] | None = None) -> None:
        self.pages = dict(pages or {})

    async def fetch(self, url: str) -> Page:
        if url not in self.pages:
            raise AppError(ErrorCode.UPSTREAM_ERROR, f"no static page for {url}")
        return Page(url, 200, self.pages[url])
