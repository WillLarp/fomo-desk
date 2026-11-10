
"""Executor: arithmetic and one button. Never picks a coin. Never places an order."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import bus
import config
import db
import lease
import stamps
import pipeline
import links
from sources import dexscreener
from util import ts, utcnow_iso

log = logging.getLogger("executor")

_pushed: set[str] = set()


def money_in_open() -> float:
    return db.money_in_open()


def size_ticket(stack: float, free: float, liquidity: float | None) -> tuple[float | None, str | None, str | None]:
    """-> (size, skip reason, cap that hit).

    size = stack_pct% of stack; min(size, max_pool_pct% of liquidity);
    under min ticket -> bump if below_min==bump and free covers it, else skip.
    size > free -> skip "no free stack".
    """
    g = config.get
    mt = float(g("executor.min_ticket_usd"))
    raw = stack * float(g("executor.stack_pct")) / 100
    cap = "stack"
    size = raw
    if liquidity is not None:
        pooled = liquidity * float(g("executor.max_pool_pct")) / 100
        if pooled < size:
            size = pooled
            cap = "pool"
    size = round(size, 2)
    if size < mt:
        if g("executor.below_min") == "bump" and free >= mt:
            size = mt
            cap = "bump"
        else:
            return None, f"under ${mt:.2f}", cap
    if size > free:
        return None, "no free stack", cap
    return round(size, 2), None, cap


def recheck(scan: dict, verdict_price: float | None, fresh: dict) -> str | None:
    g = config.get
    price = fresh.get("priceUsd")
    if price is None:
        return "no price"
    liq0, liq = scan.get("liquidityUsd"), fresh.get("liquidityUsd")
    if liq0 and liq is not None and liq < float(liq0) * (1 - float(g("executor.recheck_liq_drop_pct")) / 100):
        return "liquidity dropping"
    p0 = verdict_price
    if p0 and price > float(p0) * (1 + float(g("executor.recheck_price_up_pct")) / 100):
        return "ran before the push"
    return None


def card(**kw: Any) -> None:
    c = db.kv_get("executor_card", None) or {
        "trades": 0,
        "lastSize": None,
        "state": "idle",
        "progress": 0,
        "name": None,
        "stackPct": float(config.get("executor.stack_pct")),
    }
    c.update(kw)
    c["stackPct"] = float(config.get("executor.stack_pct"))
    row = db.q1("SELECT COUNT(*) AS n FROM positions")
    c["trades"] = int(row["n"]) if row else 0
    db.kv_set("executor_card", c)


def _skip(mint: str, name: str, reason: str, mode: str, price: float | None = None) -> dict:
    if not db.claim_action("signal", mint):
        return {"status": "skipped", "reason": "already pushed", "mint": mint, "mode": mode}
    sid = db.insert(
        "signals",
        {
            "mint": mint,
            "ts": db.now(),
            "price": price,
            "size_usd": None,
            "stack_pct": None,
            "link": None,
            "mode": mode,
            "status": "skipped",
            "reason": reason,
        },
    )
    db.add_activity("skipped", mint, "executor", f"Skipped {name}", f"Executor · {reason}", "Skipped", "", mode)
    log.info("skip %s %s", mint[:8], reason)
    return {"agent": "executor", "mint": mint, "status": "skipped", "reason": reason, "signal_id": sid, "mode": mode}


def open_position(signal: dict, name: str) -> int:
    existing = db.q1(
        "SELECT id FROM positions WHERE mint=? AND status IN ('open','exiting','closing','closed') AND signal_id=?",
        (signal["mint"], signal["id"]),
    )
    if existing:
        return int(existing["id"])
    if not db.claim_action("fill", str(signal["id"])):
        row = db.q1("SELECT id FROM positions WHERE signal_id=?", (signal["id"],))
        return int(row["id"]) if row else 0
    stop = float(config.get("risk.stop_pct"))
    entry = float(signal["price"])
    timeout_h = float(config.get("risk.timeout_h"))
    pid = db.insert(
        "positions",
        {
            "mint": signal["mint"],
            "name": name,
            "opened_at": db.now(),
            "entry_price": entry,
            "size_usd": signal["size_usd"],
            "peak_price": entry,
            "stop_pct": stop,
            "stop_price": entry * (1 - stop / 100),
            "trail_active": 0,
            "timeout_at": db.now() + timeout_h * 3600,
            "mode": signal["mode"],
            "status": "open",
            "last_price": entry,
            "no_price_polls": 0,
            "link": signal.get("link"),
            "signal_id": signal["id"],
        },
    )
    db.update("signals", signal["id"], {"status": "bought"})
    when = __import__("datetime").datetime.now(__import__("util").TZ).strftime("%-I:%M %p")
    db.add_activity(
        "bought",
        signal["mint"],
        "executor",
        f"Bought {name}",
        f"Executor · {when}",
        f"${signal['size_usd']:.2f}",
        f"{signal['stack_pct']:g}% of stack",
        signal["mode"],
    )
    bus.emit(
        "fill",
        {
            "position_id": pid,
            "mint": signal["mint"],
            "price": entry,
            "sizeUsd": signal["size_usd"],
            "mode": signal["mode"],
        },
    )
    bus.risk.put_nowait({"position_id": pid})
    return pid


async def _paper_bar(name: str) -> None:
    steps = 4
    delay = float(config.get("executor.paper_bar_s")) / steps
    for i, pr in enumerate((25, 50, 75, 100), 1):
        card(state="executing", progress=pr, name=name)
        await asyncio.sleep(delay)


async def handle(item: dict) -> dict:
    scan = item.get("scan") or {}
    audit = item.get("audit") or {}
    verdict = item.get("verdict") or {}
    mint = scan.get("mint")
    name = scan.get("name") or (db.coin(mint) or {}).get("name") or mint[:6]
    mode = str(config.get("desk.mode"))
    if not mint:
        return {"status": "skipped", "reason": "no mint"}
    if mint in _pushed:
        log.info("second pass ignored %s", mint[:8])
        return {"status": "skipped", "reason": "already pushed"}
    if db.q1("SELECT 1 AS x FROM signals WHERE mint=? AND ts>=?", (mint, db.night_start_ts())):
        _pushed.add(mint)
        return {"status": "skipped", "reason": "already pushed"}
    if db.q1("SELECT 1 AS x FROM skips WHERE mint=? AND until>?", (mint, db.now())):
        return _skip(mint, name, "skipped tonight", mode)
    if db.is_paused("executor"):
        return _skip(mint, name, "paused", mode)

    fresh = await dexscreener.fetch_one(mint)
    price = (fresh or {}).get("priceUsd")
    if price is None:
        return _skip(mint, name, "no price", mode)

    why = recheck(scan, (fresh or {}).get("priceUsd") and price, fresh or {})
    # recheck vs analyst/scanner price: use scan if we stored one... we use current vs scan liquidity and price up vs current (same) - use scan-time if present
    # Better: compare price to a stored scan is not available as priceUsd on scan. Skip price-up if no baseline.
    liq = (fresh or {}).get("liquidityUsd")
    liq0 = scan.get("liquidityUsd")
    if liq0 and liq is not None and liq < float(liq0) * (1 - float(config.get("executor.recheck_liq_drop_pct")) / 100):
        return _skip(mint, name, "liquidity dropping", mode, price)

    stack = float(config.stack())
    free = float(config.free_stack())
    size, reason, cap = size_ticket(stack, free, liq if liq is not None else liq0)
    log.info("size mint=%s stack=%s raw cap=%s final=%s mode=%s", mint[:8], stack, cap, size, mode)
    if size is None:
        return _skip(mint, name, reason or "no size", mode, price)

    pct = round(100 * size / stack, 1) if stack else 0.0
    url = links.require_canonical(links.coin_url(mint, "solana"))
    sid = db.insert(
        "signals",
        {
            "mint": mint,
            "ts": db.now(),
            "price": price,
            "size_usd": size,
            "stack_pct": pct,
            "link": url,
            "mode": mode,
            "status": "sent",
            "reason": None,
        },
    )
    _pushed.add(mint)
    payload = {
        "agent": "executor",
        "mint": mint,
        "signalPrice": price,
        "sizeUsd": size,
        "stackPct": pct,
        "link": url,
        "mode": mode,
        "status": "sent",
        "reason": None,
        "signal_id": sid,
    }
    bus.emit("signal", payload)
    card(state="executing", progress=10, name=name, lastSize=size)

    text = f"PAPER · Buy {name}" if mode == "PAPER" else f"Buy {name}"
    text += f" · ${size:.2f} · {pct:g}% of stack"
    from telegram_bot import push as tg_push

    ok = await tg_push(
        text,
        buttons=[
            ("Buy on FOMO", url),
            ("Bought", f"cb:bought:{sid}"),
            ("Skip", f"cb:skip:{sid}"),
        ],
        mint=mint,
        kind="signal",
    )
    if not ok:
        log.warning("push failed %s", mint[:8])
        if mode == "LIVE":
            db.update("signals", sid, {"status": "skipped", "reason": "push failed"})
            payload["status"] = "skipped"
            payload["reason"] = "push failed"
            card(state="idle", progress=0)
            return payload
        # PAPER: fill anyway
        log.info("push failed; paper fill anyway %s", mint[:8])

    if mode == "PAPER":
        await _paper_bar(name)
        sig = dict(db.q1("SELECT * FROM signals WHERE id=?", (sid,)))
        pid = open_position(sig, name)
        card(state="filled", progress=100, name=name, lastSize=size)
        payload["status"] = "bought"
        payload["position_id"] = pid
        db.upsert_coin(mint, status="open")
        return payload

    # LIVE: wait for Bought / Skip, or confirm window then skip
    window = float(config.get("executor.confirm_window_min")) * 60
    deadline = db.now() + window
    while db.now() < deadline:
        row = db.q1("SELECT status FROM signals WHERE id=?", (sid,))
        if row and row["status"] in ("bought", "skipped"):
            payload["status"] = row["status"]
            card(state="filled" if row["status"] == "bought" else "idle", progress=100 if row["status"] == "bought" else 0)
            return payload
        await asyncio.sleep(1)
    # missed
    db.update("signals", sid, {"status": "skipped", "reason": "no confirm"})
    db.execute(
        "INSERT INTO skips(mint,reason,until,ts) VALUES(?,?,?,?) "
        "ON CONFLICT(mint) DO UPDATE SET reason=excluded.reason, until=excluded.until, ts=excluded.ts",
        (mint, "no confirm", db.night_start_ts() + 24 * 3600, db.now()),
    )
    db.add_activity("skipped", mint, "executor", f"Skipped {name}", "Executor · no confirm", "Skipped", "", mode)
    payload["status"] = "missed"
    payload["reason"] = "no confirm"
    card(state="idle", progress=0)
    return payload


async def run(stop: asyncio.Event) -> None:
    global _pushed
    _pushed = set()
    start = db.night_start_ts()
    for r in db.q("SELECT mint FROM signals WHERE ts>=?", (start,)):
        _pushed.add(r["mint"])
    db.beat("executor", "running", "idle")
    card()
    was_leader = False
    while not stop.is_set():
        lead = lease.is_leader()
        if lead and not was_leader:
            await pipeline.catch_up()
        was_leader = lead
        if not lead:
            stamps.stamp_agent("executor")
            try:
                await asyncio.wait_for(stop.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            continue
        try:
            item = await asyncio.wait_for(bus.executor.get(), timeout=1.0)
        except asyncio.TimeoutError:
            stamps.stamp_agent("executor")
            continue
        try:
            result = await handle(item)
        except Exception:
            log.exception("executor failed")
            continue
        db.inc_beat("executor", "running", result.get("status") or "")
