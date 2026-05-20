"""
Tests for CostLedger — per-agent/tenant token tracking, cost estimation,
quota management, and spend reporting.
"""
from __future__ import annotations

import pytest

from agentix.observability.cost_ledger import CostLedger, QuotaExceededError


@pytest.fixture
def ledger(tmp_db) -> CostLedger:
    return CostLedger(db_path=str(tmp_db))


# ---------------------------------------------------------------------------
# estimate_cost
# ---------------------------------------------------------------------------

def test_estimate_cost_known_model(ledger):
    # claude-haiku-4-5: $0.80/M input, $4.00/M output
    cost = ledger.estimate_cost("claude-haiku-4-5-20251001", input_tokens=1_000_000, output_tokens=1_000_000)
    assert abs(cost - 4.80) < 0.0001


def test_estimate_cost_unknown_model_falls_back_to_default(ledger):
    # "default": $3/M input, $15/M output
    cost = ledger.estimate_cost("unknown-model", input_tokens=1_000_000, output_tokens=0)
    assert abs(cost - 3.0) < 0.0001


def test_estimate_cost_zero_tokens(ledger):
    assert ledger.estimate_cost("claude-haiku-4-5-20251001", 0, 0) == 0.0


def test_estimate_cost_partial_million(ledger):
    # 500k input @ $0.80/M = $0.40, 0 output
    cost = ledger.estimate_cost("claude-haiku-4-5-20251001", input_tokens=500_000, output_tokens=0)
    assert abs(cost - 0.40) < 0.0001


# ---------------------------------------------------------------------------
# record
# ---------------------------------------------------------------------------

def test_record_returns_cost(ledger):
    cost = ledger.record(
        agent_id="my-agent",
        tenant_id="acme",
        model_id="claude-haiku-4-5-20251001",
        input_tokens=1000,
        output_tokens=500,
    )
    assert isinstance(cost, float)
    assert cost > 0


def test_record_shows_in_summary(ledger):
    ledger.record(
        agent_id="agent-a",
        tenant_id="acme",
        model_id="claude-haiku-4-5-20251001",
        input_tokens=100,
        output_tokens=50,
        tool_calls=2,
        trigger_id="trig_001",
    )
    summary = ledger.summary(agent_id="agent-a")
    assert summary["calls"] == 1
    assert summary["total_input_tokens"] == 100
    assert summary["total_output_tokens"] == 50
    assert summary["total_tool_calls"] == 2
    assert summary["total_cost_usd"] > 0


def test_record_multiple_runs_accumulate(ledger):
    for _ in range(3):
        ledger.record(
            agent_id="agent-b",
            tenant_id="acme",
            model_id="claude-haiku-4-5-20251001",
            input_tokens=1000,
            output_tokens=500,
        )
    summary = ledger.summary(agent_id="agent-b")
    assert summary["calls"] == 3
    assert summary["total_input_tokens"] == 3000
    assert summary["total_output_tokens"] == 1500


# ---------------------------------------------------------------------------
# summary — filtering
# ---------------------------------------------------------------------------

def test_summary_filter_by_tenant(ledger):
    ledger.record("agent-a", "acme", "claude-haiku-4-5-20251001", 100, 50)
    ledger.record("agent-b", "other-tenant", "claude-haiku-4-5-20251001", 200, 100)
    s = ledger.summary(tenant_id="acme")
    assert s["calls"] == 1
    assert s["total_input_tokens"] == 100


def test_summary_filter_by_agent(ledger):
    ledger.record("agent-x", "acme", "claude-haiku-4-5-20251001", 100, 50)
    ledger.record("agent-y", "acme", "claude-haiku-4-5-20251001", 200, 100)
    s = ledger.summary(agent_id="agent-x")
    assert s["calls"] == 1
    assert s["total_input_tokens"] == 100


def test_summary_empty_returns_zeros(ledger):
    s = ledger.summary()
    assert s["calls"] == 0
    assert s["total_cost_usd"] == 0


# ---------------------------------------------------------------------------
# Quota — hard limit enforcement
# ---------------------------------------------------------------------------

def test_hard_quota_raises_when_exceeded(ledger):
    ledger.set_quota("agent", "budget-agent", "total", hard_limit_usd=0.000001)
    with pytest.raises(QuotaExceededError, match="exceeded hard limit"):
        ledger.record(
            agent_id="budget-agent",
            tenant_id="acme",
            model_id="claude-haiku-4-5-20251001",
            input_tokens=10_000,
            output_tokens=5_000,
        )


def test_hard_quota_not_raised_below_limit(ledger):
    ledger.set_quota("agent", "rich-agent", "total", hard_limit_usd=100.0)
    # tiny usage — should not raise
    cost = ledger.record(
        agent_id="rich-agent",
        tenant_id="acme",
        model_id="claude-haiku-4-5-20251001",
        input_tokens=10,
        output_tokens=5,
    )
    assert cost > 0


def test_tenant_hard_quota_raises(ledger):
    ledger.set_quota("tenant", "broke-tenant", "monthly", hard_limit_usd=0.000001)
    with pytest.raises(QuotaExceededError):
        ledger.record(
            agent_id="any-agent",
            tenant_id="broke-tenant",
            model_id="claude-haiku-4-5-20251001",
            input_tokens=10_000,
            output_tokens=5_000,
        )


# ---------------------------------------------------------------------------
# Quota — soft limit (alert callback)
# ---------------------------------------------------------------------------

def test_soft_limit_triggers_callback(ledger):
    alerts = []
    ledger.alert_callback = lambda scope, msg: alerts.append((scope, msg))
    ledger.set_quota("agent", "alert-agent", "total", soft_limit_usd=0.000001)
    ledger.record(
        agent_id="alert-agent",
        tenant_id="acme",
        model_id="claude-haiku-4-5-20251001",
        input_tokens=10_000,
        output_tokens=5_000,
    )
    assert len(alerts) == 1
    assert "alert-agent" in alerts[0][0]


# ---------------------------------------------------------------------------
# get_spend
# ---------------------------------------------------------------------------

def test_get_spend_total(ledger):
    ledger.record("agent-s", "acme", "claude-haiku-4-5-20251001", 1_000_000, 0)
    spend = ledger.get_spend("agent", "agent-s", "total")
    assert spend > 0


def test_get_spend_zero_for_unknown(ledger):
    assert ledger.get_spend("agent", "never-ran", "total") == 0.0


# ---------------------------------------------------------------------------
# top_agents_by_cost
# ---------------------------------------------------------------------------

def test_top_agents_by_cost(ledger):
    ledger.record("cheap-agent", "acme", "claude-haiku-4-5-20251001", 100, 50)
    ledger.record("expensive-agent", "acme", "claude-haiku-4-5-20251001", 10_000, 5_000)
    top = ledger.top_agents_by_cost(tenant_id="acme")
    assert top[0]["agent_id"] == "expensive-agent"
    assert top[1]["agent_id"] == "cheap-agent"


def test_top_agents_by_cost_empty(ledger):
    assert ledger.top_agents_by_cost() == []


# ---------------------------------------------------------------------------
# set_quota — upsert behaviour
# ---------------------------------------------------------------------------

def test_set_quota_upsert_updates_limit(ledger):
    ledger.set_quota("agent", "updatable-agent", "total", hard_limit_usd=1.0)
    ledger.set_quota("agent", "updatable-agent", "total", hard_limit_usd=100.0)
    # Should not raise with updated higher limit
    ledger.record(
        agent_id="updatable-agent",
        tenant_id="acme",
        model_id="claude-haiku-4-5-20251001",
        input_tokens=100,
        output_tokens=50,
    )
