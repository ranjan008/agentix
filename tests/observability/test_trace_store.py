"""
Tests for TraceStore — SQLite-backed observability trace/span store.
"""
from __future__ import annotations

import time

import pytest

from agentix.observability.trace_store import TraceStore


@pytest.fixture
def ts(tmp_db) -> TraceStore:
    return TraceStore(str(tmp_db))


# ---------------------------------------------------------------------------
# trace lifecycle
# ---------------------------------------------------------------------------

def test_start_trace_returns_id(ts):
    tid = ts.start_trace("my-agent", trigger_id="trig_001")
    assert tid.startswith("trace_")


def test_get_trace_after_start(ts):
    tid = ts.start_trace("agent-a", trigger_id="trig_x", tenant_id="acme")
    trace = ts.get_trace(tid)
    assert trace is not None
    assert trace["agent_id"] == "agent-a"
    assert trace["trigger_id"] == "trig_x"
    assert trace["tenant_id"] == "acme"
    assert trace["status"] == "running"


def test_finish_trace_updates_status(ts):
    tid = ts.start_trace("agent-b")
    ts.finish_trace(tid, status="done", total_tokens=500, total_cost_usd=0.001)
    trace = ts.get_trace(tid)
    assert trace["status"] == "done"
    assert trace["total_tokens"] == 500
    assert trace["finished_at"] is not None


def test_finish_trace_error(ts):
    tid = ts.start_trace("agent-c")
    ts.finish_trace(tid, status="failed", error="LLM timeout")
    trace = ts.get_trace(tid)
    assert trace["status"] == "failed"
    assert "timeout" in trace["error"]


def test_get_trace_not_found(ts):
    assert ts.get_trace("trace_nonexistent") is None


# ---------------------------------------------------------------------------
# span lifecycle
# ---------------------------------------------------------------------------

def test_start_and_finish_span(ts):
    tid = ts.start_trace("agent-d")
    span_id = ts.start_span(tid, "llm.call", input={"messages_count": 3})
    ts.finish_span(span_id, output={"stop_reason": "end_turn"}, attributes={"model": "claude-haiku"})

    spans = ts.get_spans(tid)
    assert len(spans) == 1
    s = spans[0]
    assert s["name"] == "llm.call"
    assert s["status"] == "ok"
    assert s["duration_ms"] is not None
    assert s["duration_ms"] >= 0
    assert s["attributes"]["model"] == "claude-haiku"


def test_span_error_status(ts):
    tid = ts.start_trace("agent-e")
    sid = ts.start_span(tid, "tool.call", input={"name": "web_search"})
    ts.finish_span(sid, status="error", error="Network timeout", attributes={"tool_name": "web_search"})

    spans = ts.get_spans(tid)
    assert spans[0]["status"] == "error"
    assert "timeout" in spans[0]["error"]


def test_span_parent_child_relationship(ts):
    tid = ts.start_trace("agent-f")
    parent_id = ts.start_span(tid, "agent.run")
    child_id = ts.start_span(tid, "llm.call", parent_span_id=parent_id)
    ts.finish_span(child_id)
    ts.finish_span(parent_id)

    spans = ts.get_spans(tid)
    assert len(spans) == 2
    child = next(s for s in spans if s["id"] == child_id)
    assert child["parent_span_id"] == parent_id


def test_span_input_output_parsed_as_dict(ts):
    tid = ts.start_trace("agent-g")
    sid = ts.start_span(tid, "llm.call", input={"key": "value"})
    ts.finish_span(sid, output={"result": 42})

    spans = ts.get_spans(tid)
    s = spans[0]
    assert isinstance(s["input"], dict)
    assert s["input"]["key"] == "value"
    assert isinstance(s["output"], dict)
    assert s["output"]["result"] == 42


def test_finish_span_nonexistent_is_noop(ts):
    ts.finish_span("span_nonexistent")  # should not raise


# ---------------------------------------------------------------------------
# list_traces filtering
# ---------------------------------------------------------------------------

def test_list_traces_returns_all(ts):
    ts.start_trace("agent-a")
    ts.start_trace("agent-b")
    ts.start_trace("agent-a")
    traces = ts.list_traces()
    assert len(traces) == 3


def test_list_traces_filter_by_agent(ts):
    ts.start_trace("agent-a")
    ts.start_trace("agent-b")
    ts.start_trace("agent-a")
    traces = ts.list_traces(agent_id="agent-a")
    assert len(traces) == 2
    assert all(t["agent_id"] == "agent-a" for t in traces)


def test_list_traces_filter_by_status(ts):
    t1 = ts.start_trace("agent-a")
    t2 = ts.start_trace("agent-a")
    ts.finish_trace(t1, status="done")
    ts.finish_trace(t2, status="failed")

    done = ts.list_traces(status="done")
    failed = ts.list_traces(status="failed")
    assert len(done) == 1
    assert len(failed) == 1


def test_list_traces_pagination(ts):
    for _ in range(5):
        ts.start_trace("agent-a")
    page1 = ts.list_traces(limit=2, offset=0)
    page2 = ts.list_traces(limit=2, offset=2)
    assert len(page1) == 2
    assert len(page2) == 2
    ids1 = {t["id"] for t in page1}
    ids2 = {t["id"] for t in page2}
    assert ids1.isdisjoint(ids2)


