"""Public API v1. Every route requires the API key. Use cases are added here."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from beyondpilot.api.auth import require_api_key

router = APIRouter(prefix="/api/v1", tags=["v1"], dependencies=[Depends(require_api_key)])


@router.get("/ping")
async def ping() -> dict[str, bool]:
    """Lets the Backend check that its key is accepted."""
    return {"ok": True}
