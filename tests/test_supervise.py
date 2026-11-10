"""File supervisor kills a stale instance after grace, without touching sqlite."""

from __future__ import annotations

import time

import supervise


def _prep(tmp_path, monkeypatch, n=0, started_ago=30, live_ago=None, scanner_ago=None, risk_ago=None):
    monkeypatch.setattr(supervise, "ROOT", tmp_path)
    d = tmp_path / "data" / "i" / str(n)
    d.mkdir(parents=True, exist_ok=True)
    now = time.time()
    (d / "started").write_text(str(now - started_ago), encoding="utf-8")
    if live_ago is not None:
        p = d / "liveness"
        p.write_text("1\n", encoding="utf-8")
        os_utime = __import__("os").utime
        os_utime(p, (now - live_ago, now - live_ago))
    for name, ago in (("scanner", scanner_ago), ("risk", risk_ago)):
        if ago is None:
            continue
        p = d / f"{name}.hb"
        p.write_text("1\n", encoding="utf-8")
        __import__("os").utime(p, (now - ago, now - ago))
    return now


def test_fresh_heartbeats_are_ok(tmp_path, monkeypatch):
    now = _prep(tmp_path, monkeypatch, started_ago=30, live_ago=1, scanner_ago=1, risk_ago=1)
    assert supervise.stale_reason(0, now) is None


def test_grace_skips_stale(tmp_path, monkeypatch):
    now = _prep(tmp_path, monkeypatch, started_ago=5, live_ago=30, scanner_ago=30, risk_ago=30)
    assert supervise.stale_reason(0, now) is None


def test_stale_liveness_after_grace(tmp_path, monkeypatch):
    now = _prep(tmp_path, monkeypatch, started_ago=30, live_ago=20, scanner_ago=1, risk_ago=1)
    reason = supervise.stale_reason(0, now)
    assert reason and "liveness stale" in reason


def test_stale_risk_after_grace(tmp_path, monkeypatch):
    now = _prep(tmp_path, monkeypatch, started_ago=30, live_ago=1, scanner_ago=1, risk_ago=20)
    reason = supervise.stale_reason(0, now)
    assert reason and "risk heartbeat stale" in reason


def test_missing_liveness_after_grace(tmp_path, monkeypatch):
    now = _prep(tmp_path, monkeypatch, started_ago=30, live_ago=None)
    assert supervise.stale_reason(0, now) == "liveness file missing"
