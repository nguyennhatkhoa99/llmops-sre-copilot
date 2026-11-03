"""Response shapes of API version 1. A breaking change gets a new module, not an edit here."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ErrorResponse(BaseModel):
    code: str
    message: str


class HealthResponse(BaseModel):
    status: str
    version: str


class ReadyResponse(BaseModel):
    status: str
    slots: int
    safety: dict[str, str]


_ID = r"^[A-Za-z0-9_.:-]{1,128}$"


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    workspace_id: str = Field(pattern=_ID)
    # Documents this user may read, decided by the Backend. Omit for the whole workspace.
    doc_ids: list[str] | None = Field(default=None, max_length=5000)


class CitationOut(BaseModel):
    index: int
    doc_id: str
    chunk_id: str
    snippet: str


class ChatResponse(BaseModel):
    answer: str
    citations: list[CitationOut]
    cached: bool
    attempts: int
    used_fallback: bool
