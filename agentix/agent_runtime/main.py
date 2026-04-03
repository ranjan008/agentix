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
from pathlib import Path

from agentix.agent_runtime.context_builder import build_messages, build_system_prompt, persist_turn
from agentix.agent_runtime.llm_client import LLMClient
from agentix.agent_runtime.loader import find_agent_spec, load_agent_spec
from agentix.agent_runtime.output_handler import extract_text, route_output
from agentix.agent_runtime.tool_executor import ToolExecutor
from agentix.skills.engine import SkillEngine
from agentix.storage.state_store import StateStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("agentix.agent_runtime")

MAX_TOOL_ITERATIONS = 20


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

    # --- Build LLM client ---
    llm = LLMClient.from_spec(agent_spec)

    # --- Build context ---
    system_prompt = build_system_prompt(agent_spec, skill_instructions)
    messages = build_messages(envelope, agent_spec, store)

    # Collect all tool schemas (built-in tools + skill tools)
    tool_names = agent_spec["spec"].get("tools", [])
    tool_schemas = executor.get_tool_schemas(tool_names) + skill_tools

    # --- Agentic loop ---
    store.audit("agent.started", envelope["id"], agent_id, envelope["caller"]["identity_id"])

    final_text = ""
    iterations = 0

    while iterations < MAX_TOOL_ITERATIONS:
        iterations += 1
        response = llm.complete(system_prompt, messages, tools=tool_schemas or None)

        if response.stop_reason == "end_turn":
            final_text = extract_text(response)
            break

        if response.stop_reason == "tool_use":
            # Append assistant message with tool_use blocks
            messages.append({"role": "assistant", "content": response.content})

            # Execute each tool call
            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                try:
                    result = executor.execute(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result) if not isinstance(result, str) else result,
                    })
                    store.audit(
                        "tool.called",
                        envelope["id"],
                        agent_id,
                        detail={"tool": block.name},
                    )
                except Exception as exc:
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": f"Error: {exc}",
                        "is_error": True,
                    })
                    store.audit(
                        "tool.error",
                        envelope["id"],
                        agent_id,
                        detail={"tool": block.name, "error": str(exc)},
                    )

            messages.append({"role": "user", "content": tool_results})
            continue

        # Any other stop reason — extract what we have
        final_text = extract_text(response)
        break

    if iterations >= MAX_TOOL_ITERATIONS:
        logger.warning("Max tool iterations reached for agent=%s", agent_id)
        if not final_text:
            final_text = "I reached the maximum number of tool calls. Please try a more specific request."

    # --- Persist conversation turn ---
    scope = f"user:{envelope['caller']['identity_id']}"
    persist_turn(
        agent_id,
        scope,
        build_messages(envelope, agent_spec, store),  # original messages (pre-tool-loop)
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
