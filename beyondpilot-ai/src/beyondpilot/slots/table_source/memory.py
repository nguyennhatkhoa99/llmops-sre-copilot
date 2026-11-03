"""Named tables held in memory (from config or tests). Params filter rows by equality."""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from typing import Any

from beyondpilot.core.errors import AppError, ErrorCode


def filter_rows(
    rows: Sequence[Mapping[str, Any]], params: Mapping[str, Any]
) -> list[dict[str, Any]]:
    return [dict(r) for r in rows if all(str(r.get(k)) == str(v) for k, v in params.items())]


class Impl:
    def __init__(self, tables: Mapping[str, Sequence[Mapping[str, Any]]] | None = None) -> None:
        self.tables = {k: [dict(r) for r in v] for k, v in (tables or {}).items()}

    async def fetch_table(self, name: str, params: Mapping[str, Any]) -> list[dict[str, Any]]:
        if name not in self.tables:
            raise AppError(ErrorCode.INVALID_INPUT, f"unknown table {name!r}")
        return copy.deepcopy(filter_rows(self.tables[name], params))
