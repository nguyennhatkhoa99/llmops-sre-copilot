"""API key check between the Backend and the AI service.

Keys are compared in constant time against every configured key, so response
time does not reveal how much of a key matched or which key is closest.
"""

from __future__ import annotations

import hmac

from fastapi import Request

from beyondpilot.core.errors import AppError, ErrorCode
from beyondpilot.core.settings import Settings


def key_matches(candidate: str, settings: Settings) -> bool:
    supplied = candidate.encode()
    matched = False
    for key in settings.api_keys:
        # Check every key; no early exit.
        matched |= hmac.compare_digest(supplied, key.get_secret_value().encode())
    return matched


async def require_api_key(request: Request) -> None:
    settings: Settings = request.app.state.settings
    if settings.auth_disabled:
        return
    supplied = request.headers.get(settings.api_key_header)
    if not supplied:
        raise AppError(ErrorCode.UNAUTHENTICATED)
    if not key_matches(supplied, settings):
        raise AppError(ErrorCode.FORBIDDEN)
