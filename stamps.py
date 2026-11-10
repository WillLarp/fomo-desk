"""Atomic heartbeat files. The supervisor reads only these, never sqlite."""

from __future__ import annotations

import os
import time
from pathlib import Path

import paths

LIVENESS_AGENTS = ("scanner", "risk")


def liveness_path() -> Path:
    return Path(paths.LIVENESS_PATH)


def agent_path(name: str) -> Path:
    return liveness_path().parent / f"{name}.hb"


def instance_dir(n: int) -> Path:
    d = paths.ROOT / "data" / "i" / str(int(n))
    d.mkdir(parents=True, exist_ok=True)
    return d


def instance_liveness_path(n: int) -> Path:
    return instance_dir(n) / "liveness"


def write_stamp(path: Path, when: float | None = None) -> None:
    when = time.time() if when is None else when
    try:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(f"{when:.3f}\n", encoding="utf-8")
        os.replace(tmp, path)
    except Exception:
        pass


def read_stamp(path: Path) -> float | None:
    try:
        raw = Path(path).read_text(encoding="utf-8").strip().split()[0]
        return float(raw)
    except Exception:
        return None


def age(path: Path, now: float | None = None) -> float | None:
    ts = read_stamp(path)
    if ts is None:
        return None
    now = time.time() if now is None else now
    return now - ts


def stamp_agent(name: str, when: float | None = None) -> None:
    write_stamp(agent_path(name), when)


def stamp_loop(when: float | None = None) -> None:
    write_stamp(liveness_path(), when)
