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
PY = ROOT / ".venv" / "bin" / "python"
RUNTIME = Path(os.environ.get("DESK_RUNTIME", "/dev/shm/fomo-desk"))
STOPFILE = RUNTIME / "stop"
LOG = RUNTIME / "supervisor.log"
INSTANCES = (0, 1)
STALE_S = 8
GRACE_S = 20
POLL_S = 1
HEALTH_STALE_S = 180


def inst_dir(n: int) -> Path:
    d = RUNTIME / str(n)
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
    """Write only to tmpfs. Never touch the workspace disk in the hot path."""
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} desk supervisor: {msg}\n"
    try:
        RUNTIME.mkdir(parents=True, exist_ok=True)
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
    out_path = inst_dir(n) / "stdout.log"
    out = out_path.open("a", encoding="utf-8")
    try:
        proc = subprocess.Popen(
            [str(PY), str(ROOT / "main.py"), "--instance", str(n)],
            stdout=out,
            stderr=out,
            stdin=subprocess.DEVNULL,
            env=env,
            cwd=str(ROOT),
            start_new_session=True,
        )
    finally:
        try:
            out.close()
        except Exception:
            pass
    pidfile(n).write_text(f"{proc.pid}\n", encoding="utf-8")
    started_file(n).write_text(f"{int(time.time())}\n", encoding="utf-8")
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
    RUNTIME.mkdir(parents=True, exist_ok=True)
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


def holder_id() -> int | None:
    path = RUNTIME / "leader.lease"
    try:
        raw = path.read_text(encoding="utf-8").splitlines()
        if not raw:
            return None
        hid = int(raw[0].strip())
        expires = float(raw[1].strip()) if len(raw) > 1 else 0.0
        if expires and expires <= time.time():
            return None
        return hid
    except Exception:
        return None


def health_report(now: float | None = None) -> tuple[int, str]:
    """File-only health. No sqlite. Exit 0 iff a live leader is fresh.

    Output:
      ok leader=<id>
      instance 0: leader pid=<pid> loop=<n>s scanner=<n>s risk=<n>s ok
      instance 1: standby pid=<pid> loop=<n>s scanner=<n>s risk=<n>s ok

    Or first line "not running" (exit 1) / "stale leader=<id>" (exit 2).
    A down instance is "instance N: down". Flag is ok or STALE.
    Ages come from tmpfs heartbeat files, not sqlite.
    desk-health should restart only when the exit code is not 0.
    """
    now = time.time() if now is None else now
    lines: list[str] = []
    any_live = False
    leader = holder_id()
    leader_ok = False
    for n in INSTANCES:
        pid = read_pid(n)
        if not is_main(pid):
            lines.append(f"instance {n}: down")
            continue
        any_live = True
        ages = []
        stale = False
        parts = []
        for label, path in (("loop", live_file(n)), ("scanner", hb_file(n, "scanner")), ("risk", hb_file(n, "risk"))):
            age = file_age(path, now)
            if age is None:
                parts.append(f"{label}=missing")
                stale = True
            else:
                parts.append(f"{label}={age:.0f}s")
                if age > HEALTH_STALE_S:
                    stale = True
        role = "leader" if leader == n else "standby"
        flag = "STALE" if stale else "ok"
        if role == "leader" and not stale:
            leader_ok = True
        lines.append(f"instance {n}: {role} pid={pid} {' '.join(parts)} {flag}")
    if not any_live:
        return 1, "not running\n" + "\n".join(lines) + "\n"
    if leader_ok:
        return 0, f"ok leader={leader}\n" + "\n".join(lines) + "\n"
    if leader is None:
        return 2, "stale leader=none\n" + "\n".join(lines) + "\n"
    return 2, f"stale leader={leader}\n" + "\n".join(lines) + "\n"


def _detach_stdio() -> None:
    """Inherited nohup may point at logs/desk.stdout on ext4; leave it."""
    RUNTIME.mkdir(parents=True, exist_ok=True)
    path = LOG
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.dup2(fd, 1)
        os.dup2(fd, 2)
    finally:
        if fd > 2:
            os.close(fd)


def main() -> None:
    if sys.argv[1:] == ["health"]:
        code, text = health_report()
        sys.stdout.write(text)
        sys.exit(code)
    if not os.environ.get("TYPESAFE_API_KEY") or not os.environ.get("TG_TOKEN"):
        sys.stderr.write("TYPESAFE_API_KEY or TG_TOKEN missing\n")
        sys.exit(1)
    if not PY.is_file():
        sys.stderr.write(f"missing venv at {PY}\n")
        sys.exit(1)
    _detach_stdio()
    sys.exit(loop())


if __name__ == "__main__":
    main()
