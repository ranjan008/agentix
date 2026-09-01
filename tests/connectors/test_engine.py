"""
Regression test for ConnectorEngine.load_for_agent()'s handling of what
StateStore.get_connector() actually returns.

StateStore.get_connector() (agentix/storage/state_store.py) returns the
full DB row for a connector: {name, type, config: {...}, enabled, status,
tenant_id, created_at, updated_at, ...} — the real credential fields
(url, api_key, credentials_json, whatever a given connector needs) live
nested one level down, under "config". load_for_agent() used to merge
that whole wrapper directly as if it WERE the flat config, so a
connector's real credentials ended up at stored["config"]["url"] (etc.)
and were never actually passed to the connector instance, which only
ever reads top-level keys via self._cfg.get(...). Every connector call
failed with its own "missing required config" error regardless of
whether the tenant had genuinely configured it correctly — found live
with a real Gmail connector, reproduced here with the built-in `webhook`
connector instead, since its connect() needs no network access (just
validates a "url" key is present via BaseConnector._require).
"""
from __future__ import annotations

import pytest

from agentix.connectors.engine import ConnectorEngine


class _FakeStore:
    """Mimics StateStore.get_connector()'s real return shape exactly —
    the wrapper dict, not a flat config."""

    def __init__(self, records: dict[str, dict]) -> None:
        self._records = records

    def get_connector(self, name: str) -> dict | None:
        return self._records.get(name)


@pytest.mark.asyncio
async def test_load_for_agent_unwraps_nested_config() -> None:
    store = _FakeStore(
        {
            "tenant-1-webhook": {
                "name": "tenant-1-webhook",
                "type": "webhook",
                "config": {"url": "https://example.com/hooks/incoming"},
                "enabled": True,
                "status": "pending",
                "tenant_id": "tenant-1",
            }
        }
    )
    engine = ConnectorEngine(store)
    tool_registry: dict = {}

    await engine.load_for_agent(["tenant-1-webhook"], tool_registry)

    assert "webhook__call" in tool_registry, (
        "connector failed to load — the nested 'config' dict was never unwrapped, "
        "so WebhookConnector never saw its own 'url' and connect() raised"
    )
    schemas = engine.tool_schemas()
    assert any(s["name"] == "webhook__call" for s in schemas)


@pytest.mark.asyncio
async def test_load_for_agent_missing_nested_config_fails_connect() -> None:
    """Sanity check the test's own premise: a record with an EMPTY config
    (still correctly wrapped) should fail to load — proving the assertion
    above is really exercising the unwrap, not just "anything works"."""
    store = _FakeStore(
        {
            "tenant-1-webhook": {
                "name": "tenant-1-webhook",
                "type": "webhook",
                "config": {},  # no "url" — connect() must fail
                "enabled": True,
                "status": "pending",
                "tenant_id": "tenant-1",
            }
        }
    )
    engine = ConnectorEngine(store)
    tool_registry: dict = {}

    await engine.load_for_agent(["tenant-1-webhook"], tool_registry)

    assert "webhook__call" not in tool_registry
