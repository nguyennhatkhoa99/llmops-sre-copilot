"""Valkey cache (Redis protocol). A cache failure is a miss, never an error.

Values are stored as JSON under ``<prefix><key>`` with a TTL. Needs the ``redis``
package (``pip install beyondpilot-ai[rag]``); Valkey speaks the same protocol.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import Any

import redis.asyncio as redis_asyncio

logger = logging.getLogger("beyondpilot.cache")


class Impl:
    def __init__(
        self,
        url: str = "redis://valkey:6379/0",
        prefix: str = "bp:",
        timeout: float = 0.5,
        client: Any | None = None,
    ) -> None:
        self.prefix = prefix
        self.client = client or redis_asyncio.from_url(
            url, socket_timeout=timeout, socket_connect_timeout=timeout
        )

    async def get(self, key: str) -> dict[str, Any] | None:
        try:
            raw = await self.client.get(self.prefix + key)
        except Exception as exc:  # noqa: BLE001 - cache must not break answers
            logger.warning("cache get failed: %s", type(exc).__name__)
            return None
        if raw is None:
            return None
        try:
            value = json.loads(raw)
        except ValueError:
            return None
        return value if isinstance(value, dict) else None

    async def set(self, key: str, value: Mapping[str, Any], ttl: int) -> None:
        try:
            await self.client.set(
                self.prefix + key, json.dumps(dict(value), ensure_ascii=False), ex=ttl
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("cache set failed: %s", type(exc).__name__)
