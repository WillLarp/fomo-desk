"""External desk supervisor. Never uses sqlite.

Watches per-instance heartbeat files and SIGKILLs a stuck process after
STALE_S seconds (past a short grace). Relaunches until desk.stop appears.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STOPFILE = ROOT / "data" / "desk.stop"
LOG = ROOT / "logs" / "desk.stdout"
PY = ROOT / ".venv" / "bin" / "python"
INSTANCES = (0, 1)
STALE_S = 8
GRACE_S = 20
POLL_S = 1


def inst_dir(n: int) -> Path:
    d = ROOT / "data" / "i" / str(n)
    d.mkdir(parents=True, exist_ok=True)
    return d


def pidfile(n: int) -> Path:
    return inst_dir(n) / "desk.pid"


def started_file(n: int) -> Path:
    return inst_dir(n) / "started"


def live_file(n: int) -> Path:
    return inst_dir(n) / "liveness"


def hb_file(n: int, name: str) -> Path:
    return inst_dir(n) / f"{name}.hb"


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} desk supervisor: {msg}\n"
    try:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def file_age(path: Path, now: float) -> float | None:
    try:
        return now - path.stat().st_mtime
    except FileNotFoundError:
        return None


def is_main(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        cmd = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
        return "main.py" in cmd
    except Exception:
        return False


def read_pid(n: int) -> int | None:
    try:
        return int(pidfile(n).read_text().strip())
    except Exception:
        return None


def launch(n: int) -> int:
    inst_dir(n)
    live_file(n).unlink(missing_ok=True)
    env = os.environ.copy()
    env["DESK_INSTANCE"] = str(n)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as out:
        proc = subprocess.Popen(
            [str(PY), str(ROOT / "main.py"), "--instance", str(n)],
            stdout=out,
            stderr=out,
            stdin=subprocess.DEVNULL,
            env=env,
            cwd=str(ROOT),
            start_new_session=True,
        )
    pidfile(n).write_text(f"{proc.pid}\n", encoding="utf-8")
    started_file(n).write_text(f"{int(time.time())}\n", encoding="utf-8")
    if n == 0:
        (ROOT / "data" / "desk.pid").write_text(f"{proc.pid}\n", encoding="utf-8")
    return proc.pid


def started_at(n: int, now: float) -> float:
    try:
        return float(started_file(n).read_text().strip())
    except Exception:
        return now


def stale_reason(n: int, now: float) -> str | None:
    if now - started_at(n, now) < GRACE_S:
        return None
    age = file_age(live_file(n), now)
    if age is None:
        return "liveness file missing"
    if age > STALE_S:
        return f"liveness stale {age:.0f}s"
    for name in ("scanner", "risk"):
        age = file_age(hb_file(n, name), now)
        if age is not None and age > STALE_S:
            return f"{name} heartbeat stale {age:.0f}s"
    return None


def kill_pid(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGKILL)
    except Exception:
        pass


def loop() -> int:
    (ROOT / "data").mkdir(parents=True, exist_ok=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    STOPFILE.unlink(missing_ok=True)
    for n in INSTANCES:
        launch(n)
    while True:
        if STOPFILE.exists():
            for n in INSTANCES:
                kill_pid(read_pid(n) or 0)
            return 0
        now = time.time()
        for n in INSTANCES:
            pid = read_pid(n)
            if not is_main(pid):
                if pid:
                    log(f"instance {n} exited; relaunching in 2s")
                    time.sleep(2)
                if STOPFILE.exists():
                    return 0
                launch(n)
                continue
            reason = stale_reason(n, now)
            if reason:
                log(f"instance {n} {reason}; kill -9")
                kill_pid(pid or 0)
                time.sleep(1)
                if STOPFILE.exists():
                    return 0
                launch(n)
        time.sleep(POLL_S)


def main() -> None:
    if not os.environ.get("TYPESAFE_API_KEY") or not os.environ.get("TG_TOKEN"):
        sys.stderr.write("TYPESAFE_API_KEY or TG_TOKEN missing\n")
        sys.exit(1)
    if not PY.is_file():
        sys.stderr.write(f"missing venv at {PY}\n")
        sys.exit(1)
    sys.exit(loop())


if __name__ == "__main__":
    main()
