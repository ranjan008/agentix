"""
BaseConnector — abstract base class for all Agentix connectors.

A connector bundles:
  - credentials / config (resolved from env vars or secrets vault)
  - a connect() method to validate credentials
  - action methods that become agent tools when the connector is loaded
  - tool_schemas() for Anthropic-compatible JSON schema export
  - action_handlers() for ToolExecutor registration
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class ConnectorAction:
    """Describes one callable action on a connector."""
    name: str
    description: str
    input_schema: dict


@dataclass
class ConnectorMeta:
    """Static metadata for a connector type — used by the catalog and UI."""
    type_name: str           # e.g. "github"
    display_name: str        # e.g. "GitHub"
    description: str
    category: str            # messaging | developer | crm | finance | database | productivity | custom
    auth_type: str = "api_key"   # api_key | oauth2 | basic | none
    icon: str = "🔌"
    docs_url: str | None = None
    required_config: list[str] = field(default_factory=list)
    optional_config: list[str] = field(default_factory=list)
    actions: list[ConnectorAction] = field(default_factory=list)


class BaseConnector(ABC):
    """
    Abstract base for all connectors.

    Subclasses must:
      - define a class-level ``meta: ConnectorMeta`` attribute
      - implement ``async connect()``
      - implement each action listed in ``meta.actions`` as an async method
        with the same name (e.g. action named "create_issue" → method create_issue())
    """

    meta: ConnectorMeta  # subclass MUST define

    def __init__(self, cfg: dict) -> None:
        self._cfg = self._expand_env(cfg)

    def _expand_env(self, cfg: dict) -> dict:
        """Resolve ${ENV_VAR} placeholders in config string values."""
        def _expand(v: Any) -> Any:
            if isinstance(v, str) and v.startswith("${") and v.endswith("}"):
                key = v[2:-1]
                return os.environ.get(key, "")
            return v

        return {k: _expand(v) for k, v in cfg.items()}

    def _require(self, key: str) -> str:
        val = self._cfg.get(key, "")
        if not val:
            raise ValueError(
                f"Connector '{self.meta.type_name}' requires config key '{key}'"
            )
        return val

    @abstractmethod
    async def connect(self) -> None:
        """
        Validate credentials and establish connection (where persistent).
        Raise an exception if credentials are invalid or unreachable.
        """

    async def disconnect(self) -> None:
        """Release resources. Override if needed (e.g. DB connections)."""

    async def test(self) -> dict[str, Any]:
        """
        Test the connector credentials.
        Returns {"ok": True} or {"ok": False, "error": "<message>"}.
        """
        try:
            await self.connect()
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def tool_schemas(self) -> list[dict]:
        """Return Anthropic-compatible tool schemas for all actions."""
        return [
            {
                "name": f"{self.meta.type_name}__{a.name}",
                "description": a.description,
                "input_schema": a.input_schema,
            }
            for a in self.meta.actions
        ]

    def action_handlers(self) -> dict[str, Callable]:
        """
        Return {tool_name: callable} for ToolExecutor registration.
        Tool names are prefixed: "<type_name>__<action_name>".
        """
        handlers: dict[str, Callable] = {}
        for action in self.meta.actions:
            fn = getattr(self, action.name, None)
            if callable(fn):
                handlers[f"{self.meta.type_name}__{action.name}"] = fn
        return handlers
