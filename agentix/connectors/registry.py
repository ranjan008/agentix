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
            # gmail/google_calendar/google_drive/google_sheets were real,
            # fully-implemented connector modules (each with its own
            # @register_connector(...) decorator) that this list simply
            # never imported — meaning their decorators never ran, and
            # get_connector_class() for any of these four always returned
            # None, unconditionally, no matter how correctly a caller
            # configured credentials or built its agent spec. Found live:
            # a Gmail-using agent had valid credentials in the store,
            # ConnectorEngine.load_for_agent() found them fine, and still
            # failed with "Unknown connector type 'gmail'" — reproduced by
            # calling get_connector_class("gmail") directly and confirming
            # it returned None even with the module sitting right there in
            # agentix/connectors/builtin/gmail.py.
            gmail, google_calendar, google_drive, google_sheets,
        )
    except Exception as exc:  # pragma: no cover
        _logger.warning("Could not load some builtin connectors: %s", exc)
