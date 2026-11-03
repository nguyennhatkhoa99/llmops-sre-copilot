"""Read-only Snowflake queries, defined in config by name.

Callers pick a query by name and pass parameters; the SQL text only ever comes
from config, and parameters are bound by the driver (``%(name)s``), never
formatted into the SQL. Use a Snowflake role with SELECT rights only.

Needs ``snowflake-connector-python``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from beyondpilot.core.errors import AppError, ErrorCode
from beyondpilot.core.resilience import call_with_retry
from beyondpilot.core.settings import ResilienceConfig


class Impl:
    accepts_resilience = True

    def __init__(
        self,
        *,
        account: str,
        user: str,
        password: str,
        warehouse: str,
        database: str,
        schema: str,
        queries: Mapping[str, str],
        resilience: ResilienceConfig,
        role: str | None = None,
    ) -> None:
        import snowflake.connector  # noqa: F401 - fail at start-up if missing

        self.conn_args = {
            "account": account,
            "user": user,
            "password": password,
            "warehouse": warehouse,
            "database": database,
            "schema": schema,
            **({"role": role} if role else {}),
        }
        self.queries = dict(queries)
        self.resilience = resilience

    def _run(self, sql: str, params: Mapping[str, Any]) -> list[dict[str, Any]]:
        import snowflake.connector

        with snowflake.connector.connect(**self.conn_args) as conn:
            cur = conn.cursor(snowflake.connector.DictCursor)
            cur.execute(sql, dict(params))
            return [{k.lower(): v for k, v in row.items()} for row in cur.fetchall()]

    async def fetch_table(self, name: str, params: Mapping[str, Any]) -> list[dict[str, Any]]:
        sql = self.queries.get(name)
        if sql is None:
            raise AppError(ErrorCode.INVALID_INPUT, f"unknown query {name!r}")
        return await call_with_retry(
            asyncio.to_thread, self._run, sql, params, config=self.resilience, op="source.snowflake"
        )
