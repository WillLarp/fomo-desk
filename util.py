"""Small helpers used across the desk."""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Budapest")

_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"
    "\U00002700-\U000027BF"
    "\U0001F1E0-\U0001F1FF"
    "\U00002600-\U000026FF"
    "]+",
    flags=re.UNICODE,
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def utcnow_iso() -> str:
    return utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def ts() -> float:
    return utcnow().timestamp()


def local_now() -> datetime:
    return datetime.now(TZ)


def iso_to_ts(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        # DexScreener pairCreatedAt is ms
        v = float(value)
        return v / 1000.0 if v > 1e12 else v
    s = str(value).strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s).timestamp()
    except ValueError:
        return None


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def as_float(v: Any, default: float | None = None) -> float | None:
    if v is None or v == "":
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def as_int(v: Any, default: int | None = None) -> int | None:
    f = as_float(v, None)
    return default if f is None else int(f)


def fmt_usd(v: float | None, digits: int = 2) -> str:
    if v is None:
        return "n/a"
    sign = "-" if v < 0 else ""
    return f"{sign}${abs(v):,.{digits}f}"


def fmt_pct(v: float | None, signed: bool = False) -> str:
    if v is None:
        return "n/a"
    prefix = "+" if signed and v > 0 else ""
    return f"{prefix}{v:.1f}%"


def short_mint(mint: str, n: int = 4) -> str:
    if not mint or len(mint) <= n * 2:
        return mint or ""
    return f"{mint[:n]}…{mint[-n:]}"


def copycat_key(name: str | None) -> str:
    """Lowercase, strip spaces, digits, emoji. Letters only."""
    if not name:
        return ""
    s = unicodedata.normalize("NFKD", str(name))
    s = _EMOJI_RE.sub("", s)
    s = s.lower()
    s = re.sub(r"[\s\d]+", "", s)
    s = re.sub(r"[^a-z]", "", s)
    return s


def is_copycat(name: str | None, symbol: str | None, runners: list[dict]) -> str | None:
    """Return the runner name if this is a copycat, else None."""
    keys = [k for k in (copycat_key(name), copycat_key(symbol)) if k]
    if not keys:
        return None
    for r in runners:
        rkeys = [k for k in (copycat_key(r.get("name")), copycat_key(r.get("symbol"))) if k]
        for a in keys:
            for b in rkeys:
                if a == b or (len(a) >= 3 and len(b) >= 3 and (a in b or b in a)):
                    return r.get("name") or r.get("symbol") or "X"
    return None


def age_min(migrated_at: float | None, now: float | None = None) -> int:
    if not migrated_at:
        return 0
    return max(0, int(((now or ts()) - migrated_at) // 60))


def pool_created_ts(migrated_at: Any = None, pair_created_at: Any = None) -> float | None:
    """Unix seconds when the PumpSwap pool was created.

    Prefer coins.migrated_at (PumpPortal migrate receive time, which matches
    the on-chain migrate blockTime). DexScreener pairCreatedAt is often the
    older pump.fun listing date, which would make a just-graduated pool look
    weeks old and skip min_pool_age_min.
    """
    migrated = iso_to_ts(migrated_at)
    if migrated:
        return migrated
    return iso_to_ts(pair_created_at)


def pool_age_min(
    migrated_at: Any = None,
    pair_created_at: Any = None,
    now: float | None = None,
) -> int:
    return age_min(pool_created_ts(migrated_at, pair_created_at), now)
