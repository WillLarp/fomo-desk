"""Watchdog liveness checks, and mid-night restart must not reset the night."""

from __future__ import annotations

import time

import pytest

import db
import paths
import watchdog


def _fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DB_PATH", tmp_path / "watchdog.sqlite")
    monkeypatch.setattr(paths, "LIVENESS_PATH", tmp_path / "liveness")
    if hasattr(db._local, "con"):
        try:
            db._local.con.close()
        except Exception:
            pass
        del db._local.con
    db.init()


def test_inspect_fresh_heartbeats(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    now = time.time()
    db.beat("scanner", "running", "0 waiting")
    db.beat("risk", "running", "idle")
    watchdog.touch_loop(now)
    result = watchdog.inspect(now=now)
    assert result["ok"] is True
    assert result["reasons"] == []


def test_inspect_stale_scanner_and_risk(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    now = time.time()
    db.beat("scanner", "running", "0 waiting")
    db.beat("risk", "running", "idle")
    db.execute("UPDATE heartbeats SET ts=?", (now - 200,))
    watchdog.touch_loop(now)  # loop is fine; heartbeats are not
    result = watchdog.inspect(now=now)
    assert result["ok"] is False
    text = " ".join(result["reasons"])
    assert "scanner" in text and "risk" in text


def test_inspect_stale_loop_tick(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    now = time.time()
    db.beat("scanner", "running", "0 waiting")
    db.beat("risk", "running", "idle")
    watchdog.touch_loop(now - 200)
    result = watchdog.inspect(now=now)
    assert result["ok"] is False
    assert any("loop" in r for r in result["reasons"])


def test_idle_auditor_is_not_liveness(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    now = time.time()
    db.beat("scanner", "running", "0 waiting")
    db.beat("risk", "running", "idle")
    db.beat("auditor", "running", "idle")
    db.execute("UPDATE heartbeats SET ts=? WHERE name='auditor'", (now - 400,))
    watchdog.touch_loop(now)
    result = watchdog.inspect(now=now)
    assert result["ok"] is True


def test_grace_period_skips_restart(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    now = time.time()
    result = watchdog.inspect(now=now, started_at=now - 5)
    assert result["ok"] is True
    assert result["grace"] is True


def test_check_calls_exit_on_stale(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    now = time.time()
    db.beat("scanner", "running", "x")
    db.beat("risk", "running", "x")
    db.execute("UPDATE heartbeats SET ts=?", (now - 200,))
    watchdog.touch_loop(now - 200)
    called = []

    def exit_fn(code):
        called.append(code)
        raise SystemExit(code)

    with pytest.raises(SystemExit) as exc:
        watchdog.check_and_maybe_restart(now=now, started_at=now - 1000, exit_fn=exit_fn)
    assert exc.value.code == watchdog.RESTART_CODE
    assert called == [watchdog.RESTART_CODE]


def test_start_night_does_not_reset_open_night(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    first = db.start_night("PAPER")
    started = db.current_night()["started_at"]
    time.sleep(0.02)
    second = db.start_night("PAPER")
    night = db.current_night()
    assert first == second
    assert night["id"] == first
    assert night["started_at"] == started
    assert night["ended_at"] is None
    assert night["mode"] == "PAPER"


def test_touch_loop_writes_liveness(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    now = time.time()
    watchdog.touch_loop(now)
    path = tmp_path / "liveness"
    assert path.is_file()
    assert abs(float(path.read_text()) - now) < 0.01
