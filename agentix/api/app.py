"""
Agentix Admin API — FastAPI application factory.

Mounts all routers and configures:
  - CORS (configurable origins)
  - Bearer token / API key authentication
  - OpenAPI docs at /docs
  - Static files for the React UI at /ui

Run:
  uvicorn agentix.api.app:create_app --factory --reload --port 8090
"""
from __future__ import annotations

import logging
import os

# Load .env before anything reads os.environ (handles direct uvicorn invocation)
try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=".env", override=True)
except ImportError:
    pass

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from agentix.api.routers import agents, triggers, skills, audit, tenants, health, metrics, auth, chat
from agentix.api.routers import connectors

_logger = logging.getLogger("agentix.api")


def create_app(cfg: dict | None = None) -> FastAPI:
    cfg = cfg or {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # noqa: ARG001
        port = int(os.environ.get("PORT", 8090))
        _logger.info("=" * 60)
        _logger.info("  Agentix Admin API  →  http://localhost:%d/docs", port)
        _logger.info("  Admin UI           →  http://localhost:%d/ui", port)
        _logger.info("  Login: %s / <ADMIN_PASSWORD in .env>", os.environ.get("ADMIN_EMAIL", "admin@agentix.local"))
        _logger.info("=" * 60)
        yield  # server runs here

    app = FastAPI(
        title="Agentix Admin API",
        version="1.0.0",
        description="Enterprise management API for the Agentix agentic platform",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    # CORS
    origins = cfg.get("cors_origins") or os.environ.get("CORS_ORIGINS", "*").split(",")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Routers
    prefix = "/api/v1"
    app.include_router(health.router, tags=["Health"])
    app.include_router(auth.router, prefix=prefix, tags=["Auth"])
    app.include_router(chat.router, prefix=prefix, tags=["Chat"])
    app.include_router(agents.router, prefix=prefix, tags=["Agents"])
    app.include_router(triggers.router, prefix=prefix, tags=["Triggers"])
    app.include_router(skills.router, prefix=prefix, tags=["Skills"])
    app.include_router(audit.router, prefix=prefix, tags=["Audit"])
    app.include_router(tenants.router, prefix=prefix, tags=["Tenants"])
    app.include_router(metrics.router, prefix=prefix, tags=["Metrics"])
    app.include_router(connectors.router, prefix=prefix, tags=["Connectors"])

    # Serve compiled React UI if present
    ui_dist = os.path.join(os.path.dirname(__file__), "..", "..", "ui", "dist")
    if os.path.isdir(ui_dist):
        app.mount("/ui", StaticFiles(directory=ui_dist, html=True), name="ui")

    return app


# Allow direct `uvicorn agentix.api.app:app` too
app = create_app()
