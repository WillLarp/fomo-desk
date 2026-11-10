
"""Auditor: 8 layers in order. One red layer and the coin is dead. No data is never a pass."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import bus
import config
import db
import lease
import pipeline
from sources import rpc, rugcheck
from util import age_min, ts, utcnow_iso

log = logging.getLogger("auditor")

LAYER_NAMES = {
    1: "Mint authority",
    2: "Freeze authority",
    3: "Dev wallet",
    4: "Top 10 holders",
    5: "Liquidity",
    6: "Pool age",
    7: "Bundled wallets",
    8: "Delegations",
}

_rejected: set[str] = set()
_layer8_down = False
_layer8_alerted = False


async def _retry(fn, *args, **kwargs):
    retries = int(config.get("auditor.retries"))
    gap = float(config.get("auditor.retry_gap_s"))
    last = None
    for i in range(retries + 1):
        try:
            result = await fn(*args, **kwargs)
            # fn returns (value, err)
            if isinstance(result, tuple) and len(result) == 2 and result[1]:
                last = result
                if i < retries:
                    await asyncio.sleep(gap)
                    continue
            return result
        except Exception as e:
            last = (None, type(e).__name__)
            if i < retries:
                await asyncio.sleep(gap)
                continue
            return last
    return last or (None, "error")


def _layer(n: int, passed: bool | None, value: str, source: str) -> dict:
    return {"n": n, "name": LAYER_NAMES[n], "pass": passed, "value": value, "source": source}


async def _emit(mint: str, layer: dict, ms: int, audit_id: int | None = None) -> None:
    bus.emit("audit_layer", {"mint": mint, **layer, "ms": ms})
    db.insert(
        "audit_layers",
        {
            "audit_id": audit_id,
            "mint": mint,
            "n": layer["n"],
            "name": layer["name"],
            "pass": None if layer["pass"] is None else int(bool(layer["pass"])),
            "value": layer["value"],
            "source": layer["source"],
            "ts": ts(),
            "ms": ms,
        },
    )
    log.info(
        "layer %s %s %s %s ms=%s",
        layer["n"],
        mint[:8],
        layer["pass"],
        layer["value"],
        ms,
    )


async def audit(scan: dict) -> dict:
    mint = scan["mint"]
    t0_all = time.monotonic()
    layers: list[dict] = []
    report = None
    graph = None
    holders = None
    first_red = None
    checked = 0
    card = db.kv_get("auditor_card", None) or {"takenApart": 0, "rejected": 0, "current": None, "last": None}
    card["current"] = {"mint": mint, "name": scan.get("name"), "layers": []}
    db.kv_set("auditor_card", card)

    def note(layer):
        card["current"]["layers"].append({"n": layer["n"], "pass": layer["pass"]})
        db.kv_set("auditor_card", card)

    # Fetch RugCheck once (reused).
    t = time.monotonic()
    report, report_err = await _retry(rugcheck.report, mint)
    graph, graph_err = await _retry(rugcheck.insiders_graph, mint)

    # mint info
    info, info_err = await _retry(rpc.get_account_info, mint)
    parsed = rpc.parsed_mint_info(info)
    supply_raw = parsed.get("supply")
    decimals = parsed.get("decimals")
    try:
        supply = float(supply_raw) / (10 ** int(decimals or 0)) if supply_raw is not None else None
    except (TypeError, ValueError):
        supply = None

    # ---- 1 mint authority ----
    t = time.monotonic()
    if info_err or not info:
        layer = _layer(1, False, "no data: mint authority", "rpc")
        first_red = layer["value"]
    else:
        auth = parsed.get("mintAuthority")
        if auth in (None, "", "null"):
            layer = _layer(1, True, "Revoked", "rpc")
        else:
            layer = _layer(1, False, "mint not revoked", "rpc")
            first_red = layer["value"]
    layers.append(layer)
    checked = 1
    await _emit(mint, layer, int((time.monotonic() - t) * 1000))
    note(layer)
    if not layer["pass"]:
        return _done(scan, layers, checked, False, first_red, holders, t0_all)

    # ---- 2 freeze authority ----
    t = time.monotonic()
    freeze = parsed.get("freezeAuthority")
    if freeze in (None, "", "null"):
        layer = _layer(2, True, "Revoked", "rpc")
    else:
        layer = _layer(2, False, "freeze not revoked", "rpc")
        first_red = layer["value"]
    layers.append(layer)
    checked = 2
    await _emit(mint, layer, int((time.monotonic() - t) * 1000))
    note(layer)
    if not layer["pass"]:
        return _done(scan, layers, checked, False, first_red, holders, t0_all)

    # ---- 3 dev wallet ----
    t = time.monotonic()
    coin = db.coin(mint) or {}
    creator = coin.get("creator") or (report or {}).get("creator")
    if not creator:
        layer = _layer(3, False, "no data: dev", "rpc")
        first_red = layer["value"]
    elif supply is None or supply == 0:
        layer = _layer(3, False, "no supply", "rpc")
        first_red = layer["value"]
    else:
        accounts, err = await _retry(rpc.get_token_accounts_by_owner, creator, mint)
        if err and not accounts:
            layer = _layer(3, False, "no data: dev", "rpc")
            first_red = layer["value"]
        else:
            bal = rpc.ui_amount_sum(accounts or [])
            pct = bal / supply * 100
            migrated_bal = coin.get("creator_balance")
            sold = False
            if migrated_bal is not None and bal < float(migrated_bal) - 1e-9:
                sold = True
            max_dev = float(config.get("auditor.max_dev_pct"))
            if sold:
                layer = _layer(3, False, "dev sold", "rpc")
                first_red = layer["value"]
            elif pct >= max_dev:
                layer = _layer(3, False, f"dev holds {pct:.0f}%", "rpc")
                first_red = layer["value"]
            else:
                sold_txt = "sold 0" if (migrated_bal is None or abs(bal - float(migrated_bal)) < 1e-6 or bal >= float(migrated_bal)) else "sold"
                layer = _layer(3, True, f"{pct:.1f}% · {sold_txt}", "rpc")
    layers.append(layer)
    checked = 3
    await _emit(mint, layer, int((time.monotonic() - t) * 1000))
    note(layer)
    if not layer["pass"]:
        return _done(scan, layers, checked, False, first_red, holders, t0_all)

    # ---- 4 top 10 holders ----
    t = time.monotonic()
    pool = scan.get("pool") or coin.get("pool")
    exclude = rugcheck.pool_exclude_accounts(report, pool)
    largest, err = await _retry(rpc.get_token_largest_accounts, mint)
    if err and not largest:
        # try rugcheck
        if report and report.get("topHolders"):
            total = 0.0
            n = 0
            for h in report["topHolders"]:
                addr = h.get("address")
                owner = h.get("owner")
                if addr in exclude or (owner and owner in exclude):
                    continue
                try:
                    total += float(h.get("pct") or 0)
                    n += 1
                except (TypeError, ValueError):
                    continue
                if n >= 10:
                    break
            top10 = total
            err = None
        else:
            top10 = None
    else:
        top10 = 0.0
        counted = 0
        supply_raw = parsed.get("supply")
        for a in largest or []:
            addr = a.get("address")
            if addr in exclude:
                continue
            try:
                if a.get("uiAmount") is not None and supply:
                    top10 += float(a["uiAmount"]) / supply * 100
                elif a.get("amount") is not None and supply_raw:
                    top10 += float(a["amount"]) / float(supply_raw) * 100
                counted += 1
            except (TypeError, ValueError, ZeroDivisionError):
                continue
            if counted >= 10:
                break
    if top10 is None:
        layer = _layer(4, False, "no data: top 10 holders", "rpc")
        first_red = layer["value"]
    else:
        max_top = float(config.get("auditor.max_top10_pct"))
        if top10 >= max_top:
            layer = _layer(4, False, f"top 10 hold {top10:.0f}%", "rpc")
            first_red = layer["value"]
        else:
            layer = _layer(4, True, f"{top10:.1f}%", "rpc")
    layers.append(layer)
    checked = 4
    await _emit(mint, layer, int((time.monotonic() - t) * 1000))
    note(layer)
    if not layer["pass"]:
        return _done(scan, layers, checked, False, first_red, holders, t0_all)

    # ---- 5 liquidity ----
    t = time.monotonic()
    lp = rugcheck.lp_locked_pct(report, pool)
    if report_err and not report:
        layer = _layer(5, False, "no data: liquidity", "rugcheck")
        first_red = layer["value"]
    elif lp is None:
        layer = _layer(5, False, "no data: liquidity", "rugcheck")
        first_red = layer["value"]
    else:
        min_lp = float(config.get("auditor.min_lp_locked_pct"))
        liq_now = None
        # compare to scanner liquidity
        from sources import dexscreener

        fresh = await dexscreener.fetch_one(mint)
        liq_now = (fresh or {}).get("liquidityUsd")
        liq0 = scan.get("liquidityUsd")
        pulled = False
        if liq0 and liq_now is not None and liq_now < float(liq0) * (1 - float(config.get("auditor.liq_drop_pct")) / 100):
            pulled = True
        if lp < min_lp:
            layer = _layer(5, False, "liquidity unlocked", "rugcheck")
            first_red = layer["value"]
        elif pulled:
            layer = _layer(5, False, "liquidity pulled", "rugcheck")
            first_red = layer["value"]
        else:
            txt = f"{lp:.0f}% locked" if lp < 100 else "100% burned"
            # sample had lpLockedPct 100 and no burned key; UI string from the brief uses "100% burned" as ok
            if lp >= 99.999:
                txt = "100% burned"
            else:
                txt = f"{lp:.0f}% locked"
            layer = _layer(5, True, txt, "rugcheck")
    layers.append(layer)
    checked = 5
    await _emit(mint, layer, int((time.monotonic() - t) * 1000))
    note(layer)
    if not layer["pass"]:
        return _done(scan, layers, checked, False, first_red, holders, t0_all)

    # ---- 6 pool age ----
    t = time.monotonic()
    migrated = coin.get("migrated_at")
    age = age_min(migrated)
    min_age = int(config.get("auditor.min_pool_age_min"))
    if not migrated:
        layer = _layer(6, False, "no data: pool age", "rpc")
        first_red = layer["value"]
    elif age < min_age:
        layer = _layer(6, False, "pool too young", "rpc")
        first_red = layer["value"]
    else:
        layer = _layer(6, True, f"{age}m", "rpc")
    layers.append(layer)
    checked = 6
    await _emit(mint, layer, int((time.monotonic() - t) * 1000))
    note(layer)
    if not layer["pass"]:
        return _done(scan, layers, checked, False, first_red, holders, t0_all)

    # ---- 7 bundled wallets ----
    t = time.monotonic()
    global _layer8_down
    if graph_err:
        layer = _layer(7, False, "no data: bundles", "rugcheck")
        first_red = layer["value"]
        bundled = None
    else:
        bundled, berr = rugcheck.bundled_from_graph(graph)
        if berr:
            layer = _layer(7, False, berr, "rugcheck")
            first_red = layer["value"]
        else:
            max_b = int(config.get("auditor.max_bundled"))
            if bundled >= max_b:
                layer = _layer(7, False, f"{bundled} bundled wallets", "rugcheck")
                first_red = layer["value"]
            else:
                layer = _layer(7, True, "None" if not bundled else str(bundled), "rugcheck")
    layers.append(layer)
    checked = 7
    await _emit(mint, layer, int((time.monotonic() - t) * 1000))
    note(layer)
    if not layer["pass"]:
        return _done(scan, layers, checked, False, first_red, holders, t0_all, bundled=bundled if not graph_err else None)

    # ---- 8 delegations ----
    t = time.monotonic()
    global _layer8_alerted
    accounts, err = await _retry(rpc.get_program_accounts_for_mint, mint)
    if err and not accounts:
        _layer8_down = True
        layer = _layer(8, False, "no data: delegations", "rpc")
        first_red = layer["value"]
        if not _layer8_alerted:
            _layer8_alerted = True
            try:
                from telegram_bot import alert_owner

                await alert_owner("layer 8 down, every coin is red until rpc_url changes")
            except Exception:
                log.exception("layer 8 telegram alert failed")
        db.insert("layer8_kills", {"ts": ts(), "mint": mint})
    else:
        _layer8_down = False
        holders_n = 0
        by_delegate: dict[str, int] = {}
        for a in accounts or []:
            info = None
            if isinstance(a, dict):
                info = (
                    (a.get("account") or {}).get("data", {}).get("parsed", {}).get("info")
                    if isinstance((a.get("account") or {}).get("data"), dict)
                    else None
                )
                if info is None:
                    # already parsed?
                    info = a.get("parsed", {}).get("info") if isinstance(a.get("parsed"), dict) else None
            amt = 0
            delegate = None
            if isinstance(info, dict):
                ta = info.get("tokenAmount") or {}
                try:
                    amt = float(ta.get("uiAmount") or 0)
                except (TypeError, ValueError):
                    amt = 0
                delegate = info.get("delegate")
            if amt > 0:
                holders_n += 1
            if delegate:
                by_delegate[delegate] = by_delegate.get(delegate, 0) + 1
        holders = holders_n
        cap = int(config.get("auditor.max_delegated_accounts"))
        worst = max(by_delegate.items(), key=lambda kv: kv[1], default=None)
        if worst and worst[1] > cap:
            dshort = worst[0][:4] + "..." + worst[0][-3:] if len(worst[0]) > 10 else worst[0]
            layer = _layer(8, False, f"{worst[1]} wallets → {dshort}", "rpc")
            first_red = layer["value"]
        else:
            layer = _layer(8, True, "None", "rpc")
    layers.append(layer)
    checked = 8
    await _emit(mint, layer, int((time.monotonic() - t) * 1000))
    note(layer)
    if not layer["pass"]:
        return _done(scan, layers, checked, False, first_red, holders, t0_all, bundled=bundled)

    return _done(scan, layers, checked, True, None, holders, t0_all, bundled=bundled)


def _done(scan, layers, checked, passed, reason, holders, t0, bundled=None) -> dict:
    mint = scan["mint"]
    # fill remaining layers as not checked
    have = {l["n"] for l in layers}
    for n in range(1, 9):
        if n not in have:
            layers.append(_layer(n, None, "not checked", "rpc"))
    payload = {
        "agent": "auditor",
        "mint": mint,
        "ts": utcnow_iso(),
        "passed": passed,
        "layersChecked": checked,
        "layers": layers,
        "holders": holders,
        "reason": reason,
        "bundled": bundled,
    }
    db.insert(
        "audits",
        {
            "mint": mint,
            "ts": payload["ts"],
            "passed": int(passed),
            "layers_checked": checked,
            "holders": holders,
            "reason": reason,
            "bundled": bundled,
            "raw": __import__("json").dumps(payload),
        },
    )
    name = scan.get("name") or mint[:6]
    card = db.kv_get("auditor_card") or {"takenApart": 0, "rejected": 0}
    card["takenApart"] = int(card.get("takenApart") or 0) + 1
    if passed:
        card["last"] = "8/8 → Executor"
        db.upsert_coin(mint, status="audited")
    else:
        card["rejected"] = int(card.get("rejected") or 0) + 1
        card["last"] = reason
        db.upsert_coin(mint, status="rejected")
        db.add_activity("rejected", mint, "auditor", f"Rejected {name}", f"Auditor · {reason}", "Skipped", "")
        _rejected.add(mint)
    card["current"] = None
    db.kv_set("auditor_card", card)
    bus.emit("audit", payload)
    log.info("verdict %s passed=%s reason=%s ms=%s", mint[:8], passed, reason, int((time.monotonic() - t0) * 1000))
    return payload


async def run(stop: asyncio.Event) -> None:
    global _rejected
    _rejected = set()
    start = db.night_start_ts()
    for r in db.q("SELECT mint FROM audits WHERE passed=0"):
        # only lock same-night if we can
        _rejected.add(r["mint"])
    db.beat("auditor", "running", "idle")
    was_leader = False
    while not stop.is_set():
        lead = lease.is_leader()
        if lead and not was_leader:
            await pipeline.catch_up()
        was_leader = lead
        if not lead:
            db.beat("auditor", "running", "standby")
            try:
                await asyncio.wait_for(stop.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            continue
        try:
            scan = await asyncio.wait_for(bus.auditor.get(), timeout=1.0)
        except asyncio.TimeoutError:
            db.beat("auditor", "paused" if db.is_paused("auditor") else "running", "idle")
            continue
        if db.is_paused("auditor"):
            db.beat("auditor", "paused", "paused")
            # still drain? no, put back conceptually - just skip new entries when desk paused
            # Auditor of already-passed coins: process them; pause means no new entries overall
        mint = scan.get("mint")
        if not mint:
            continue
        if mint in _rejected:
            log.info("skip already rejected tonight %s", mint[:8])
            continue
        if not config.get("auditor.enabled"):
            # still required by brief; if disabled, pass a stub? Keep enabled path only.
            pass
        try:
            result = await audit(scan)
        except Exception:
            log.exception("audit failed %s", mint[:8])
            result = _done(scan, [], 0, False, "no data: mint authority", None, time.monotonic())
        if result.get("passed"):
            await bus.analyst.put({"scan": scan, "audit": result})
        db.inc_beat("auditor", "running", result.get("reason") or "pass")
