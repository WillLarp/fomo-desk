"""Hard timeouts on outbound HTTP and git publish."""

from __future__ import annotations

import asyncio
import socket
import subprocess
import threading
import time

import httpx
import pytest

from sources.http import aclose, get, hard_cap
import publisher


def test_hard_cap_adds_margin():
    assert hard_cap(10) == 12.0
    assert hard_cap(httpx.Timeout(10.0, connect=8.0)) == 12.0


def test_http_get_times_out_on_hang():
    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    port = sock.getsockname()[1]
    ready = threading.Event()

    def serve():
        ready.set()
        try:
            conn, _ = sock.accept()
            time.sleep(30)
            conn.close()
        except Exception:
            pass

    threading.Thread(target=serve, daemon=True).start()
    ready.wait(2)

    async def main():
        t0 = time.monotonic()
        with pytest.raises(httpx.TimeoutException):
            await get(f"http://127.0.0.1:{port}/", timeout=1.0)
        elapsed = time.monotonic() - t0
        assert elapsed < 4, elapsed
        await aclose()

    try:
        asyncio.run(main())
    finally:
        sock.close()


def test_run_git_times_out():
    t0 = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        publisher._run_git(["sleep", "10"], timeout=0.4)
    assert time.monotonic() - t0 < 2
