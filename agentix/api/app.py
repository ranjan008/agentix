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

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from agentix.api.routers import agents, triggers, skills, audit, tenants, health, metrics


def create_app(cfg: dict | None = None) -> FastAPI:
    cfg = cfg or {}

    app = FastAPI(
        title="Agentix Admin API",
        version="4.0.0",
        description="Enterprise management API for the Agentix agentic platform",
        docs_url="/docs",
        redoc_url="/redoc",
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
    app.include_router(agents.router, prefix=prefix, tags=["Agents"])
    app.include_router(triggers.router, prefix=prefix, tags=["Triggers"])
    app.include_router(skills.router, prefix=prefix, tags=["Skills"])
    app.include_router(audit.router, prefix=prefix, tags=["Audit"])
    app.include_router(tenants.router, prefix=prefix, tags=["Tenants"])
    app.include_router(metrics.router, prefix=prefix, tags=["Metrics"])

    # Serve compiled React UI if present
    ui_dist = os.path.join(os.path.dirname(__file__), "..", "..", "ui", "dist")
    if os.path.isdir(ui_dist):
        app.mount("/ui", StaticFiles(directory=ui_dist, html=True), name="ui")

    return app


# Allow direct `uvicorn agentix.api.app:app` too
app = create_app()
