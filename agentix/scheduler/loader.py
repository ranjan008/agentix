"""
Schedule YAML loader — registers schedules defined in YAML files into the Scheduler.

Supports agentix/v1 Schedule and Pipeline kinds.

Example Schedule YAML:
  apiVersion: agentix/v1
  kind: Schedule
  metadata:
    name: weekly-report
  spec:
    type: cron
    expression: "0 18 * * FRI"
    timezone: America/New_York
    agent: report-generator
    payload:
      report_type: weekly_summary

Example Pipeline YAML:
  apiVersion: agentix/v1
  kind: Pipeline
  metadata:
    name: nightly-etl
  spec:
    trigger:
      type: cron
      expression: "0 2 * * *"
    steps:
      - id: extract
        agent: data-extractor
      - id: transform
        agent: data-transformer
        depends_on: [extract]
      - id: load
        agent: db-loader
        depends_on: [transform]
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

import yaml

from agentix.scheduler.engine import Scheduler

logger = logging.getLogger(__name__)


def load_schedules_dir(scheduler: Scheduler, schedules_dir: str | Path = "schedules") -> int:
    """
    Scan a directory for *.yaml schedule/pipeline definitions and register them.
    Returns count of schedules loaded.
    """
    schedules_dir = Path(schedules_dir)
    if not schedules_dir.exists():
        return 0

    count = 0
    for path in sorted(schedules_dir.glob("**/*.yaml")):
        try:
            with open(path) as f:
                doc = yaml.safe_load(f)
            _register(scheduler, doc, str(path))
            count += 1
        except Exception as e:
            logger.warning("Failed to load schedule %s: %s", path, e)
    return count


def _register(scheduler: Scheduler, doc: dict, source: str) -> None:
    if doc.get("apiVersion") != "agentix/v1":
        raise ValueError(f"Unsupported apiVersion in {source}")

    kind = doc.get("kind")
    name = doc["metadata"]["name"]
    spec = doc.get("spec", {})
    tenant_id = doc.get("metadata", {}).get("tenant_id", "default")
    run_as_role = spec.get("rbac", {}).get("run_as_role", "scheduler-service")

    if kind == "Schedule":
        stype = spec.get("type", "cron")
        if stype == "cron":
            scheduler.add_cron(
                name=name,
                expression=spec["expression"],
                agent_id=spec["agent"],
                payload=spec.get("payload", {}),
                tenant_id=tenant_id,
                run_as_role=run_as_role,
                timezone=spec.get("timezone", "UTC"),
            )
        elif stype == "one_shot":
            fire_at = spec.get("fire_at")
            if isinstance(fire_at, str):
                from datetime import datetime, timezone
                fire_at = datetime.fromisoformat(fire_at).timestamp()
            scheduler.add_one_shot(
                name=name,
                fire_at=float(fire_at or time.time() + 60),
                agent_id=spec["agent"],
                payload=spec.get("payload", {}),
                tenant_id=tenant_id,
                run_as_role=run_as_role,
            )
        else:
            raise ValueError(f"Unknown schedule type '{stype}' in {source}")

    elif kind == "Pipeline":
        scheduler.add_dag(
            name=name,
            steps=spec.get("steps", []),
            trigger_spec=spec.get("trigger", {"type": "cron", "expression": "0 2 * * *"}),
            tenant_id=tenant_id,
            run_as_role=run_as_role,
        )
    else:
        raise ValueError(f"Unknown kind '{kind}' in {source}")

    logger.info("Registered %s: %s (from %s)", kind, name, source)
