
"""Risk: exits only. Never moves a stop down. Never waits for a bounce. Never places an order."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import bus
import config
import db
import lease
import stamps
import fees
import links
from sources import dexscreener
from util import ts

log = logging.getLogger("risk")


def tighten_stop(pos: dict) -> dict:
    current = float(pos.get("stop_pct") or config.get("risk.stop_pct"))
    setting = float(config.get("risk.stop_pct"))
    if setting < current:
        current = setting
        entry = float(pos["entry_price"])
        stop_price = entry * (1 - current / 100)
        db.update("positions", int(pos["id"]), {"stop_pct": current, "stop_price": stop_price})
        pos["stop_pct"] = current
        pos["stop_price"] = stop_price
    return pos


def evaluate(pos: dict, price: float) -> str | None:
    """Return exit reason or None. Order: peak, stop, trail on, trail, timeout."""
    entry = float(pos["entry_price"])
    peak = max(float(pos.get("peak_price") or entry), price)
    stop_pct = float(pos.get("stop_pct") or config.get("risk.stop_pct"))
    trail_start = float(config.get("risk.trail_start_pct"))
    trail_pct = float(config.get("risk.trail_pct"))
    timeout_h = float(config.get("risk.timeout_h"))
    timeout_gain = float(config.get("risk.timeout_min_gain_pct"))
    trail_active = bool(pos.get("trail_active"))
    if price >= entry * (1 + trail_start / 100):
        trail_active = True
    if price <= entry * (1 - stop_pct / 100):
        return "stop"
    if trail_active and price <= peak * (1 - trail_pct / 100):
        return "trail"
    opened = float(pos.get("opened_at") or 0)
    if opened and (ts() - opened) >= timeout_h * 3600 and price < entry * (1 + timeout_gain / 100):
        return "timeout"
    return None


async def close_position(pos: dict, price: float, reason: str) -> dict:
    if not db.claim_action("exit", str(pos["id"])):
        log.info("exit already claimed %s", pos.get("mint", "")[:8])
        return {"status": "skipped", "reason": "already claimed", "position_id": pos["id"]}
    entry = float(pos["entry_price"])
    size = float(pos["size_usd"])
    mode = pos.get("mode") or config.get("desk.mode")
    discount = float(config.get("desk.fees_ref_discount") or 0)
    exit_usd = size * (price / entry) if entry else size
    fee = fees.round_trip_fee(size, exit_usd, discount)
    pnl = exit_usd - size - fee
    pct = ((price / entry) - 1) * 100 if entry else 0.0
    name = pos.get("name") or (db.coin(pos["mint"]) or {}).get("name") or pos["mint"][:6]
    url = pos.get("link") or links.coin_url(pos["mint"], "solana")
    links.require_canonical(url)
    payload = {
        "agent": "risk",
        "mint": pos["mint"],
        "name": name,
        "ts": __import__("util").utcnow_iso(),
        "reason": reason,
        "entryPrice": entry,
        "exitPrice": price,
        "peakPrice": float(pos.get("peak_price") or entry),
        "pnlUsd": round(pnl, 2),
        "pct": round(pct, 2),
        "feesUsd": round(fee, 4),
        "stopPct": float(pos.get("stop_pct") or config.get("risk.stop_pct")),
        "mode": mode,
        "status": "closed" if mode == "PAPER" else "exiting",
        "link": url,
        "position_id": pos["id"],
    }
    fields = {
        "exit_price": price,
        "exit_reason": reason,
        "exit_ts": db.now(),
        "pnl_usd": payload["pnlUsd"],
        "pnl_pct": payload["pct"],
        "fees_usd": payload["feesUsd"],
        "last_price": price,
        "status": payload["status"],
        "link": url,
    }
    db.update("positions", int(pos["id"]), fields)
    if reason == "trail":
        db.add_activity(
            "sold",
            pos["mint"],
            "risk",
            f"Sold {name}",
            "Risk · trailing stop",
            f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}",
            f"{pct:+.1f}%",
            mode,
        )
        text = f"Sold {name} · +${pnl:.2f} ({pct:+.1f}%)" if pnl >= 0 else f"Sold {name} · -${abs(pnl):.2f} ({pct:+.1f}%)"
    elif reason == "stop":
        stop_pct = payload["stopPct"]
        db.add_activity(
            "stopped",
            pos["mint"],
            "risk",
            f"Stopped {name}",
            f"Risk · -{stop_pct:g}% stop",
            f"-${abs(pnl):.2f}",
            f"{pct:+.1f}%",
            mode,
        )
        text = f"{name} hit the -{stop_pct:g}% stop · -${abs(pnl):.2f}"
    elif reason == "timeout":
        db.add_activity(
            "timeout",
            pos["mint"],
            "risk",
            f"Sold {name}",
            "Risk · dead money",
            f"${pnl:+.2f}",
            f"{pct:+.1f}%",
            mode,
        )
        text = f"Sell {name}, dead money · {pct:.1f}% in {float(config.get('risk.timeout_h')):g}h"
    else:
        db.add_activity(
            "sold",
            pos["mint"],
            "risk",
            f"Sold {name}",
            f"Risk · {reason}",
            f"${pnl:+.2f}",
            f"{pct:+.1f}%",
            mode,
        )
        text = f"Sell {name} · liquidity pulled"
    prefix = "PAPER · " if mode == "PAPER" else ""
    from telegram_bot import push as tg_push

    buttons = [("Sell on FOMO", url)]
    if mode == "LIVE":
        buttons.append(("Sold", f"cb:sold:{pos['id']}"))
    await tg_push(prefix + text, buttons=buttons, mint=pos["mint"], kind="exit")
    bus.emit("exit", payload)
    if mode == "PAPER":
        db.upsert_coin(pos["mint"], status="closed")
        card_last = {"stop": "Stopped", "trail": "Trailed out", "timeout": "Timed out", "pulled": "Pulled"}.get(reason, reason)
        _card(last=card_last)
    else:
        # wait for Sold; reminder after confirm_window
        asyncio.create_task(_live_remind(int(pos["id"]), url, name))
        _card(last="Exiting")
    log.info(
        "exit %s reason=%s entry=%s exit=%s pnl=%s",
        pos["mint"][:8],
        reason,
        entry,
        price,
        payload["pnlUsd"],
    )
    return payload


async def _live_remind(pid: int, url: str, name: str) -> None:
    wait = float(config.get("risk.confirm_window_min")) * 60
    await asyncio.sleep(wait)
    row = db.q1("SELECT * FROM positions WHERE id=?", (pid,))
    if not row or row["status"] != "exiting":
        return
    from telegram_bot import push as tg_push

    await tg_push(
        f"{name} still open",
        buttons=[("Sell on FOMO", url), ("Sold", f"cb:sold:{pid}")],
        mint=row["mint"],
        kind="exit",
    )


def _card(**kw):
    c = db.kv_get("risk_card") or {
        "open": 0,
        "guarding": False,
        "last": None,
        "stopPct": float(config.get("risk.stop_pct")),
        "trailPct": float(config.get("risk.trail_pct")),
    }
    c.update(kw)
    opens = db.open_positions()
    c["open"] = len(opens)
    c["guarding"] = any(p["status"] == "open" for p in opens)
    c["stopPct"] = float(config.get("risk.stop_pct"))
    c["trailPct"] = float(config.get("risk.trail_pct"))
    db.kv_set("risk_card", c)


async def poll_once() -> None:
    if not lease.is_leader():
        db.beat("risk", "running", "standby")
        return
    positions = [p for p in db.open_positions() if p["status"] in ("open",)]
    # exiting LIVE positions are not re-exited
    _card()
    if not positions:
        db.beat("risk", "running", "idle")
        return
    mints = list({p["mint"] for p in positions})
    try:
        pairs = await dexscreener.fetch_pairs(mints)
    except Exception:
        log.exception("risk poll failed")
        return
    # 429 is handled inside fetch_pairs (wait). If a mint is missing, count as no price.
    for pos in positions:
        pos = tighten_stop(dict(pos))
        pair = pairs.get(pos["mint"]) or {}
        price = pair.get("priceUsd")
        name = pos.get("name") or pos["mint"][:6]
        if price is None:
            n = int(pos.get("no_price_polls") or 0) + 1
            db.update("positions", int(pos["id"]), {"no_price_polls": n})
            log.info("poll %s price=none peak=%s stop=%s action=no-price n=%s", pos["mint"][:8], pos.get("peak_price"), pos.get("stop_price"), n)
            if n >= int(config.get("risk.no_price_polls")):
                url = pos.get("link") or links.coin_url(pos["mint"], "solana")
                from telegram_bot import push as tg_push

                await tg_push(
                    f"{name}: no price for 1 min",
                    buttons=[("Sell on FOMO", url)],
                    mint=pos["mint"],
                    kind="warn",
                )
                # reset counter so we don't spam every poll
                db.update("positions", int(pos["id"]), {"no_price_polls": 0})
            continue
        db.add_price(pos["mint"], price, (pair.get("volume") or {}).get("m5"))
        db.update_market(pos["mint"], pair)
        liq = pair.get("liquidityUsd")
        entry = float(pos["entry_price"])
        peak = max(float(pos.get("peak_price") or entry), price)
        trail_start = float(config.get("risk.trail_start_pct"))
        trail_pct = float(config.get("risk.trail_pct"))
        trail_active = bool(pos.get("trail_active")) or price >= entry * (1 + trail_start / 100)
        trail_line = peak * (1 - trail_pct / 100) if trail_active else None
        updates = {
            "peak_price": peak,
            "last_price": price,
            "no_price_polls": 0,
            "trail_active": 1 if trail_active else 0,
            "trail_price": trail_line,
        }
        db.update("positions", int(pos["id"]), updates)
        pos.update(updates)
        action = "hold"
        if liq is not None and liq <= 0:
            action = "pulled"
            await close_position(pos, price, "pulled")
        else:
            reason = evaluate(pos, price)
            if reason:
                action = reason
                await close_position(pos, price, reason)
        log.info(
            "poll %s price=%s peak=%s stop=%s trail=%s action=%s",
            pos["mint"][:8],
            price,
            peak,
            pos.get("stop_price"),
            trail_line,
            action,
        )
    db.beat("risk", "running", f"{len(db.open_positions())} open")


async def run(stop: asyncio.Event) -> None:
    db.beat("risk", "running", "idle")
    _card()
    # also accept bus.risk fills
    async def drain():
        while not stop.is_set():
            try:
                await asyncio.wait_for(bus.risk.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

    async def liveness():
        while not stop.is_set():
            try:
                stamps.stamp_agent("risk")
            except Exception:
                log.exception("risk liveness beat failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=1)
            except asyncio.TimeoutError:
                continue

    t = asyncio.create_task(drain())
    live = asyncio.create_task(liveness())
    try:
        while not stop.is_set():
            try:
                # Risk cannot be paused while a position is open.
                await poll_once()
            except Exception:
                log.exception("risk poll")
            try:
                await asyncio.wait_for(stop.wait(), timeout=float(config.get("risk.poll_s")))
            except asyncio.TimeoutError:
                continue
    finally:
        live.cancel()
        t.cancel()
