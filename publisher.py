
"""Plan B JSON writer. Local files every 15s. Pages at most every 6-10 min."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import config
import db
import links
import paths
from util import ts

log = logging.getLogger("publisher")

SECRET_KEYS = ("TYPESAFE_API_KEY", "TG_TOKEN", "DATA_PATH", "VAPID_PRIVATE_KEY", "APP_TOKEN")


def _scrub(obj):
    """Refuse a snapshot that would leak a secret value."""
    blob = json.dumps(obj, default=str)
    for name in SECRET_KEYS:
        val = os.environ.get(name)
        if val and len(val) >= 8 and val in blob:
            raise RuntimeError(f"snapshot refused: would leak {name}")
    # also refuse if DATA_PATH from secrets file is in the blob as a standalone path
    try:
        dp = paths.data_path()
        if dp and dp in blob:
            raise RuntimeError("snapshot refused: would leak data path")
    except RuntimeError as e:
        if "would leak" in str(e):
            raise
    return obj


def _write(dir_path: Path, name: str, obj) -> None:
    dir_path.mkdir(parents=True, exist_ok=True)
    dest = dir_path / name
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    tmp.write_text(json.dumps(_scrub(obj), default=str, separators=(",", ":")))
    tmp.replace(dest)


def snapshot() -> dict:
    mode = config.get("desk.mode")
    pnl, pct = db.today_pnl()
    bal = config.stack()
    agents = []
    for name in ("scanner", "auditor", "analyst", "executor", "risk"):
        hb = db.q1("SELECT * FROM heartbeats WHERE name=?", (name,))
        card = db.kv_get(f"{name}_card") or {}

        if name == "scanner":
            waiting = db.q1("SELECT COUNT(*) AS n FROM waiting")
            feed = []
            for r in db.q("SELECT * FROM scans ORDER BY id DESC LIMIT 6"):
                feed.append({
                    "mint": r["mint"], "name": r["name"], "symbol": r["symbol"],
                    "verdict": r["verdict"], "reason": r["reason"],
                    "liq": r["liquidity_usd"], "liquidityUsd": r["liquidity_usd"],
                })
            card = dict(card or {})
            card.setdefault("feed", feed)
            card.setdefault("pools", int(waiting["n"]) if waiting else 0)
        agents.append(
            {
                "name": name,
                "status": (hb["status"] if hb else "down"),
                "count": (hb["count"] if hb else 0),
                "subtitle": (hb["detail"] if hb else ""),
                "card": card,
            }
        )
    positions = []
    for p in db.open_positions():
        positions.append(
            {
                "id": p["id"],
                "mint": p["mint"],
                "name": p.get("name"),
                "sizeUsd": p.get("size_usd"),
                "entryPrice": p.get("entry_price"),
                "lastPrice": p.get("last_price"),
                "peakPrice": p.get("peak_price"),
                "stopPrice": p.get("stop_price"),
                "trailPrice": p.get("trail_price"),
                "trailActive": bool(p.get("trail_active")),
                "pnlUsd": _open_pnl(p),
                "pnlPct": _open_pct(p),
                "mode": p.get("mode"),
                "status": p.get("status"),
                "coinUrl": p.get("link") or links.coin_url(p["mint"], "solana"),
                "openedAt": p.get("opened_at"),
            }
        )
    coins = []
    for r in db.q("SELECT * FROM coins ORDER BY first_seen DESC LIMIT 80"):
        coins.append(
            {
                "mint": r["mint"],
                "name": r["name"],
                "symbol": r["symbol"],
                "status": r["status"],
                "pool": r["pool"],
            }
        )
    ticket = config.ticket_check()
    start_bal = (
        float(config.get("desk.paper_start_stack"))
        if mode == "PAPER"
        else float(config.get("desk.live_stack") or 0)
    )
    multiplier = round(bal / start_bal, 2) if start_bal else 1.0
    running = sum(1 for a in agents if a.get("status") == "running")
    act_rows = db.activity_rows(80)
    latest_act = act_rows[0]["id"] if act_rows else 0
    telegram_on = Path(paths.ROOT / "owner_chat_id").is_file() or bool(os.environ.get("OWNER_CHAT_ID"))
    state = {
        "balance": bal,
        "todayPct": pct,
        "todayUsd": pnl,
        "multiplier": multiplier,
        "nightStartBalance": start_bal,
        "mode": mode,
        "paused": bool(config.get("desk.paused")),
        "nightStart": db.night_start_ts(),
        "online": True,
        "updatedAt": ts(),
        "unread": 0,
        "latestActivityId": latest_act,
        "handle": config.get("desk.fomo_handle"),
        "agents": agents,
        "agentsRunning": running,
        "positions": positions,
        "coins": coins,
        "ticketWarning": ticket.get("message"),
        "stopPct": config.get("risk.stop_pct"),
        "trailPct": config.get("risk.trail_pct"),
        "stackPct": config.get("executor.stack_pct"),
        "telegramConnected": telegram_on,
    }
    ranges = {name: db.chart_range(name) for name in ("LIVE", "1H", "4H", "1D", "1W", "ALL")}
    live = ranges["LIVE"]
    chart = {
        "points": live["points"],
        "start": live["start"],
        "marks": live["marks"],
        "range": "LIVE",
        "ranges": ranges,
    }
    activity = {
        "rows": [
            {
                "id": r["id"],
                "ts": r["ts"],
                "kind": r["kind"],
                "mint": r["mint"],
                "agent": r["agent"],
                "title": r["title"],
                "subtitle": r["subtitle"],
                "value": r["value"],
                "value2": r["value2"],
                "mode": r["mode"],
            }
            for r in act_rows
        ]
    }
    handle = str(config.get("desk.fomo_handle") or "")
    account = {
        "mode": mode,
        "stack": bal,
        "paper_start_stack": config.get("desk.paper_start_stack"),
        "stack_live": config.get("desk.live_stack"),
        "fomo_handle": handle,
        "profile_url": links.profile_url(handle) if handle else None,
        "signup_url": links.SIGNUP_URL,
        "notifier": "telegram",
        "webpush": False,
        "telegramConnected": telegram_on,
        "readonly": True,
        "pushNote": "No web push on plan B. Telegram is the only notifier. Settings, pause and mode changes go through Telegram.",
        "ticket_warning": ticket.get("message"),
        "analyst_mode": config.get("analyst.mode"),
        "paused": bool(config.get("desk.paused")),
        "settings": config.public_settings(),
        "ts": ts(),
    }
    agents_doc = {
        name: {"rows": _with_urls(db.decisions(name, 30))}
        for name in ("scanner", "auditor", "analyst", "executor", "risk")
    }
    return {
        "state": state,
        "chart": chart,
        "activity": activity,
        "account": account,
        "agents": agents_doc,
    }


def _with_urls(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        item = dict(r)
        if r.get("mint"):
            item["coinUrl"] = links.coin_url(r["mint"], "solana")
            c = db.coin(r["mint"]) or {}
            item.setdefault("name", c.get("name"))
            item.setdefault("symbol", c.get("symbol"))
        out.append(item)
    return out


def _open_pnl(p) -> float | None:
    if not p.get("last_price") or not p.get("entry_price"):
        return None
    return round((p["last_price"] / p["entry_price"] - 1) * (p.get("size_usd") or 0), 2)


def _open_pct(p) -> float | None:
    if not p.get("last_price") or not p.get("entry_price"):
        return None
    return round((p["last_price"] / p["entry_price"] - 1) * 100, 2)


def write_local() -> Path:
    root = paths.local_data_dir()
    snap = snapshot()
    _write(root, "state.json", snap["state"])
    _write(root, "chart.json", snap["chart"])
    chart_dir = root / "chart"
    for name, doc in (snap["chart"].get("ranges") or {}).items():
        _write(chart_dir, f"{name}.json", doc)
    _write(root, "activity.json", snap["activity"])
    _write(root, "account.json", snap["account"])
    _write(root, "agents.json", snap["agents"])
    agents_dir = root / "agents"
    for name, doc in snap["agents"].items():
        d = agents_dir / name
        _write(d, "decisions.json", doc)
    # per-coin and audit
    coin_dir = root / "coin"
    audit_dir = root / "audit"
    mints = {r["mint"] for r in db.q("SELECT mint FROM coins ORDER BY first_seen DESC LIMIT 80")}
    for p in db.open_positions():
        mints.add(p["mint"])
    for mint in mints:
        _write(coin_dir, f"{mint}.json", coin_doc(mint))
        audit = db.latest_audit(mint)
        if audit:
            c = db.coin(mint) or {}
            audit = dict(audit)
            audit["name"] = c.get("name")
            audit["symbol"] = c.get("symbol")
            audit["coinUrl"] = links.coin_url(mint, "solana")
            audit["enabled"] = bool(config.get("auditor.enabled"))
            sig = db.q1("SELECT size_usd FROM signals WHERE mint=? ORDER BY id DESC LIMIT 1", (mint,))
            if sig:
                audit["sizeUsd"] = sig["size_usd"]
            _write(audit_dir, f"{mint}.json", audit)
    # no index in d/ or in the secret folder
    return root


def coin_doc(mint: str) -> dict:
    c = db.coin(mint) or {}
    pos_row = db.q1(
        "SELECT * FROM positions WHERE mint=? AND status IN ('open','exiting') ORDER BY id DESC LIMIT 1",
        (mint,),
    )
    pos = None
    exit_line = None
    if pos_row:
        p = dict(pos_row)
        if p.get("trail_active") and p.get("trail_price"):
            exit_line = p["trail_price"]
        else:
            exit_line = p.get("stop_price")
        pos = {
            "sizeUsd": p.get("size_usd"),
            "entryPrice": p.get("entry_price"),
            "stopPrice": p.get("stop_price"),
            "trailPrice": p.get("trail_price"),
            "trailActive": bool(p.get("trail_active")),
            "timeoutAt": p.get("timeout_at"),
            "pnlUsd": _open_pnl(p),
            "pnlPct": _open_pct(p),
            "mode": p.get("mode"),
        }
    candles = db.ohlc_candles(mint, limit=400)
    audit = db.latest_audit(mint)
    scan = db.latest_scan(mint) or {}
    shield = None
    layers_checked = None
    if audit and audit.get("layers"):
        layers = audit["layers"]
        passed = sum(1 for l in layers if l.get("pass") is True)
        layers_checked = audit.get("layersChecked") or len([l for l in layers if l.get("pass") is not None])
        if layers_checked:
            shield = f"{passed}/{layers_checked}"
    latest_price = candles[-1][4] if candles else c.get("last_price")
    holders = None
    if audit and audit.get("holders") is not None:
        holders = audit.get("holders")
    # layer 8 is the holders / delegations count when present
    if holders is None and audit and audit.get("layers"):
        for layer in audit["layers"]:
            if layer.get("n") == 8 and layer.get("holders") is not None:
                holders = layer.get("holders")
    market_cap = c.get("market_cap")
    liquidity = c.get("liquidity_usd")
    if liquidity is None:
        liquidity = scan.get("liquidityUsd")
    volume24h = c.get("volume_24h")
    sig = db.q1("SELECT size_usd FROM signals WHERE mint=? ORDER BY id DESC LIMIT 1", (mint,))
    return {
        "mint": mint,
        "name": c.get("name"),
        "symbol": c.get("symbol"),
        "image": c.get("image"),
        "chain": "solana",
        "migratedAt": c.get("migrated_at"),
        "ageH": ((ts() - (c.get("migrated_at") or ts())) / 3600) if c.get("migrated_at") else None,
        "price": latest_price,
        "sinceEntryPct": pos["pnlPct"] if pos else None,
        "shield": shield,
        "layersChecked": layers_checked,
        "auditPassed": bool(audit.get("passed")) if audit else None,
        "marketCap": market_cap,
        "liquidity": liquidity,
        "volume24h": volume24h,
        "holders": holders,
        "candles": candles,
        "position": pos,
        "exitLine": exit_line,
        "coinUrl": links.coin_url(mint, "solana"),
        "status": c.get("status"),
        "sizeUsd": (sig["size_usd"] if sig else None),
    }


def publish_pages() -> bool:
    """Orphan gh-pages force-push of web/ + d/{DATA_PATH}/ as a single commit.

    main never contains DATA_PATH. The deploy branch of a public repo does.
    """
    if os.environ.get("PAGES_PUSH", "1") in ("0", "false", "no"):
        log.info("PAGES_PUSH disabled")
        return False
    repo_dir = paths.ROOT
    web = paths.WEB_DIR
    if not (web / "index.html").is_file():
        log.warning("web/index.html missing; skip pages push")
        return False
    data = paths.local_data_dir()
    # build a temp tree without echoing the secret path
    tmp = Path(tempfile.mkdtemp(prefix="desk-pages-"))
    try:
        for item in web.iterdir():
            dest = tmp / item.name
            if item.is_dir():
                shutil.copytree(item, dest, dirs_exist_ok=True)
            else:
                shutil.copy2(item, dest)
        # copy data under d/<DATA_PATH> — do not write an index in d/
        droot = tmp / "d"
        droot.mkdir(exist_ok=True)
        # no index.html in d/
        secret_name = data.name
        shutil.copytree(data, droot / secret_name, dirs_exist_ok=True)
        (tmp / ".nojekyll").write_text("")
        # 404 at root (already in web/ if present)
        git = ["git", "-C", str(tmp)]
        subprocess.run(["git", "init", "-q", str(tmp)], check=True, capture_output=True)
        subprocess.run(git + ["checkout", "-q", "-b", "gh-pages"], check=True, capture_output=True)
        subprocess.run(git + ["add", "-A"], check=True, capture_output=True)
        env = os.environ.copy()
        env["GIT_AUTHOR_NAME"] = env.get("GIT_AUTHOR_NAME", "fomo-desk")
        env["GIT_AUTHOR_EMAIL"] = env.get("GIT_AUTHOR_EMAIL", "desk@local")
        env["GIT_COMMITTER_NAME"] = env["GIT_AUTHOR_NAME"]
        env["GIT_COMMITTER_EMAIL"] = env["GIT_AUTHOR_EMAIL"]
        subprocess.run(
            git + ["commit", "-q", "-m", "publish desk data"],
            check=True,
            capture_output=True,
            env=env,
        )
        # remote from the main checkout
        remote = subprocess.check_output(
            ["git", "-C", str(repo_dir), "remote", "get-url", "origin"],
            text=True,
        ).strip()
        subprocess.run(git + ["remote", "add", "origin", remote], check=True, capture_output=True)
        r = subprocess.run(
            git + ["push", "-q", "--force", "origin", "gh-pages"],
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            log.warning("pages push failed")
            return False
        log.info("pages published")
        return True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
