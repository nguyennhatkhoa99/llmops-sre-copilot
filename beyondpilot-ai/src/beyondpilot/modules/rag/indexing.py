"""Chunk documents by heading and put them in the store.

Markdown headings start a new section; the heading path ("Chính sách > Đổi trả")
is kept at the top of every chunk so a chunk still makes sense on its own.
Long sections are split on paragraph boundaries. Module D will call this after
reading a file; it is also handy for local runs and tests.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence

from beyondpilot.core.contracts import Chunk, Document, Embedder, Store

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")


def chunk_document(doc: Document, workspace_id: str, max_chars: int = 1200) -> list[Chunk]:
    sections: list[tuple[list[str], list[str]]] = []
    path: list[str] = []
    current: list[str] = []
    for line in doc.text.splitlines():
        match = _HEADING_RE.match(line.strip())
        if match:
            if current:
                sections.append((list(path), current))
                current = []
            level = len(match.group(1))
            path = path[: level - 1] + [match.group(2).strip()]
        else:
            current.append(line)
    if current:
        sections.append((list(path), current))

    chunks: list[Chunk] = []
    for heading, lines in sections:
        prefix = " > ".join(heading)
        paragraphs = [p.strip() for p in "\n".join(lines).split("\n\n") if p.strip()]
        buffer = ""
        for para in paragraphs:
            if buffer and len(buffer) + len(para) + 2 > max_chars:
                chunks.append(_make(doc, workspace_id, prefix, buffer, len(chunks)))
                buffer = ""
            buffer = f"{buffer}\n\n{para}" if buffer else para
        if buffer:
            chunks.append(_make(doc, workspace_id, prefix, buffer, len(chunks)))
    return chunks


def _make(doc: Document, workspace_id: str, prefix: str, body: str, n: int) -> Chunk:
    text = f"{prefix}\n{body}" if prefix else body
    digest = hashlib.sha1(f"{workspace_id}:{doc.id}:{n}".encode()).hexdigest()[:16]
    return Chunk(
        id=f"{doc.id}#{n}-{digest}",
        doc_id=doc.id,
        workspace_id=workspace_id,
        text=text,
        metadata={**dict(doc.metadata), "section": prefix, "position": n},
    )


async def index_documents(
    docs: Sequence[Document], workspace_id: str, embedder: Embedder, store: Store
) -> int:
    chunks = [c for d in docs for c in chunk_document(d, workspace_id)]
    if not chunks:
        return 0
    vectors = await embedder.embed([c.text for c in chunks])
    await store.upsert(chunks, vectors)
    return len(chunks)
