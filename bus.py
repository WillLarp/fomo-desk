"""In-process asyncio queues plus a simple pub/sub for live UI events."""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from typing import Any, Callable

auditor: asyncio.Queue = asyncio.Queue()
analyst: asyncio.Queue = asyncio.Queue()
executor: asyncio.Queue = asyncio.Queue()
risk: asyncio.Queue = asyncio.Queue()

_subs: dict[str, list[Callable]] = defaultdict(list)
_recent: deque = deque(maxlen=200)


def reset() -> None:
    """Drop pending messages (tests)."""
    for q in (auditor, analyst, executor, risk):
        while not q.empty():
            try:
                q.get_nowait()
            except asyncio.QueueEmpty:
                break
    _recent.clear()


def emit(kind: str, data: dict[str, Any]) -> None:
    ev = {"type": kind, "data": data}
    _recent.append(ev)
    for fn in list(_subs.get(kind, []) + _subs.get("*", [])):
        try:
            fn(kind, data)
        except Exception:
            pass


def subscribe(kind: str, fn: Callable) -> None:
    _subs[kind].append(fn)


def recent() -> list[dict]:
    return list(_recent)
