"""DexScreener token pairs. No API key. Batch by comma-separated mints."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import config
from fields import dig, pumpswap_pair
from sources.http import client

log = logging.getLogger("dexscreener")

BASE = "https://api.dexscreener.com/tokens/v1/solana/"
_window: list[float] = []
_lock = asyncio.Lock()


def _pair_view(pair: dict | None) -> dict:
    if not pair:
        return {}
    return {
        "pairAddress": dig(pair, "pairAddress"),
        "priceUsd": _f(dig(pair, "priceUsd")),
        "liquidityUsd": _f(dig(pair, "liquidity.usd")),
        "marketCap": _f(dig(pair, "marketCap")),
        "volume": {
            "m5": _f(dig(pair, "volume.m5")),
            "h1": _f(dig(pair, "volume.h1")),
            "h24": _f(dig(pair, "volume.h24")),
        },
        "txns": {
            "m5": {
                "buys": _i(dig(pair, "txns.m5.buys")),
                "sells": _i(dig(pair, "txns.m5.sells")),
            },
            "h1": {
                "buys": _i(dig(pair, "txns.h1.buys")),
                "sells": _i(dig(pair, "txns.h1.sells")),
            },
        },
        "priceChange": {
            "m5": _f(dig(pair, "priceChange.m5")),
            "h1": _f(dig(pair, "priceChange.h1")),
            "h24": _f(dig(pair, "priceChange.h24")),
        },
        "pairCreatedAt": dig(pair, "pairCreatedAt"),
        "name": dig(pair, "baseToken.name"),
        "symbol": dig(pair, "baseToken.symbol"),
        "image": dig(pair, "info.imageUrl"),
        "raw": pair,
    }


def _f(v):
    try:
        return float(v) if v is not None and v != "" else None
    except (TypeError, ValueError):
        return None


def _i(v):
    try:
        return int(v) if v is not None and v != "" else None
    except (TypeError, ValueError):
        return None


async def _throttle() -> None:
    cap = int(config.get("scanner.dex_limit_per_min"))
    async with _lock:
        now = time.time()
        while _window and now - _window[0] > 60:
            _window.pop(0)
        if len(_window) >= cap:
            wait = 60 - (now - _window[0]) + 0.05
            await asyncio.sleep(max(0.0, wait))
        _window.append(time.time())


async def fetch_pairs(mints: list[str]) -> dict[str, dict]:
    """Return mint -> PumpSwap pair view. Missing mints are omitted (not known yet)."""
    out: dict[str, dict] = {}
    chunk = 20
    for i in range(0, len(mints), chunk):
        batch = [m for m in mints[i : i + chunk] if m]
        if not batch:
            continue
        await _throttle()
        url = BASE + ",".join(batch)
        try:
            r = await client().get(url)
        except httpx.HTTPError as e:
            log.warning("dexscreener error: %s", type(e).__name__)
            continue
        if r.status_code == 429:
            wait = int(config.get("scanner.dex_429_wait_s"))
            log.warning("dexscreener 429, waiting %ss", wait)
            await asyncio.sleep(wait)
            continue
        if r.status_code != 200:
            log.warning("dexscreener HTTP %s", r.status_code)
            continue
        try:
            data = r.json()
        except Exception:
            continue
        if not isinstance(data, list):
            continue
        by_mint: dict[str, list] = {}
        for p in data:
            if not isinstance(p, dict):
                continue
            mint = dig(p, "baseToken.address") or dig(p, "tokenAddress")
            if not mint:
                continue
            by_mint.setdefault(mint, []).append(p)
        for mint, pairs in by_mint.items():
            view = _pair_view(pumpswap_pair(pairs))
            if view.get("pairAddress") or view.get("priceUsd") is not None:
                out[mint] = view
    return out


async def fetch_one(mint: str) -> dict:
    return (await fetch_pairs([mint])).get(mint) or {}


# late import used only in type of exception message
import httpx  # noqa: E402
