"""
Shared pytest fixtures for the Agentix test suite.

Every test module can import these fixtures via normal pytest discovery.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import tempfile
import time
from pathlib import Path
from typing import AsyncGenerator, Generator
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentix.storage.state_store import StateStore


# ---------------------------------------------------------------------------
# Event loop (needed for async tests)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# ---------------------------------------------------------------------------
# In-memory SQLite StateStore
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    """Return a path to a fresh SQLite DB in a temp directory."""
    return tmp_path / "test.db"


@pytest.fixture
def store(tmp_db: Path) -> StateStore:
    """A clean StateStore backed by a temporary SQLite file."""
    return StateStore(str(tmp_db))


# ---------------------------------------------------------------------------
# Mock LLM provider
# ---------------------------------------------------------------------------

class _MockResponse:
    """Mimics the response object returned by LLMRouter.complete()."""
    def __init__(self, content: str = "mock response", stop_reason: str = "end_turn", tool_calls=None):
        self.content = content
        self.stop_reason = stop_reason
        self.tool_calls = tool_calls or []
        self.raw = {"blocks": [{"type": "text", "text": content}]}
        self.input_tokens = 10
        self.output_tokens = 20


class MockLLM:
    """
    Scriptable mock LLM.  Pass a list of responses; each call to complete()
    pops the next one.  If the list is exhausted, returns the default.
    """
    def __init__(self, responses: list[_MockResponse] | None = None):
        self._responses = list(responses or [])
        self._default = _MockResponse()
        self.calls: list[dict] = []

    async def complete(self, messages, system="", tools=None, **kwargs) -> _MockResponse:
        self.calls.append({"messages": messages, "system": system, "tools": tools})
        if self._responses:
            return self._responses.pop(0)
        return self._default

    def push(self, *responses: _MockResponse):
        self._responses.extend(responses)


@pytest.fixture
def mock_llm() -> MockLLM:
    return MockLLM()


@pytest.fixture
def tool_use_response():
    """Build a mock LLM response that triggers a tool call."""
    def _make(tool_name: str, tool_input: dict, tool_use_id: str = "tu_test") -> _MockResponse:
        return _MockResponse(
            content="",
            stop_reason="tool_use",
            tool_calls=[MagicMock(name=tool_name, input=tool_input, id=tool_use_id)],
        )
    return _make


# ---------------------------------------------------------------------------
# Mock on_trigger callback (records dispatched envelopes)
# ---------------------------------------------------------------------------

@pytest.fixture
def captured_triggers() -> list[dict]:
    return []


@pytest.fixture
def mock_on_trigger(captured_triggers: list[dict]):
    async def _on_trigger(envelope: dict) -> None:
        captured_triggers.append(envelope)
    return _on_trigger


# ---------------------------------------------------------------------------
# Envelope / agent spec factories (imported from factories module)
# ---------------------------------------------------------------------------

from tests.factories import make_envelope, make_agent_spec, make_dag_spec  # noqa: E402

__all__ = [
    "store", "tmp_db", "mock_llm", "MockLLM", "_MockResponse",
    "tool_use_response", "captured_triggers", "mock_on_trigger",
    "make_envelope", "make_agent_spec", "make_dag_spec",
]
