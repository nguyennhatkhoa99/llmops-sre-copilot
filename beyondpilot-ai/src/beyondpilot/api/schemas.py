"""Response shapes of API version 1. A breaking change gets a new module, not an edit here."""

from __future__ import annotations

from pydantic import BaseModel


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
