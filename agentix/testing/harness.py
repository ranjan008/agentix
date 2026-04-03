"""
AgentTestHarness — run agent specs in isolation without spawning subprocesses.

Features:
  - Injects MockLLMProvider instead of real LLM
  - In-memory SQLite state store (no disk I/O)
  - Captures all tool calls and LLM turns
  - Returns AgentRunResult with full trace
  - async-friendly (pytest-asyncio compatible)

Quick start:

  harness = AgentTestHarness.from_yaml("agents/research-assistant.yaml")

  script = LLMScript([
      LLMTurn(
          tool_calls=[ToolCall(id="t1", name="web_search", input={"query": "climate"})],
          stop_reason="tool_use",
      ),
      LLMTurn(content="Climate change is...", stop_reason="end_turn"),
  ])

  result = await harness.run(
      trigger_text="Tell me about climate change",
      llm_script=script,
  )

  assert result.final_text == "Climate change is..."
  assert result.tool_calls[0].name == "web_search"
  assert result.llm_turns == 2
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field

import yaml

from agentix.llm.base import ToolCall
from agentix.llm.router import LLMRouter
from agentix.testing.mock_llm import MockLLMProvider, LLMScript, LLMTurn


@dataclass
class AgentRunResult:
    final_text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    llm_turns: int = 0
    elapsed_sec: float = 0.0
    error: str | None = None
    messages: list[dict] = field(default_factory=list)


class AgentTestHarness:
    """
    Runs an agent synchronously/asynchronously in an isolated environment.
    """

    def __init__(self, agent_spec: dict, db_path: str = ":memory:") -> None:
        self._spec = agent_spec
        self._db_path = db_path

    @classmethod
    def from_yaml(cls, path: str, db_path: str = ":memory:") -> "AgentTestHarness":
        with open(path) as f:
            spec = yaml.safe_load(f)
        return cls(agent_spec=spec, db_path=db_path)

    @classmethod
    def from_dict(cls, spec: dict, db_path: str = ":memory:") -> "AgentTestHarness":
        return cls(agent_spec=spec, db_path=db_path)

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    async def run(
        self,
        trigger_text: str,
        llm_script: LLMScript | list[LLMTurn] | None = None,
        identity: dict | None = None,
        extra_context: dict | None = None,
        max_iterations: int = 20,
    ) -> AgentRunResult:
        from agentix.storage.state_store import StateStore
        from agentix.agent_runtime.tool_executor import ToolExecutor
        from agentix.skills.engine import SkillEngine

        mock = MockLLMProvider(llm_script)
        router = LLMRouter.__new__(LLMRouter)
        router._providers = {"mock": mock}
        router._default_provider_name = "mock"
        router._routing_rules = []
        router._fallback_chain = []

        store = StateStore(self._db_path)
        agent_id = self._spec.get("metadata", {}).get("name", "test-agent")
        store.upsert_agent(self._spec)

        skill_engine = SkillEngine(store)
        skill_names = self._spec.get("spec", {}).get("skills", [])
        skill_engine.load_skills(skill_names)
        skill_tools = skill_engine.get_tool_schemas(skill_names)

        executor = ToolExecutor(self._spec.get("spec", {}).get("tools", None))
        skill_engine.register_skill_tools(skill_names, executor)

        identity = identity or {"identity_id": "test-user", "roles": ["operator"], "tenant_id": "test"}

        {
            "id": f"trig_{uuid.uuid4().hex[:16]}",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "channel": "test",
            "channel_meta": {},
            "caller": identity,
            "payload": {"text": trigger_text, "attachments": [], "context": extra_context or {}},
            "agent_id": agent_id,
            "priority": "normal",
            "idempotency_key": uuid.uuid4().hex,
        }

        system_prompt = self._spec.get("spec", {}).get("system_prompt", "You are a helpful assistant.")
        tool_names = self._spec.get("spec", {}).get("tools", [])
        tool_schemas = executor.get_tool_schemas(tool_names) + skill_tools

        messages: list[dict] = [{"role": "user", "content": trigger_text}]
        all_tool_calls: list[ToolCall] = []
        final_text = ""
        iterations = 0
        t0 = time.monotonic()
        error = None

        try:
            while iterations < max_iterations:
                iterations += 1
                resp = await router.complete(
                    messages=messages,
                    system=system_prompt,
                    tools=tool_schemas or None,
                )

                if resp.stop_reason in ("end_turn", "stop"):
                    final_text = resp.content
                    break

                if resp.stop_reason == "tool_use" and resp.tool_calls:
                    messages.append({"role": "assistant", "content": resp.content or ""})
                    all_tool_calls.extend(resp.tool_calls)

                    tool_results = []
                    for tc in resp.tool_calls:
                        try:
                            result = executor.execute(tc.name, tc.input)
                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": tc.id,
                                "content": json.dumps(result) if not isinstance(result, str) else result,
                            })
                        except Exception as exc:
                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": tc.id,
                                "content": f"Error: {exc}",
                                "is_error": True,
                            })

                    messages.append({"role": "user", "content": tool_results})
                    continue

                final_text = resp.content
                break

        except Exception as exc:
            error = str(exc)

        return AgentRunResult(
            final_text=final_text,
            tool_calls=all_tool_calls,
            llm_turns=mock.call_count,
            elapsed_sec=time.monotonic() - t0,
            error=error,
            messages=messages,
        )

    def run_sync(self, *args, **kwargs) -> AgentRunResult:
        """Synchronous wrapper for non-async test contexts."""
        return asyncio.run(self.run(*args, **kwargs))
