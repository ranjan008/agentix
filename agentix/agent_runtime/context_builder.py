"""
Context Builder — assembles the initial context passed to the LLM.
Injects system prompt, conversation history, and skill instructions.
"""
from __future__ import annotations

from agentix.storage.state_store import StateStore


_BASE_SYSTEM = """You are {agent_name}, an AI agent running on the Agentix platform.
You have access to tools and skills listed below.
Always reason step-by-step before calling a tool.
After completing the task, provide a clear, concise response.
"""


def build_system_prompt(agent_spec: dict, skill_instructions: list[str]) -> str:
    name = agent_spec["metadata"]["name"]
    base = _BASE_SYSTEM.format(agent_name=name)

    if agent_spec["spec"].get("instructions"):
        base += f"\n{agent_spec['spec']['instructions']}\n"

    for instruction in skill_instructions:
        if instruction:
            base += f"\n---\n{instruction}\n"

    return base.strip()


def build_messages(
    envelope: dict,
    agent_spec: dict,
    store: StateStore,
) -> list[dict]:
    """
    Build the messages list for the LLM call.
    Includes short-term history from state store + the new user message.
    """
    agent_id = agent_spec["metadata"]["name"]
    scope = f"user:{envelope['caller']['identity_id']}"
    history_key = "conversation_history"

    # Load previous turns from state store
    history: list[dict] = store.get_state(agent_id, scope, history_key) or []

    # New user message
    text = envelope["payload"]["text"]
    user_message = {"role": "user", "content": text}

    messages = history + [user_message]

    # Trim to last N turns (configurable via spec)
    max_turns = agent_spec["spec"].get("memory", {}).get("max_history_turns", 10)
    if len(messages) > max_turns * 2:
        messages = messages[-(max_turns * 2):]

    return messages


def persist_turn(
    agent_id: str,
    scope: str,
    messages: list[dict],
    assistant_reply: str,
    store: StateStore,
    ttl_sec: int = 3600,
) -> None:
    """Append assistant reply and save conversation history."""
    updated = messages + [{"role": "assistant", "content": assistant_reply}]
    store.set_state(agent_id, scope, "conversation_history", updated, ttl_sec=ttl_sec)
