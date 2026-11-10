#!/usr/bin/env bash
# Idempotent start/stop/status for the desk process.
# start launches a supervisor that relaunches main.py if it exits
# (watchdog self-restart or a crash). stop writes a flag so the
# supervisor does not relaunch, then signals both processes.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
PIDFILE="$ROOT/data/desk.pid"
SUPFILE="$ROOT/data/supervisor.pid"
STOPFILE="$ROOT/data/desk.stop"
LOG="$ROOT/logs/desk.stdout"
PY="$ROOT/.venv/bin/python"
mkdir -p "$ROOT/data" "$ROOT/logs"

load_secrets() {
  if [[ -f /home/box/.desk_secrets ]]; then
    set -a
    # shellcheck disable=SC1091
    source /home/box/.desk_secrets
    set +a
  fi
}

is_running() {
  if [[ -f "$PIDFILE" ]]; then
    local pid
    pid="$(cat "$PIDFILE" 2>/dev/null || true)"
    if [[ -n "${pid:-}" ]] && kill -0 "$pid" 2>/dev/null; then
      if tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null | grep -q "main.py"; then
        echo "$pid"
        return 0
      fi
    fi
  fi
  local p
  p="$(pgrep -f "$ROOT/main.py" | head -n1 || true)"
  if [[ -n "${p:-}" ]]; then
    echo "$p"
    return 0
  fi
  return 1
}

is_supervisor() {
  if [[ -f "$SUPFILE" ]]; then
    local pid
    pid="$(cat "$SUPFILE" 2>/dev/null || true)"
    if [[ -n "${pid:-}" ]] && kill -0 "$pid" 2>/dev/null; then
      if tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null | grep -qE "run.sh|_supervise"; then
        echo "$pid"
        return 0
      fi
      # still treat as supervisor if the pid file points at a live bash
      echo "$pid"
      return 0
    fi
  fi
  return 1
}

cmd="${1:-start}"
case "$cmd" in
  status)
    if pid="$(is_running)"; then
      echo "running pid=$pid"
      exit 0
    fi
    echo "not running"
    exit 1
    ;;
  stop)
    touch "$STOPFILE"
    if sup="$(is_supervisor)"; then
      kill "$sup" 2>/dev/null || true
    fi
    if pid="$(is_running)"; then
      kill "$pid" 2>/dev/null || true
      for i in 1 2 3 4 5; do
        kill -0 "$pid" 2>/dev/null || break
        sleep 1
      done
      kill -9 "$pid" 2>/dev/null || true
    fi
    if sup="$(is_supervisor)"; then
      kill -9 "$sup" 2>/dev/null || true
    fi
    rm -f "$PIDFILE" "$SUPFILE"
    echo "stopped"
    ;;
  restart)
    "$0" stop || true
    exec "$0" start
    ;;
  health)
    if pid="$(is_running)"; then
      echo "ok pid=$pid"
      # scanner and risk must stay fresh; other agents may be idle
      "$PY" - <<'PY'
import time, sys
sys.path.insert(0, ".")
import db
db.init()
now = time.time()
rows = db.q("SELECT name, ts, status, detail FROM heartbeats")
ok = True
required = {"scanner", "risk"}
for r in rows:
    age = now - (r["ts"] or 0)
    need = r["name"] in required
    if need:
        flag = "ok" if age < 180 else "STALE"
        if flag != "ok":
            ok = False
    else:
        flag = "ok" if age < 180 else "idle"
    print(f"{r['name']}: {r['status']} {r['detail'] or ''} age={age:.0f}s {flag}")
if not rows:
    print("no heartbeats yet")
sys.exit(0 if ok or not rows else 2)
PY
      exit $?
    else
      echo "not running"
      exit 1
    fi
    ;;
  _supervise)
    # Internal: relaunch main.py until STOPFILE appears.
    trap 'touch "$STOPFILE"; if [[ -f "$PIDFILE" ]]; then kill "$(cat "$PIDFILE")" 2>/dev/null || true; fi; exit 0' TERM INT
    rm -f "$STOPFILE"
    while true; do
      if [[ -f "$STOPFILE" ]]; then
        exit 0
      fi
      load_secrets
      if [[ -z "${TYPESAFE_API_KEY:-}" || -z "${TG_TOKEN:-}" ]]; then
        echo "TYPESAFE_API_KEY or TG_TOKEN missing" >&2
        exit 1
      fi
      "$PY" "$ROOT/main.py" >>"$LOG" 2>&1 < /dev/null &
      echo $! > "$PIDFILE"
      set +e
      wait $! 2>/dev/null
      code=$?
      set -e
      if [[ -f "$STOPFILE" ]]; then
        exit 0
      fi
      echo "$(date '+%Y-%m-%d %H:%M:%S') desk supervisor: exited code=${code}; relaunching in 2s" >>"$LOG"
      sleep 2
    done
    ;;
  start|*)
    if pid="$(is_running)"; then
      echo "already running pid=$pid"
      exit 0
    fi
    if [[ ! -x "$PY" ]]; then
      echo "missing venv at $PY" >&2
      exit 1
    fi
    load_secrets
    if [[ -z "${TYPESAFE_API_KEY:-}" || -z "${TG_TOKEN:-}" ]]; then
      echo "TYPESAFE_API_KEY or TG_TOKEN missing" >&2
      exit 1
    fi
    rm -f "$STOPFILE"
    setsid nohup "$0" _supervise >>"$LOG" 2>&1 < /dev/null &
    echo $! > "$SUPFILE"
    for i in 1 2 3 4 5 6 7 8 9 10; do
      if pid="$(is_running)"; then
        echo "started pid=$pid"
        exit 0
      fi
      sleep 0.3
    done
    echo "failed to start; see $LOG" >&2
    exit 1
    ;;
esac
