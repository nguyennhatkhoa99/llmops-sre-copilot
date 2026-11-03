"""Progress hook: ``POST {base_url}/progress`` to the Backend.

Each event carries ``event_id`` (unique, for de-duplication on the Backend) and
the caller's ``seq`` (order within one job). A failed notification is logged and
dropped: progress is informational and must not fail the job.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from typing import Any

import httpx

from beyondpilot.core.errors import AppError
from beyondpilot.core.resilience import call_with_retry
from beyondpilot.core.settings import ResilienceConfig

logger = logging.getLogger("beyondpilot.notifier")


class Impl:
    accepts_resilience = True

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        resilience: ResilienceConfig,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.url = base_url.rstrip("/") + "/progress"
        self.headers = {"X-API-Key": api_key}
        self.resilience = resilience
        self.client = client or httpx.AsyncClient()

    async def notify(self, event: str, payload: Mapping[str, Any]) -> None:
        body = {"event": event, "event_id": uuid.uuid4().hex, **dict(payload)}

        async def call() -> None:
            response = await self.client.post(self.url, json=body, headers=self.headers)
            response.raise_for_status()

        try:
            await call_with_retry(call, config=self.resilience, op="notifier.webhook")
        except AppError as exc:
            logger.warning("progress event %s dropped: %s", event, exc.code.value)
