
"""SQLite WAL store for the desk."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

import config
import paths
from util import local_now, ts

_local = threading.local()
_write_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS change_log (
  id INTEGER PRIMARY KEY,
  ts REAL NOT NULL,
  key TEXT NOT NULL,
  old TEXT,
  new TEXT
);
CREATE TABLE IF NOT EXISTS nights (
  id INTEGER PRIMARY KEY,
  started_at REAL NOT NULL,
  ended_at REAL,
  mode TEXT,
  note TEXT
);
CREATE TABLE IF NOT EXISTS coins (
  mint TEXT PRIMARY KEY,
  name TEXT,
  symbol TEXT,
  pool TEXT,
  signature TEXT,
  creator TEXT,
  creator_balance REAL,
  creator_balance_pct REAL,
  migrated_at REAL,
  first_seen REAL,
  image TEXT,
  status TEXT,
  night_id INTEGER,
  market_cap REAL,
  liquidity_usd REAL,
  volume_24h REAL,
  last_price REAL
);
CREATE TABLE IF NOT EXISTS waiting (
  mint TEXT PRIMARY KEY,
  entered_at REAL NOT NULL,
  last_poll REAL
);
CREATE TABLE IF NOT EXISTS prices (
  id INTEGER PRIMARY KEY,
  mint TEXT NOT NULL,
  ts REAL NOT NULL,
  price REAL,
  volume_m5 REAL
);
CREATE INDEX IF NOT EXISTS idx_prices_mint_ts ON prices(mint, ts);
CREATE TABLE IF NOT EXISTS scans (
  id INTEGER PRIMARY KEY,
  mint TEXT, name TEXT, symbol TEXT, pool TEXT,
  liquidity_usd REAL, top10_pct REAL, h1_change_pct REAL, age_min INTEGER,
  verdict TEXT, reason TEXT, ts TEXT, raw TEXT
);
CREATE TABLE IF NOT EXISTS audits (
  id INTEGER PRIMARY KEY,
  mint TEXT, ts TEXT, passed INTEGER, layers_checked INTEGER,
  holders INTEGER, reason TEXT, bundled INTEGER, raw TEXT
);
CREATE TABLE IF NOT EXISTS audit_layers (
  id INTEGER PRIMARY KEY,
  audit_id INTEGER, mint TEXT, n INTEGER, name TEXT,
  pass INTEGER, value TEXT, source TEXT, ts REAL, ms INTEGER
);
CREATE TABLE IF NOT EXISTS verdicts (
  id INTEGER PRIMARY KEY,
  mint TEXT, ts TEXT, score INTEGER, passed INTEGER,
  alive_6h REAL, organic REAL, flush REAL, chase REAL,
  failed TEXT, model TEXT, input_tokens INTEGER, state_hash TEXT, raw TEXT
);
CREATE TABLE IF NOT EXISTS signals (
  id INTEGER PRIMARY KEY,
  mint TEXT, ts REAL, price REAL, size_usd REAL, stack_pct REAL,
  link TEXT, mode TEXT, status TEXT, reason TEXT
);
CREATE TABLE IF NOT EXISTS skips (
  mint TEXT PRIMARY KEY,
  reason TEXT,
  until REAL,
  ts REAL
);
CREATE TABLE IF NOT EXISTS positions (
  id INTEGER PRIMARY KEY,
  mint TEXT, name TEXT, opened_at REAL, entry_price REAL, size_usd REAL,
  peak_price REAL, stop_pct REAL, stop_price REAL, trail_active INTEGER,
  trail_price REAL, timeout_at REAL, last_price REAL, no_price_polls INTEGER,
  mode TEXT, status TEXT, exit_price REAL, exit_reason TEXT, exit_ts REAL,
  pnl_usd REAL, pnl_pct REAL, fees_usd REAL, link TEXT, signal_id INTEGER
);
CREATE TABLE IF NOT EXISTS activity (
  id INTEGER PRIMARY KEY,
  ts REAL, kind TEXT, mint TEXT, agent TEXT,
  title TEXT, subtitle TEXT, value TEXT, value2 TEXT, mode TEXT
);
CREATE TABLE IF NOT EXISTS heartbeats (
  name TEXT PRIMARY KEY,
  ts REAL, status TEXT, detail TEXT, count INTEGER
);
CREATE TABLE IF NOT EXISTS kv (
  key TEXT PRIMARY KEY,
  value TEXT
);
CREATE TABLE IF NOT EXISTS layer8_kills (
  id INTEGER PRIMARY KEY,
  ts REAL, mint TEXT
);
CREATE TABLE IF NOT EXISTS action_claims (
  key TEXT PRIMARY KEY,
  ts REAL NOT NULL,
  kind TEXT,
  instance INTEGER
);
"""


