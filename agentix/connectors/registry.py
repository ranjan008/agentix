"""
ConnectorRegistry — maps type_name strings to BaseConnector subclasses.

Usage:
    @register_connector("github")
    class GitHubConnector(BaseConnector): ...

    cls = get_connector_class("github")
    instance = cls(cfg)
"""
from __future__ import annotations

import logging
from typing import Type

from agentix.connectors.base import BaseConnector

_logger = logging.getLogger(__name__)
_CONNECTOR_TYPES: dict[str, Type[BaseConnector]] = {}
_builtins_loaded = False


def register_connector(type_name: str):
    """Class decorator — registers a connector implementation by type name."""
    def decorator(cls: Type[BaseConnector]) -> Type[BaseConnector]:
        _CONNECTOR_TYPES[type_name] = cls
        _logger.debug("Registered connector type: %s → %s", type_name, cls.__name__)
        return cls
    return decorator


def get_connector_class(type_name: str) -> Type[BaseConnector] | None:
    _load_builtins()
    return _CONNECTOR_TYPES.get(type_name)


def list_registered_types() -> list[str]:
    _load_builtins()
    return sorted(_CONNECTOR_TYPES.keys())


def _load_builtins() -> None:
    global _builtins_loaded
    if _builtins_loaded:
        return
    _builtins_loaded = True
    try:
        from agentix.connectors.builtin import (  # noqa: F401
            github, slack, notion, jira, hubspot,
            stripe, sendgrid, twilio, airtable,
            linear, discord, webhook,
        )
    except Exception as exc:  # pragma: no cover
        _logger.warning("Could not load some builtin connectors: %s", exc)
