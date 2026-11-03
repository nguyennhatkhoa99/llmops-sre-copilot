"""FastAPI app: load settings, build every slot and every configured module, then serve.

Run with ``uvicorn beyondpilot.api.app:create_app --factory``. A bad config
stops start-up instead of failing on the first request. A module is active when
its name appears under ``modules:`` in config; its endpoint answers
``FEATURE_UNAVAILABLE`` otherwise.
"""

from __future__ import annotations

import importlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from beyondpilot import __version__
from beyondpilot.api.errors import install_error_handlers
from beyondpilot.api.routers import internal, modules, v1
from beyondpilot.core.registry import Registry
from beyondpilot.core.safety import SafetyEngine
from beyondpilot.core.settings import Settings, load_settings

# Config name -> class with ``from_registry``. Imported lazily so a module's heavy
# dependencies (Pillow, pypdfium2, LightGBM...) are only needed when it is enabled.
MODULES: dict[str, str] = {
    "rag": "beyondpilot.modules.rag.pipeline:RagPipeline",  # B
    "report": "beyondpilot.modules.report:ReportModule",  # A
    "content_check": "beyondpilot.modules.content_check:ContentCheckModule",  # C
    "documents": "beyondpilot.modules.documents:DocumentModule",  # D
    "vision": "beyondpilot.modules.vision:VisionModule",  # E
    "forecast": "beyondpilot.modules.forecast:ForecastModule",  # F
    "ot": "beyondpilot.modules.ot:OtModule",  # G
    "radar": "beyondpilot.modules.radar:RadarModule",  # H
}


def load_module_class(name: str) -> Any:
    path, cls = MODULES[name].split(":")
    return getattr(importlib.import_module(path), cls)


def build_modules(registry: Registry, settings: Settings) -> dict[str, Any]:
    return {
        name: load_module_class(name).from_registry(registry, settings)
        for name in MODULES
        if name in settings.modules
    }


def create_app(settings: Settings | None = None, registry: Registry | None = None) -> FastAPI:
    """``registry`` lets tests inject ready-made slots; production builds it from config."""
    settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.registry = registry or Registry(settings)
        app.state.safety = SafetyEngine(block_on_pii=settings.safety.block_on_pii)
        app.state.modules = build_modules(app.state.registry, settings)
        app.state.rag = app.state.modules.get("rag")
        yield

    app = FastAPI(title="BeyondPilot AI service", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    install_error_handlers(app)
    app.include_router(internal.router)
    app.include_router(v1.router)
    app.include_router(modules.router)
    return app
