"""
Context Compactor — keeps the agentic loop's message list within a token budget.

Why this matters
----------------
Each LLM iteration sends the FULL message history (system + all prior turns).
When an agent reads multiple large files or fetches big API responses, tool_result
content accumulates quickly and can push the context past the API rate limit
(30 k tokens/min on the free tier) or the model's context window.

Two strategies (configured per-agent in spec.context):

  truncate   — Fast, no extra LLM call.  Older tool_result blocks whose content
               exceeds max_tool_result_chars are truncated in-place.  The most
               recent `keep_recent_turns` assistant+user pairs are always kept
               intact.  If the budget is still exceeded after truncation, the
               oldest compactable messages are dropped entirely.

  summarize  — Slower, costs one cheap Haiku call.  After truncation, if the
               budget is still exceeded the "middle" portion of the conversation
               (everything outside the first user message and the last N turns)
               is summarised and replaced with a single assistant message.

Token estimation
----------------
We use `len(str(content)) // 4` as a fast approximation (1 token ≈ 4 chars).
This avoids the overhead of a real tokeniser and is accurate enough for budgeting.

Usage (called before every llm.complete() in the agentic loop)
--------------------------------------------------------------
    from agentix.agent_runtime.context_compactor import compact_messages

    messages = compact_messages(
        messages,
        token_budget=20_000,
        strategy="truncate",          # or "summarize"
        max_tool_result_chars=2_000,
        keep_recent_turns=4,
        llm=llm,                      # required only for strategy="summarize"
        system_prompt=system_prompt,  # required only for strategy="summarize"
    )
"""
from __future__ import annotations

import copy
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Chars truncated from a tool result are replaced with this suffix.
_TRUNCATION_MARKER = "... [truncated: {removed} chars removed by context compactor]"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compact_messages(
    messages: list[dict],
    *,
    token_budget: int = 20_000,
    strategy: str = "truncate",
    max_tool_result_chars: int = 2_000,
    keep_recent_turns: int = 4,
    llm: Any = None,
    system_prompt: str = "",
) -> list[dict]:
    """
    Return a (possibly compacted) copy of *messages* that fits within
    *token_budget* estimated tokens.

    The original list is never mutated.
    """
    estimated = _estimate_tokens(messages)
    if estimated <= token_budget:
        return messages

    logger.info(
        "Context compactor triggered: estimated=%d tokens budget=%d strategy=%s",
        estimated, token_budget, strategy,
    )

    # Always try truncation first — it's free.
    compacted = _compact_truncate(
        messages,
        token_budget=token_budget,
        max_tool_result_chars=max_tool_result_chars,
        keep_recent_turns=keep_recent_turns,
    )

    after_truncation = _estimate_tokens(compacted)
    logger.info("After truncation: estimated=%d tokens", after_truncation)

    if after_truncation <= token_budget:
        return compacted

    # If still over budget and summarize strategy is requested, summarize.
    if strategy == "summarize" and llm is not None:
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # We're inside an async context — create a task (fire-and-forget isn't
                # right here; use run_until_complete on a new loop in a thread instead).
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(
                        asyncio.run,
                        _compact_summarize(compacted, token_budget, keep_recent_turns, llm, system_prompt),
                    )
                    compacted = future.result(timeout=30)
            else:
                compacted = loop.run_until_complete(
                    _compact_summarize(compacted, token_budget, keep_recent_turns, llm, system_prompt)
                )
        except Exception as exc:
            logger.warning("Summarization compaction failed (%s) — keeping truncated context", exc)

    return compacted


# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------

def _estimate_tokens(messages: list[dict]) -> int:
    """Fast approximation: 1 token ≈ 4 chars."""
    total = 0
    for m in messages:
        content = m.get("content", "")
        if isinstance(content, str):
            total += len(content) // 4
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    # tool_result / tool_use blocks
                    block_content = block.get("content", "") or block.get("input", "")
                    if isinstance(block_content, str):
                        total += len(block_content) // 4
                    elif isinstance(block_content, (dict, list)):
                        total += len(json.dumps(block_content)) // 4
                    # name + type fields are small — ignore
    return total


# ---------------------------------------------------------------------------
# Strategy A: Truncation
# ---------------------------------------------------------------------------

