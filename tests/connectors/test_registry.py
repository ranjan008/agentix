"""
Regression test for ConnectorRegistry._load_builtins().

gmail, google_calendar, google_drive, and google_sheets each define a
real, fully-implemented BaseConnector subclass with its own
@register_connector(...) decorator — but _load_builtins() never imported
those four modules, so the decorators never ran and get_connector_class()
returned None for all of them unconditionally, no matter how correctly a
caller configured credentials or built an agent spec. Found live: a
Gmail-using agent had valid credentials already in the store,
ConnectorEngine.load_for_agent() found them fine, and still failed with
"Unknown connector type 'gmail'".

This test enumerates every module in agentix/connectors/builtin/ that
defines a class decorated with @register_connector(...) and asserts each
one is actually reachable via get_connector_class() — so a future connector
module added to that directory without also being added to
_load_builtins()'s import list fails a test instead of silently existing
but never loading, the same way these four did.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from agentix.connectors.registry import get_connector_class, list_registered_types

_BUILTIN_DIR = Path(__file__).resolve().parents[2] / "agentix" / "connectors" / "builtin"


def _decorated_connector_type_names() -> list[str]:
    """Statically scan every builtin connector module for its
    @register_connector("...") type name, without importing anything —
    this must not rely on _load_builtins() itself, or a module missing
    from that function's import list would just as easily be missing from
    this check."""
    names: list[str] = []
    for path in sorted(_BUILTIN_DIR.glob("*.py")):
        if path.stem.startswith("_") or path.stem == "__init__":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for deco in node.decorator_list:
                if (
                    isinstance(deco, ast.Call)
                    and isinstance(deco.func, ast.Name)
                    and deco.func.id == "register_connector"
                    and deco.args
                    and isinstance(deco.args[0], ast.Constant)
                ):
                    names.append(deco.args[0].value)
    return names


@pytest.mark.parametrize("type_name", _decorated_connector_type_names())
def test_every_decorated_connector_is_loadable(type_name: str) -> None:
    assert get_connector_class(type_name) is not None, (
        f"'{type_name}' has a @register_connector decorator in "
        f"agentix/connectors/builtin/ but get_connector_class({type_name!r}) "
        "returned None — it's missing from ConnectorRegistry._load_builtins()'s "
        "import list, exactly like gmail/google_calendar/google_drive/"
        "google_sheets were before this fix."
    )


def test_google_family_connectors_specifically_load() -> None:
    """The exact four this bug hid — pinned explicitly, not just covered
    by the generic scan above, so a future refactor of the scan itself
    can't accidentally stop catching this specific regression."""
    for name in ("gmail", "google_calendar", "google_drive", "google_sheets"):
        assert get_connector_class(name) is not None, name
    registered = list_registered_types()
    for name in ("gmail", "google_calendar", "google_drive", "google_sheets"):
        assert name in registered
