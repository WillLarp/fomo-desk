
"""Bought / Skip / Sold callbacks used by Telegram. No orders are placed."""

from __future__ import annotations

import db
from agents import executor as executor_mod


async def bought(signal_id: int) -> str:
    row = db.q1("SELECT * FROM signals WHERE id=?", (signal_id,))
    if not row:
        return "No such signal."
    sig = dict(row)
    if sig["status"] == "bought":
        return "Already marked bought."
    if sig["status"] not in ("sent",):
        return f"Signal is {sig['status']}."
    if not sig.get("price") or not sig.get("size_usd"):
        return "Signal has no price or size."
    coin = db.coin(sig["mint"]) or {}
    name = coin.get("name") or sig["mint"][:6]
    # LIVE fill at the signal price (as specified)
    pid = executor_mod.open_position(sig, name)
    db.upsert_coin(sig["mint"], status="open")
    executor_mod.card(state="filled", progress=100, name=name, lastSize=sig["size_usd"])
    return f"Bought {name} · ${sig['size_usd']:.2f} recorded. Risk is guarding it."


async def skip(signal_id: int) -> str:
    row = db.q1("SELECT * FROM signals WHERE id=?", (signal_id,))
    if not row:
        return "No such signal."
    sig = dict(row)
    if sig["status"] == "bought":
        return "Already bought; cannot skip."
    db.update("signals", signal_id, {"status": "skipped", "reason": "user skip"})
    db.execute(
        "INSERT INTO skips(mint,reason,until,ts) VALUES(?,?,?,?) "
        "ON CONFLICT(mint) DO UPDATE SET reason=excluded.reason, until=excluded.until, ts=excluded.ts",
        (sig["mint"], "user skip", db.night_start_ts() + 24 * 3600, db.now()),
    )
    name = (db.coin(sig["mint"]) or {}).get("name") or sig["mint"][:6]
    db.add_activity("skipped", sig["mint"], "executor", f"Skipped {name}", "Executor · skip", "Skipped", "", sig.get("mode"))
    return f"Skipped {name} for the night."


async def sold(position_id: int) -> str:
    row = db.q1("SELECT * FROM positions WHERE id=?", (position_id,))
    if not row:
        return "No such position."
    pos = dict(row)
    if pos["status"] == "closed":
        return "Already closed."
    db.update("positions", position_id, {"status": "closed"})
    db.upsert_coin(pos["mint"], status="closed")
    name = pos.get("name") or pos["mint"][:6]
    return f"Marked {name} sold."
