"""Named CSV (or Excel exported to CSV) files. Params filter rows by equality.

Numbers are converted to float when the whole cell parses as a number.
"""

from __future__ import annotations

import asyncio
import csv
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from beyondpilot.core.errors import AppError, ErrorCode
from beyondpilot.slots.table_source.memory import filter_rows


def _cell(value: str) -> Any:
    try:
        return float(value) if value.strip() else value
    except ValueError:
        return value


class Impl:
    def __init__(self, files: Mapping[str, str]) -> None:
        self.files = {k: Path(v) for k, v in files.items()}

    def _read(self, path: Path) -> list[dict[str, Any]]:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            return [{k: _cell(v) for k, v in row.items()} for row in csv.DictReader(handle)]

    async def fetch_table(self, name: str, params: Mapping[str, Any]) -> list[dict[str, Any]]:
        path = self.files.get(name)
        if path is None:
            raise AppError(ErrorCode.INVALID_INPUT, f"unknown table {name!r}")
        try:
            rows = await asyncio.to_thread(self._read, path)
        except OSError as exc:
            raise AppError(ErrorCode.UPSTREAM_ERROR, f"cannot read {path}") from exc
        return filter_rows(rows, params)
