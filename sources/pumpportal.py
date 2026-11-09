"""One PumpPortal websocket for the whole desk. subscribeMigration only."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import websockets

import config
from util import utcnow_iso

log = logging.getLogger("pumpportal")

URL = "wss://pumpportal.fun/api/data"


async def run(on_migration: Callable[[dict], Awaitable[None]], stop: asyncio.Event) -> None:
    delay = 1
    max_delay = 60
    silence = int(config.get("scanner.ws_silence_s"))
    while not stop.is_set():
        try:
            log.info("connecting to pumpportal")
            async with websockets.connect(URL, ping_interval=20, ping_timeout=20, close_timeout=5) as ws:
                await ws.send(json.dumps({"method": "subscribeMigration"}))
                log.info("subscribed to migration")
                delay = 1
                while not stop.is_set():
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=silence)
                    except asyncio.TimeoutError:
                        log.warning("websocket silent for %ss, reconnect", silence)
                        break
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
