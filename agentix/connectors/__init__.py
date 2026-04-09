"""
Agentix Connectors — pluggable integrations for external services.

Quick-start:
    from agentix.connectors.registry import get_connector_class
    from agentix.connectors.catalog import CATALOG
    from agentix.connectors.engine import ConnectorEngine
"""

from agentix.connectors.base import BaseConnector, ConnectorAction, ConnectorMeta
from agentix.connectors.catalog import CATALOG, CATALOG_BY_TYPE, CATEGORIES
from agentix.connectors.engine import ConnectorEngine
from agentix.connectors.registry import get_connector_class, register_connector

__all__ = [
    "BaseConnector",
    "ConnectorAction",
    "ConnectorMeta",
    "ConnectorEngine",
    "register_connector",
    "get_connector_class",
    "CATALOG",
    "CATALOG_BY_TYPE",
    "CATEGORIES",
]
