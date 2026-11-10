
"""Settings with defaults and ranges. Stored in db.settings. Secrets stay in env."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Spec:
    default: Any
    lo: float | None = None
    hi: float | None = None
    choices: tuple | None = None
    label: str = ""
    kind: str = ""  # num|bool|enum|str, inferred if empty


def _kind(spec: Spec) -> str:
    if spec.kind:
        return spec.kind
    if spec.choices:
        if spec.choices == (True, False):
            return "bool"
        return "enum:" + ",".join(map(str, spec.choices))
    if isinstance(spec.default, bool):
        return "bool"
    if isinstance(spec.default, (int, float)) and not isinstance(spec.default, bool):
        return "num"
    return "str"


S: dict[str, Spec] = {
    "scanner.min_liquidity_usd": Spec(40_000, 1_000, 1_000_000, label="Min liquidity $"),
    "scanner.max_top10_pct": Spec(25, 1, 100, label="Max top 10 %"),
    "scanner.max_h1_change_pct": Spec(80, 0, 5_000, label="Max 1h change %"),
    "scanner.poll_s": Spec(60, 10, 600, label="Poll s"),
    "scanner.min_pool_age_min": Spec(10, 0, 240, label="Min pool age min"),
    "scanner.max_wait_min": Spec(60, 5, 720, label="Max wait min"),
    "scanner.runner_h24_pct": Spec(300, 50, 10_000, label="Runner 24h %"),
    "scanner.dex_limit_per_min": Spec(250, 10, 300, label="DexScreener req/min cap"),
    "scanner.ws_silence_s": Spec(120, 30, 600, label="Websocket silence s"),
    "scanner.dex_429_wait_s": Spec(30, 5, 300, label="DexScreener 429 wait s"),
    "auditor.enabled": Spec(True, choices=(True, False), label="Take apart before buying"),
    "auditor.max_dev_pct": Spec(5, 0, 100, label="Max dev %"),
    "auditor.max_top10_pct": Spec(25, 1, 100, label="Max top 10 %"),
    "auditor.min_lp_locked_pct": Spec(99, 0, 100, label="Min LP locked %"),
    "auditor.min_pool_age_min": Spec(10, 0, 240, label="Min pool age min"),
    "auditor.max_bundled": Spec(5, 1, 10_000, label="Max bundled wallets"),
    "auditor.max_delegated_accounts": Spec(10, 1, 100_000, label="Max delegated accounts"),
    "auditor.retries": Spec(3, 0, 10, label="Retries"),
    "auditor.retry_gap_s": Spec(2, 1, 30, label="Retry gap s"),
    "auditor.liq_drop_pct": Spec(50, 1, 100, label="Max liquidity drop % since scan"),
    "analyst.mode": Spec("jev", choices=("jev", "rules"), label="Analyst mode"),
    "analyst.min_alive_6h": Spec(0.70, 0, 1, label="Min alive 6h"),
    "analyst.min_organic": Spec(0.65, 0, 1, label="Min organic"),
    "analyst.max_chase": Spec(0.30, 0, 1, label="Max chase"),
    "analyst.min_score": Spec(70, 0, 100, label="Min score"),
    "analyst.timeout_s": Spec(10, 1, 120, label="Jev timeout s"),
    "analyst.retry_gap_s": Spec(2, 1, 30, label="Jev retry gap s"),
    "analyst.rules_alive_volume_h1": Spec(25_000, 0, 100_000_000, label="Rules: alive volume 1h $"),
    "analyst.rules_organic_buys_h1": Spec(150, 0, 1_000_000, label="Rules: organic buys 1h"),
    "analyst.rules_bundled_penalty": Spec(0.5, 0, 1, label="Rules: bundled penalty"),
    "analyst.rules_bundled_from": Spec(3, 1, 10_000, label="Rules: bundled from wallets"),
    "analyst.rules_flush_drop_m5_pct": Spec(5, 0, 100, label="Rules: flush drop 5m %"),
    "analyst.rules_chase_h1_pct": Spec(80, 1, 5_000, label="Rules: chase 1h %"),
    "analyst.queue_429_s": Spec(60, 5, 600, label="Jev 429 queue s"),
    "executor.stack_pct": Spec(8, 0.5, 100, label="Stack %"),
    "executor.max_pool_pct": Spec(2, 0.1, 100, label="Max % of pool"),
    "executor.min_ticket_usd": Spec(47.50, 5, 10_000, label="Min ticket $"),
    "executor.below_min": Spec("bump", choices=("bump", "skip"), label="Below min"),
    "executor.recheck_liq_drop_pct": Spec(20, 0, 100, label="Recheck liq drop %"),
    "executor.recheck_price_up_pct": Spec(15, 0, 1_000, label="Recheck price up %"),
    "executor.confirm_window_min": Spec(5, 1, 120, label="Confirm window min"),
    "executor.paper_bar_s": Spec(1.2, 0.2, 10, label="Paper bar seconds"),
    "risk.poll_s": Spec(15, 5, 600, label="Poll s"),
    "risk.stop_pct": Spec(6, 0.5, 90, label="Stop %"),
    "risk.trail_start_pct": Spec(20, 1, 1_000, label="Trail starts at +%"),
    "risk.trail_pct": Spec(12, 0.5, 99, label="Trail %"),
    "risk.timeout_h": Spec(3, 0.25, 72, label="Timeout h"),
    "risk.timeout_min_gain_pct": Spec(5, 0, 1_000, label="Timeout min gain %"),
    "risk.confirm_window_min": Spec(2, 0.5, 120, label="Sell confirm window min"),
    "risk.no_price_polls": Spec(4, 1, 20, label="No-price polls before alert"),
    "risk.dex_429_wait_s": Spec(30, 5, 300, label="Risk 429 wait s"),
    "desk.mode": Spec("PAPER", choices=("PAPER", "LIVE"), label="Mode"),
    "desk.paper_start_stack": Spec(200, 10, 1_000_000, label="Paper start stack $"),
    "desk.live_stack": Spec(0, 0, 100_000_000, label="Live stack $"),
    "desk.report_time": Spec("08:00", label="Report time"),
    "desk.notifier": Spec("telegram", label="Notifier"),
    "desk.rpc_url": Spec("https://api.mainnet-beta.solana.com", label="Solana RPC"),
    "desk.push_rejects": Spec(False, choices=(True, False), label="Push rejects"),
    "desk.fees_ref_discount": Spec(0, 0, 0.9, label="Ref fee discount"),
    "desk.fomo_handle": Spec("WillLarp", label="FOMO handle"),
    "desk.paused": Spec(False, choices=(True, False), label="Paused"),
    "desk.publish_s": Spec(15, 5, 120, label="Local JSON write s"),
    "desk.pages_push_s": Spec(480, 360, 600, label="Pages publish s"),
}

ALIASES = {
    "stop": "risk.stop_pct",
    "trail": "risk.trail_pct",
    "trail start": "risk.trail_start_pct",
    "timeout": "risk.timeout_h",
    "stack pct": "executor.stack_pct",
    "stack": "desk.live_stack",
    "min liquidity": "scanner.min_liquidity_usd",
    "min score": "analyst.min_score",
    "mode": "desk.mode",
    "handle": "desk.fomo_handle",
    "pause": "desk.paused",
    "rpc": "desk.rpc_url",
    "rpc_url": "desk.rpc_url",
}

_cache: dict[str, Any] = {}


class SettingError(ValueError):
    pass


def resolve(name: str) -> str:
    n = name.strip().lower().replace("-", "_")
    if n in S:
        return n
    if n in ALIASES:
        return ALIASES[n]
    hits = [k for k in S if k.split(".", 1)[1] == n.replace(" ", "_")]
    if len(hits) == 1:
        return hits[0]
    raise SettingError(f"unknown setting {name!r}")


def coerce(key: str, value: Any) -> Any:
    spec = S[key]
    d = spec.default
    try:
        if isinstance(d, bool):
            if isinstance(value, str):
                v = value.strip().lower() in ("1", "true", "on", "yes")
            else:
                v = bool(value)
        elif isinstance(d, (int, float)) and not isinstance(d, bool):
            v = float(str(value).replace("%", "").replace("$", "").replace(",", ""))
            if isinstance(d, int) and abs(v - int(v)) < 1e-9:
                v = int(v)
        else:
            v = str(value).strip()
    except (TypeError, ValueError) as e:
        raise SettingError(f"{key}: not a valid value: {value!r}") from e
    if isinstance(v, str) and spec.choices:
        for c in spec.choices:
            if str(c).upper() == v.upper():
                v = c
                break
    if spec.choices is not None and v not in spec.choices:
        raise SettingError(f"{key}: one of {', '.join(map(str, spec.choices))}")
    if spec.lo is not None and isinstance(v, (int, float)) and not isinstance(v, bool):
        if not (spec.lo <= v <= (spec.hi if spec.hi is not None else v)):
            raise SettingError(f"{key}: range {spec.lo:g} to {spec.hi:g}")
    return v


def get(key: str) -> Any:
    return _cache.get(key, S[key].default)


def load(rows: dict[str, str]) -> None:
    _cache.clear()
    for k, raw in rows.items():
        if k in S:
            try:
                _cache[k] = coerce(k, json.loads(raw))
            except Exception:
                pass


def set_value(key: str, value: Any) -> tuple[str, Any, Any]:
    key = resolve(key)
    new = coerce(key, value)
    old = get(key)
    _cache[key] = new
    return key, old, new


def all_settings() -> dict[str, dict]:
    out = {}
    for k, s in S.items():
        out[k] = {
            "value": get(k),
            "default": s.default,
            "min": s.lo,
            "max": s.hi,
            "kind": _kind(s),
            "choices": list(s.choices) if s.choices else None,
            "label": s.label,
        }
    return out


def public_settings() -> dict[str, dict]:
    """Account-tab shape: value + range + label, no secrets."""
    out = {}
    for k, s in S.items():
        val = get(k)
        if k == "desk.rpc_url":
            # A custom RPC URL may contain a provider API key. Only publish the public default.
            text = str(val or "")
            if "api.mainnet-beta.solana.com" not in text:
                val = "custom"
        out[k] = {
            "value": val,
            "min": s.lo,
            "max": s.hi,
            "kind": _kind(s),
            "label": s.label,
            "choices": list(s.choices) if s.choices else None,
            "group": k.split(".", 1)[0],
        }
    return out


def stack() -> float:
    from db import paper_balance, money_in_open

    if get("desk.mode") == "LIVE":
        return float(get("desk.live_stack"))
    return paper_balance()


def free_stack() -> float:
    from db import money_in_open

    return max(0.0, stack() - money_in_open())


def ticket_check(stack_usd: float | None = None) -> dict:
    """Ticket check. $200 stack at 8% is $16, bumped to $47.50."""
    if stack_usd is None:
        stack_usd = stack()
    pct = float(get("executor.stack_pct"))
    mt = float(get("executor.min_ticket_usd"))
    min_stack = mt / (pct / 100) if pct else mt
    if stack_usd >= min_stack:
        return {"ok": True, "min_stack": round(min_stack, 2), "message": None}
    if get("executor.below_min") == "bump":
        p = round(100 * mt / stack_usd, 1) if stack_usd > 0 else 100
        return {
            "ok": True,
            "min_stack": round(min_stack, 2),
            "message": f"tickets bumped to ${mt:.2f} = {p}% of stack",
        }
    return {
        "ok": False,
        "min_stack": round(min_stack, 2),
        "message": f"every coin will be skipped, stack under ${min_stack:,.2f}",
    }
