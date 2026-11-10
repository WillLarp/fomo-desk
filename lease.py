"""File-based leader lease so two desk instances do not both act.

The lease lives on disk and is locked with flock. It never uses sqlite, so a
stuck database cannot block failover. TTL is 5s; the holder renews every 1s.
"""

from __future__ import annotations

import fcntl
import logging
import os
import threading
import time
from pathlib import Path

import paths

log = logging.getLogger("desk")

LEASE_TTL_S = 5.0
RENEW_S = 1.0
INSTANCES = (0, 1)

_id = 0
_enabled = False
_stop = threading.Event()
_thread: threading.Thread | None = None
_held = False
_lock = threading.Lock()


def runtime_root() -> Path:
    return Path(os.environ.get("DESK_RUNTIME", "/dev/shm/fomo-desk"))


def lease_path() -> Path:
    return Path(os.environ.get("DESK_LEASE", str(runtime_root() / "leader.lease")))


def set_id(n: int) -> None:
    global _id
    _id = int(n)
    os.environ["DESK_INSTANCE"] = str(_id)
    # Per-instance liveness so the supervisor can watch each process.
    root = runtime_root() / str(_id)
    root.mkdir(parents=True, exist_ok=True)
    paths.LIVENESS_PATH = root / "liveness"
    paths.PID_FILE = root / "desk.pid"


def parse_instance(argv: list[str] | None = None) -> int:
    import sys

    args = list(sys.argv[1:] if argv is None else argv)
    inst = os.environ.get("DESK_INSTANCE", "0")
    if "--instance" in args:
        i = args.index("--instance")
        if i + 1 < len(args):
            inst = args[i + 1]
    set_id(int(inst))
    return _id


def instance_id() -> int:
    return _id


def enabled() -> bool:
    return _enabled


def _read_unlocked(fh) -> tuple[int | None, float]:
    try:
        fh.seek(0)
        raw = fh.read()
        if not raw:
            return None, 0.0
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        holder = int(lines[0])
        expires = float(lines[1]) if len(lines) > 1 else 0.0
        return holder, expires
    except Exception:
        return None, 0.0


def _write_unlocked(fh, holder: int, expires: float, pid: int | None = None) -> None:
    fh.seek(0)
    fh.truncate()
    fh.write(f"{holder}\n{expires:.3f}\n{pid if pid is not None else os.getpid()}\n")
    fh.flush()
    os.fsync(fh.fileno())


def _open() -> "object":
    path = lease_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # a+ so the file exists and is readable/writable
    return path.open("a+", encoding="utf-8")


def holder(now: float | None = None) -> int | None:
    now = time.time() if now is None else now
    try:
        with _open() as fh:
            fcntl.flock(fh, fcntl.LOCK_SH)
            hid, expires = _read_unlocked(fh)
            if hid is None or expires <= now:
                return None
            return hid
    except Exception:
        return None


def is_leader() -> bool:
    # Single-process tests and a lone manual start act as leader.
    if not _enabled:
        return True
    with _lock:
        return _held


def try_acquire(now: float | None = None, ttl: float = LEASE_TTL_S) -> bool:
    global _held
    now = time.time() if now is None else now
    me = instance_id()
    try:
        with _open() as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            hid, expires = _read_unlocked(fh)
            if hid is not None and hid != me and expires > now:
                with _lock:
                    _held = False
                return False
            _write_unlocked(fh, me, now + ttl)
            with _lock:
                _held = True
            return True
    except Exception:
        log.exception("lease acquire failed")
        return False


def renew(now: float | None = None, ttl: float = LEASE_TTL_S) -> bool:
    global _held
    now = time.time() if now is None else now
    me = instance_id()
    try:
        with _open() as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            hid, expires = _read_unlocked(fh)
            if hid != me or expires <= now:
                # Lost it or it expired — try to take it.
                if hid is not None and hid != me and expires > now:
                    with _lock:
                        _held = False
                    return False
                _write_unlocked(fh, me, now + ttl)
                with _lock:
                    _held = True
                return True
            _write_unlocked(fh, me, now + ttl)
            with _lock:
                _held = True
            return True
    except Exception:
        log.exception("lease renew failed")
        return False


def release() -> None:
    global _held
    me = instance_id()
    try:
        with _open() as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            hid, _expires = _read_unlocked(fh)
            if hid == me:
                _write_unlocked(fh, me, 0.0)
    except Exception:
        pass
    with _lock:
        _held = False


def _thread_main() -> None:
    was = False
    while not _stop.is_set():
        now = time.time()
        ok = renew(now=now) if is_leader() else try_acquire(now=now)
        if ok and not was:
            log.info("lease acquired instance=%s", instance_id())
        elif was and not ok:
            log.warning("lease lost instance=%s", instance_id())
        was = ok
        if _stop.wait(RENEW_S):
            return


def tick() -> bool:
    """Renew or acquire from the event loop. A stuck loop cannot tick, so the
    lease expires and the standby takes over even if other threads still run."""
    if not _enabled:
        return True
    was = is_leader()
    ok = renew() if was else try_acquire()
    if ok and not was:
        log.info("lease acquired instance=%s", instance_id())
    elif was and not ok:
        log.warning("lease lost instance=%s", instance_id())
    return ok


def start() -> None:
    """Enable the lease and take it if it is free. Renewal happens in tick()."""
    global _enabled, _thread
    _enabled = True
    _stop.clear()
    try_acquire()
    # No renewer thread: a blocked event loop must not keep the lease alive.


def stop() -> None:
    _stop.set()
    # Releasing immediately lets the standby take over on a planned stop.
    release()
