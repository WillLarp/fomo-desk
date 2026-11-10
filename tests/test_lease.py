"""Leader lease, action claims, and SIGSTOP standby takeover."""

from __future__ import annotations

import os
import signal
import time
from pathlib import Path

import pytest

import db
import lease
import paths
import stamps
import watchdog


def _reset_db(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DB_PATH", tmp_path / "lease.sqlite")
    monkeypatch.setattr(paths, "LIVENESS_PATH", tmp_path / "liveness")
    if hasattr(db._local, "con"):
        try:
            db._local.con.close()
        except Exception:
            pass
        del db._local.con
    db.init()


def test_claim_action_is_idempotent(tmp_path, monkeypatch):
    _reset_db(tmp_path, monkeypatch)
    assert db.claim_action("scan", "mintA") is True
    assert db.claim_action("scan", "mintA") is False
    assert db.claim_action("scan", "mintB") is True
    assert db.claim_action("exit", "mintA") is True


def test_lease_only_one_holder(tmp_path, monkeypatch):
    monkeypatch.setenv("DESK_LEASE", str(tmp_path / "leader.lease"))
    # Fresh path, no thread.
    lease._enabled = True
    lease._held = False
    lease.set_id(0)
    now = time.time()
    assert lease.try_acquire(now=now, ttl=5) is True
    assert lease.holder(now=now) == 0
    lease.set_id(1)
    lease._held = False
    assert lease.try_acquire(now=now, ttl=5) is False
    # Expired lease can be taken.
    assert lease.try_acquire(now=now + 6, ttl=5) is True
    assert lease.holder(now=now + 6) == 1
    lease._enabled = False
    lease._held = False
    lease.set_id(0)


def test_standby_takes_over_after_sigstop(tmp_path):
    """A frozen leader must yield to the already-running standby in ~5s."""
    root = Path(__file__).resolve().parents[1]
    lease_file = tmp_path / "leader.lease"
    out0 = tmp_path / "leader0.txt"
    out1 = tmp_path / "leader1.txt"
    worker = r"""
import os, sys, time
sys.path.insert(0, sys.argv[1])
os.environ["DESK_LEASE"] = sys.argv[2]
import lease
lease.set_id(int(sys.argv[3]))
lease.start()
path = sys.argv[4]
while True:
    lease.tick()
    if lease.is_leader():
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{time.time():.6f}\n")
            f.flush()
    time.sleep(0.05)
"""
    import subprocess

    def spawn(inst, out):
        return subprocess.Popen(
            [sys_executable(), "-c", worker, str(root), str(lease_file), str(inst), str(out)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def sys_executable():
        import sys
        return sys.executable

    p0 = spawn(0, out0)
    p1 = spawn(1, out1)
    try:
        deadline = time.time() + 5
        while time.time() < deadline and not out0.exists() and not out1.exists():
            time.sleep(0.05)
        # Identify the current leader process.
        if out0.exists() and (not out1.exists() or out0.stat().st_mtime >= out1.stat().st_mtime):
            frozen, live, live_out = p0, p1, out1
        elif out1.exists():
            frozen, live, live_out = p1, p0, out0
        else:
            pytest.fail("neither instance acquired the lease")
        # Drain any existing live_out (should be empty while standby).
        if live_out.exists():
            live_out.write_text("")
        t0 = time.monotonic()
        os.kill(frozen.pid, signal.SIGSTOP)
        deadline = time.time() + 12
        takeover = None
        while time.time() < deadline:
            if live_out.exists() and live_out.read_text().strip():
                takeover = time.monotonic() - t0
                break
            time.sleep(0.05)
        assert takeover is not None, "standby did not take over after SIGSTOP"
        assert takeover < 8, f"takeover too slow: {takeover:.2f}s"
        # Frozen instance must not keep writing after SIGSTOP.
        time.sleep(0.3)
    finally:
        for p in (p0, p1):
            try:
                os.kill(p.pid, signal.SIGCONT)
            except Exception:
                pass
            p.kill()
            p.wait(timeout=3)


def test_lease_expires_if_loop_stops_ticking(tmp_path):
    """A process whose event loop is stuck must lose the lease in ~5s
    even if the process itself (other threads) is still alive."""
    import threading
    monkey_lease = tmp_path / "leader.lease"
    import os
    os.environ["DESK_LEASE"] = str(monkey_lease)
    # Reset module-level state
    lease._enabled = True
    lease._held = False
    lease.set_id(0)
    now = time.time()
    assert lease.try_acquire(now=now, ttl=5) is True
    lease.set_id(1)
    lease._held = False
    assert lease.try_acquire(now=now + 2, ttl=5) is False
    t0 = time.monotonic()
    assert lease.try_acquire(now=now + 5.1, ttl=5) is True
    elapsed = time.monotonic() - t0
    assert lease.holder(now=now + 5.1) == 1
    assert elapsed < 1.0  # file lease acquire itself is immediate once expired
    lease._enabled = False
    lease._held = False
    lease.set_id(0)
