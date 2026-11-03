"""Facts computed with DuckDB SQL over the fetched rows, loaded as table ``t``.

Metric spec is a SQL query returning one number, written in config (trusted),
for example ``SELECT sum(revenue) FROM t WHERE quarter = 'Q3'``.
Needs ``duckdb``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from typing import Any

import duckdb

from beyondpilot.core.errors import AppError, ErrorCode


def _sql_type(values: list[Any]) -> str:
    present = [v for v in values if v is not None]
    if present and all(isinstance(v, int | float) and not isinstance(v, bool) for v in present):
        return "DOUBLE"
    return "VARCHAR"


class Impl:
    def _run(
        self, rows: Sequence[Mapping[str, Any]], metrics: Mapping[str, Any]
    ) -> dict[str, float]:
        con = duckdb.connect(":memory:")
        try:
            columns = sorted({k for r in rows for k in r})
            if columns:
                types = {c: _sql_type([r.get(c) for r in rows]) for c in columns}
                con.execute(
                    "CREATE TABLE t (" + ", ".join(f'"{c}" {types[c]}' for c in columns) + ")"
                )
                placeholders = ", ".join("?" for _ in columns)
                con.executemany(
                    f"INSERT INTO t VALUES ({placeholders})",
                    [
                        [
                            r.get(c)
                            if types[c] == "DOUBLE"
                            else (None if r.get(c) is None else str(r.get(c)))
                            for c in columns
                        ]
                        for r in rows
                    ],
                )
            facts: dict[str, float] = {}
            for fact_id, sql in metrics.items():
                try:
                    result = con.execute(str(sql)).fetchone()
                except duckdb.Error as exc:
                    raise AppError(ErrorCode.CONFIG_INVALID, f"metric {fact_id}: {exc}") from exc
                if result and result[0] is not None:
                    facts[fact_id] = float(result[0])
            return facts
        finally:
            con.close()

    async def compute(
        self, rows: Sequence[Mapping[str, Any]], metrics: Mapping[str, Any]
    ) -> dict[str, float]:
        return await asyncio.to_thread(self._run, rows, metrics)
