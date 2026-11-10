"""In-process watchdog. Lives on a thread so a blocked event loop cannot stall it.

If the scanner or risk heartbeat is stale, or the loop tick is stale, the
process exits so run.sh can relaunch it. Heartbeats are written by the agents
themselves; this module never talks to Telegram.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Callable

import db

log = logging.getLogger("desk")

# Scanner and risk must beat on a timer even when idle. Other agents may be
# quiet for long stretches when no coin has passed, so they are not liveness.
LIVENESS_AGENTS = ("scanner", "risk")
STALE_S = 120.0
GRACE_S = 60.0
POLL_S = 15.0
RESTART_CODE = 78

_loop_ts = 0.0
_lock = threading.Lock()
_thread: threading.Thread | None = None
_stop = threading.Event()


def touch_loop(when: float | None = None) -> None:
    global _loop_ts
    with _lock:
        _loop_ts = when if when is not None else time.time()


def loop_age(now: float | None = None) -> float:
    now = now if now is not None else time.time()
    with _lock:
        ts = _loop_ts
    if not ts:
        return now  # not yet touched: treat as very old unless still in grace
    return now - ts


def inspect(now: float | None = None, started_at: float | None = None) -> dict:
    """Pure check used by the thread and by tests. Does not exit."""
    now = now if now is not None else time.time()
    if started_at is not None and (now - started_at) < GRACE_S:
        return {"ok": True, "reasons": [], "ages": {}, "grace": True}
    ages: dict[str, float] = {"loop": loop_age(now)}
    reasons: list[str] = []
    if ages["loop"] >= STALE_S:
        reasons.append(f"loop tick stale {ages['loop']:.0f}s")
    for name in LIVENESS_AGENTS:
        try:
            row = db.q1("SELECT ts, status, detail FROM heartbeats WHERE name=?", (name,))
        except Exception as e:
            reasons.append(f"{name} heartbeat unreadable")
            ages[name] = -1
            log.warning("watchdog could not read %s heartbeat: %s", name, type(e).__name__)
            continue
        if not row or not row["ts"]:
            ages[name] = now
            reasons.append(f"{name} heartbeat missing")
            continue
        age = now - float(row["ts"])
        ages[name] = age
        if age >= STALE_S:
            reasons.append(f"{name} heartbeat stale {age:.0f}s")
    return {"ok": not reasons, "reasons": reasons, "ages": ages, "grace": False}


def _dump_stacks(reasons: list[str]) -> None:
    """Write all thread stacks for later diagnosis. Tracebacks have no locals, so no secrets."""
    try:
        import faulthandler

        import paths

        paths.LOG_DIR.mkdir(parents=True, exist_ok=True)
        stack_path = paths.LOG_DIR / "watchdog-stack.log"
        with stack_path.open("a") as f:
            f.write(f"\n======= {time.strftime('%Y-%m-%d %H:%M:%S')} reasons={reasons} =======\n")
            faulthandler.dump_traceback(file=f, all_threads=True)
    except Exception:
        log.exception("watchdog failed to dump stacks")


def restart(reasons: list[str], exit_fn: Callable[[int], None] | None = None) -> None:
    ages = inspect().get("ages") or {}
    log.error(
        "watchdog: self-restart (%s) ages=%s",
        "; ".join(reasons) or "stale",
        {k: round(v) for k, v in ages.items()},
    )
    if exit_fn is None:
        _dump_stacks(reasons)
    for h in logging.getLogger().handlers:
        try:
            h.flush()
        except Exception:
            pass
    fn = exit_fn or os._exit
    fn(RESTART_CODE)


def check_and_maybe_restart(
    now: float | None = None,
    started_at: float | None = None,
    exit_fn: Callable[[int], None] | None = None,
) -> dict:
    result = inspect(now=now, started_at=started_at)
    if not result["ok"]:
        restart(result["reasons"], exit_fn=exit_fn)
    return result


def _thread_main(started_at: float) -> None:
    # Sleep in slices so stop can be noticed quickly.
    deadline = started_at + GRACE_S
    while not _stop.is_set():
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        if _stop.wait(min(POLL_S, remaining)):
            return
    while not _stop.is_set():
        try:
            check_and_maybe_restart(started_at=started_at)
        except SystemExit:
            raise
        except Exception:
            log.exception("watchdog check failed")
        if _stop.wait(POLL_S):
            return


def start() -> None:
    global _thread
    _stop.clear()
    touch_loop()
    if _thread is not None and _thread.is_alive():
        return
    started_at = time.time()
    _thread = threading.Thread(target=_thread_main, args=(started_at,), name="watchdog", daemon=True)
    _thread.start()
    log.info("watchdog started stale=%.0fs grace=%.0fs", STALE_S, GRACE_S)


def stop() -> None:
    _stop.set()


async def ticker(stop_event) -> None:
    """Asyncio task: proves the event loop is still scheduling work."""
    import asyncio

    touch_loop()
    while not stop_event.is_set():
        touch_loop()
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            continue
