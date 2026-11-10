
"""Analyst: build a state, ask Jev 4 noul questions, code applies the gate."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import Any

import bus
import config
import db
import lease
import stamps
import pipeline
from sources import dexscreener, jev
from util import age_min, as_float, clamp, ts, utcnow_iso

log = logging.getLogger("analyst")

_asked: set[str] = set()


def build_state(scan: dict, audit: dict, pair: dict) -> str:
    """Plain text, numbers only, no opinions. No thresholds, balance, or the word buy."""
    coin = db.coin(scan["mint"]) or {}
    name = scan.get("name") or coin.get("name") or scan["mint"][:6]
    age = scan.get("ageMin")
    if age is None:
        age = age_min(coin.get("migrated_at"))
    liq = pair.get("liquidityUsd") if pair.get("liquidityUsd") is not None else scan.get("liquidityUsd")
    mcap = pair.get("marketCap")
    vol = pair.get("volume") or {}
    tx = pair.get("txns") or {}
    pc = pair.get("priceChange") or {}
    b5 = (tx.get("m5") or {}).get("buys")
    s5 = (tx.get("m5") or {}).get("sells")
    v5 = vol.get("m5")
    p5 = pc.get("m5")
    b1 = (tx.get("h1") or {}).get("buys")
    s1 = (tx.get("h1") or {}).get("sells")
    v1 = vol.get("h1")
    p1 = pc.get("h1")
    top10 = scan.get("top10Pct")
    # dev / lp / bundled / delegations from audit layer values
    dev = "n/a"
    lp = "n/a"
    bund = "n/a"
    delg = "n/a"
    for layer in audit.get("layers") or []:
        n = layer.get("n")
        val = layer.get("value") or "n/a"
        if n == 3:
            dev = val
        elif n == 5:
            lp = val
        elif n == 7:
            bund = val
        elif n == 8:
            delg = val
    # Layer 3 value looks like "0.9% · sold 0"
    state = (
        f"Coin {name} on Solana, graduated {age} min ago. "
        f"Liquidity ${fmt_num(liq)}. Market cap ${fmt_num(mcap)}. "
        f"Last 5 min: {fmt_int(b5)} buys, {fmt_int(s5)} sells, volume ${fmt_num(v5)}, price {fmt_chg(p5)}%. "
        f"Last 1h: {fmt_int(b1)} buys, {fmt_int(s1)} sells, volume ${fmt_num(v1)}, price {fmt_chg(p1)}%. "
        f"Top 10 hold {fmt_num(top10)}%. Dev holds {dev}. LP {lp}. Bundled {bund}. Delegations {delg}."
    )
    if len(state) > 3000:
        state = state[:3000]
    return state


def fmt_num(v) -> str:
    if v is None:
        return "n/a"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "n/a"
    if abs(f) >= 100:
        return f"{f:,.0f}"
    if abs(f) >= 1:
        return f"{f:,.2f}"
    return f"{f:.6g}"


def fmt_int(v) -> str:
    if v is None:
        return "n/a"
    try:
        return str(int(v))
    except (TypeError, ValueError):
        return "n/a"


def fmt_chg(v) -> str:
    if v is None:
        return "n/a"
    try:
        return f"{float(v):.2f}"
    except (TypeError, ValueError):
        return "n/a"


def rules_answers(pair: dict, audit: dict) -> dict[str, float]:
    vol = (pair.get("volume") or {}).get("h1") or 0
    buys = ((pair.get("txns") or {}).get("h1") or {}).get("buys") or 0
    sells = ((pair.get("txns") or {}).get("h1") or {}).get("sells") or 0
    m5 = (pair.get("priceChange") or {}).get("m5")
    h1 = (pair.get("priceChange") or {}).get("h1")
    buys_m5 = ((pair.get("txns") or {}).get("m5") or {}).get("buys") or 0
    sells_m5 = ((pair.get("txns") or {}).get("m5") or {}).get("sells") or 0
    alive = clamp(float(vol) / float(config.get("analyst.rules_alive_volume_h1") or 1), 0, 1)
    organic = clamp(float(buys) / float(config.get("analyst.rules_organic_buys_h1") or 1), 0, 1)
    bundled = audit.get("bundled") or 0
    for layer in audit.get("layers") or []:
        if layer.get("n") == 7 and layer.get("pass") is True:
            # "None" or a number
            val = layer.get("value")
            if val and val != "None":
                try:
                    bundled = int(str(val).split()[0])
                except ValueError:
                    pass
    if bundled >= int(config.get("analyst.rules_bundled_from")):
        organic = organic * float(config.get("analyst.rules_bundled_penalty"))
    drop = float(config.get("analyst.rules_flush_drop_m5_pct"))
    if m5 is not None and m5 <= -drop and buys_m5 >= sells_m5:
        flush = 1.0
    elif m5 is not None and m5 < 0:
        flush = 0.5
    else:
        flush = 0.0
    chase = clamp(float(h1 or 0) / float(config.get("analyst.rules_chase_h1_pct") or 1), 0, 1)
    return {
        "alive_6h": round(alive, 4),
        "organic": round(organic, 4),
        "flush": flush,
        "chase": round(chase, 4),
    }


def apply_gate(answers: dict[str, float]) -> tuple[int, bool, list[str]]:
    failed = []
    a = answers.get("alive_6h")
    o = answers.get("organic")
    c = answers.get("chase")
    f = answers.get("flush")
    if a is None or a < float(config.get("analyst.min_alive_6h")):
        failed.append(f"alive_6h {a}")
    if o is None or o < float(config.get("analyst.min_organic")):
        failed.append(f"organic {o}")
    if c is None or c > float(config.get("analyst.max_chase")):
        failed.append(f"chase {c}")
    score = round(100 * ((a or 0) + (o or 0) + (f or 0) + (1 - (c or 0))) / 4)
    if score < int(config.get("analyst.min_score")):
        failed.append(f"score {score}")
    passed = not failed
    return score, passed, failed


async def score_coin(scan: dict, audit: dict) -> dict:
    mint = scan["mint"]
    pair = await dexscreener.fetch_one(mint)
    state = build_state(scan, audit, pair or {})
    state_hash = hashlib.sha256(state.encode()).hexdigest()[:16]
    mode = str(config.get("analyst.mode"))
    answers = None
    model = None
    tokens = 0
    reason = None
    if mode == "rules":
        answers = rules_answers(pair or {}, audit)
        model = "rules"
        tokens = 0
    else:
        # jev; never fall back to rules
        timeout = float(config.get("analyst.timeout_s"))
        gap = float(config.get("analyst.retry_gap_s"))
        try:
            res = await _jev_with_retry(state, timeout, gap)
            answers = res["answers"]
            model = res["model"]
            tokens = res["inputTokens"]
        except jev.JevError as e:
            reason = "no answer" if e.kind != "bad answer" else "bad answer"
            if e.kind == "bad answer":
                log.warning("bad answer raw=%s", _safe_raw(e.raw))
            answers = None
    if answers is None:
        payload = _payload(mint, {"alive_6h": None, "organic": None, "flush": None, "chase": None}, 0, False, [reason or "no answer"], model or "jev", tokens)
        _store(scan, payload, state_hash)
        return payload
    score, passed, failed = apply_gate(answers)
    payload = _payload(mint, answers, score, passed, failed, model, tokens)
    _store(scan, payload, state_hash)
    return payload


def _safe_raw(raw) -> str:
    try:
        s = str(raw)
    except Exception:
        return "unprintable"
    return s[:400]


async def _jev_with_retry(state: str, timeout: float, gap: float) -> dict:
    try:
        return await jev.ask(state, timeout_s=timeout)
    except jev.JevError as e:
        if e.kind == "429":
            # queue up to queue_429_s then reject
            wait = float(config.get("analyst.queue_429_s"))
            log.warning("jev 429, queue %.0fs", wait)
            await asyncio.sleep(min(wait, 60))
            return await jev.ask(state, timeout_s=timeout)
        # retry once after gap
        await asyncio.sleep(gap)
        return await jev.ask(state, timeout_s=timeout)


def _payload(mint, answers, score, passed, failed, model, tokens) -> dict:
    return {
        "agent": "analyst",
        "mint": mint,
        "answers": answers,
        "score": score,
        "passed": passed,
        "failed": failed,
        "model": model,
        "inputTokens": tokens,
    }


def _store(scan: dict, payload: dict, state_hash: str) -> None:
    mint = scan["mint"]
    ans = payload.get("answers") or {}
    db.insert(
        "verdicts",
        {
            "mint": mint,
            "ts": utcnow_iso(),
            "score": payload.get("score"),
            "passed": int(bool(payload.get("passed"))),
            "alive_6h": ans.get("alive_6h"),
            "organic": ans.get("organic"),
            "flush": ans.get("flush"),
            "chase": ans.get("chase"),
            "failed": ",".join(payload.get("failed") or []),
            "model": payload.get("model"),
            "input_tokens": payload.get("inputTokens"),
            "state_hash": state_hash,
            "raw": __import__("json").dumps(payload),
        },
    )
    log.info(
        "mint=%s hash=%s %s score=%s passed=%s tokens=%s",
        mint[:8],
        state_hash,
        ans,
        payload.get("score"),
        payload.get("passed"),
        payload.get("inputTokens"),
    )
    name = scan.get("name") or mint[:6]
    if not payload.get("passed"):
        first = (payload.get("failed") or ["rejected"])[0]
        label = "Analyst (rules)" if payload.get("model") == "rules" else "Analyst"
        db.add_activity("rejected", mint, "analyst", f"Rejected {name}", f"{label} · {first}", "Skipped", "")
        db.upsert_coin(mint, status="rejected")
    else:
        db.upsert_coin(mint, status="scored")
    card = {
        "scoring": {
            "mint": mint,
            "name": name,
            "score": payload.get("score") or 0,
            "passed": bool(payload.get("passed")),
        },
        "scored": int((db.kv_get("analyst_card") or {}).get("scored") or 0) + 1,
        "mode": config.get("analyst.mode"),
    }
    db.kv_set("analyst_card", card)
    bus.emit("verdict", payload)


async def run(stop: asyncio.Event) -> None:
    global _asked
    _asked = set()
    db.beat("analyst", "running", "idle")
    was_leader = False
    while not stop.is_set():
        lead = lease.is_leader()
        if lead and not was_leader:
            await pipeline.catch_up()
        was_leader = lead
        if not lead:
            stamps.stamp_agent("analyst")
            try:
                await asyncio.wait_for(stop.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            continue
        try:
            item = await asyncio.wait_for(bus.analyst.get(), timeout=1.0)
        except asyncio.TimeoutError:
            stamps.stamp_agent("analyst")
            continue
        scan = item.get("scan") or {}
        audit = item.get("audit") or {}
        mint = scan.get("mint")
        if not mint:
            continue
        if mint in _asked:
            log.info("already asked %s", mint[:8])
            continue
        if db.is_paused("analyst"):
            db.beat("analyst", "paused", "paused")
            continue
        _asked.add(mint)
        try:
            result = await score_coin(scan, audit)
        except Exception:
            log.exception("analyst failed %s", mint[:8])
            result = _payload(mint, {}, 0, False, ["no answer"], str(config.get("analyst.mode")), 0)
            _store(scan, result, "error")
        if result.get("passed"):
            await bus.executor.put({"scan": scan, "audit": audit, "verdict": result})
        db.inc_beat("analyst", "running", "pass" if result.get("passed") else (result.get("failed") or ["fail"])[0])
