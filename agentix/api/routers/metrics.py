"""
Metrics / observability router.

GET  /metrics/cost        — cost ledger summary (per agent / per tenant)
GET  /metrics/triggers    — trigger throughput stats
GET  /metrics/agents      — agent execution stats
GET  /metrics/prometheus  — Prometheus text format scrape endpoint
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import PlainTextResponse

from agentix.api.deps import get_store, require_admin, get_current_identity
from agentix.storage.state_store import StateStore

router = APIRouter()


@router.get("/metrics/cost")
async def cost_summary(
    store: Annotated[StateStore, Depends(get_store)],
    identity: Annotated[dict, Depends(require_admin)],
    tenant_id: str | None = Query(None),
    agent_id: str | None = Query(None),
) -> dict:
    import os
    from agentix.observability.cost_ledger import CostLedger
    db_path = os.environ.get("AGENTIX_DB_PATH", "data/agentix.db")
    ledger = CostLedger(db_path=db_path)
    summary = ledger.summary(tenant_id=tenant_id, agent_id=agent_id)
    return summary


@router.get("/metrics/triggers")
async def trigger_stats(
    store: Annotated[StateStore, Depends(get_store)],
    identity: Annotated[dict, Depends(require_admin)],
    hours: int = Query(24, le=720),
) -> dict:
    stats = store.trigger_stats(hours=hours)
    return stats


@router.get("/metrics/agents")
async def agent_stats(
    store: Annotated[StateStore, Depends(get_store)],
    identity: Annotated[dict, Depends(require_admin)],
) -> list[dict]:
    return store.agent_execution_stats()


@router.get("/metrics/prometheus", response_class=PlainTextResponse)
async def prometheus_metrics(
    store: Annotated[StateStore, Depends(get_store)],
    identity: Annotated[dict, Depends(get_current_identity)],
) -> str:
    """
    Minimal Prometheus exposition format.
    For production, use the OpenTelemetry Prometheus exporter instead.
    """
    lines = []
    try:
        stats = store.trigger_stats(hours=1)
        total = stats.get("total", 0)
        running = stats.get("running", 0)
        failed = stats.get("failed", 0)
        lines += [
            "# HELP agentix_triggers_total Total triggers in last hour",
            "# TYPE agentix_triggers_total gauge",
            f"agentix_triggers_total {total}",
            "# HELP agentix_triggers_running Currently running triggers",
            "# TYPE agentix_triggers_running gauge",
            f"agentix_triggers_running {running}",
            "# HELP agentix_triggers_failed_total Failed triggers in last hour",
            "# TYPE agentix_triggers_failed_total gauge",
            f"agentix_triggers_failed_total {failed}",
        ]
    except Exception:
        lines.append("# metrics unavailable")

    return "\n".join(lines) + "\n"