# ---------------------------------------------------------------------------
# get_trace_with_spans
# ---------------------------------------------------------------------------

def test_get_trace_with_spans(ts):
    tid = ts.start_trace("agent-h")
    ts.start_span(tid, "llm.call")
    ts.start_span(tid, "tool.call")

    result = ts.get_trace_with_spans(tid)
    assert result is not None
    assert "spans" in result
    assert len(result["spans"]) == 2


def test_get_trace_with_spans_not_found(ts):
    assert ts.get_trace_with_spans("trace_missing") is None


# ---------------------------------------------------------------------------
# get_trace_by_trigger
# ---------------------------------------------------------------------------

def test_get_trace_by_trigger(ts):
    tid = ts.start_trace("agent-i", trigger_id="trig_abc")
    ts.start_span(tid, "llm.call")

    result = ts.get_trace_by_trigger("trig_abc")
    assert result is not None
    assert result["id"] == tid
    assert len(result["spans"]) == 1


def test_get_trace_by_trigger_not_found(ts):
    assert ts.get_trace_by_trigger("trig_missing") is None


# ---------------------------------------------------------------------------
# delete + purge
# ---------------------------------------------------------------------------

def test_delete_trace_removes_spans(ts):
    tid = ts.start_trace("agent-j")
    ts.start_span(tid, "llm.call")
    ts.delete_trace(tid)

    assert ts.get_trace(tid) is None
    assert ts.get_spans(tid) == []


def test_purge_before_removes_old_traces(ts):
    t1 = ts.start_trace("agent-k")
    t2 = ts.start_trace("agent-k")

    cutoff = time.time() + 1
    removed = ts.purge_before(cutoff)
    assert removed == 2
    assert ts.get_trace(t1) is None
    assert ts.get_trace(t2) is None


def test_purge_before_keeps_new_traces(ts):
    cutoff = time.time() - 1
    ts.start_trace("agent-l")
    removed = ts.purge_before(cutoff)
    assert removed == 0
    assert len(ts.list_traces()) == 1


def test_purge_before_tenant_scoped_leaves_other_tenants_alone(ts):
    """A pooled store serves many tenants with different plans/retention —
    purge_before(cutoff, tenant_id=...) must only ever touch that one
    tenant's rows, never another tenant's, even with an identical cutoff
    that would otherwise remove both."""
    old_a = ts.start_trace("agent-a", tenant_id="tenant-a")
    old_b = ts.start_trace("agent-b", tenant_id="tenant-b")
    cutoff = time.time() + 1

    removed = ts.purge_before(cutoff, tenant_id="tenant-a")

    assert removed == 1
    assert ts.get_trace(old_a) is None
    assert ts.get_trace(old_b) is not None  # tenant-b untouched


def test_purge_before_tenant_scoped_ignores_unmatched_tenant(ts):
    ts.start_trace("agent-a", tenant_id="tenant-a")
    cutoff = time.time() + 1

    removed = ts.purge_before(cutoff, tenant_id="tenant-does-not-exist")

    assert removed == 0
    assert len(ts.list_traces()) == 1


def test_list_traces_since_ts_excludes_older_traces(ts):
    ts.start_trace("agent-a")  # started before the cutoff below
    time.sleep(0.01)
    cutoff = time.time()
    time.sleep(0.01)
    ts.start_trace("agent-a")  # started after

    visible = ts.list_traces(since_ts=cutoff)

    assert len(visible) == 1


def test_list_traces_since_ts_none_is_unfiltered(ts):
    ts.start_trace("agent-a")
    ts.start_trace("agent-a")
    assert len(ts.list_traces(since_ts=None)) == 2


# ---------------------------------------------------------------------------
# count_traces
# ---------------------------------------------------------------------------

def test_count_traces_total(ts):
    ts.start_trace("agent-a")
    ts.start_trace("agent-b")
    ts.start_trace("agent-a")
    assert ts.count_traces() == 3


def test_count_traces_filter_by_agent(ts):
    ts.start_trace("agent-a")
    ts.start_trace("agent-b")
    ts.start_trace("agent-a")
    assert ts.count_traces(agent_id="agent-a") == 2
    assert ts.count_traces(agent_id="agent-b") == 1
    assert ts.count_traces(agent_id="agent-z") == 0


def test_count_traces_filter_by_status(ts):
    t1 = ts.start_trace("agent-a")
    t2 = ts.start_trace("agent-a")
    ts.start_trace("agent-a")   # stays "running"
    ts.finish_trace(t1, status="done")
    ts.finish_trace(t2, status="failed")
    assert ts.count_traces(status="done") == 1
    assert ts.count_traces(status="failed") == 1
    assert ts.count_traces(status="running") == 1


def test_count_traces_combined_filters(ts):
    t1 = ts.start_trace("agent-a")
    ts.start_trace("agent-b")
    ts.finish_trace(t1, status="done")
    assert ts.count_traces(agent_id="agent-a", status="done") == 1
    assert ts.count_traces(agent_id="agent-a", status="running") == 0
    assert ts.count_traces(agent_id="agent-b", status="done") == 0


def test_count_traces_empty_store(ts):
    assert ts.count_traces() == 0
