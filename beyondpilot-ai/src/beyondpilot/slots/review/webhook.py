"""Send review items to the Backend, which owns the Review UI.

``POST {base_url}/reviews`` with ``{"kind", "payload"}``; the Backend answers
``{"id": ...}``. The AI service never approves anything itself.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

import httpx

from beyondpilot.core.resilience import call_with_retry
from beyondpilot.core.settings import ResilienceConfig


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
        self.url = base_url.rstrip("/") + "/reviews"
        self.headers = {"X-API-Key": api_key}
        self.resilience = resilience
        self.client = client or httpx.AsyncClient()

    async def submit(self, kind: str, payload: Mapping[str, Any]) -> str:
        body = {"kind": kind, "payload": dict(payload)}
        # Idempotency key: a retried POST must not create two review items.
        headers = {**self.headers, "Idempotency-Key": uuid.uuid4().hex}

        async def call() -> Any:
            response = await self.client.post(self.url, json=body, headers=headers)
            response.raise_for_status()
            return response.json()

        data = await call_with_retry(call, config=self.resilience, op="review.webhook")
        return str(data.get("id", ""))
