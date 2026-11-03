"""FastAPI app: load settings, build every slot, then serve.

Run with ``uvicorn beyondpilot.api.app:create_app --factory``. A bad config
stops start-up instead of failing on the first request.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from beyondpilot import __version__
from beyondpilot.api.errors import install_error_handlers
from beyondpilot.api.routers import internal, v1
from beyondpilot.core.registry import Registry
from beyondpilot.core.safety import SafetyEngine
from beyondpilot.core.settings import Settings, load_settings
from beyondpilot.modules.rag import pipeline as rag
from beyondpilot.modules.rag.pipeline import RagPipeline


def create_app(settings: Settings | None = None, registry: Registry | None = None) -> FastAPI:
    """``registry`` lets tests inject ready-made slots; production builds it from config."""
    settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.registry = registry or Registry(settings)
        app.state.safety = SafetyEngine(block_on_pii=settings.safety.block_on_pii)
        app.state.rag = (
            RagPipeline.from_registry(app.state.registry, settings)
            if rag.MODULE in settings.modules
            else None
        )
        yield

    app = FastAPI(title="BeyondPilot AI service", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    install_error_handlers(app)
    app.include_router(internal.router)
    app.include_router(v1.router)
    return app
