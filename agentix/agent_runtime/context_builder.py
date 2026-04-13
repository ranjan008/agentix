"""
Context Builder — assembles the initial context passed to the LLM.
Injects system prompt, conversation history, and skill instructions.

System prompt resolution (handled by loader.py before we are called):
  spec.system_prompt  — already a plain string after loader resolves
                        system_prompt_file / prompt_sections references.

Skill instructions appended after the agent's own system prompt so they
don't override it but do extend it — the same pattern used by CLAUDE.md
and Anthropic's internal skill context files.
"""
from __future__ import annotations

from agentix.storage.state_store import StateStore


_BASE_SYSTEM = (
    "You are {agent_name}, an AI agent running on the Agentix platform.\n"
    "You have access to tools and skills listed below.\n"
    "Always reason step-by-step before calling a tool.\n"
    "After completing the task, provide a clear, concise response."
)


def build_system_prompt(agent_spec: dict, skill_instructions: list[str]) -> str:
    """
    Assemble the final system prompt sent to the LLM.

    Priority (highest → lowest):
      1. spec.system_prompt  — set by the agent author (may come from a .md file)
      2. spec.instructions   — legacy key, still supported
      3. _BASE_SYSTEM        — minimal fallback when neither is provided

    Skill instructions are always appended after the agent prompt so that
    built-in and community skills can inject their own context sections
    (tool descriptions, safety notes, output format hints) without the
    agent author having to copy-paste them manually.
    """
    name = agent_spec["metadata"]["name"]
    spec = agent_spec.get("spec", {})

    # Use the richest prompt available
    agent_prompt = (
        spec.get("system_prompt")
        or spec.get("instructions")
        or _BASE_SYSTEM.format(agent_name=name)
    )

    parts = [agent_prompt.strip()]

    # Append skill context sections (skill.md equivalent)
    for instruction in skill_instructions:
        if instruction and instruction.strip():
            parts.append(instruction.strip())

    return "\n\n---\n\n".join(parts)


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

    # New user message — include context metadata if present
    text = envelope["payload"]["text"]
    context = envelope["payload"].get("context", {})
    if context:
        import json as _json
        text = f"{text}\n\n[Context: {_json.dumps(context)}]"
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
