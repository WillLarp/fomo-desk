"""Shared async HTTP client with hard total timeouts."""

from __future__ import annotations

import asyncio

import httpx

# Default for market APIs (DexScreener, RugCheck, Solana RPC, Jev).
DEFAULT_TIMEOUT = httpx.Timeout(10.0, connect=8.0, pool=5.0, write=10.0)
HARD_CAP_S = 15.0

_client: httpx.AsyncClient | None = None


def client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=DEFAULT_TIMEOUT,
            headers={"User-Agent": "fomo-desk/1.0"},
            follow_redirects=True,
        )
    return _client


def hard_cap(timeout: httpx.Timeout | float | int | None = None) -> float:
    """Seconds to wait before asyncio.wait_for cancels a request."""
    if timeout is None:
        return HARD_CAP_S
    if isinstance(timeout, (int, float)):
        return float(timeout) + 2.0
    vals = []
    for attr in ("connect", "read", "write", "pool"):
        v = getattr(timeout, attr, None)
        if isinstance(v, (int, float)):
            vals.append(float(v))
    return (max(vals) if vals else HARD_CAP_S) + 2.0


async def request(method: str, url: str, **kwargs) -> httpx.Response:
    timeout = kwargs.pop("timeout", DEFAULT_TIMEOUT)
    cap = hard_cap(timeout)
    try:
        return await asyncio.wait_for(
            client().request(method, url, timeout=timeout, **kwargs),
            timeout=cap,
        )
    except asyncio.TimeoutError as e:
        raise httpx.TimeoutException(f"hard cap {cap:.0f}s exceeded") from e


async def get(url: str, **kwargs) -> httpx.Response:
    return await request("GET", url, **kwargs)


async def post(url: str, **kwargs) -> httpx.Response:
    return await request("POST", url, **kwargs)


async def aclose() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None
