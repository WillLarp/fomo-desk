#!/usr/bin/env bash
# Idempotent start/stop/status for the desk process.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
PIDFILE="$ROOT/data/desk.pid"
LOG="$ROOT/logs/desk.stdout"
PY="$ROOT/.venv/bin/python"
mkdir -p "$ROOT/data" "$ROOT/logs"

is_running() {
  if [[ -f "$PIDFILE" ]]; then
    local pid
    pid="$(cat "$PIDFILE" 2>/dev/null || true)"
    if [[ -n "${pid:-}" ]] && kill -0 "$pid" 2>/dev/null; then
      # confirm it is our process
      if tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null | grep -q "main.py"; then
        echo "$pid"
        return 0
      fi
    fi
  fi
  # fallback: find existing
  local p
  p="$(pgrep -f "$ROOT/main.py" | head -n1 || true)"
  if [[ -n "${p:-}" ]]; then
    echo "$p"
    return 0
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
    if pid="$(is_running)"; then
      kill "$pid" 2>/dev/null || true
      for i in 1 2 3 4 5; do
        kill -0 "$pid" 2>/dev/null || break
        sleep 1
      done
      kill -9 "$pid" 2>/dev/null || true
      rm -f "$PIDFILE"
      echo "stopped"
    else
      echo "not running"
    fi
    ;;
  restart)
    "$0" stop || true
    exec "$0" start
    ;;
  health)
    if pid="$(is_running)"; then
      echo "ok pid=$pid"
      if [[ -f "$ROOT/data/publish/d/.keep" ]]; then
        true
      fi
      # heartbeat freshness
      "$PY" - <<'PY'
import time, sys
sys.path.insert(0, ".")
import db
db.init()
now = time.time()
rows = db.q("SELECT name, ts, status, detail FROM heartbeats")
ok = True
for r in rows:
    age = now - (r["ts"] or 0)
    flag = "ok" if age < 180 else "STALE"
    if flag != "ok":
        ok = False
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
  start|*)
    if pid="$(is_running)"; then
      echo "already running pid=$pid"
      exit 0
    fi
    if [[ ! -x "$PY" ]]; then
      echo "missing venv at $PY" >&2
      exit 1
    fi
    # load secrets (DATA_PATH, tokens) without printing them
    if [[ -f /home/box/.desk_secrets ]]; then
      set -a
      # shellcheck disable=SC1091
      source /home/box/.desk_secrets
      set +a
    fi
    if [[ -z "${TYPESAFE_API_KEY:-}" || -z "${TG_TOKEN:-}" ]]; then
      echo "TYPESAFE_API_KEY or TG_TOKEN missing" >&2
      exit 1
    fi
    # survive this shell
    setsid nohup "$PY" "$ROOT/main.py" >>"$LOG" 2>&1 < /dev/null &
    echo $! > "$PIDFILE"
    sleep 0.3
    if pid="$(is_running)"; then
      echo "started pid=$pid"
    else
      echo "failed to start; see $LOG" >&2
      exit 1
    fi
    ;;
esac
