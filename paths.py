"""Load desk paths and secrets from a file outside the repo. Never print values."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SECRETS_FILE = Path(os.environ.get("DESK_SECRETS", "/home/box/.desk_secrets"))
OWNER_CHAT_FILE = ROOT / "owner_chat_id"
DB_PATH = Path(os.environ.get("DESK_DB", str(ROOT / "data" / "desk.sqlite")))
LOG_DIR = ROOT / "logs"
WEB_DIR = ROOT / "web"
PUBLISH_DIR = Path(os.environ.get("DESK_PUBLISH", str(ROOT / "data" / "publish")))
PID_FILE = ROOT / "data" / "desk.pid"
LIVENESS_PATH = Path(os.environ.get("DESK_LIVENESS", str(ROOT / "data" / "liveness")))


def _parse_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def load_secrets() -> dict[str, str]:
    return _parse_env_file(SECRETS_FILE)


def data_path() -> str:
    val = os.environ.get("DATA_PATH") or load_secrets().get("DATA_PATH", "")
    if len(val) < 32:
        raise RuntimeError("DATA_PATH is missing or shorter than 32 characters")
    # reject path separators
    if any(ch in val for ch in "/\\"):
        raise RuntimeError("DATA_PATH must be a single path segment")
    return val


def pages_base() -> str:
    return (
        os.environ.get("PAGES_BASE")
        or load_secrets().get("PAGES_BASE")
        or "https://willlarp.github.io/fomo-desk/"
    ).rstrip("/") + "/"


def open_url() -> str:
    return load_secrets().get("OPEN_URL") or (pages_base() + "#d=" + data_path())


def owner_chat_id() -> int | None:
    raw = os.environ.get("TG_OWNER_CHAT")
    if not raw and OWNER_CHAT_FILE.is_file():
        raw = OWNER_CHAT_FILE.read_text().strip()
    if not raw:
        return None
    return int(raw)


def local_data_dir() -> Path:
    d = PUBLISH_DIR / "d" / data_path()
    d.mkdir(parents=True, exist_ok=True)
    return d
