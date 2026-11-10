#!/usr/bin/env bash
# Idempotent start/stop/status for two desk instances (hot standby).
# start launches a supervisor that relaunches either instance if it exits
# or if its heartbeat files go stale. stop writes a flag so the
# supervisor does not relaunch, then signals every process.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
SUPFILE="$ROOT/data/supervisor.pid"
RUNTIME="${DESK_RUNTIME:-/dev/shm/fomo-desk}"
STOPFILE="$RUNTIME/stop"
LOG="$ROOT/logs/desk.stdout"
PY="$ROOT/.venv/bin/python"
INSTANCES="0 1"
STALE_S=8
GRACE_S=20
POLL_S=1
mkdir -p "$ROOT/data" "$ROOT/logs" "${DESK_RUNTIME:-/dev/shm/fomo-desk}/0" "${DESK_RUNTIME:-/dev/shm/fomo-desk}/1"

load_secrets() {
  if [[ -f /home/box/.desk_secrets ]]; then
    set -a
    # shellcheck disable=SC1091
    source /home/box/.desk_secrets
    set +a
  fi
}

RUNTIME="${DESK_RUNTIME:-/dev/shm/fomo-desk}"

instance_pidfile() {
  echo "$RUNTIME/$1/desk.pid"
}

instance_live() {
  echo "$RUNTIME/$1/liveness"
}

instance_hb() {
  echo "$RUNTIME/$1/$2.hb"
}

is_pid_main() {
  local pid="$1"
  [[ -n "${pid:-}" ]] && kill -0 "$pid" 2>/dev/null && \
    tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null | grep -q "main.py"
}

instance_pid() {
  local n="$1" pf pid
  pf="$(instance_pidfile "$n")"
  if [[ -f "$pf" ]]; then
    pid="$(cat "$pf" 2>/dev/null || true)"
    if is_pid_main "$pid"; then
      echo "$pid"
      return 0
    fi
  fi
  return 1
}

all_pids() {
  local n pid
  for n in $INSTANCES; do
    if pid="$(instance_pid "$n")"; then
      echo "$pid"
    fi
  done
}

is_running() {
  # First live instance, for status compatibility.
  local n pid
  for n in $INSTANCES; do
    if pid="$(instance_pid "$n")"; then
      echo "$pid"
      return 0
    fi
  done
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
      if tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null | grep -qE "supervise.py|run.sh|_supervise"; then
        echo "$pid"
        return 0
      fi
      echo "$pid"
      return 0
    fi
  fi
  return 1
}

stamp_age() {
  local f="$1" now
  now="$(date +%s)"
  if [[ ! -f "$f" ]]; then
    echo "missing"
    return 0
  fi
  echo "$(( now - $(stat -c %Y "$f") ))"
}

cmd="${1:-start}"
case "$cmd" in
  status)
    found=0
    for n in $INSTANCES; do
      if pid="$(instance_pid "$n")"; then
        echo "running instance=$n pid=$pid"
        found=1
      else
        echo "not running instance=$n"
      fi
    done
    if [[ "$found" -eq 1 ]]; then
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
    for n in $INSTANCES; do
      if pid="$(instance_pid "$n")"; then
        kill "$pid" 2>/dev/null || true
      fi
    done
    # also catch a stray main.py
    if pid="$(is_running)"; then
      kill "$pid" 2>/dev/null || true
    fi
    for i in 1 2 3 4 5; do
      any=0
      for n in $INSTANCES; do
        if pid="$(instance_pid "$n")"; then
          any=1
          break
        fi
      done
      if [[ "$any" -eq 0 ]]; then
        break
      fi
      sleep 1
    done
    for n in $INSTANCES; do
      if pid="$(instance_pid "$n")"; then
        kill -9 "$pid" 2>/dev/null || true
      fi
    done
    pkill -9 -f "$ROOT/main.py" 2>/dev/null || true
    if sup="$(is_supervisor)"; then
      kill -9 "$sup" 2>/dev/null || true
    fi
    rm -f "$ROOT/data/desk.pid" "$SUPFILE"
    for n in $INSTANCES; do
      rm -f "$(instance_pidfile "$n")"
    done
    echo "stopped"
    ;;
  restart)
    "$0" stop || true
    exec "$0" start
    ;;
  health)
    # File-only, no sqlite. See supervise.health_report for the format.
    exec "$PY" "$ROOT/supervise.py" health
    ;;
  _supervise)
    # External supervisor: file heartbeats only, never sqlite.
    load_secrets
    if [[ -z "${TYPESAFE_API_KEY:-}" || -z "${TG_TOKEN:-}" ]]; then
      echo "TYPESAFE_API_KEY or TG_TOKEN missing" >&2
      exit 1
    fi
    exec "$PY" "$ROOT/supervise.py"
    ;;
  start|*)
    if pid="$(is_running)"; then
      # already have at least one; start supervisor only if missing
      if is_supervisor >/dev/null; then
        echo "already running pid=$pid"
        exit 0
      fi
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
    mkdir -p "$RUNTIME"
    rm -f "$STOPFILE"
    setsid nohup "$0" _supervise >>"$RUNTIME/supervisor.log" 2>&1 < /dev/null &
    echo $! > "$SUPFILE"
    for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
      ok=1
      for n in $INSTANCES; do
        if ! instance_pid "$n" >/dev/null; then
          ok=0
        fi
      done
      if [[ "$ok" -eq 1 ]]; then
        echo "started pids=$(all_pids | tr '\n' ',' | sed 's/,$//')"
        exit 0
      fi
      sleep 0.3
    done
    echo "failed to start; see $LOG" >&2
    exit 1
    ;;
esac
