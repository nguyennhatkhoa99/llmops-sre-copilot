"""PostgreSQL store: pgvector for vector search, full-text search for keywords.

Every query filters by ``workspace_id`` (and ``doc_ids`` when given) in SQL, so a
chunk the caller may not see never leaves the database. Values are always bound
as parameters; the only interpolated name is the table, checked against a strict
pattern.

Full-text uses the ``simple`` configuration with ``ts_rank_cd``. Postgres has no
Vietnamese dictionary; ``simple`` keeps syllables as they are, which works for
Vietnamese. A real BM25 index (for example ParadeDB ``pg_search``) can replace
``search_text`` later without touching the module.

Needs ``psycopg`` and ``psycopg-pool`` (``pip install beyondpilot-ai[rag]``).
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any

from beyondpilot.core.contracts import AccessFilter, Chunk, Hit
from beyondpilot.core.errors import AppError, ErrorCode

_TABLE_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
_COLUMNS = "id, doc_id, workspace_id, text, metadata"


def schema_sql(table: str, dimensions: int) -> str:
    return f"""
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS {table} (
    id           text PRIMARY KEY,
    doc_id       text NOT NULL,
    workspace_id text NOT NULL,
    text         text NOT NULL,
    metadata     jsonb NOT NULL DEFAULT '{{}}',
    embedding    vector({dimensions}) NOT NULL,
    tsv          tsvector GENERATED ALWAYS AS (to_tsvector('simple', text)) STORED
);
CREATE INDEX IF NOT EXISTS {table}_ws_doc ON {table} (workspace_id, doc_id);
CREATE INDEX IF NOT EXISTS {table}_tsv ON {table} USING gin (tsv);
CREATE INDEX IF NOT EXISTS {table}_emb ON {table} USING hnsw (embedding vector_cosine_ops);
"""


def vector_literal(vector: Sequence[float]) -> str:
    return "[" + ",".join(f"{float(v):.7g}" for v in vector) + "]"


class Impl:
    def __init__(
        self,
        dsn: str = "",
        table: str = "rag_chunks",
        dimensions: int = 1024,
        pool: Any | None = None,
    ) -> None:
        if not _TABLE_RE.match(table):
            raise AppError(ErrorCode.CONFIG_INVALID, f"invalid table name {table!r}")
        self.table = table
        self.dimensions = dimensions
        self._dsn = dsn
        self._pool = pool
        if pool is None:
            from psycopg_pool import AsyncConnectionPool  # imported here so tests need no driver

            self._pool = AsyncConnectionPool(dsn, open=False, min_size=1, max_size=10)
        self._opened = pool is not None

    async def _ensure_open(self) -> None:
        if not self._opened:
            await self._pool.open()
            self._opened = True

    async def _fetch(self, query: str, params: dict[str, Any]) -> list[tuple]:
        await self._ensure_open()
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(query, params)
                return list(await cur.fetchall())

    async def ensure_schema(self) -> None:
        await self._ensure_open()
        async with self._pool.connection() as conn:
            await conn.execute(schema_sql(self.table, self.dimensions))

    @staticmethod
    def _access_params(access: AccessFilter) -> dict[str, Any]:
        return {
            "ws": access.workspace_id,
            "docs": list(access.doc_ids) if access.doc_ids is not None else None,
        }

    @staticmethod
    def _hits(rows: Sequence[tuple]) -> list[Hit]:
        hits = []
        for id_, doc_id, ws, text, metadata, score in rows:
            meta = metadata if isinstance(metadata, dict) else json.loads(metadata or "{}")
            hits.append(Hit(Chunk(id_, doc_id, ws, text, meta), float(score)))
        return hits

    async def upsert(self, chunks: Sequence[Chunk], vectors: Sequence[Sequence[float]]) -> None:
        if len(chunks) != len(vectors):
            raise ValueError("chunks and vectors must have the same length")
        query = (
            f"INSERT INTO {self.table} ({_COLUMNS}, embedding) "
            "VALUES (%(id)s, %(doc_id)s, %(ws)s, %(text)s, %(meta)s::jsonb, %(vec)s::vector) "
            "ON CONFLICT (id) DO UPDATE SET doc_id = EXCLUDED.doc_id, "
            "workspace_id = EXCLUDED.workspace_id, text = EXCLUDED.text, "
            "metadata = EXCLUDED.metadata, embedding = EXCLUDED.embedding"
        )
        rows = [
            {
                "id": c.id,
                "doc_id": c.doc_id,
                "ws": c.workspace_id,
                "text": c.text,
                "meta": json.dumps(dict(c.metadata), ensure_ascii=False),
                "vec": vector_literal(v),
            }
            for c, v in zip(chunks, vectors, strict=True)
        ]
        await self._ensure_open()
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.executemany(query, rows)

    async def search_vector(
        self, vector: Sequence[float], access: AccessFilter, top_k: int
    ) -> list[Hit]:
        query = (
            f"SELECT {_COLUMNS}, 1 - (embedding <=> %(vec)s::vector) AS score FROM {self.table} "
            "WHERE workspace_id = %(ws)s AND (%(docs)s::text[] IS NULL OR doc_id = ANY(%(docs)s)) "
            "ORDER BY embedding <=> %(vec)s::vector LIMIT %(k)s"
        )
        params = {**self._access_params(access), "vec": vector_literal(vector), "k": top_k}
        return self._hits(await self._fetch(query, params))

    async def search_text(self, query: str, access: AccessFilter, top_k: int) -> list[Hit]:
        sql = (
            f"SELECT {_COLUMNS}, ts_rank_cd(tsv, q) AS score "
            f"FROM {self.table}, plainto_tsquery('simple', %(q)s) AS q "
            "WHERE workspace_id = %(ws)s AND (%(docs)s::text[] IS NULL OR doc_id = ANY(%(docs)s)) "
            "AND tsv @@ q ORDER BY score DESC LIMIT %(k)s"
        )
        params = {**self._access_params(access), "q": query, "k": top_k}
        return self._hits(await self._fetch(sql, params))
