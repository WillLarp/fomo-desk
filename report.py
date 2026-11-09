
"""Night report. The morning routine can call night_report_text() or write_night_json()."""

from __future__ import annotations

import json
from pathlib import Path

import config
import db
import paths
from util import local_now


def night_stats() -> dict:
    start = db.night_start_ts()
    scans = db.q("SELECT verdict, COUNT(*) AS n FROM scans GROUP BY verdict")
    by = {r["verdict"]: r["n"] for r in scans}
    trades = db.q("SELECT * FROM positions WHERE opened_at>=?", (start,))
    closed = [dict(r) for r in trades if r["status"] == "closed"]
    wins = [p for p in closed if (p.get("pnl_usd") or 0) > 0]
    losses = [p for p in closed if (p.get("pnl_usd") or 0) <= 0]
    pnl = sum((p.get("pnl_usd") or 0) for p in closed)
    fees = sum((p.get("fees_usd") or 0) for p in closed)
    l8 = db.q1("SELECT COUNT(*) AS n FROM layer8_kills WHERE ts>=?", (start,))
    return {
        "started_at": start,
        "mode": config.get("desk.mode"),
        "stack": config.stack(),
        "scans": by,
        "passed": by.get("pass", 0),
        "killed": by.get("kill", 0),
        "trades": len(trades),
        "closed": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "pnl": round(pnl, 2),
        "fees": round(fees, 4),
        "open": len(db.open_positions()),
        "layer8_kills": int(l8["n"]) if l8 else 0,
        "positions": [dict(p) for p in trades],
    }


def night_report_text() -> str:
    s = night_stats()
    lines = [
        f"Night report · {local_now().strftime('%Y-%m-%d')} · {s['mode']}",
        f"Stack ${s['stack']:,.2f} · PnL ${s['pnl']:+.2f} · fees ${s['fees']:.2f}",
        f"Scanner pass {s['passed']} · kill {s['killed']}",
        f"Trades {s['trades']} · closed {s['closed']} · wins {s['wins']} · losses {s['losses']} · still open {s['open']}",
    ]
    if s["layer8_kills"]:
        lines.append(f"Layer 8 down killed {s['layer8_kills']} coins (no data: delegations).")
    for p in s["positions"]:
        name = p.get("name") or p.get("mint", "")[:6]
        if p.get("status") == "closed":
            lines.append(
                f"  {name} {p.get('exit_reason')} {p.get('pnl_pct'):+.1f}% ${p.get('pnl_usd'):+.2f}"
            )
        else:
            lines.append(f"  {name} still {p.get('status')}")
    return "\n".join(lines)


def write_night_json(path: Path | None = None) -> Path:
    """Write the night's data as JSON so a routine can pick it up."""
    dest = path or (paths.ROOT / "data" / "night_report.json")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps({"text": night_report_text(), "stats": night_stats()}, default=str, indent=2))
    return dest


if __name__ == "__main__":
    print(night_report_text())
    p = write_night_json()
    print("wrote", p)
