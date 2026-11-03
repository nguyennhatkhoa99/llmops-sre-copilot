"""API v1 endpoints for modules A and C–H. Module B (``/chat``) lives in ``v1.py``.

Every route needs the API key. Each one checks that its module is configured,
calls it, and returns plain JSON. Uploads (documents, photos) use multipart.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from pydantic import BaseModel, Field

from beyondpilot.api.auth import require_api_key
from beyondpilot.core.errors import AppError, ErrorCode

router = APIRouter(prefix="/api/v1", tags=["modules"], dependencies=[Depends(require_api_key)])

_ID = r"^[A-Za-z0-9_.:-]{1,128}$"
Scalar = str | int | float


def module(request: Request, name: str) -> Any:
    found = getattr(request.app.state, "modules", {}).get(name)
    if found is None:
        raise AppError(ErrorCode.FEATURE_UNAVAILABLE, f"module {name} is not configured")
    return found


async def read_upload(upload: UploadFile, limit: int) -> bytes:
    data = await upload.read(limit + 1)
    if len(data) > limit:
        raise AppError(ErrorCode.INVALID_INPUT, f"file larger than {limit} bytes")
    return data


# ---------- A: reports ----------


class ReportRequest(BaseModel):
    report: str = Field(pattern=_ID)
    params: dict[str, Scalar] = Field(default_factory=dict, max_length=20)


@router.post("/reports")
async def build_report(body: ReportRequest, request: Request) -> dict[str, Any]:
    result = await module(request, "report").build(body.report, body.params)
    return asdict(result)


# ---------- C: content check ----------


class ContentCheckRequest(BaseModel):
    text: str = Field(min_length=1, max_length=20000)
    channel: str = Field(default="", max_length=64)


@router.post("/checks/content")
async def check_content(body: ContentCheckRequest, request: Request) -> dict[str, Any]:
    mod = module(request, "content_check")
    text = await request.app.state.safety.ensure_safe(body.text)
    return asdict(await mod.check(text, body.channel))


# ---------- D: documents ----------


@router.post("/documents")
async def ingest_document(
    request: Request,
    file: UploadFile = File(...),
    workspace_id: str = Form(..., pattern=_ID),
    doc_id: str = Form(..., pattern=_ID),
    title: str = Form("", max_length=300),
) -> dict[str, Any]:
    mod = module(request, "documents")
    data = await read_upload(file, mod.max_bytes)
    result = await mod.ingest(
        data,
        declared_mime=file.content_type or "application/octet-stream",
        workspace_id=workspace_id,
        doc_id=doc_id,
        title=title,
    )
    return asdict(result)


# ---------- E: image check ----------


@router.post("/checks/image")
async def check_image(
    request: Request,
    file: UploadFile = File(...),
    store_id: str = Form(..., pattern=_ID),
    rubric: str = Form(..., pattern=_ID),
    store_lat: float | None = Form(None, ge=-90, le=90),
    store_lng: float | None = Form(None, ge=-180, le=180),
) -> dict[str, Any]:
    mod = module(request, "vision")
    data = await read_upload(file, mod.max_bytes)
    location = (store_lat, store_lng) if store_lat is not None and store_lng is not None else None
    result = await mod.check(data, store_id=store_id, rubric=rubric, store_location=location)
    return asdict(result)


# ---------- F: forecast ----------


class ForecastRequest(BaseModel):
    series: str = Field(pattern=_ID)
    horizon: int = Field(ge=1, le=365)
    supply: float | None = Field(default=None, ge=0)
    caps: dict[str, float] = Field(default_factory=dict, max_length=500)
    params: dict[str, Scalar] = Field(default_factory=dict, max_length=20)


@router.post("/forecasts")
async def forecast(body: ForecastRequest, request: Request) -> dict[str, Any]:
    result = await module(request, "forecast").run(
        body.series, horizon=body.horizon, supply=body.supply, caps=body.caps, params=body.params
    )
    return asdict(result)


# ---------- G: setpoint advice ----------


class SetpointRequest(BaseModel):
    line_id: str = Field(pattern=_ID)


@router.post("/setpoints/advise")
async def advise_setpoint(body: SetpointRequest, request: Request) -> dict[str, Any]:
    return asdict(await module(request, "ot").advise(body.line_id))


# ---------- H: radar ----------


@router.post("/radar/runs")
async def run_radar(request: Request) -> dict[str, Any]:
    return asdict(await module(request, "radar").run())
