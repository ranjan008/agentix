"""
Agent Router — shared text-based routing for inbound channel adapters.

Supported prefixes (checked in order):
  1. Slash-command  /agent-name rest…   →  agent-name
  2. At-mention     @agent-name rest…   →  agent-name
  3. Fallback       default_agent_id

Both hyphens and underscores are normalised (discom_ot == discom-ot).

Usage:
    router = AgentRouter(default_agent_id="telegram-agent")
    agent_id = router.resolve("@trading-agent check NIFTY")
    # → "trading-agent"

    text = router.strip_prefix(raw_text)
    # → "check NIFTY"  (prefix removed so the agent sees a clean message)
"""
from __future__ import annotations

import re


class AgentRouter:
    """Resolves which agent should handle a plain-text message."""

    # Matches /agent-name or @agent-name at the start of a message
    _PREFIX_RE = re.compile(r"^[/@]([\w-]+)\s*", re.IGNORECASE)

    def __init__(self, default_agent_id: str = "") -> None:
        self.default_agent_id = default_agent_id

    def resolve(self, text: str) -> str:
        """Return the agent_id to route this message to."""
        candidate = self._extract_candidate(text)
        if candidate:
            return candidate
        return self.default_agent_id

    def strip_prefix(self, text: str) -> str:
        """Remove the /agent or @agent prefix from the message text."""
        m = self._PREFIX_RE.match(text.strip())
        if m:
            return text.strip()[m.end():]
        return text

    # ------------------------------------------------------------------

    def _extract_candidate(self, text: str) -> str:
        stripped = text.strip()
        if not stripped:
            return ""
        first_char = stripped[0]
        if first_char not in ("/", "@"):
            return ""
        m = self._PREFIX_RE.match(stripped)
        if not m:
            return ""
        word = m.group(1)
        # Normalise: underscores → hyphens (both styles accepted)
        return word.replace("_", "-")
