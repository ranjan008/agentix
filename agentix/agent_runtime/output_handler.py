"""
Output Handler — formats and routes agent output back to the originating channel.
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


def extract_text(response) -> str:
    """Pull the text content from an Anthropic Message response."""
    for block in response.content:
        if block.type == "text":
            return block.text
    return ""


def route_output(envelope: dict, text: str) -> None:
    """
    Route the agent's reply back to the caller's channel.
    Phase 1: log to stdout (channels handle their own replies in later phases).
    """
    channel = envelope["channel"]
    trigger_id = envelope["id"]
    agent_id = envelope["agent_id"]

    output = {
        "trigger_id": trigger_id,
        "agent_id": agent_id,
        "channel": channel,
        "caller": envelope["caller"],
        "response": text,
    }

    # Write structured output to stdout — the watchdog (or a channel adapter)
    # can pick this up from the child process's stdout in future phases.
    print(json.dumps(output), flush=True)
    logger.info("Agent output: agent=%s trigger=%s len=%d chars", agent_id, trigger_id, len(text))
