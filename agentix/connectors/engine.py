"""
ConnectorEngine — loads connectors listed in an agent spec and
registers their action-tools into the ToolExecutor.

Flow:
  1. Agent YAML lists ``spec.connectors: [github, slack]`` (name refs)
     or inline: ``[{name: github, config: {token: sk-xxx}}]``
  2. ConnectorEngine.load_for_agent() looks up each name in the state
     store (where admins configure credentials via UI / YAML).
  3. It instantiates the connector, calls connect(), registers tools.
  4. At agent shutdown, disconnect() is called.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from agentix.connectors.registry import get_connector_class

if TYPE_CHECKING:
    pass

_logger = logging.getLogger(__name__)


class ConnectorEngine:
    def __init__(self, store: Any = None) -> None:
        self._store = store
        self._active: dict[str, Any] = {}   # name → BaseConnector instance

    # ── Public API ──────────────────────────────────────────────────────

    async def load_for_agent(
        self,
        connector_refs: list[str | dict],
        tool_registry: dict,
    ) -> None:
        """
        Load and connect all connectors referenced in an agent spec,
        registering their tools into ``tool_registry`` (the global
        _TOOL_REGISTRY dict used by ToolExecutor).

        :param connector_refs: from agent YAML ``spec.connectors``
        :param tool_registry: the shared {tool_name: callable} dict
        """
        for ref in connector_refs:
            name, inline_cfg = self._parse_ref(ref)
            if not name:
                continue

            # Merge: store config wins over inline for credentials,
            # inline config can override non-secret options.
            stored = self._fetch_from_store(name)
            if stored is None and not inline_cfg:
                _logger.warning("Connector %r not found in store — skipping", name)
                continue

            merged_cfg = {**(stored or {}), **inline_cfg}
            connector_type = merged_cfg.pop("type", name)

            cls = get_connector_class(connector_type)
            if cls is None:
                _logger.warning("Unknown connector type %r (name=%r)", connector_type, name)
                continue

            try:
                inst = cls(merged_cfg)
                await inst.connect()
                self._active[name] = inst

                for tool_name, fn in inst.action_handlers().items():
                    tool_registry[tool_name] = fn

                _logger.info(
                    "Connector %r loaded (%s), tools: %s",
                    name, connector_type, list(inst.action_handlers()),
                )
            except Exception as exc:
                _logger.error("Failed to load connector %r: %s", name, exc)

    def tool_schemas(self) -> list[dict]:
        """Collect Anthropic tool schemas from all active connectors."""
        schemas: list[dict] = []
        for inst in self._active.values():
            schemas.extend(inst.tool_schemas())
        return schemas

    async def shutdown(self) -> None:
        for name, inst in self._active.items():
            try:
                await inst.disconnect()
            except Exception as exc:
                _logger.debug("Error disconnecting %r: %s", name, exc)
        self._active.clear()

    # ── Private helpers ─────────────────────────────────────────────────

    @staticmethod
    def _parse_ref(ref: str | dict) -> tuple[str, dict]:
        if isinstance(ref, str):
            return ref, {}
        if isinstance(ref, dict):
            return ref.get("name", ""), ref.get("config", {})
        return "", {}

    def _fetch_from_store(self, name: str) -> dict | None:
        if self._store is None:
            return None
        try:
            return self._store.get_connector(name)
        except Exception:
            return None
