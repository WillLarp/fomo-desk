"""Resume in-memory bus work from the db after a leader failover."""

from __future__ import annotations

import logging

import bus
import db
import lease

log = logging.getLogger("desk")

_queued: set[tuple[str, str]] = set()


def reset() -> None:
    _queued.clear()


async def catch_up() -> int:
    """Enqueue unfinished work for the new leader. Idempotent per process."""
    if not lease.is_leader():
        return 0
    start = db.night_start_ts()
    n = 0
    for r in db.q(
        "SELECT mint FROM scans WHERE verdict='pass' AND mint IS NOT NULL"
    ):
        mint = r["mint"]
        coin = db.coin(mint) or {}
        status = coin.get("status")
        first = coin.get("first_seen") or 0
        if first and first < start and status not in ("passed", "audited", "scored"):
            continue
        audit = db.latest_audit(mint)
        verdict = db.latest_verdict(mint)
        scan = db.latest_scan(mint) or {"mint": mint}
        if not audit and status in (None, "passed", "waiting"):
            key = ("audit", mint)
            if key not in _queued:
                await bus.auditor.put(scan)
                _queued.add(key)
                n += 1
            continue
        if audit and audit.get("passed") and not verdict:
            key = ("analyst", mint)
            if key not in _queued:
                await bus.analyst.put({"scan": scan, "audit": audit})
                _queued.add(key)
                n += 1
            continue
        if verdict and verdict.get("passed"):
            sig = db.q1(
                "SELECT 1 AS x FROM signals WHERE mint=? AND ts>=?",
                (mint, start),
            )
            if not sig:
                key = ("executor", mint)
                if key not in _queued:
                    await bus.executor.put(
                        {"scan": scan, "audit": audit or {}, "verdict": verdict}
                    )
                    _queued.add(key)
                    n += 1
    if n:
        log.info("pipeline catch-up queued %s items", n)
    return n
