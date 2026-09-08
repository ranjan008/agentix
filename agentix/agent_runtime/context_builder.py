"""
Context Builder — assembles the initial context passed to the LLM.
Injects system prompt, conversation history, and skill instructions.

System prompt resolution (handled by loader.py before we are called):
  spec.system_prompt  — already a plain string after loader resolves
                        system_prompt_file / prompt_sections references.

Skill instructions appended after the agent's own system prompt so they
don't override it but do extend it — the same pattern used by CLAUDE.md
and Anthropic's internal skill context files.

Durable memory (spec.memory.durable): a distinct thing from
conversation_history below, which is a rolling raw-transcript window
(default 10 turns, 1hr TTL) — useful within one extended session, but a
fact stated 11 turns ago is gone regardless of how much wall-clock time
has passed. Durable memory extracts small, standing facts (preferences,
identity, ongoing projects) into their OWN permanent per-identity store
(reusing StateStore's existing generic KV+TTL mechanism, ttl_sec=None —
no new storage engine), recalled on every future call for that identity
independent of the rolling window. Plan-gated upstream (Yantra's
builder-svc, before a spec with memory.durable=true is ever saved) — this
module just executes what's already in the spec.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from agentix.storage.state_store import StateStore

log = logging.getLogger(__name__)

DURABLE_MEMORY_KEY = "durable_facts"
MAX_DURABLE_FACTS = 50      # oldest evicted first — a standing cap, not a per-call one
MAX_NEW_FACTS_PER_TURN = 10  # sanity cap on a single extraction call's output


_BASE_SYSTEM = (
    "You are {agent_name}, an AI agent running on the Agentix platform.\n"
    "You have access to tools and skills listed below.\n"
    "Always reason step-by-step before calling a tool.\n"
    "After completing the task, provide a clear, concise response."
)


def build_system_prompt(
    agent_spec: dict,
    skill_instructions: list[str],
    durable_facts: list[dict] | None = None,
) -> str:
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

    durable_facts (optional — only ever non-empty for spec.memory.durable
    agents, see the module docstring): standing facts about the calling
    identity, recalled here regardless of the rolling conversation-history
    window's turn count — that's the actual point of the feature, so this
    section is appended unconditionally when facts exist, not trimmed
    alongside the rest of the prompt.
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

    if durable_facts:
        facts_text = "\n".join(f"- {f.get('text', '')}" for f in durable_facts if f.get("text"))
        if facts_text:
            parts.append(f"What you remember about this user from past conversations:\n{facts_text}")

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

    # Prepend sender identity so agents can do RBAC based on channel + username.
    # _identity is the raw identity dict set by TriggerEnvelope.to_dict().
    identity = envelope.get("_identity", {})
    channel = envelope.get("channel", "")
    if identity or channel:
        parts: list[str] = []
        if channel:
            parts.append(f"channel={channel}")
        uid = identity.get("username") or identity.get("user_id") or envelope.get("caller", {}).get("identity_id", "")
        if uid:
            # Prefix @ for Telegram/Slack usernames that don't already have it
            if channel in ("telegram", "slack") and uid and not str(uid).startswith("@") and not str(uid).isdigit():
                uid = f"@{uid}"
            parts.append(f"username={uid}")
        name = identity.get("first_name") or identity.get("name") or ""
        if name:
            parts.append(f"name={name}")
        if parts:
            text = f"[Sender: {', '.join(parts)}]\n{text}"

    context = envelope["payload"].get("context", {})

    # Inject upstream DAG step outputs so the agent sees prior step results
    upstream = context.get("upstream_outputs") if context else None
    if upstream:
        import json as _json
        lines = [f"  [{sid}]: {output}" for sid, output in upstream.items()]
        text = "[Pipeline context — outputs from upstream steps]\n" + "\n".join(lines) + f"\n\n{text}"

    # Append remaining context (excluding upstream_outputs, already injected above)
    remaining_ctx = {k: v for k, v in context.items() if k != "upstream_outputs"} if context else {}
    if remaining_ctx:
        import json as _json
        text = f"{text}\n\n[Context: {_json.dumps(remaining_ctx)}]"

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


def get_durable_facts(store: StateStore, agent_id: str, scope: str) -> list[dict]:
    """Read-side of durable memory — see the module docstring. A plain
    lookup against the same generic StateStore every other piece of agent
    state already uses; returns [] (not None) for an identity with none
    recorded yet, or for an agent that's never had memory.durable enabled."""
    return store.get_state(agent_id, scope, DURABLE_MEMORY_KEY) or []


async def maybe_extract_durable_memory(
    agent_spec: dict,
    agent_id: str,
    scope: str,
    user_text: str,
    assistant_text: str,
    llm: Any,
    store: StateStore,
) -> None:
    """Write-side of durable memory. A no-op (single dict lookup, no LLM
    call) unless spec.memory.durable is set — the entitlement gate already
    happened upstream, in builder-svc, before this could ever be true in a
    saved spec; this function trusts that and just executes it.

    Deliberately best-effort: any failure here (a malformed extraction
    response, a provider error) is logged and swallowed, never raised —
    this runs after the real response has already been computed, so a
    memory-extraction hiccup must never turn an otherwise-successful
    trigger into a failed one."""
    if not agent_spec.get("spec", {}).get("memory", {}).get("durable"):
        return

    try:
        existing = get_durable_facts(store, agent_id, scope)
        existing_text = "\n".join(f"- {f.get('text', '')}" for f in existing) or "(none yet)"

        response = await llm.complete(
            messages=[{
                "role": "user",
                "content": (
                    f"Existing durable facts already remembered about this user:\n{existing_text}\n\n"
                    f"Latest exchange:\nUser: {user_text}\nAssistant: {assistant_text}\n\n"
                    "Extract any NEW durable facts worth remembering long-term about this user "
                    "(stable preferences, identity, ongoing projects, constraints) from the latest "
                    "exchange only — not anything already listed above, and not routine "
                    "conversational content that has no standing value. Reply with ONLY a JSON "
                    'array of short fact strings, e.g. ["prefers formal tone", "timezone is PST"], '
                    "or [] if nothing new is worth remembering."
                ),
            }],
            max_tokens=300,
            temperature=0,
        )
        new_facts = _parse_fact_list(response.content)
        if not new_facts:
            return

        now = datetime.now(timezone.utc).isoformat()
        merged = existing + [{"text": f, "learned_at": now} for f in new_facts]
        merged = merged[-MAX_DURABLE_FACTS:]  # standing cap — oldest evicted first
        store.set_state(agent_id, scope, DURABLE_MEMORY_KEY, merged, ttl_sec=None)  # permanent
    except Exception:
        log.warning("Durable memory extraction failed for agent=%s scope=%s", agent_id, scope, exc_info=True)


def _parse_fact_list(text: str) -> list[str]:
    text = (text or "").strip()
    if text.startswith("```"):
        # Models sometimes wrap JSON in a code fence despite instructions
        # asking for ONLY the array — strip a leading ```/```json line and
        # a trailing ``` rather than failing the whole extraction over it.
        text = text.strip("`")
        if "\n" in text:
            text = text.split("\n", 1)[1]
    try:
        parsed = json.loads(text)
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(f).strip() for f in parsed if str(f).strip()][:MAX_NEW_FACTS_PER_TURN]
