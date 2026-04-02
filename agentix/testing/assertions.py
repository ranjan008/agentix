"""
AgentAssertions — fluent assertion API for AgentRunResult.

Usage (pytest):

  result = await harness.run("What is the weather?", llm_script=script)

  AgentAssertions(result) \
      .completed_successfully() \
      .called_tool("get_weather") \
      .tool_input_contains("get_weather", {"location": "NYC"}) \
      .response_contains("sunny") \
      .llm_turns_at_most(3) \
      .no_error()
"""
from __future__ import annotations

from agentix.testing.harness import AgentRunResult


class AssertionError(Exception):
    """Raised when an agent assertion fails."""


class AgentAssertions:
    def __init__(self, result: AgentRunResult) -> None:
        self._result = result

    # ------------------------------------------------------------------
    # Lifecycle assertions
    # ------------------------------------------------------------------

    def completed_successfully(self) -> "AgentAssertions":
        """Assert that the agent run completed without error."""
        if self._result.error:
            raise AssertionError(f"Agent run failed with error: {self._result.error}")
        if not self._result.final_text:
            raise AssertionError("Agent run produced no final text")
        return self

    def no_error(self) -> "AgentAssertions":
        if self._result.error:
            raise AssertionError(f"Expected no error but got: {self._result.error}")
        return self

    def failed_with(self, error_substring: str) -> "AgentAssertions":
        if not self._result.error:
            raise AssertionError("Expected agent to fail, but it succeeded")
        if error_substring not in self._result.error:
            raise AssertionError(
                f"Expected error containing '{error_substring}', got: {self._result.error}"
            )
        return self

    # ------------------------------------------------------------------
    # Response assertions
    # ------------------------------------------------------------------

    def response_contains(self, substring: str, case_sensitive: bool = True) -> "AgentAssertions":
        text = self._result.final_text
        if not case_sensitive:
            text = text.lower()
            substring = substring.lower()
        if substring not in text:
            raise AssertionError(
                f"Expected response to contain '{substring}'.\nActual: {self._result.final_text[:300]}"
            )
        return self

    def response_not_contains(self, substring: str) -> "AgentAssertions":
        if substring in self._result.final_text:
            raise AssertionError(
                f"Expected response NOT to contain '{substring}'.\nActual: {self._result.final_text[:300]}"
            )
        return self

    def response_matches(self, pattern: str) -> "AgentAssertions":
        import re
        if not re.search(pattern, self._result.final_text):
            raise AssertionError(
                f"Expected response to match pattern '{pattern}'.\nActual: {self._result.final_text[:300]}"
            )
        return self

    def response_length_between(self, min_len: int, max_len: int) -> "AgentAssertions":
        n = len(self._result.final_text)
        if not (min_len <= n <= max_len):
            raise AssertionError(f"Expected response length {min_len}–{max_len}, got {n}")
        return self

    # ------------------------------------------------------------------
    # Tool call assertions
    # ------------------------------------------------------------------

    def called_tool(self, tool_name: str) -> "AgentAssertions":
        names = [tc.name for tc in self._result.tool_calls]
        if tool_name not in names:
            raise AssertionError(
                f"Expected tool '{tool_name}' to be called, but tools called were: {names}"
            )
        return self

    def not_called_tool(self, tool_name: str) -> "AgentAssertions":
        names = [tc.name for tc in self._result.tool_calls]
        if tool_name in names:
            raise AssertionError(f"Expected tool '{tool_name}' NOT to be called, but it was")
        return self

    def tool_call_count(self, expected: int) -> "AgentAssertions":
        actual = len(self._result.tool_calls)
        if actual != expected:
            raise AssertionError(f"Expected {expected} tool call(s), got {actual}")
        return self

    def tool_call_count_at_most(self, max_calls: int) -> "AgentAssertions":
        actual = len(self._result.tool_calls)
        if actual > max_calls:
            raise AssertionError(f"Expected at most {max_calls} tool calls, got {actual}")
        return self

    def tool_input_contains(self, tool_name: str, expected_fields: dict) -> "AgentAssertions":
        """Assert that a specific tool was called with at least the given input fields."""
        for tc in self._result.tool_calls:
            if tc.name == tool_name:
                for key, val in expected_fields.items():
                    if tc.input.get(key) != val:
                        raise AssertionError(
                            f"Tool '{tool_name}' called with {tc.input}, expected field {key}={val!r}"
                        )
                return self
        raise AssertionError(f"Tool '{tool_name}' was never called")

    def tool_called_before(self, first: str, second: str) -> "AgentAssertions":
        names = [tc.name for tc in self._result.tool_calls]
        if first not in names:
            raise AssertionError(f"Tool '{first}' was never called")
        if second not in names:
            raise AssertionError(f"Tool '{second}' was never called")
        if names.index(first) >= names.index(second):
            raise AssertionError(f"Expected '{first}' to be called before '{second}', got order: {names}")
        return self

    # ------------------------------------------------------------------
    # Performance assertions
    # ------------------------------------------------------------------

    def llm_turns_at_most(self, max_turns: int) -> "AgentAssertions":
        if self._result.llm_turns > max_turns:
            raise AssertionError(f"Expected at most {max_turns} LLM turn(s), got {self._result.llm_turns}")
        return self

    def llm_turns_exactly(self, expected: int) -> "AgentAssertions":
        if self._result.llm_turns != expected:
            raise AssertionError(f"Expected exactly {expected} LLM turn(s), got {self._result.llm_turns}")
        return self

    def elapsed_under(self, max_sec: float) -> "AgentAssertions":
        if self._result.elapsed_sec > max_sec:
            raise AssertionError(
                f"Expected run to complete in under {max_sec}s, took {self._result.elapsed_sec:.2f}s"
            )
        return self

    # ------------------------------------------------------------------
    # Custom assertion
    # ------------------------------------------------------------------

    def satisfies(self, predicate, description: str = "custom predicate") -> "AgentAssertions":
        """Assert that a callable(result) returns True."""
        if not predicate(self._result):
            raise AssertionError(f"Result does not satisfy: {description}")
        return self
