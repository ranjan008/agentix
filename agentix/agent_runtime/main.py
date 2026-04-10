"""
Agent Runtime entry point.
Invoked as: python -m agentix.agent_runtime.main

Reads AGENTIX_TRIGGER from environment, loads the agent spec,
runs the agentic loop (LLM + tool calls), and writes output to stdout.
"""
from __future__ import annotations

import json
import logging
import os
import sys

from agentix.agent_runtime.context_builder import build_messages, build_system_prompt, persist_turn
from agentix.agent_runtime.loader import find_agent_spec, load_agent_spec
from agentix.agent_runtime.output_handler import route_output
from agentix.agent_runtime.tool_executor import ToolExecutor
from agentix.connectors.engine import ConnectorEngine
from agentix.llm.router import build_router
from agentix.skills.engine import SkillEngine
from agentix.storage.state_store import StateStore
from agentix.watchdog.config import load_config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("agentix.agent_runtime")

MAX_TOOL_ITERATIONS = 20  # default; overridden by spec.max_tool_calls


def run(envelope: dict) -> None:
    agent_id = envelope["agent_id"]
    db_path = os.environ.get("AGENTIX_DB_PATH", "data/agentix.db")
    agents_dir = os.environ.get("AGENTIX_AGENTS_DIR", "agents")

    store = StateStore(db_path)

    # --- Load agent spec ---
    spec_path = find_agent_spec(agent_id, agents_dir)
    if not spec_path:
        # Fall back to spec stored in DB
        db_spec = store.get_agent(agent_id)
        if not db_spec:
            logger.error("Agent '%s' not found in agents dir or database", agent_id)
            sys.exit(1)
        agent_spec = db_spec
    else:
        agent_spec = load_agent_spec(spec_path)

    logger.info("Agent loaded: %s v%s", agent_id, agent_spec["metadata"].get("version", "?"))

    # --- Load skills ---
    skill_engine = SkillEngine(store)
    skill_names = agent_spec["spec"].get("skills", [])
    skill_instructions = skill_engine.load_skills(skill_names)
    skill_tools = skill_engine.get_tool_schemas(skill_names)

    # --- Build tool executor ---
    allowed_tools = agent_spec["spec"].get("tools", None)
    executor = ToolExecutor(allowed_tools)
    # Register skill-provided tools
    skill_engine.register_skill_tools(skill_names, executor)

    # --- Prepare connector engine ---
    connector_engine = ConnectorEngine(store)
    connector_refs = agent_spec["spec"].get("connectors", [])

    # --- Build LLM router ---
    config_path = os.environ.get("AGENTIX_CONFIG", "config/watchdog.yaml")
    try:
        cfg = load_config(config_path)
    except Exception:
        cfg = {}
    llm = build_router(cfg)

    # --- Build context ---
    system_prompt = build_system_prompt(agent_spec, skill_instructions)
    messages = build_messages(envelope, agent_spec, store)

    # Collect all tool schemas (built-in tools + skill tools), deduplicated by name
    tool_names = agent_spec["spec"].get("tools", [])
    _seen: set[str] = set()
    tool_schemas: list[dict] = []
    for schema in executor.get_tool_schemas(tool_names) + skill_tools:
        if schema["name"] not in _seen:
            _seen.add(schema["name"])
            tool_schemas.append(schema)

    # LLM config from agent spec (spec.llm overrides watchdog defaults)
    agent_llm_cfg = agent_spec["spec"].get("llm", agent_spec["spec"].get("model", {}))
    agent_model = agent_llm_cfg.get("model_id") or agent_llm_cfg.get("model") or None
    agent_provider = agent_llm_cfg.get("provider") or None
    agent_temperature = agent_llm_cfg.get("temperature", 1.0)
    agent_max_tokens = agent_llm_cfg.get("max_tokens", 4096)
    agent_tags = agent_spec["spec"].get("tags", [])

    # --- Agentic loop ---
    store.audit("agent.started", envelope["id"], agent_id, envelope["caller"]["identity_id"])

    max_iterations = agent_spec["spec"].get("max_tool_calls", MAX_TOOL_ITERATIONS)
    final_text = ""
    iterations = 0

    import asyncio as _asyncio

    async def _agentic_loop():
        nonlocal final_text, iterations, messages

        # Load connectors (async: network calls to verify credentials)
        from agentix.agent_runtime.tool_executor import _TOOL_REGISTRY
        await connector_engine.load_for_agent(connector_refs, _TOOL_REGISTRY)
        for schema in connector_engine.tool_schemas():
            if schema["name"] not in _seen:
                _seen.add(schema["name"])
                tool_schemas.append(schema)

        try:
            while iterations < max_iterations:
                iterations += 1
                response = await llm.complete(
                    messages=messages,
                    system=system_prompt,
                    tools=tool_schemas or None,
                    tags=agent_tags,
                    model=agent_model,
                    provider=agent_provider,
                    temperature=agent_temperature,
                    max_tokens=agent_max_tokens,
                )

                if response.stop_reason in ("end_turn", "stop"):
                    final_text = response.content
                    break

                if response.stop_reason == "tool_use" and response.tool_calls:
                    # Append assistant message with the full content blocks (text + tool_use).
                    # Anthropic requires tool_use blocks to be present here so the subsequent
                    # tool_result blocks have a matching tool_use_id to reference.
                    raw_blocks = None
                    if isinstance(response.raw, dict):
                        raw_blocks = response.raw.get("blocks")
                    assistant_content = raw_blocks if raw_blocks else (response.content or "")
                    messages.append({"role": "assistant", "content": assistant_content})

                    # Execute each tool call
                    tool_results = []
                    for tc in response.tool_calls:
                        try:
                            result = executor.execute(tc.name, tc.input)
                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": tc.id,
                                "content": json.dumps(result) if not isinstance(result, str) else result,
                            })
                            store.audit("tool.called", envelope["id"], agent_id, detail={"tool": tc.name})
                        except Exception as exc:
                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": tc.id,
                                "content": f"Error: {exc}",
                                "is_error": True,
                            })
                            store.audit("tool.error", envelope["id"], agent_id, detail={"tool": tc.name, "error": str(exc)})

                    messages.append({"role": "user", "content": tool_results})
                    continue

                # Any other stop reason
                final_text = response.content
                break
        finally:
            await connector_engine.shutdown()

    _asyncio.run(_agentic_loop())

    if iterations >= max_iterations:
        logger.warning("Max tool iterations reached for agent=%s", agent_id)
        if not final_text:
            final_text = "I reached the maximum number of tool calls. Please try a more specific request."

    # --- Persist conversation turn ---
    scope = f"user:{envelope['caller']['identity_id']}"
    persist_turn(
        agent_id,
        scope,
        build_messages(envelope, agent_spec, store),
        final_text,
        store,
        ttl_sec=agent_spec["spec"].get("memory", {}).get("ttl_sec", 3600),
    )

    # --- Emit output ---
    route_output(envelope, final_text)
    store.audit("agent.completed", envelope["id"], agent_id, detail={"response_len": len(final_text)})
    logger.info("Agent finished: %s", agent_id)


def main() -> None:
    raw = os.environ.get("AGENTIX_TRIGGER")
    if not raw:
        logger.error("AGENTIX_TRIGGER environment variable not set")
        sys.exit(1)
    try:
        envelope = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error("Failed to parse AGENTIX_TRIGGER: %s", e)
        sys.exit(1)

    run(envelope)


if __name__ == "__main__":
    main()
