
"""Scanner: count and cut. Every PumpSwap graduate. Never judges quality."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import bus
import config
import db
from sources import dexscreener, pumpportal, rpc, rugcheck
from util import age_min, as_float, is_copycat, iso_to_ts, ts, utcnow_iso

log = logging.getLogger("scanner")

# Tokens we already decided on this night (pass or kill or drop).
_decided: set[str] = set()


def _reason_liq(liq: float) -> str:
    k = liq / 1000
    return f"liq ${k:.1f}K"


def _reason_top10(pct: float) -> str:
    return f"top 10 hold {pct:.0f}%"


def _reason_h1(pct: float) -> str:
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct:.0f}% in 1h"


def _normalize_holders(accounts: list, exclude: set[str], supply: float | None) -> float | None:
    """Sum top 10 holder percentages after dropping pool/curve accounts."""
    rows = []
    for a in accounts:
        addr = a.get("address") or a.get("pubkey")
        owner = a.get("owner")
        pct = a.get("pct")
        amount = a.get("uiAmount") or a.get("uiAmountString") or a.get("amount")
        if addr in exclude or (owner and owner in exclude):
            continue
        if pct is None and supply and amount is not None:
            try:
                pct = float(amount) / supply * 100 if isinstance(amount, (int, float)) or str(amount).replace(".", "", 1).isdigit() else None
                if pct is None:
                    amt = float(amount)
                    pct = amt / supply * 100
            except (TypeError, ValueError):
                pct = None
        if pct is None:
            continue
        try:
            rows.append(float(pct))
        except (TypeError, ValueError):
            continue
    rows.sort(reverse=True)
    if not rows and not accounts:
        return None
    return round(sum(rows[:10]), 4)


async def _top10_pct(mint: str, pool: str | None, report: dict | None) -> float | None:
    exclude = rugcheck.pool_exclude_accounts(report, pool)
    # getTokenLargestAccounts
    accounts, err = await rpc.get_token_largest_accounts(mint)
    supply = None
    info, _ = await rpc.get_account_info(mint)
    parsed = rpc.parsed_mint_info(info)
    try:
        dec = int(parsed.get("decimals") or 0)
        raw = float(parsed.get("supply") or 0)
        supply = raw / (10 ** dec) if dec is not None else raw
    except (TypeError, ValueError):
        supply = None
    rows = []
    for a in accounts or []:
        addr = a.get("address")
        # largest-accounts has no owner; drop if address is excluded
        if addr in exclude:
            continue
        ui = a.get("uiAmount")
        amt = a.get("amount")
        pct = None
        if ui is not None and supply:
            try:
                pct = float(ui) / supply * 100
            except (TypeError, ValueError):
                pct = None
        if pct is None and amt is not None and parsed.get("supply"):
            try:
                pct = float(amt) / float(parsed["supply"]) * 100
            except (TypeError, ValueError):
                pct = None
        if pct is not None:
            rows.append(pct)
    if rows:
        rows.sort(reverse=True)
        return round(sum(rows[:10]), 4)
    # fallback: rugcheck topHolders
    if report and report.get("topHolders"):
        return _normalize_holders(report["topHolders"], exclude, supply)
    return None


async def _creator_balance(mint: str, creator: str | None, supply: float | None) -> tuple[float | None, float | None]:
    if not creator:
        return None, None
    accounts, err = await rpc.get_token_accounts_by_owner(creator, mint)
    if err and not accounts:
        return None, None
    bal = rpc.ui_amount_sum(accounts)
    pct = (bal / supply * 100) if supply else None
    return bal, pct


def _log_decision(payload: dict) -> None:
    line = (
        f"{payload.get('ts')} {payload.get('mint')} {payload.get('verdict')} "
        f"{payload.get('reason')} liq={payload.get('liquidityUsd')} "
        f"top10={payload.get('top10Pct')} h1={payload.get('h1ChangePct')} "
        f"age={payload.get('ageMin')}"
    )
    log.info(line)


def _payload(mint, name, symbol, pool, liq, top10, h1, age, verdict, reason) -> dict:
    return {
        "agent": "scanner",
        "mint": mint,
        "name": name,
        "symbol": symbol,
        "pool": pool,
        "liquidityUsd": liq,
        "top10Pct": top10,
        "h1ChangePct": h1,
        "ageMin": age,
        "verdict": verdict,
        "reason": reason,
        "ts": utcnow_iso(),
    }


async def _decide(mint: str, pair: dict, meta: dict) -> None:
    name = pair.get("name") or meta.get("name") or mint[:6]
    symbol = pair.get("symbol") or meta.get("symbol") or ""
    pool = pair.get("pairAddress") or meta.get("pool")
    liq = pair.get("liquidityUsd")
    h1 = pair.get("priceChange", {}).get("h1") if isinstance(pair.get("priceChange"), dict) else None
    created = iso_to_ts(pair.get("pairCreatedAt")) or meta.get("migrated_at")
    age = age_min(created)
    # kill order
    min_liq = float(config.get("scanner.min_liquidity_usd"))
    if liq is not None and liq < min_liq:
        await _finish(_payload(mint, name, symbol, pool, liq, None, h1, age, "kill", _reason_liq(liq)))
        return
    # top 10 — need RPC; report if we have one cached on the coin
    report = None
    # We may not have fetched RugCheck yet; fetch for top10 / creator if needed
    if meta.get("creator") is None or True:
        report, _ = await rugcheck.report(mint)
        if report and not meta.get("creator"):
            creator = report.get("creator")
            db.upsert_coin(mint, creator=creator)
            meta["creator"] = creator
    top10 = await _top10_pct(mint, pool, report)
    max_top = float(config.get("scanner.max_top10_pct"))
    if top10 is not None and top10 > max_top:
        await _finish(_payload(mint, name, symbol, pool, liq, top10, h1, age, "kill", _reason_top10(top10)))
        return
    max_h1 = float(config.get("scanner.max_h1_change_pct"))
    if h1 is not None and h1 > max_h1:
        await _finish(_payload(mint, name, symbol, pool, liq, top10, h1, age, "kill", _reason_h1(h1)))
        return
    # copycat against today's runners
    hit = is_copycat(name, symbol, db.runners_today())
    if hit:
        await _finish(_payload(mint, name, symbol, pool, liq, top10, h1, age, "kill", f"copycat of {hit}"))
        return
    # record as a runner if it itself is a runner (for later copycats)
    h24 = pair.get("priceChange", {}).get("h24") if isinstance(pair.get("priceChange"), dict) else None
    if h24 is not None and h24 >= float(config.get("scanner.runner_h24_pct")):
        runners = db.runners_today()
        if not any(r.get("mint") == mint for r in runners):
            runners.append({"mint": mint, "name": name, "symbol": symbol})
            db.set_runners_today(runners)
    min_age = int(config.get("scanner.min_pool_age_min"))
    if age < min_age:
        # stay in the waiting room
        return
    # pass once
    await _finish(_payload(mint, name, symbol, pool, liq, top10, h1, age, "pass", None))


async def _finish(payload: dict) -> None:
    mint = payload["mint"]
    if mint in _decided:
        return
    _decided.add(mint)
    db.add_scan(payload)
    db.upsert_coin(
        mint,
        name=payload.get("name"),
        symbol=payload.get("symbol"),
        pool=payload.get("pool"),
        status="passed" if payload["verdict"] == "pass" else "killed",
    )
    execute = db.execute
    execute("DELETE FROM waiting WHERE mint=?", (mint,))
    _log_decision(payload)
    if payload["verdict"] == "pass":
        await bus.auditor.put(payload)
        bus.emit("scan", payload)
    else:
        bus.emit("scan", payload)
    db.inc_beat("scanner", "running", payload["verdict"])


async def on_migration(msg: dict) -> None:
    mint = msg.get("mint")
    if not mint:
        return
    if mint in _decided:
        return
    if db.coin(mint) and db.coin(mint).get("status") in ("passed", "killed", "dropped"):
        # already handled this night
        start = db.night_start_ts()
        c = db.coin(mint)
        if (c.get("first_seen") or 0) >= start:
            _decided.add(mint)
            return
    created = ts()
    db.upsert_coin(
        mint,
        signature=msg.get("signature"),
        migrated_at=created,
        status="waiting",
        night_id=(db.current_night() or {}).get("id"),
    )
    # creator is not in the PumpPortal message (fields.py); RugCheck later
    db.execute(
        "INSERT INTO waiting(mint,entered_at,last_poll) VALUES(?,?,?) "
        "ON CONFLICT(mint) DO NOTHING",
        (mint, created, None),
    )
    # try to get creator + creator balance now
    report, _ = await rugcheck.report(mint)
    creator = (report or {}).get("creator")
    supply = None
    info, _ = await rpc.get_account_info(mint)
    parsed = rpc.parsed_mint_info(info)
    try:
        dec = int(parsed.get("decimals") or 0)
        raw = float(parsed.get("supply") or 0)
        supply = raw / (10 ** dec) if raw else None
    except (TypeError, ValueError):
        supply = None
    bal, pct = await _creator_balance(mint, creator, supply)
    db.upsert_coin(
        mint,
        creator=creator,
        creator_balance=bal,
        creator_balance_pct=pct,
    )
    log.info("waiting room %s creator=%s", mint[:8], "yes" if creator else "null")


async def _poll_waiting() -> None:
    rows = db.q("SELECT * FROM waiting")
    if not rows:
        db.beat("scanner", "running", "0 waiting")
        return
    mints = [r["mint"] for r in rows]
    entered = {r["mint"]: r["entered_at"] for r in rows}
    pairs = await dexscreener.fetch_pairs(mints)
    max_wait = int(config.get("scanner.max_wait_min")) * 60
    now = ts()
    for mint in mints:
        pair = pairs.get(mint)
        if pair and pair.get("priceUsd") is not None:
            db.add_price(mint, pair.get("priceUsd"), (pair.get("volume") or {}).get("m5"))
            db.upsert_coin(
                mint,
                name=pair.get("name"),
                symbol=pair.get("symbol"),
                pool=pair.get("pool") or pair.get("pairAddress"),
                image=pair.get("image"),
            )
        # drop if never qualified
        if now - (entered.get(mint) or now) >= max_wait:
            if mint in _decided:
                db.execute("DELETE FROM waiting WHERE mint=?", (mint,))
                continue
            c = db.coin(mint) or {}
            payload = _payload(
                mint,
                c.get("name") or mint[:6],
                c.get("symbol") or "",
                c.get("pool"),
                (pair or {}).get("liquidityUsd"),
                None,
                (pair or {}).get("priceChange", {}).get("h1") if pair else None,
                age_min(c.get("migrated_at") or entered.get(mint)),
                "kill",
                "never qualified",
            )
            # treat as drop, not a pass
            _decided.add(mint)
            db.add_scan(payload)
            db.upsert_coin(mint, status="dropped")
            db.execute("DELETE FROM waiting WHERE mint=?", (mint,))
            _log_decision(payload)
            bus.emit("scan", payload)
            continue
        if not pair:
            # DexScreener doesn't know it yet: wait
            continue
        meta = db.coin(mint) or {"migrated_at": entered.get(mint)}
        try:
            await _decide(mint, pair, meta)
        except Exception:
            log.exception("decide failed %s", mint[:8])
        db.execute("UPDATE waiting SET last_poll=? WHERE mint=?", (now, mint))
    db.beat("scanner", "running", f"{len(rows)} waiting")


async def run(stop: asyncio.Event) -> None:
    global _decided
    _decided = set()
    start = db.night_start_ts()
    for r in db.q("SELECT mint, status FROM coins WHERE first_seen>=?", (start,)):
        if r["status"] in ("passed", "killed", "dropped"):
            _decided.add(r["mint"])
    db.beat("scanner", "running", "starting")
    poll_s = float(config.get("scanner.poll_s"))

    async def poll_loop():
        while not stop.is_set():
            try:
                if not db.is_paused("scanner"):
                    await _poll_waiting()
                else:
                    db.beat("scanner", "paused", "paused")
            except Exception:
                log.exception("waiting-room poll failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=poll_s)
            except asyncio.TimeoutError:
                continue

    poll_task = asyncio.create_task(poll_loop())
    try:
        await pumpportal.run(on_migration, stop)
    finally:
        poll_task.cancel()
        try:
            await poll_task
        except asyncio.CancelledError:
            pass
