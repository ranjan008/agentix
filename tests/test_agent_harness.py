"""
Example agent tests using AgentTestHarness.

Run:
  pytest tests/test_agent_harness.py -v
"""
import pytest

from agentix.llm.base import ToolCall
from agentix.testing import AgentTestHarness, AgentAssertions, LLMScript, MockLLMProvider
from agentix.testing.mock_llm import LLMTurn, LLMScriptExhausted
from agentix.testing.fixtures import minimal_agent_spec


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def simple_spec():
    return minimal_agent_spec(name="test-agent", system_prompt="You answer questions.")


@pytest.fixture
def harness(simple_spec):
    return AgentTestHarness.from_dict(simple_spec)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_simple_response(harness):
    """Agent returns a plain text response without tool use."""
    script = LLMScript([LLMTurn(content="42", stop_reason="end_turn")])
    result = await harness.run("What is 6 times 7?", llm_script=script)

    AgentAssertions(result) \
        .completed_successfully() \
        .response_contains("42") \
        .tool_call_count(0) \
        .llm_turns_exactly(1) \
        .no_error()


@pytest.mark.asyncio
async def test_tool_use_flow(harness):
    """Agent calls a tool then gives final answer."""
    script = LLMScript([
        LLMTurn(
            tool_calls=[ToolCall(id="tc1", name="get_time", input={})],
            stop_reason="tool_use",
        ),
        LLMTurn(content="The current time is unknown in test mode.", stop_reason="end_turn"),
    ])

    # Register a mock tool
    harness._spec["spec"]["tools"] = ["get_time"]

    result = await harness.run("What time is it?", llm_script=script)

    # Tool was attempted but may have errored (get_time not registered) —
    # the harness handles errors gracefully
    assert result.llm_turns == 2


@pytest.mark.asyncio
async def test_tool_ordering(harness):
    """Verify tool calls happen in expected order."""
    script = LLMScript([
        LLMTurn(
            tool_calls=[
                ToolCall(id="a", name="search", input={"q": "python"}),
                ToolCall(id="b", name="summarise", input={"text": "..."}),
            ],
            stop_reason="tool_use",
        ),
        LLMTurn(content="Python is a language.", stop_reason="end_turn"),
    ])

    result = await harness.run("Tell me about Python", llm_script=script)

    AgentAssertions(result) \
        .called_tool("search") \
        .called_tool("summarise") \
        .tool_called_before("search", "summarise")


@pytest.mark.asyncio
async def test_response_no_pii(harness):
    """Ensure the mock response does not leak PII."""
    from agentix.compliance.pii import PIIDetector

    script = LLMScript([LLMTurn(content="Here is a summary.", stop_reason="end_turn")])
    result = await harness.run("Summarise this", llm_script=script)

    detector = PIIDetector(use_presidio=False)
    assert not detector.contains_pii(result.final_text)


@pytest.mark.asyncio
async def test_script_exhausted_raises(harness):
    """MockLLMProvider raises when script runs out."""
    script = LLMScript([LLMTurn(content="first", stop_reason="tool_use", tool_calls=[
        ToolCall(id="t1", name="loop_tool", input={})
    ])])
    result = await harness.run("trigger loop", llm_script=script)
    # The harness catches LLMScriptExhausted and records it as an error
    assert result.error is not None


def test_run_sync(harness):
    """Verify the synchronous run wrapper works."""
    script = LLMScript([LLMTurn(content="sync works", stop_reason="end_turn")])
    result = harness.run_sync("test", llm_script=script)
    assert result.final_text == "sync works"
