"""Probes for k3s: liveness and readiness. No API key."""

from __future__ import annotations

from fastapi import APIRouter, Request

from beyondpilot import __version__
from beyondpilot.api.schemas import HealthResponse, ReadyResponse
from beyondpilot.core.errors import AppError, ErrorCode

router = APIRouter(prefix="/internal/v1", tags=["internal"])


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok", version=__version__)


@router.get("/ready", response_model=ReadyResponse)
async def ready(request: Request) -> ReadyResponse:
    registry = getattr(request.app.state, "registry", None)
    safety = getattr(request.app.state, "safety", None)
    if registry is None or safety is None:
        raise AppError(ErrorCode.NOT_READY)
    return ReadyResponse(status="ready", slots=len(registry.slots()), safety=safety.backends)