def _compact_truncate(
    messages: list[dict],
    *,
    token_budget: int,
    max_tool_result_chars: int,
    keep_recent_turns: int,
) -> list[dict]:
    """
    Work on a deep copy.  Truncate large tool_result content in older messages,
    then drop oldest messages if still over budget.
    """
    msgs = copy.deepcopy(messages)

    # The "protected" tail: last keep_recent_turns pairs (user + assistant = 2 msgs each)
    protected_count = keep_recent_turns * 2
    compactable_end = max(0, len(msgs) - protected_count)

    # Pass 1 — truncate large tool_result content in compactable region
    for i in range(compactable_end):
        msg = msgs[i]
        content = msg.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    block_content = block.get("content", "")
                    if isinstance(block_content, str) and len(block_content) > max_tool_result_chars:
                        removed = len(block_content) - max_tool_result_chars
                        block["content"] = (
                            block_content[:max_tool_result_chars]
                            + _TRUNCATION_MARKER.format(removed=removed)
                        )
        elif isinstance(content, str) and len(content) > max_tool_result_chars * 2:
            # Large assistant text in old turns — also trim
            removed = len(content) - max_tool_result_chars * 2
            msgs[i]["content"] = (
                content[: max_tool_result_chars * 2]
                + _TRUNCATION_MARKER.format(removed=removed)
            )

    # Pass 2 — if still over budget, drop oldest compactable messages
    while _estimate_tokens(msgs) > token_budget and compactable_end > 0:
        dropped = msgs.pop(0)
        compactable_end -= 1
        logger.debug(
            "Dropped old message (role=%s) to stay within token budget",
            dropped.get("role", "?"),
        )

    return msgs


# ---------------------------------------------------------------------------
# Strategy B: Summarization (async)
# ---------------------------------------------------------------------------

async def _compact_summarize(
    messages: list[dict],
    token_budget: int,
    keep_recent_turns: int,
    llm: Any,
    system_prompt: str,
) -> list[dict]:
    """
    Summarise the middle section of *messages* using the cheapest available model.
    Returns a new list with the summarised section replaced by a single message.
    """
    protected_count = keep_recent_turns * 2

    if len(messages) <= protected_count + 1:
        # Not enough messages to summarise — nothing we can do
        return messages

    # Split: keep first user message, summarise middle, keep tail
    first = messages[:1]
    middle = messages[1: max(1, len(messages) - protected_count)]
    tail = messages[max(1, len(messages) - protected_count):]

    if not middle:
        return messages

    # Build a condensed text of the middle section for the summarisation prompt
    middle_text = _messages_to_text(middle)

    summarise_prompt = (
        "You are a conversation summariser. Below is a portion of a conversation "
        "between an AI agent and its tool results. Produce a concise summary (under 500 words) "
        "that preserves all key facts, decisions, tool outputs, and intermediate results "
        "needed to continue the task. Do not include filler or meta-commentary.\n\n"
        f"--- CONVERSATION EXCERPT ---\n{middle_text}\n--- END ---"
    )

    try:
        response = await llm.complete(
            messages=[{"role": "user", "content": summarise_prompt}],
            system="You summarise AI agent conversation excerpts accurately and concisely.",
            tools=None,
            model="claude-haiku-4-5-20251001",  # always use cheapest model for compaction
        )
        summary_text = f"[Compacted summary of earlier conversation]\n{response.content}"
    except Exception as exc:
        logger.warning("Summarisation LLM call failed: %s", exc)
        # Fall back: just keep truncated middle
        return messages

    summary_message = {"role": "assistant", "content": summary_text}
    compacted = first + [summary_message] + tail

    logger.info(
        "Summarization compaction: %d messages → %d (summary: %d chars)",
        len(messages), len(compacted), len(summary_text),
    )
    return compacted


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _messages_to_text(messages: list[dict]) -> str:
    """Convert a messages list to a readable text block for summarisation."""
    lines: list[str] = []
    for m in messages:
        role = m.get("role", "unknown").upper()
        content = m.get("content", "")
        if isinstance(content, str):
            lines.append(f"{role}: {content[:1000]}")
        elif isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type", "")
                if btype == "tool_use":
                    lines.append(
                        f"TOOL_CALL [{block.get('name')}]: "
                        + json.dumps(block.get("input", {}))[:300]
                    )
                elif btype == "tool_result":
                    result_content = block.get("content", "")
                    if isinstance(result_content, str):
                        lines.append(f"TOOL_RESULT: {result_content[:500]}")
                elif btype == "text":
                    lines.append(f"{role}: {block.get('text', '')[:500]}")
    return "\n".join(lines)
