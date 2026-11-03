"""Fixed tag values from config, stamped with the current time. For local runs and tests."""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence

from beyondpilot.core.contracts import TagValue


class Impl:
    def __init__(self, values: Mapping[str, float] | None = None) -> None:
        self.values = {k: float(v) for k, v in (values or {}).items()}

    async def read_tags(self, tags: Sequence[str]) -> dict[str, TagValue]:
        now = time.time()
        return {t: TagValue(self.values[t], now) for t in tags if t in self.values}
