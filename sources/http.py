"""Shared HTTP client.

Requests run in a worker thread so a stalled connect or DNS lookup cannot
block the asyncio event loop. This box has no default IPv6 route; a hanging
AAAA connect has been seen to freeze the desk for several minutes even when
httpx timeouts were set, because asyncio.wait_for cannot interrupt a
synchronous socket call on the loop thread.
"""

from __future__ import annotations

import asyncio
import socket
import threading

import httpx

# Default for market APIs (DexScreener, RugCheck, Solana RPC, Jev).
DEFAULT_TIMEOUT = httpx.Timeout(10.0, connect=8.0, pool=5.0, write=10.0)
HARD_CAP_S = 15.0
SOCKET_TIMEOUT_S = 15.0

_orig_getaddrinfo = socket.getaddrinfo
_installed = False
_tls = threading.local()
_clients_lock = threading.Lock()
_sync_clients: list[httpx.Client] = []
_client: httpx.AsyncClient | None = None


def install_network_guards() -> None:
    """Force IPv4 and a process-wide socket timeout. Safe to call more than once."""
    global _installed
    if _installed:
        return
    socket.setdefaulttimeout(SOCKET_TIMEOUT_S)

    def _ipv4_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        if family == 0:
            family = socket.AF_INET
        return _orig_getaddrinfo(host, port, family, type, proto, flags)

    socket.getaddrinfo = _ipv4_getaddrinfo  # type: ignore[assignment]
    _installed = True


install_network_guards()


def client() -> httpx.AsyncClient:
    """Legacy async client (Telegram used this). Prefer get()/post()."""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=DEFAULT_TIMEOUT,
            headers={"User-Agent": "fomo-desk/1.0"},
            follow_redirects=True,
            trust_env=False,
            transport=httpx.AsyncHTTPTransport(local_address="0.0.0.0", retries=0),
        )
    return _client


def _sync_client() -> httpx.Client:
    c = getattr(_tls, "client", None)
    if c is None or c.is_closed:
        c = httpx.Client(
            timeout=DEFAULT_TIMEOUT,
            headers={"User-Agent": "fomo-desk/1.0"},
            follow_redirects=True,
            trust_env=False,
            transport=httpx.HTTPTransport(local_address="0.0.0.0", retries=0),
        )
        _tls.client = c
        with _clients_lock:
            _sync_clients.append(c)
    return c


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


def _sync_request(method: str, url: str, timeout, kwargs: dict) -> httpx.Response:
    return _sync_client().request(method, url, timeout=timeout, **kwargs)


async def request(method: str, url: str, **kwargs) -> httpx.Response:
    timeout = kwargs.pop("timeout", DEFAULT_TIMEOUT)
    cap = hard_cap(timeout)
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_sync_request, method, url, timeout, kwargs),
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
    with _clients_lock:
        clients = list(_sync_clients)
        _sync_clients.clear()
    for c in clients:
        try:
            c.close()
        except Exception:
            pass
    if getattr(_tls, "client", None) is not None:
        _tls.client = None
