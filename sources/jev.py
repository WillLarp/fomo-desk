"""TypeSafe Jev caller. Only noul questions. Never log the API key."""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from sources.http import client

log = logging.getLogger("jev")

URL = "https://api.typesafe.ai/v1/systemone"
QUESTIONS = {
    "alive_6h": {
        "type": "noul",
        "instructions": "This coin is still actively traded 6 hours from now",
    },
    "organic": {
        "type": "noul",
        "instructions": "Most buyers in the last 30 minutes are separate real wallets",
    },
    "flush": {
        "type": "noul",
        "instructions": "Price just flushed down and buyers are absorbing it",
    },
    "chase": {
        "type": "noul",
        "instructions": "This entry is chasing a green candle that already ran",
    },
}


class JevError(Exception):
    def __init__(self, kind: str, status: int | None = None, raw: Any = None):
        super().__init__(kind)
        self.kind = kind
        self.status = status
        self.raw = raw


async def ask(state: str, timeout_s: float = 10.0) -> dict:
    key = os.environ.get("TYPESAFE_API_KEY")
    if not key:
        raise JevError("no answer")
    payload = {"model": "jev-latest", "state": state, "questions": QUESTIONS}
    try:
        r = await client().post(
            URL,
            json=payload,
            headers={"Authorization": f"Bearer {key}"},
            timeout=timeout_s,
        )
    except httpx.TimeoutException as e:
        raise JevError("no answer") from e
    except httpx.HTTPError as e:
        raise JevError("no answer") from e
    if r.status_code == 429:
        raise JevError("429", status=429)
    if r.status_code != 200:
        log.warning("jev HTTP %s", r.status_code)
        raise JevError("no answer", status=r.status_code)
    try:
        data = r.json()
    except Exception as e:
        raise JevError("bad answer", raw=r.text[:500]) from e
    answers = data.get("answers") or {}
    parsed: dict[str, float] = {}
    for name in QUESTIONS:
        node = answers.get(name) or {}
        if not isinstance(node, dict) or "noul" not in node:
            raise JevError("bad answer", raw=data)
        try:
            v = float(node["noul"])
        except (TypeError, ValueError) as e:
            raise JevError("bad answer", raw=data) from e
        if v < 0 or v > 1:
            raise JevError("bad answer", raw=data)
        parsed[name] = v
    usage = data.get("usage") or {}
    return {
        "answers": parsed,
        "model": data.get("model") or "jev-latest",
        "inputTokens": int(usage.get("input_tokens") or usage.get("inputTokens") or 0),
        "raw": data,
    }
