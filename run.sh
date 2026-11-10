#!/usr/bin/env bash
# Idempotent start/stop/status for two desk instances (hot standby).
# start launches a supervisor that relaunches either instance if it exits
# or if its heartbeat files go stale. stop writes a flag so the
# supervisor does not relaunch, then signals every process.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
SUPFILE="$ROOT/data/supervisor.pid"
STOPFILE="$ROOT/data/desk.stop"
LOG="$ROOT/logs/desk.stdout"
PY="$ROOT/.venv/bin/python"
INSTANCES="0 1"
STALE_S=8
GRACE_S=20
POLL_S=1
mkdir -p "$ROOT/data" "$ROOT/logs" "$ROOT/data/i/0" "$ROOT/data/i/1"

load_secrets() {
  if [[ -f /home/box/.desk_secrets ]]; then
    set -a
    # shellcheck disable=SC1091
    source /home/box/.desk_secrets
    set +a
  fi
}

instance_pidfile() {
  echo "$ROOT/data/i/$1/desk.pid"
}

instance_live() {
  echo "$ROOT/data/i/$1/liveness"
}

instance_hb() {
  echo "$ROOT/data/i/$1/$2.hb"
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
      if tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null | grep -qE "run.sh|_supervise"; then
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
    if ! is_running >/dev/null; then
      echo "not running"
      exit 1
    fi
    leader="none"
    if [[ -f "$ROOT/data/leader.lease" ]]; then
      leader="$(head -n1 "$ROOT/data/leader.lease" 2>/dev/null || echo none)"
    fi
    ok=1
    leader_ok=0
    echo "ok leader=${leader}"
    for n in $INSTANCES; do
      if pid="$(instance_pid "$n")"; then
        loop_a="$(stamp_age "$(instance_live "$n")")"
        sc_a="$(stamp_age "$(instance_hb "$n" scanner)")"
        rk_a="$(stamp_age "$(instance_hb "$n" risk)")"
        flag="ok"
        role="standby"
        if [[ "$leader" == "$n" ]]; then
          role="leader"
        fi
        stale_inst=0
        for a in "$loop_a" "$sc_a" "$rk_a"; do
          if [[ "$a" == "missing" || "$a" -gt 180 ]]; then
            stale_inst=1
          fi
        done
        if [[ "$stale_inst" -eq 1 ]]; then
          flag="STALE"
          if [[ "$role" == "leader" ]]; then
            ok=0
          fi
        elif [[ "$role" == "leader" ]]; then
          leader_ok=1
        fi
        echo "instance $n: $role pid=$pid loop=${loop_a}s scanner=${sc_a}s risk=${rk_a}s $flag"
      else
        echo "instance $n: down"
        if [[ "$leader" == "$n" ]]; then
          ok=0
        fi
      fi
    done
    # sqlite details when possible, 5s busy timeout already set in db.init
    if "$PY" - <<'PY'
import time, sys
sys.path.insert(0, ".")
import db
try:
    db.init()
except Exception as e:
    print(f"heartbeats: unavailable ({type(e).__name__})")
    sys.exit(0)
now = time.time()
rows = db.q("SELECT name, ts, status, detail FROM heartbeats")
required = {"scanner", "risk"}
ok = True
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
sys.exit(0)
PY
    then
      :
    fi
    # Healthy if a live leader is fresh, or if any instance is fresh while lease is catching up.
    if [[ "$leader_ok" -eq 1 ]]; then
      exit 0
    fi
    if [[ "$ok" -eq 1 ]] && is_running >/dev/null; then
      exit 0
    fi
    exit 2
    ;;
  _supervise)
    trap 'touch "$STOPFILE"; for n in '"$INSTANCES"'; do pf="$ROOT/data/i/$n/desk.pid"; if [[ -f "$pf" ]]; then kill "$(cat "$pf")" 2>/dev/null || true; fi; done; exit 0' TERM INT
    rm -f "$STOPFILE"
    declare -A started=()
    launch_one() {
      local n="$1"
      load_secrets
      if [[ -z "${TYPESAFE_API_KEY:-}" || -z "${TG_TOKEN:-}" ]]; then
        echo "TYPESAFE_API_KEY or TG_TOKEN missing" >&2
        return 1
      fi
      mkdir -p "$ROOT/data/i/$n"
      rm -f "$(instance_live "$n")"
      DESK_INSTANCE="$n" "$PY" "$ROOT/main.py" --instance "$n" >>"$LOG" 2>&1 < /dev/null &
      echo $! > "$(instance_pidfile "$n")"
      date +%s > "$ROOT/data/i/$n/started"
      started["$n"]="$(date +%s)"
      if [[ "$n" == "0" ]]; then
        echo $! > "$ROOT/data/desk.pid"
      fi
    }
    for n in $INSTANCES; do
      launch_one "$n"
    done
    while true; do
      if [[ -f "$STOPFILE" ]]; then
        exit 0
      fi
      now="$(date +%s)"
      for n in $INSTANCES; do
        pid="$(cat "$(instance_pidfile "$n")" 2>/dev/null || true)"
        live=0
        if is_pid_main "${pid:-}"; then
          live=1
        fi
        if [[ "$live" -eq 0 ]]; then
          if [[ -n "${pid:-}" ]]; then
            echo "$(date '+%Y-%m-%d %H:%M:%S') desk supervisor: instance $n exited; relaunching in 2s" >>"$LOG"
            sleep 2
          fi
          if [[ -f "$STOPFILE" ]]; then
            exit 0
          fi
          launch_one "$n"
          continue
        fi
        s="$(cat "$ROOT/data/i/$n/started" 2>/dev/null || echo "${started[$n]:-$now}")"
        if [ $((now - s)) -lt "$GRACE_S" ]; then
          continue
        fi
        lf="$(instance_live "$n")"
        sf="$(instance_hb "$n" scanner)"
        rf="$(instance_hb "$n" risk)"
        reason=""
        if [ ! -f "$lf" ]; then
          reason="liveness file missing"
        else
          age=$(( now - $(stat -c %Y "$lf") ))
          if [ "$age" -gt "$STALE_S" ]; then
            reason="liveness stale ${age}s"
          fi
        fi
        if [ -z "$reason" ] && [ -f "$sf" ]; then
          age=$(( now - $(stat -c %Y "$sf") ))
          if [ "$age" -gt "$STALE_S" ]; then
            reason="scanner heartbeat stale ${age}s"
          fi
        fi
        if [ -z "$reason" ] && [ -f "$rf" ]; then
          age=$(( now - $(stat -c %Y "$rf") ))
          if [ "$age" -gt "$STALE_S" ]; then
            reason="risk heartbeat stale ${age}s"
          fi
        fi
        if [[ -n "$reason" ]]; then
          echo "$(date '+%Y-%m-%d %H:%M:%S') desk supervisor: instance $n $reason; kill -9" >>"$LOG"
          kill -9 "$pid" 2>/dev/null || true
          sleep 1
          if [[ -f "$STOPFILE" ]]; then
            exit 0
          fi
          launch_one "$n"
        fi
      done
      sleep "$POLL_S"
    done
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
    rm -f "$STOPFILE"
    setsid nohup "$0" _supervise >>"$LOG" 2>&1 < /dev/null &
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
