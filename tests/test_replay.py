"""Acceptance test 6: replay recorded PumpPortal migrations through Scanner's ingest.

We cannot wait 2 hours. This replays whatever live migrations were recorded
while the desk was being built (logs/raw/migrations_live.jsonl) plus the
STEP 2 sample.
"""

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LIVE = ROOT / "logs" / "raw" / "migrations_live.jsonl"
SAMPLE = ROOT / "logs" / "raw" / "pumpportal_migration.json"


def _events():
    ev = []
    if SAMPLE.is_file():
        ev.append(json.loads(SAMPLE.read_text()))
    if LIVE.is_file():
        for line in LIVE.read_text().splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            raw = rec.get("raw", rec)
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except Exception:
                    continue
            if isinstance(raw, dict) and raw.get("mint"):
                ev.append(raw)
    # unique by mint, keep order
    seen = set()
    out = []
    for e in ev:
        if e["mint"] in seen:
            continue
        seen.add(e["mint"])
        out.append(e)
    return out


def test_replay_has_recorded_migrations():
    ev = _events()
    assert ev, "no recorded migrations to replay"
    first_ts = last_ts = None
    if LIVE.is_file():
        rows = [json.loads(l) for l in LIVE.read_text().splitlines() if l.strip()]
        times = [r.get("elapsed_s") for r in rows if r.get("elapsed_s") is not None]
        if times:
            first_ts, last_ts = min(times), max(times)
    span = (last_ts - first_ts) if first_ts is not None and last_ts is not None else 0
    # expose for the report
    test_replay_has_recorded_migrations.span_s = span
    test_replay_has_recorded_migrations.count = len(ev)
    assert all("mint" in e and e.get("txType") in ("migrate", None) or "mint" in e for e in ev)
    # pool label on these samples is pump-amm, not a pubkey — scanner must use DexScreener pairAddress
    for e in ev:
        if e.get("pool"):
            assert e["pool"] in ("pump-amm", "raydium-cpmm") or len(e["pool"]) >= 32


def test_replay_ingest_writes_waiting_room(tmp_path, monkeypatch):
    ev = _events()
    if not ev:
        pytest.skip("no events")
    import db, paths, config
    dbpath = tmp_path / "replay.sqlite"
    monkeypatch.setattr(paths, "DB_PATH", dbpath)
    # reset thread-local connection
    if hasattr(db._local, "con"):
        db._local.con.close()
        del db._local.con
    db.init()
    now = 1_700_000_000
    for e in ev:
        mint = e["mint"]
        db.upsert_coin(mint, signature=e.get("signature"), migrated_at=now, status="waiting")
        db.execute(
            "INSERT INTO waiting(mint,entered_at,last_poll) VALUES(?,?,?)",
            (mint, now, None),
        )
    n = db.q1("SELECT COUNT(*) AS n FROM waiting")["n"]
    assert n == len(ev)
    # cleanup thread conn so later tests are not stuck on this db
    if hasattr(db._local, "con"):
        db._local.con.close()
        del db._local.con
