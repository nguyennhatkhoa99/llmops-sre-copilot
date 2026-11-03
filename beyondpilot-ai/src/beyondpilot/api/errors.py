"""Map every error to ``{"code", "message"}``. Details go to the log, not the client."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from beyondpilot.core.errors import AppError, ErrorCode

logger = logging.getLogger("beyondpilot.api")


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error(request: Request, exc: AppError) -> JSONResponse:
        logger.warning(
            "app_error code=%s path=%s detail=%s", exc.code.value, request.url.path, exc.detail
        )
        return JSONResponse(status_code=exc.status, content=exc.to_dict())

    @app.exception_handler(RequestValidationError)
    async def bad_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        err = AppError(ErrorCode.INVALID_INPUT)
        return JSONResponse(status_code=err.status, content=err.to_dict())

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.error("unhandled %s at %s", type(exc).__name__, request.url.path)
        err = AppError(ErrorCode.INTERNAL)
        return JSONResponse(status_code=err.status, content=err.to_dict())
