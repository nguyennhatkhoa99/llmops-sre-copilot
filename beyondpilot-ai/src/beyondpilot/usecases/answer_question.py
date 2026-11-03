"""Use case answer_question: safety check, then module B.

The Backend has already decided which workspace and documents the user may see
and sends them with the request; the AI service enforces that list but does not
know about users.
"""

from __future__ import annotations

from collections.abc import Sequence

from beyondpilot.core.contracts import AccessFilter
from beyondpilot.core.safety import SafetyEngine
from beyondpilot.modules.rag.pipeline import RagAnswer, RagPipeline


async def answer_question(
    question: str,
    workspace_id: str,
    doc_ids: Sequence[str] | None,
    *,
    safety: SafetyEngine,
    pipeline: RagPipeline,
) -> RagAnswer:
    clean = await safety.ensure_safe(question)  # masks PII, raises UNSAFE_INPUT on injection
    access = AccessFilter(workspace_id=workspace_id, doc_ids=list(doc_ids) if doc_ids else None)
    return await pipeline.answer(clean, access)
