"""
Tool Executor — dispatches tool_use blocks from the LLM to registered tool functions.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)

# Registry: tool_name -> callable
_TOOL_REGISTRY: dict[str, Callable] = {}


def register_tool(name: str, fn: Callable) -> None:
    _TOOL_REGISTRY[name] = fn


def get_registered_tools() -> dict[str, Callable]:
    return dict(_TOOL_REGISTRY)


class ToolExecutor:
    def __init__(self, allowed_tools: list[str] | None = None) -> None:
        # allowed_tools = None means all registered tools are allowed
        self.allowed_tools = set(allowed_tools) if allowed_tools is not None else None

    def execute(self, tool_name: str, tool_input: dict) -> Any:
        if self.allowed_tools is not None and tool_name not in self.allowed_tools:
            raise PermissionError(f"Tool '{tool_name}' is not in this agent's allowed tool list")

        fn = _TOOL_REGISTRY.get(tool_name)
        if not fn:
            raise ValueError(f"Tool '{tool_name}' not found in registry")

        logger.info("Executing tool: %s input=%s", tool_name, json.dumps(tool_input)[:200])
        try:
            result = fn(**tool_input)
            logger.debug("Tool '%s' result: %s", tool_name, str(result)[:200])
            return result
        except Exception as exc:
            logger.error("Tool '%s' error: %s", tool_name, exc)
            raise

    def get_tool_schemas(self, tool_names: list[str]) -> list[dict]:
        """Return Anthropic-compatible tool schemas for the given tool names."""
        schemas = []
        for name in tool_names:
            fn = _TOOL_REGISTRY.get(name)
            if fn and hasattr(fn, "_tool_schema"):
                schemas.append(fn._tool_schema)
            else:
                logger.warning("No schema found for tool '%s'", name)
        return schemas


def tool(name: str, description: str, input_schema: dict):
    """Decorator to register a function as a tool."""
    def decorator(fn: Callable) -> Callable:
        schema = {
            "name": name,
            "description": description,
            "input_schema": input_schema,
        }
        fn._tool_schema = schema
        register_tool(name, fn)
        return fn
    return decorator
