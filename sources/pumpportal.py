"""One PumpPortal websocket for the whole desk. subscribeMigration only."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import websockets


log = logging.getLogger("pumpportal")

URL = "wss://pumpportal.fun/api/data"


async def run(on_migration: Callable[[dict], Awaitable[None]], stop: asyncio.Event) -> None:
    delay = 1
    max_delay = 60
    while not stop.is_set():
        try:
            log.info("connecting to pumpportal")
            # ping_interval/ping_timeout keep the socket honest; a quiet
            # market (no migrations) is normal and must not reconnect.
            async with websockets.connect(URL, ping_interval=20, ping_timeout=20, close_timeout=5) as ws:
                await ws.send(json.dumps({"method": "subscribeMigration"}))
                log.info("subscribed to migration")
                delay = 1
                while not stop.is_set():
                    recv = asyncio.create_task(ws.recv())
                    waiter = asyncio.create_task(stop.wait())
                    done, pending = await asyncio.wait(
                        {recv, waiter}, return_when=asyncio.FIRST_COMPLETED
                    )
                    for task in pending:
                        task.cancel()
                    if waiter in done and recv not in done:
                        break
                    try:
                        raw = recv.result()
                    except Exception:
                        raise
                    try:
                        msg = json.loads(raw) if isinstance(raw, str) else raw
                    except Exception:
                        continue
                    if not isinstance(msg, dict):
                        continue
                    if msg.get("message") and not msg.get("mint"):
                        # ack
                        continue
                    if not msg.get("mint"):
                        continue
                    try:
                        await on_migration(msg)
                    except Exception:
                        log.exception("migration handler failed")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("websocket closed: %s, backoff %ss", type(e).__name__, delay)
        if stop.is_set():
            break
        await asyncio.sleep(delay)
        delay = min(max_delay, delay * 2)
