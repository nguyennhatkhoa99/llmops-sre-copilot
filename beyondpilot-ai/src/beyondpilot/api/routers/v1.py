"""Public API v1. Every route requires the API key."""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, Request

from beyondpilot.api.auth import require_api_key
from beyondpilot.api.schemas import ChatRequest, ChatResponse
from beyondpilot.core.errors import AppError, ErrorCode
from beyondpilot.usecases.answer_question import answer_question

router = APIRouter(prefix="/api/v1", tags=["v1"], dependencies=[Depends(require_api_key)])


@router.get("/ping")
async def ping() -> dict[str, bool]:
    """Lets the Backend check that its key is accepted."""
    return {"ok": True}


@router.post("/chat", response_model=ChatResponse)
async def chat(body: ChatRequest, request: Request) -> ChatResponse:
    """Use case answer_question: an answer with a citation on every sentence."""
    pipeline = request.app.state.rag
    if pipeline is None:
        raise AppError(ErrorCode.FEATURE_UNAVAILABLE, "module rag is not configured")
    result = await answer_question(
        body.question,
        body.workspace_id,
        body.doc_ids,
        safety=request.app.state.safety,
        pipeline=pipeline,
    )
    return ChatResponse.model_validate(asdict(result))
