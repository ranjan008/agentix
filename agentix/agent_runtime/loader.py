"""
Agent Loader — reads an agent YAML definition and validates it.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


REQUIRED_FIELDS = ("metadata.name", "spec.model.provider", "spec.model.model_id")


class AgentLoadError(Exception):
    pass


def load_agent_spec(path: str | Path) -> dict:
    """Load and minimally validate an agent YAML file."""
    path = Path(path)
    if not path.exists():
        raise AgentLoadError(f"Agent spec not found: {path}")

    with open(path) as f:
        spec = yaml.safe_load(f)

    if spec.get("apiVersion") != "agentix/v1":
        raise AgentLoadError("Agent spec must have apiVersion: agentix/v1")
    if spec.get("kind") != "Agent":
        raise AgentLoadError("Agent spec must have kind: Agent")

    for field in REQUIRED_FIELDS:
        obj = spec
        for part in field.split("."):
            obj = obj.get(part, {}) if isinstance(obj, dict) else None
        if not obj:
            raise AgentLoadError(f"Missing required field: {field}")

    return spec


def find_agent_spec(agent_id: str, agents_dir: str | Path = "agents") -> Path | None:
    """Locate an agent YAML file by agent_id."""
    agents_dir = Path(agents_dir)
    for pattern in (f"{agent_id}.yaml", f"{agent_id}.yml", f"**/{agent_id}.yaml"):
        matches = list(agents_dir.glob(pattern))
        if matches:
            return matches[0]
    return None