def _connect() -> sqlite3.Connection:
    path = Path(paths.DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None, timeout=5.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA busy_timeout=5000")
    con.execute("PRAGMA foreign_keys=ON")
    return con


def conn() -> sqlite3.Connection:
    c = getattr(_local, "con", None)
    if c is None:
        c = _connect()
        _local.con = c
    return c


def init() -> None:
    c = conn()
    with _write_lock:
        c.executescript(SCHEMA)
    _migrate()
    rows = {r["key"]: r["value"] for r in c.execute("SELECT key, value FROM settings")}
    config.load(rows)


def q(sql: str, args: tuple = ()) -> list[sqlite3.Row]:
    return list(conn().execute(sql, args))


def q1(sql: str, args: tuple = ()) -> sqlite3.Row | None:
    return conn().execute(sql, args).fetchone()


def execute(sql: str, args: tuple = ()) -> sqlite3.Cursor:
    with _write_lock:
        return conn().execute(sql, args)


def insert(table: str, data: dict) -> int:
    cols = ", ".join(data.keys())
    ph = ", ".join("?" * len(data))
    cur = execute(f"INSERT INTO {table} ({cols}) VALUES ({ph})", tuple(data.values()))
    return int(cur.lastrowid)


def update(table: str, row_id: int, data: dict) -> None:
    if not data:
        return
    sets = ", ".join(f"{k}=?" for k in data)
    execute(f"UPDATE {table} SET {sets} WHERE id=?", (*data.values(), row_id))


def now() -> float:
    return ts()


def save_setting(key: str, value: Any, old: Any = None) -> None:
    execute(
        "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, json.dumps(value)),
    )
    execute(
        "INSERT INTO change_log(ts,key,old,new) VALUES(?,?,?,?)",
        (now(), key, json.dumps(old), json.dumps(value)),
    )


def kv_get(key: str, default: Any = None) -> Any:
    row = q1("SELECT value FROM kv WHERE key=?", (key,))
    if not row:
        return default
    try:
        return json.loads(row["value"])
    except Exception:
        return default


def kv_set(key: str, value: Any) -> None:
    execute(
        "INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, json.dumps(value)),
    )


def beat(name: str, status: str = "running", detail: str = "", count: int | None = None) -> None:
    # File stamp first so a hung sqlite write cannot hide a live process
    # from the supervisor, and so a hang after this still goes stale quickly.
    try:
        import stamps
        stamps.stamp_agent(name)
    except Exception:
        pass
    try:
        import lease
        if lease.enabled() and not lease.is_leader():
            name = f"{name}:{lease.instance_id()}"
    except Exception:
        pass
    if count is None:
        row = q1("SELECT count FROM heartbeats WHERE name=?", (name,))
        count = int(row["count"]) if row else 0
    execute(
        "INSERT INTO heartbeats(name,ts,status,detail,count) VALUES(?,?,?,?,?) "
        "ON CONFLICT(name) DO UPDATE SET ts=excluded.ts, status=excluded.status, "
        "detail=excluded.detail, count=excluded.count",
        (name, now(), status, detail, count),
    )


def claim_action(kind: str, key: str) -> bool:
    """Return True if this process is the first to claim kind+key."""
    token = f"{kind}:{key}"
    try:
        import lease
        inst = lease.instance_id()
    except Exception:
        inst = 0
    try:
        execute(
            "INSERT INTO action_claims(key,ts,kind,instance) VALUES(?,?,?,?)",
            (token, now(), kind, inst),
        )
        return True
    except sqlite3.IntegrityError:
        return False


def inc_beat(name: str, status: str = "running", detail: str = "") -> None:
    row = q1("SELECT count FROM heartbeats WHERE name=?", (name,))
    n = (int(row["count"]) if row else 0) + 1
    beat(name, status, detail, n)


def is_paused(name: str | None = None) -> bool:
    # Risk cannot be paused while a position is open.
    if name == "risk" and open_positions():
        return False
    return bool(config.get("desk.paused"))


def add_activity(
    kind: str,
    mint: str | None,
    agent: str,
    title: str,
    subtitle: str,
    value: str = "",
    value2: str = "",
    mode: str | None = None,
) -> int:
    return insert(
        "activity",
        {
            "ts": now(),
            "kind": kind,
            "mint": mint,
            "agent": agent,
            "title": title,
            "subtitle": subtitle,
            "value": value,
            "value2": value2,
            "mode": mode or config.get("desk.mode"),
        },
    )


def activity_rows(limit: int = 80) -> list[dict]:
    rows = q("SELECT * FROM activity ORDER BY id DESC LIMIT ?", (limit,))
    return [dict(r) for r in rows]


def coin(mint: str) -> dict | None:
    row = q1("SELECT * FROM coins WHERE mint=?", (mint,))
    return dict(row) if row else None


def upsert_coin(mint: str, **fields: Any) -> None:
    existing = coin(mint)
    if not existing:
        data = {"mint": mint, "first_seen": now(), **fields}
        cols = ", ".join(data)
        ph = ", ".join("?" * len(data))
        execute(f"INSERT INTO coins ({cols}) VALUES ({ph})", tuple(data.values()))
        return
    if fields:
        sets = ", ".join(f"{k}=?" for k in fields)
        execute(f"UPDATE coins SET {sets} WHERE mint=?", (*fields.values(), mint))


def add_price(mint: str, price: float | None, volume_m5: float | None = None, when: float | None = None) -> None:
    if price is None:
        return
    execute(
        "INSERT INTO prices(mint,ts,price,volume_m5) VALUES(?,?,?,?)",
        (mint, when or now(), price, volume_m5),
    )


def prices_for(mint: str, since: float | None = None, limit: int = 1500) -> list[dict]:
    if since is None:
        rows = q(
            "SELECT ts, price, volume_m5 FROM prices WHERE mint=? ORDER BY ts DESC LIMIT ?",
            (mint, limit),
        )
    else:
        rows = q(
            "SELECT ts, price, volume_m5 FROM prices WHERE mint=? AND ts>=? ORDER BY ts",
            (mint, since),
        )
        return [dict(r) for r in rows]
    out = [dict(r) for r in rows]
    out.reverse()
    return out


def current_night() -> dict | None:
    row = q1("SELECT * FROM nights WHERE ended_at IS NULL ORDER BY id DESC LIMIT 1")
    return dict(row) if row else None


def night_start_ts() -> float:
    n = current_night()
    return float(n["started_at"]) if n else now() - 16 * 3600


def start_night(mode: str, note: str | None = None) -> int:
    open_n = current_night()
    if open_n:
        return int(open_n["id"])
    return insert("nights", {"started_at": now(), "mode": mode, "note": note})


def end_night() -> None:
    n = current_night()
    if n:
        update("nights", int(n["id"]), {"ended_at": now()})


def seen_tonight(mint: str, table: str) -> bool:
    start = night_start_ts()
    if table == "scans":
        row = q1("SELECT 1 AS x FROM scans WHERE mint=? AND ts>=?", (mint, _iso_or_ts_since(start)))
        # scans.ts is ISO; also check coins
    row = q1("SELECT 1 AS x FROM coins WHERE mint=? AND first_seen>=?", (mint, start))
    return row is not None


def _iso_or_ts_since(start: float) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(start, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def passed_tonight(mint: str) -> bool:
    start = night_start_ts()
    row = q1(
        "SELECT 1 AS x FROM scans WHERE mint=? AND verdict='pass' AND id IN "
        "(SELECT id FROM scans WHERE mint=?)",
        (mint, mint),
    )
    # Use first_seen / a pass flag on coins
    c = coin(mint)
    if c and c.get("status") == "passed" and (c.get("first_seen") or 0) >= start:
        return True
    row = q1("SELECT 1 AS x FROM scans WHERE mint=? AND verdict='pass'", (mint,))
    return row is not None and (c is None or (c.get("first_seen") or 0) >= start - 86400)


def rejected_tonight(mint: str) -> bool:
    start = night_start_ts()
    row = q1(
        "SELECT 1 AS x FROM audits WHERE mint=? AND passed=0",
        (mint,),
    )
    c = coin(mint)
    return row is not None and (not c or (c.get("first_seen") or now()) >= start - 12 * 3600)


def open_positions() -> list[dict]:
    return [dict(r) for r in q("SELECT * FROM positions WHERE status IN ('open','exiting')")]


def money_in_open() -> float:
    return float(sum((p.get("size_usd") or 0) for p in open_positions()))


def realized_pnl(mode: str) -> float:
    row = q1(
        "SELECT COALESCE(SUM(pnl_usd),0) AS n FROM positions WHERE status='closed' AND mode=?",
        (mode,),
    )
    return float(row["n"]) if row else 0.0


def paper_balance() -> float:
    start = float(config.get("desk.paper_start_stack"))
    return round(start + realized_pnl("PAPER"), 2)


def today_pnl() -> tuple[float, float]:
    """(usd, pct of start stack) since night start."""
    start = night_start_ts()
    row = q1(
        "SELECT COALESCE(SUM(pnl_usd),0) AS n FROM positions WHERE status='closed' AND exit_ts>=?",
        (start,),
    )
    usd = float(row["n"]) if row else 0.0
    # mark open
    for p in open_positions():
        if p.get("last_price") and p.get("entry_price"):
            usd += (p["last_price"] / p["entry_price"] - 1) * (p.get("size_usd") or 0)
            usd -= p.get("fees_usd") or 0
    base = paper_balance() - usd if config.get("desk.mode") == "PAPER" else float(config.get("desk.live_stack") or 0)
    # simpler: pct vs start stack
    start_stack = (
        float(config.get("desk.paper_start_stack"))
        if config.get("desk.mode") == "PAPER"
        else float(config.get("desk.live_stack") or 0)
    )
    pct = (usd / start_stack * 100) if start_stack else 0.0
    return round(usd, 2), round(pct, 2)


def runners_today() -> list[dict]:
    """Coins seen today with h24 change >= runner threshold. Filled by Scanner."""
    return kv_get("runners_today", []) or []


def set_runners_today(rows: list[dict]) -> None:
    kv_set("runners_today", rows)


def add_scan(payload: dict) -> int:
    return insert(
        "scans",
        {
            "mint": payload.get("mint"),
            "name": payload.get("name"),
            "symbol": payload.get("symbol"),
            "pool": payload.get("pool"),
            "liquidity_usd": payload.get("liquidityUsd"),
            "top10_pct": payload.get("top10Pct"),
            "h1_change_pct": payload.get("h1ChangePct"),
            "age_min": payload.get("ageMin"),
            "verdict": payload.get("verdict"),
            "reason": payload.get("reason"),
            "ts": payload.get("ts"),
            "raw": json.dumps(payload),
        },
    )


def latest_audit(mint: str) -> dict | None:
    row = q1("SELECT * FROM audits WHERE mint=? ORDER BY id DESC LIMIT 1", (mint,))
    if not row:
        return None
    d = dict(row)
    if d.get("raw"):
        try:
            return json.loads(d["raw"])
        except Exception:
            pass
    return d


def latest_scan(mint: str) -> dict | None:
    row = q1("SELECT * FROM scans WHERE mint=? ORDER BY id DESC LIMIT 1", (mint,))
    if not row:
        return None
    if row["raw"]:
        try:
            return json.loads(row["raw"])
        except Exception:
            pass
    return dict(row)


def latest_verdict(mint: str) -> dict | None:
    row = q1("SELECT * FROM verdicts WHERE mint=? ORDER BY id DESC LIMIT 1", (mint,))
    if not row:
        return None
    if row["raw"]:
        try:
            return json.loads(row["raw"])
        except Exception:
            pass
    return dict(row)


def _migrate() -> None:
    """Add columns introduced after the first night without breaking existing DBs."""
    cols = {r["name"] for r in q("PRAGMA table_info(coins)")}
    for name, typ in (
        ("market_cap", "REAL"),
        ("liquidity_usd", "REAL"),
        ("volume_24h", "REAL"),
        ("last_price", "REAL"),
    ):
        if name not in cols:
            execute(f"ALTER TABLE coins ADD COLUMN {name} {typ}")


def update_market(mint: str, pair: dict | None) -> None:
    """Persist DexScreener market stats for the Coin screen."""
    if not mint or not pair:
        return
    fields: dict[str, Any] = {}
    if pair.get("marketCap") is not None:
        fields["market_cap"] = pair["marketCap"]
    if pair.get("liquidityUsd") is not None:
        fields["liquidity_usd"] = pair["liquidityUsd"]
    vol = pair.get("volume") or {}
    if isinstance(vol, dict) and vol.get("h24") is not None:
        fields["volume_24h"] = vol["h24"]
    if pair.get("priceUsd") is not None:
        fields["last_price"] = pair["priceUsd"]
    if pair.get("name"):
        fields["name"] = pair["name"]
    if pair.get("symbol"):
        fields["symbol"] = pair["symbol"]
    if pair.get("image"):
        fields["image"] = pair["image"]
    pool = pair.get("pool") or pair.get("pairAddress")
    if pool:
        fields["pool"] = pool
    if fields:
        upsert_coin(mint, **fields)


def ohlc_candles(mint: str, limit: int = 400) -> list[list]:
    """1-minute OHLC from db.prices. Volume bar = change of DexScreener volume.m5."""
    rows = prices_for(mint, limit=8000)
    if not rows:
        return []
    buckets: dict[int, list] = {}
    order: list[int] = []
    last_m5 = None
    for r in rows:
        if r.get("price") is None:
            continue
        minute = int(float(r["ts"]) // 60) * 60
        px = float(r["price"])
        m5 = r.get("volume_m5")
        m5 = float(m5) if m5 is not None else None
        if minute not in buckets:
            delta = 0.0
            if m5 is not None and last_m5 is not None:
                delta = m5 - last_m5
                if delta < 0:
                    delta = m5  # rolling window reset
            buckets[minute] = [minute, px, px, px, px, max(0.0, delta)]
            order.append(minute)
        else:
            b = buckets[minute]
            b[2] = max(b[2], px)
            b[3] = min(b[3], px)
            b[4] = px
            if m5 is not None and last_m5 is not None:
                d = m5 - last_m5
                b[5] += max(0.0, d if d >= 0 else m5)
        if m5 is not None:
            last_m5 = m5
    out = [buckets[k] for k in order]
    return out[-limit:]


def _start_stack() -> float:
    if config.get("desk.mode") == "LIVE":
        return float(config.get("desk.live_stack") or config.get("desk.paper_start_stack"))
    return float(config.get("desk.paper_start_stack"))


def equity_series() -> tuple[list[list], list[dict], float]:
    """Full equity curve from the first night (or now) plus trade marks."""
    start_stack = _start_stack()
    first = q1("SELECT MIN(started_at) AS t FROM nights")
    origin = float(first["t"]) if first and first["t"] else night_start_ts()
    rows = q(
        "SELECT opened_at AS t, size_usd AS v, 'buy' AS k, mint, name, stop_pct, exit_reason, pnl_usd "
        "FROM positions WHERE opened_at IS NOT NULL "
        "UNION ALL "
        "SELECT exit_ts AS t, pnl_usd AS v, COALESCE(exit_reason, 'exit') AS k, mint, name, stop_pct, exit_reason, pnl_usd "
        "FROM positions WHERE status='closed' AND exit_ts IS NOT NULL "
        "ORDER BY t"
    )
    pts = [[origin, start_stack]]
    equity = start_stack
    marks: list[dict] = []
    for r in rows:
        if r["t"] is None:
            continue
        name = r["name"] or ""
        if r["k"] == "buy":
            marks.append({"ts": r["t"], "kind": "buy", "label": f"Bought {name}".strip(), "mint": r["mint"]})
        else:
            val = float(r["v"] or 0)
            equity += val
            pts.append([r["t"], round(equity, 2)])
            stop = r["stop_pct"]
            reason = (r["exit_reason"] or r["k"] or "")
            if val < 0 and "trail" not in str(reason).lower() and stop is not None:
                label = f"-{float(stop):g}% · -${abs(val):.2f}"
                kind = "loss"
            elif val >= 0:
                label = f"+${val:.2f}"
                kind = "win"
            else:
                label = f"-${abs(val):.2f}"
                kind = "loss"
            marks.append({"ts": r["t"], "kind": kind, "label": label, "mint": r["mint"], "reason": reason})
    now_v = round(paper_balance() if config.get("desk.mode") == "PAPER" else start_stack + realized_pnl("LIVE"), 2)
    pts.append([now(), now_v])
    return pts, marks, start_stack


def _value_at(pts: list[list], t: float, fallback: float) -> float:
    if not pts:
        return fallback
    if t <= pts[0][0]:
        return float(pts[0][1])
    prev = pts[0]
    for p in pts[1:]:
        if p[0] >= t:
            if p[0] == prev[0]:
                return float(p[1])
            k = (t - prev[0]) / (p[0] - prev[0])
            return float(prev[1]) + (float(p[1]) - float(prev[1])) * k
        prev = p
    return float(pts[-1][1])


RANGE_SECS = {"1H": 3600, "4H": 14400, "1D": 86400, "1W": 604800}


def chart_range(name: str) -> dict:
    """Slice the equity series for LIVE | 1H | 4H | 1D | 1W | ALL."""
    pts, marks, start_stack = equity_series()
    now_t = now()
    name = (name or "LIVE").upper()
    if name == "ALL":
        t0 = pts[0][0] if pts else now_t - 1
        start = float(pts[0][1]) if pts else start_stack
    elif name == "LIVE" or name not in RANGE_SECS:
        name = "LIVE"
        t0 = night_start_ts()
        start = start_stack
    else:
        t0 = now_t - RANGE_SECS[name]
        start = _value_at(pts, t0, start_stack)
    sliced = [p for p in pts if p[0] >= t0]
    if not sliced or sliced[0][0] > t0:
        sliced = [[t0, round(start, 2)]] + sliced
    if not sliced or sliced[-1][0] < now_t:
        last = sliced[-1][1] if sliced else start
        sliced.append([now_t, last])
    ms = [m for m in marks if t0 <= (m.get("ts") or 0) <= now_t]
    return {"points": sliced, "start": start, "marks": ms, "range": name}


def equity_points(since: float | None = None, limit: int = 2000) -> tuple[list[list], list[dict]]:
    """Back-compat: LIVE (or since=) window of the equity curve."""
    if since is None:
        doc = chart_range("LIVE")
    else:
        doc = chart_range("ALL")
        t0 = since
        pts = [p for p in doc["points"] if p[0] >= t0]
        start = _value_at(doc["points"], t0, doc["start"])
        if not pts or pts[0][0] > t0:
            pts = [[t0, round(start, 2)]] + pts
        doc = {"points": pts[-limit:], "marks": [m for m in doc["marks"] if (m.get("ts") or 0) >= t0], "start": start}
    return doc["points"], doc["marks"]

def decisions(agent: str, limit: int = 40) -> list[dict]:
    if agent == "scanner":
        rows = q("SELECT * FROM scans ORDER BY id DESC LIMIT ?", (limit,))
        out = []
        for r in rows:
            out.append(
                {
                    "ts": r["ts"],
                    "mint": r["mint"],
                    "name": r["name"],
                    "verdict": r["verdict"],
                    "reason": r["reason"],
                }
            )
        return out
    if agent == "auditor":
        rows = q("SELECT * FROM audits ORDER BY id DESC LIMIT ?", (limit,))
        return [
            {
                "ts": r["ts"],
                "mint": r["mint"],
                "verdict": "pass" if r["passed"] else "kill",
                "reason": r["reason"],
            }
            for r in rows
        ]
    if agent == "analyst":
        rows = q("SELECT * FROM verdicts ORDER BY id DESC LIMIT ?", (limit,))
        return [
            {
                "ts": r["ts"],
                "mint": r["mint"],
                "verdict": "pass" if r["passed"] else "kill",
                "reason": r["failed"],
                "score": r["score"],
            }
            for r in rows
        ]
    if agent == "executor":
        rows = q("SELECT * FROM signals ORDER BY id DESC LIMIT ?", (limit,))
        return [
            {
                "ts": r["ts"],
                "mint": r["mint"],
                "verdict": r["status"],
                "reason": r["reason"],
            }
            for r in rows
        ]
    if agent == "risk":
        rows = q(
            "SELECT * FROM positions WHERE status='closed' OR status='exiting' ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        return [
            {
                "ts": r["exit_ts"] or r["opened_at"],
                "mint": r["mint"],
                "verdict": r["exit_reason"] or r["status"],
                "reason": r["exit_reason"],
            }
            for r in rows
        ]
    return []


def search_coins(query: str, limit: int = 20) -> list[dict]:
    like = f"%{query}%"
    rows = q(
        "SELECT mint, name, symbol FROM coins WHERE mint LIKE ? OR name LIKE ? OR symbol LIKE ? "
        "ORDER BY first_seen DESC LIMIT ?",
        (like, like, like, limit),
    )
    return [dict(r) for r in rows]
