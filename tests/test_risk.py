"""Acceptance test 4 (part): exit order, stop never loosens, no invented price."""

import config
from agents.risk import evaluate, tighten_stop


def setup_function():
    config._cache.clear()


def pos(**kw):
    base = {
        "id": 1,
        "entry_price": 1.0,
        "peak_price": 1.0,
        "stop_pct": 6,
        "stop_price": 0.94,
        "trail_active": 0,
        "opened_at": 0,
        "size_usd": 47.5,
    }
    base.update(kw)
    return base


def test_stop_fires_at_line():
    assert evaluate(pos(), 0.94) == "stop"
    assert evaluate(pos(), 0.939) == "stop"
    assert evaluate(pos(), 0.95) is None


def test_trail_arms_at_plus_20_and_never_turns_off():
    p = pos(peak_price=1.25, trail_active=0)
    # price still above entry*1.20 and drops 12% from peak: 1.25 * 0.88 = 1.1
    assert evaluate(p, 1.25) is None  # arms but no exit
    p2 = pos(peak_price=1.25, trail_active=1)
    assert evaluate(p2, 1.1) == "trail"


def test_timeout_dead_money():
    import time
    p = pos(opened_at=time.time() - 3 * 3600 - 10, peak_price=1.02)
    assert evaluate(p, 1.02) == "timeout"
    assert evaluate(p, 1.06) is None  # already +5%


def test_stop_does_not_loosen(monkeypatch):
    # tighten_stop writes to db; stub update
    import agents.risk as risk
    called = {}

    def fake_update(table, pid, data):
        called.update(data)

    monkeypatch.setattr(risk.db, "update", fake_update)
    config.set_value("risk.stop_pct", 8)  # wider, must be ignored
    p = tighten_stop(pos(stop_pct=6, entry_price=1.0, id=1))
    assert p["stop_pct"] == 6
    assert called == {}
    config.set_value("risk.stop_pct", 4)  # tighter, applies
    p = tighten_stop(pos(stop_pct=6, entry_price=1.0, id=1))
    assert p["stop_pct"] == 4
    assert abs(p["stop_price"] - 0.96) < 1e-9
