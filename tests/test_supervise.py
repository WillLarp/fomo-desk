"""File supervisor kills a stale instance after grace, without touching sqlite."""

from __future__ import annotations

import time

import supervise


def _prep(tmp_path, monkeypatch, n=0, started_ago=30, live_ago=None, scanner_ago=None, risk_ago=None):
    monkeypatch.setattr(supervise, "ROOT", tmp_path)
    runtime = tmp_path / "run"
    monkeypatch.setattr(supervise, "RUNTIME", runtime)
    d = runtime / str(n)
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


def test_health_report_leader_ok(tmp_path, monkeypatch):
    import os
    now = _prep(tmp_path, monkeypatch, n=0, started_ago=30, live_ago=1, scanner_ago=1, risk_ago=1)
    _prep(tmp_path, monkeypatch, n=1, started_ago=30, live_ago=1, scanner_ago=1, risk_ago=1)
    runtime = tmp_path / "run"
    lease = runtime / "leader.lease"
    lease.write_text(f"0\n{now + 5:.3f}\n1\n", encoding="utf-8")
    # fake pids that is_main will reject — mark down. Instead stub is_main and read_pid.
    monkeypatch.setattr(supervise, "is_main", lambda pid: pid in (11, 22))
    monkeypatch.setattr(supervise, "read_pid", lambda n: 11 if n == 0 else 22)
    code, text = supervise.health_report(now=now)
    assert code == 0
    assert text.startswith("ok leader=0\n")
    assert "instance 0: leader pid=11 loop=1s scanner=1s risk=1s ok" in text
    assert "instance 1: standby pid=22 loop=1s scanner=1s risk=1s ok" in text
    assert "scanner:0" not in text


def test_health_report_stale_leader(tmp_path, monkeypatch):
    now = _prep(tmp_path, monkeypatch, n=0, started_ago=30, live_ago=200, scanner_ago=1, risk_ago=1)
    runtime = tmp_path / "run"
    (runtime / "leader.lease").write_text(f"0\n{now + 5:.3f}\n1\n", encoding="utf-8")
    monkeypatch.setattr(supervise, "is_main", lambda pid: pid == 11)
    monkeypatch.setattr(supervise, "read_pid", lambda n: 11 if n == 0 else None)
    code, text = supervise.health_report(now=now)
    assert code == 2
    assert text.startswith("stale leader=0\n")
    assert "instance 0: leader pid=11" in text and "STALE" in text
    assert "instance 1: down" in text
