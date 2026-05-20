"""Tests for HITLGate."""
from __future__ import annotations


from agentix.hitl.gate import HITLDecision, HITLGate
from tests.factories import make_agent_spec


# --- from_spec ---

def test_from_spec_empty_hitl_config():
    spec = make_agent_spec()
    gate = HITLGate.from_spec(spec)
    assert not gate.enabled


def test_from_spec_with_interrupt_before():
    spec = make_agent_spec(hitl={"interrupt_before": ["file_write", "send_telegram"]})
    gate = HITLGate.from_spec(spec)
    assert gate.enabled


def test_from_spec_custom_timeout():
    spec = make_agent_spec(hitl={"interrupt_before": ["x"], "timeout_sec": 1800})
    gate = HITLGate.from_spec(spec)
    assert gate.timeout_sec == 1800.0


# --- check_before ---

def test_check_before_interrupt_when_tool_in_list():
    gate = HITLGate(interrupt_before=["file_write", "send_email"])
    assert gate.check_before("file_write", {}) == HITLDecision.INTERRUPT


def test_check_before_pass_when_tool_not_in_list():
    gate = HITLGate(interrupt_before=["file_write"])
    assert gate.check_before("web_search", {}) == HITLDecision.PASS


def test_check_before_empty_gate_always_passes():
    gate = HITLGate()
    assert gate.check_before("file_write", {}) == HITLDecision.PASS
    assert gate.check_before("send_telegram", {}) == HITLDecision.PASS


# --- check_after ---

def test_check_after_interrupt_when_tool_in_list():
    gate = HITLGate(interrupt_after=["web_search"])
    assert gate.check_after("web_search", {"result": "..."}) == HITLDecision.INTERRUPT


def test_check_after_pass_when_tool_not_in_list():
    gate = HITLGate(interrupt_after=["web_search"])
    assert gate.check_after("file_read", "data") == HITLDecision.PASS


def test_check_after_empty_gate_always_passes():
    gate = HITLGate()
    assert gate.check_after("web_search", {}) == HITLDecision.PASS


# --- enabled property ---

def test_enabled_false_with_no_lists():
    assert not HITLGate().enabled


def test_enabled_true_with_interrupt_before():
    assert HITLGate(interrupt_before=["x"]).enabled


def test_enabled_true_with_interrupt_after():
    assert HITLGate(interrupt_after=["y"]).enabled


def test_enabled_true_with_both():
    assert HITLGate(interrupt_before=["x"], interrupt_after=["y"]).enabled
