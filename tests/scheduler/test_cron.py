"""
Regression tests for Scheduler's cron-expression handling
(agentix/scheduler/engine.py::_cron_next).

croniter used to be a soft, undeclared dependency: when unavailable,
_cron_next() silently fell back to a flat 60-second interval for EVERY
cron expression, logging only a WARNING. Found live in production: a
schedule created with "0 18 * * *" (intended: once daily) instead fired
every 60 seconds for several minutes before being caught, because
croniter was never actually installed anywhere importing this package —
the fallback's log line was indistinguishable from routine operation at
a glance, so nothing caught it before real agent-run cost accrued.

croniter is now a real, declared dependency (pyproject.toml), and the
fallback is gone entirely — an unavailable croniter now raises instead of
silently changing what a schedule means.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest

from agentix.scheduler.engine import _cron_next


def test_cron_next_respects_the_real_expression_not_a_flat_interval() -> None:
    """The actual regression: "0 18 * * *" (once daily, 18:00 UTC) must
    compute a next-fire time roughly a day out — not 60 seconds out, which
    is what the old ImportError fallback silently returned for every
    expression regardless of what it actually said."""
    now = datetime(2026, 9, 3, 9, 50, 0, tzinfo=timezone.utc)
    next_fire = _cron_next("0 18 * * *", after=now.timestamp())
    next_dt = datetime.fromtimestamp(next_fire, tz=timezone.utc)

    assert next_dt.hour == 18 and next_dt.minute == 0
    # Same day, later today — nowhere near the old 60-second fallback.
    assert next_dt.date() == now.date()
    gap_seconds = next_fire - now.timestamp()
    assert gap_seconds > 3600, f"expected an hours-away next fire, got {gap_seconds}s"


def test_cron_next_second_call_is_not_60_seconds_later() -> None:
    """Directly reproduces what was actually observed live: computing
    "the next fire" twice in a row (as _fire_cron does once per firing)
    must never land exactly 60 seconds apart for a daily schedule — that
    specific gap is the fallback's fingerprint."""
    t1 = _cron_next("0 18 * * *", after=time.time())
    t2 = _cron_next("0 18 * * *", after=t1)
    gap = t2 - t1
    assert gap != pytest.approx(60.0, abs=1.0)
    assert gap == pytest.approx(86400.0, abs=1.0)  # one day later


def test_cron_next_raises_when_croniter_unavailable(monkeypatch) -> None:
    """The old behavior silently substituted a 60-second interval here.
    It must now raise, loudly, rather than quietly changing what the
    schedule does."""
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "croniter":
            raise ImportError("simulated: croniter not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(ImportError):
        _cron_next("0 18 * * *")
