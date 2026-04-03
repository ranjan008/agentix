# syntax=docker/dockerfile:1.7
# Agentix Platform — Production Docker Image
# Multi-stage build: builder → slim runtime

# ---------------------------------------------------------------------------
# Stage 1 — builder
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS builder

WORKDIR /build

# System deps for native extensions
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy project files
COPY pyproject.toml ./
COPY agentix/ ./agentix/

# Install into a prefix we'll copy to the runtime stage
RUN pip install --prefix=/install --no-cache-dir ".[standard]"

# ---------------------------------------------------------------------------
# Stage 2 — runtime
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

# Security: non-root user
RUN useradd --uid 1000 --create-home --shell /bin/bash agentix
WORKDIR /app

# Runtime system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application source
COPY --chown=agentix:agentix agentix/ ./agentix/
COPY --chown=agentix:agentix config/ ./config/
COPY --chown=agentix:agentix agents/ ./agents/

# Create data directory
RUN mkdir -p /data && chown agentix:agentix /data

USER agentix

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:8080/healthz || exit 1

EXPOSE 8080 8090

# Default: run watchdog
CMD ["python", "-m", "agentix.watchdog.main", "config/watchdog.yaml"]
