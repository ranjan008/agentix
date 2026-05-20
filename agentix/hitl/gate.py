"""
HITL Gate — decides whether a tool call should pause for human review.

Config (from agent spec.hitl):
  interrupt_before: [file_write, send_telegram]   # pause BEFORE these tools run
  interrupt_after:  [web_search]                  # pause AFTER these tools run
  timeout_sec: 3600                               # auto-expire checkpoint after this

Usage:
    gate = HITLGate.from_spec(agent_spec)
    decision = gate.check_before("file_write", {"path": "...", "content": "..."})
    # → HITLDecision.INTERRUPT or HITLDecision.PASS
"""
from __future__ import annotations

from enum import Enum
from typing import Any


class HITLDecision(str, Enum):
    PASS = "pass"
    INTERRUPT = "interrupt"


class HITLGate:
    """
    Evaluates interrupt rules against tool name and input.
    An empty / absent hitl config makes all checks return PASS.
    """

    def __init__(
        self,
        interrupt_before: list[str] | None = None,
        interrupt_after: list[str] | None = None,
        timeout_sec: float = 3600.0,
    ) -> None:
        self._before: set[str] = set(interrupt_before or [])
        self._after: set[str] = set(interrupt_after or [])
        self.timeout_sec = timeout_sec

    @classmethod
    def from_spec(cls, agent_spec: dict) -> "HITLGate":
        hitl_cfg = agent_spec.get("spec", {}).get("hitl") or {}
        return cls(
            interrupt_before=hitl_cfg.get("interrupt_before"),
            interrupt_after=hitl_cfg.get("interrupt_after"),
            timeout_sec=float(hitl_cfg.get("timeout_sec", 3600)),
        )

    @property
    def enabled(self) -> bool:
        return bool(self._before or self._after)

    def check_before(self, tool_name: str, tool_input: dict) -> HITLDecision:
        """Called before a tool executes."""
        if tool_name in self._before:
            return HITLDecision.INTERRUPT
        return HITLDecision.PASS

    def check_after(self, tool_name: str, tool_result: Any) -> HITLDecision:
        """Called after a tool executes."""
        if tool_name in self._after:
            return HITLDecision.INTERRUPT
        return HITLDecision.PASS
