"""Record store on PostgreSQL: one table, ``(collection, key) -> jsonb``.

Used for extracted document fields (D), image results (E), model runs (F),
OT audit (G) and radar fingerprints (H). Needs ``psycopg`` + ``psycopg-pool``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from beyondpilot.core.errors import AppError, ErrorCode

_TABLE_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def schema_sql(table: str) -> str:
    return f"""
CREATE TABLE IF NOT EXISTS {table} (
    collection text NOT NULL,
    key        text NOT NULL,
    data       jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (collection, key)
);
"""


class Impl:
    def __init__(self, dsn: str = "", table: str = "ai_records", pool: Any | None = None) -> None:
        if not _TABLE_RE.match(table):
            raise AppError(ErrorCode.CONFIG_INVALID, f"invalid table name {table!r}")
        self.table = table
        if pool is None:
            from psycopg_pool import AsyncConnectionPool

            pool = AsyncConnectionPool(dsn, open=False, min_size=1, max_size=5)
            self._opened = False
        else:
            self._opened = True
        self._pool = pool

    async def _rows(self, query: str, params: dict[str, Any], fetch: bool) -> list[tuple]:
        if not self._opened:
            await self._pool.open()
            self._opened = True
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(query, params)
                return list(await cur.fetchall()) if fetch else []

    async def save(self, collection: str, key: str, data: Mapping[str, Any]) -> None:
        await self._rows(
            f"INSERT INTO {self.table} (collection, key, data) VALUES (%(c)s, %(k)s, %(d)s::jsonb) "
            "ON CONFLICT (collection, key) DO UPDATE SET data = EXCLUDED.data, updated_at = now()",
            {"c": collection, "k": key, "d": json.dumps(dict(data), ensure_ascii=False)},
            fetch=False,
        )

    @staticmethod
    def _load(value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else json.loads(value)

    async def get(self, collection: str, key: str) -> dict[str, Any] | None:
        rows = await self._rows(
            f"SELECT data FROM {self.table} WHERE collection = %(c)s AND key = %(k)s",
            {"c": collection, "k": key},
            fetch=True,
        )
        return self._load(rows[0][0]) if rows else None

    async def list(self, collection: str, prefix: str = "") -> list[dict[str, Any]]:
        escaped = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        rows = await self._rows(
            f"SELECT data FROM {self.table} WHERE collection = %(c)s AND key LIKE %(p)s "
            "ORDER BY key",
            {"c": collection, "p": escaped + "%"},
            fetch=True,
        )
        return [self._load(r[0]) for r in rows]
