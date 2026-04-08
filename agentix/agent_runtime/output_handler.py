"""
Output Handler — formats and routes agent output back to the originating channel.
"""
from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger(__name__)


def extract_text(response) -> str:
    """Pull the text content from an Anthropic Message response."""
    for block in response.content:
        if block.type == "text":
            return block.text
    return ""


def route_output(envelope: dict, text: str) -> None:
    """Route the agent's reply back to the caller's channel."""
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

    print(json.dumps(output), flush=True)
    logger.info("Agent output: agent=%s trigger=%s len=%d chars", agent_id, trigger_id, len(text))

    # Persist response in DB so the chat UI can poll for it
    try:
        from agentix.storage.state_store import StateStore
        db_path = os.environ.get("AGENTIX_DB_PATH", "data/agentix.db")
        StateStore(db_path).save_trigger_response(trigger_id, text)
    except Exception as exc:
        logger.warning("Could not persist trigger response: %s", exc)

    if channel == "telegram":
        _reply_telegram(envelope, text)


def _reply_telegram(envelope: dict, text: str) -> None:
    """Send reply back to the Telegram chat that triggered this agent."""
    import urllib.request
    import urllib.error

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        logger.warning("TELEGRAM_BOT_TOKEN not set — cannot reply to Telegram")
        return

    # chat_id is stored in payload.context by the TriggerEnvelope normaliser
    chat_id = (
        envelope.get("payload", {}).get("context", {}).get("chat_id")
    )
    if not chat_id:
        logger.warning("No chat_id in envelope — cannot reply to Telegram")
        return

    # Telegram has a 4096-char message limit
    MAX_LEN = 4096
    chunks = [text[i:i + MAX_LEN] for i in range(0, len(text), MAX_LEN)] if text else ["(no response)"]

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    for chunk in chunks:
        payload = json.dumps({"chat_id": chat_id, "text": chunk}).encode()
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=10)
        except urllib.error.URLError as exc:
            logger.error("Failed to send Telegram reply: %s", exc)
