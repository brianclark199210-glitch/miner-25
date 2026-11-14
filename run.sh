#!/usr/bin/env bash
# SPDX-License-Identifier: MPL-2.0
#
# SN25 (UR) miner launcher — PM2 mode.
#
#   ./run.sh                 # ensure miner is up under PM2 (idempotent)
#   ./run.sh start           # PM2: provider provide   (safe to run twice)
#   ./run.sh all             # PM2: --all-operators    (safe to run twice)
#   ./run.sh stop            # pm2 stop + delete
#   ./run.sh restart         # pm2 restart
#   ./run.sh logs            # pm2 logs, or journal if the installer owns it
#   ./run.sh status|doctor   # health checks
#   ./run.sh dashboard       # local miner / project dashboard (http)
#   ./run.sh setup|operators|auth|wallet|claim …
#   ./run.sh build
#
# Double-start is a no-op: if the miner is already online (PM2, installer
# service, or live status file), this script exits 0 and leaves it running.
#
# Config (.env next to this script, or environment):
#   PROVIDER  HOTKEY_SEED_FILE  COLDKEY_SS58  COLDKEY_SEED_FILE
#   MAX_MEMORY  PORT  OPERATORS_URL  URNETWORK_STATE_DIR
#   PM2_NAME          default: sn25-miner
#   PM2_NAME_ALL      default: sn25-miner-all
#   SN25_TAKEOVER=1   stop installer/other owners before a fresh PM2 start
#   DASHBOARD_HOST    default: 0.0.0.0 (reachable on this host's public IP)
#   DASHBOARD_PORT    default: 8787
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd -P)"
cd "$ROOT"

# shellcheck disable=SC1091
[[ -f "$ROOT/.env" ]] && . "$ROOT/.env"

HOTKEY_SEED_FILE="${HOTKEY_SEED_FILE:-$ROOT/.secrets/hotkey.seed}"
COLDKEY_SEED_FILE="${COLDKEY_SEED_FILE:-$ROOT/.secrets/coldkey.seed}"
COLDKEY_SS58="${COLDKEY_SS58:-}"
MAX_MEMORY="${MAX_MEMORY:-}"
PORT="${PORT:-0}"
OPERATORS_URL="${OPERATORS_URL:-}"
PM2_NAME="${PM2_NAME:-sn25-miner}"
PM2_NAME_ALL="${PM2_NAME_ALL:-sn25-miner-all}"
SN25_TAKEOVER="${SN25_TAKEOVER:-1}"
PM2_HOME_DIR="${PM2_HOME:-$HOME/.pm2}"
DASHBOARD_HOST="${DASHBOARD_HOST:-0.0.0.0}"
DASHBOARD_PORT="${DASHBOARD_PORT:-8787}"

log()  { printf '%s [run.sh] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
err()  { printf '%s [run.sh] ERROR: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2; }
die()  { err "$*"; exit 1; }

find_provider() {
  local c
  if [[ -n "${PROVIDER:-}" && -x "$PROVIDER" ]]; then
    printf '%s\n' "$PROVIDER"
    return 0
  fi
  for c in \
    "$HOME/.local/share/urnetwork-provider/bin/urnetwork" \
    "$HOME/.local/bin/provider" \
    "$HOME/.local/bin/urnetwork" \
    "$ROOT/miner/provider" \
    "$ROOT/provider" \
    "$ROOT/cli/miner/miner"
  do
    [[ -x "$c" ]] || continue
    printf '%s\n' "$c"
    return 0
  done
  command -v provider 2>/dev/null && return 0
  command -v urnetwork 2>/dev/null && return 0
  return 1
}

PROVIDER_BIN="$(find_provider || true)"

require_provider() {
  [[ -n "$PROVIDER_BIN" && -x "$PROVIDER_BIN" ]] || die \
    "provider binary not found. Install the official miner, place miner/provider, or: ./run.sh build"
}

require_pm2() {
  command -v pm2 >/dev/null 2>&1 || die "pm2 not found. Install with: npm i -g pm2"
}

provider() {
  require_provider
  "$PROVIDER_BIN" "$@"
}

# --- running-state detection -------------------------------------------------

status_file_alive() {
  local status="${1:-${URNETWORK_STATE_DIR:-$HOME/.urnetwork}/miner-status.json}"
  [[ -f "$status" ]] || return 1
  python3 - "$status" <<'PY' 2>/dev/null
import json, os, sys
from datetime import datetime, timezone
s = json.load(open(sys.argv[1]))
pid = int(s.get("pid") or 0)
if pid <= 0 or s.get("stopped_at"):
    sys.exit(1)
try:
    os.kill(pid, 0)
except OSError:
    sys.exit(1)
updated = s.get("updated_at")
if updated:
    try:
        t = datetime.fromisoformat(updated.replace("Z", "+00:00"))
        if (datetime.now(timezone.utc) - t).total_seconds() > 180:
            sys.exit(1)
    except Exception:
        pass
sys.exit(0)
PY
}

installer_active() {
  systemctl --user is-active --quiet urnetwork.service 2>/dev/null
}

pm2_id() {
  # Prints the PM2 id for name, or empty.
  local name="$1"
  pm2 jlist 2>/dev/null | python3 -c '
import json,sys
name=sys.argv[1]
try:
    apps=json.load(sys.stdin)
except Exception:
    raise SystemExit(0)
for a in apps:
    if a.get("name")==name:
        print(a.get("pm_id",""))
        break
' "$name" 2>/dev/null || true
}

pm2_online() {
  local name="$1"
  pm2 jlist 2>/dev/null | python3 -c '
import json,sys
name=sys.argv[1]
try:
    apps=json.load(sys.stdin)
except Exception:
    raise SystemExit(1)
for a in apps:
    if a.get("name")==name and (a.get("pm2_env") or {}).get("status")=="online":
        raise SystemExit(0)
raise SystemExit(1)
' "$name" 2>/dev/null
}

# Any live owner of the provider credential (PM2, installer, or status file).
miner_already_running() {
  pm2_online "$PM2_NAME" && return 0
  pm2_online "$PM2_NAME_ALL" && return 0
  installer_active && return 0
  status_file_alive && return 0
  return 1
}

# provide --all-operators writes each operator's miner-status.json under
# operators/<domain>, not at the top of the state directory. provider status
# with no override reads the top, which still describes the installer after
# that service has stopped.
live_operator_state_dirs() {
  local base="${URNETWORK_STATE_DIR:-$HOME/.urnetwork}/operators" dir nullglob_was=0
  [[ -d "$base" ]] || return 0
  shopt -q nullglob && nullglob_was=1
  shopt -s nullglob
  for dir in "$base"/*/; do
    if status_file_alive "${dir}miner-status.json"; then
      printf '%s\n' "${dir%/}"
    fi
  done
  [[ "$nullglob_was" == 1 ]] || shopt -u nullglob
}

# Status for the process that is actually mining. The all-operators PM2 app
# is one supervisor plus a provider per operator; the installer's status file
# is a different process and must not be reported as that app.
show_status() {
  if pm2_online "$PM2_NAME_ALL"; then
    local dir found=0 code=0
    while IFS= read -r dir; do
      [[ -n "$dir" ]] || continue
      found=1
      log "operator $(basename "$dir")"
      URNETWORK_STATE_DIR="$dir" provider status "$@" || code=$?
    done < <(live_operator_state_dirs)
    if [[ "$found" == 0 ]]; then
      err "$PM2_NAME_ALL is online in pm2, but no operator miner is up"
      return 1
    fi
    return "$code"
  fi
  provider status "$@"
}

show_running() {
  log "miner already running — leaving it alone (double-start is a no-op)"
  if pm2_online "$PM2_NAME"; then
    pm2 describe "$PM2_NAME" | head -20 || true
  fi
  if pm2_online "$PM2_NAME_ALL"; then
    pm2 describe "$PM2_NAME_ALL" | head -20 || true
  fi
  show_status || true
}

# --- stop helpers ------------------------------------------------------------

stop_installer() {
  if [[ -f "$HOME/.config/systemd/user/urnetwork.service" ]]; then
    if installer_active || systemctl --user is-enabled --quiet urnetwork.service 2>/dev/null; then
      log "stopping installer urnetwork.service (credential must have one owner)"
      systemctl --user disable --now urnetwork.service 2>/dev/null || systemctl --user stop urnetwork.service 2>/dev/null || true
    fi
  fi
  # Also stop any stray provide that still holds the status file.
  local status="${URNETWORK_STATE_DIR:-$HOME/.urnetwork}/miner-status.json"
  if [[ -f "$status" ]]; then
    local pid
    pid="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("pid") or "")' "$status" 2>/dev/null || true)"
    if [[ -n "${pid:-}" ]] && kill -0 "$pid" 2>/dev/null; then
      # Don't kill our own PM2 child — only non-pm2 owners.
      if ! pm2 jlist 2>/dev/null | python3 -c '
import json,sys
pid=int(sys.argv[1])
apps=json.load(sys.stdin)
for a in apps:
    if int((a.get("pid") or 0))==pid:
        raise SystemExit(0)
raise SystemExit(1)
' "$pid" 2>/dev/null; then
        log "stopping non-pm2 miner pid=$pid"
        kill "$pid" 2>/dev/null || true
        sleep 1
        kill -9 "$pid" 2>/dev/null || true
      fi
    fi
  fi
}

pm2_delete_quiet() {
  local name="$1"
  if [[ -n "$(pm2_id "$name")" ]]; then
    pm2 delete "$name" >/dev/null 2>&1 || true
  fi
}

cmd_stop() {
  require_pm2
  stop_installer
  pm2_delete_quiet "$PM2_NAME"
  pm2_delete_quiet "$PM2_NAME_ALL"
  pm2 save --force >/dev/null 2>&1 || true
  log "stopped"
}

# --- PM2 start ---------------------------------------------------------------

common_provide_args() {
  local -a args=()
  [[ -n "$MAX_MEMORY" ]] && args+=(--max-memory="$MAX_MEMORY")
  [[ "$PORT" != "0" && -n "$PORT" ]] && args+=(--port="$PORT")
  printf '%s\0' "${args[@]}"
}

# Start (or keep) one PM2 app. Idempotent: online → success without restart.
pm2_ensure() {
  local name="$1"
  shift
  # Remaining args: provide argv after binary (e.g. provide --all-operators …)
  local -a provide_args=("$@")

  require_pm2
  require_provider

  if pm2_online "$name"; then
    show_running
    return 0
  fi

  # Takeover is the point of PM2 mode: the installer or a stray provide must
  # not make start a no-op, or this process never becomes the owner.
  if [[ "$SN25_TAKEOVER" == "1" ]]; then
    stop_installer
    if [[ "$name" == "$PM2_NAME" ]]; then
      pm2_delete_quiet "$PM2_NAME_ALL"
    else
      pm2_delete_quiet "$PM2_NAME"
    fi
  elif miner_already_running; then
    show_running
    return 0
  fi

  # Stale stopped entry with same name.
  pm2_delete_quiet "$name"

  mkdir -p "$ROOT/.secrets" "$ROOT/logs"
  chmod 700 "$ROOT/.secrets" 2>/dev/null || true

  local -a env_args=()
  [[ -n "${URNETWORK_STATE_DIR:-}" ]] && env_args+=(--env "URNETWORK_STATE_DIR=$URNETWORK_STATE_DIR")

  log "pm2 start name=$name → $PROVIDER_BIN ${provide_args[*]}"
  pm2 start "$PROVIDER_BIN" \
    --name "$name" \
    --interpreter none \
    --kill-timeout 20000 \
    --restart-delay 30000 \
    --max-restarts 50 \
    --exp-backoff-restart-delay 100 \
    "${env_args[@]}" \
    -- "${provide_args[@]}"

  pm2 save --force >/dev/null 2>&1 || true
  sleep 2
  pm2 describe "$name" | head -40 || pm2 list
  provider status 2>/dev/null || true
}

cmd_start() {
  local -a flags=(provide)
  local f
  while IFS= read -r -d '' f; do
    [[ -n "$f" ]] && flags+=("$f")
  done < <(common_provide_args)
  flags+=("$@")
  pm2_ensure "$PM2_NAME" "${flags[@]}"
}

cmd_all() {
  [[ -f "$HOTKEY_SEED_FILE" ]] || die "hotkey seed missing: $HOTKEY_SEED_FILE"
  local -a flags=(
    provide
    --all-operators
    --auto-register
    --hotkey_seed_file="$HOTKEY_SEED_FILE"
  )
  local f
  while IFS= read -r -d '' f; do
    [[ -n "$f" ]] && flags+=("$f")
  done < <(common_provide_args)
  [[ -n "$OPERATORS_URL" ]] && flags+=(--operators-url="$OPERATORS_URL")
  flags+=("$@")
  pm2_ensure "$PM2_NAME_ALL" "${flags[@]}"
}

cmd_default() {
  # A live PM2 app is the owner. Otherwise start one, taking the credential
  # from the installer when SN25_TAKEOVER=1.
  if pm2_online "$PM2_NAME" || pm2_online "$PM2_NAME_ALL"; then
    show_running
    return 0
  fi
  if [[ "$SN25_TAKEOVER" != "1" ]] && miner_already_running; then
    show_running
    return 0
  fi
  cmd_start "$@"
}

cmd_restart() {
  require_pm2
  if pm2_online "$PM2_NAME"; then
    pm2 restart "$PM2_NAME"
  elif pm2_online "$PM2_NAME_ALL"; then
    pm2 restart "$PM2_NAME_ALL"
  elif [[ -n "$(pm2_id "$PM2_NAME")" ]]; then
    pm2 restart "$PM2_NAME"
  elif [[ -n "$(pm2_id "$PM2_NAME_ALL")" ]]; then
    pm2 restart "$PM2_NAME_ALL"
  else
    log "no pm2 miner app — starting"
    cmd_start "$@"
    return 0
  fi
  sleep 1
  provider status 2>/dev/null || true
}

cmd_logs() {
  if pm2_online "$PM2_NAME" || [[ -n "$(pm2_id "$PM2_NAME")" ]]; then
    require_pm2
    pm2 logs "$PM2_NAME" "$@"
  elif pm2_online "$PM2_NAME_ALL" || [[ -n "$(pm2_id "$PM2_NAME_ALL")" ]]; then
    require_pm2
    pm2 logs "$PM2_NAME_ALL" "$@"
  elif installer_active; then
    # ./run.sh start is a no-op while urnetwork.service holds the credential,
    # so logs must follow that unit instead of demanding a PM2 app.
    local -a jargs=(--user -u urnetwork.service -f)
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --lines|-n)
          [[ $# -ge 2 ]] || die "--lines needs a value"
          jargs+=(-n "$2")
          shift 2
          ;;
        --lines=*)
          jargs+=(-n "${1#*=}")
          shift
          ;;
        --nostream)
          local -a kept=() a
          for a in "${jargs[@]}"; do
            [[ "$a" == "-f" ]] || kept+=("$a")
          done
          jargs=("${kept[@]}")
          shift
          ;;
        *)
          die "logs: unknown arg: $1"
          ;;
      esac
    done
    local follow=0 a
    for a in "${jargs[@]}"; do
      [[ "$a" == "-f" ]] && follow=1
    done
    if [[ "$follow" == 1 ]]; then
      log "miner is urnetwork.service — following its journal (Ctrl-C to stop)"
    else
      log "miner is urnetwork.service — showing its journal"
    fi
    exec journalctl "${jargs[@]}"
  else
    die "no sn25 miner in pm2 — start with ./run.sh start"
  fi
}

build_provider() {
  export PATH="${HOME}/.local/go/bin:/usr/local/go/bin:${PATH}"
  command -v go >/dev/null 2>&1 || die "go not found (need the version in go.mod)"
  local missing=() dep
  for dep in connect sdk server warp proxy userwireguard glog goidenticons gvisor; do
    [[ -d "$ROOT/../$dep" ]] || missing+=("$dep")
  done
  if ((${#missing[@]})); then
    die "sibling checkouts missing under $(dirname "$ROOT"): ${missing[*]}
See https://ur.xyz/docs/miner/install — or use the installer binary."
  fi
  mkdir -p "$ROOT/miner"
  log "building provider → miner/provider"
  (
    cd "$ROOT"
    env GOWORK=off GOEXPERIMENT=greenteagc CGO_ENABLED=0 \
      go build -trimpath -o "$ROOT/miner/provider" ./cli/miner
  )
  PROVIDER_BIN="$ROOT/miner/provider"
  log "built $($PROVIDER_BIN --version 2>/dev/null || echo miner/provider)"
}

cmd_wallet_set() {
  require_provider
  local ss58="${1:-$COLDKEY_SS58}"
  [[ -n "$ss58" ]] || die "usage: ./run.sh wallet-set <coldkey_ss58>"
  shift || true
  if [[ -f "$COLDKEY_SEED_FILE" ]]; then
    provider wallet set "$ss58" --coldkey_seed_file="$COLDKEY_SEED_FILE" "$@"
  else
    die "no coldkey seed at $COLDKEY_SEED_FILE — use wallet challenge + signature instead"
  fi
}

cmd_dashboard() {
  local host="$DASHBOARD_HOST" port="$DASHBOARD_PORT"
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --host) host="${2:-}"; shift 2 || die "--host needs a value" ;;
      --port) port="${2:-}"; shift 2 || die "--port needs a value" ;;
      --host=*) host="${1#*=}"; shift ;;
      --port=*) port="${1#*=}"; shift ;;
      -h|--help)
        cat <<EOF
Usage: ./run.sh dashboard [--host HOST] [--port PORT]

HTTP dashboard for miner health and how SN25 fits together.
Default: http://${DASHBOARD_HOST}:${DASHBOARD_PORT}/
0.0.0.0 listens on every interface, so the host's public IP works.
EOF
        return 0
        ;;
      *) die "dashboard: unknown arg: $1" ;;
    esac
  done
  [[ -f "$ROOT/dashboard/server.py" ]] || die "dashboard/server.py missing"
  command -v python3 >/dev/null 2>&1 || die "python3 not found"
  log "dashboard → http://${host}:${port}/"
  exec python3 "$ROOT/dashboard/server.py" --host "$host" --port "$port"
}

usage() {
  cat <<EOF
UR Subnet (SN25) miner — PM2 runner

Usage:
  ./run.sh                 Ensure miner is up (idempotent)
  ./run.sh start [args]    PM2 start: provider provide
  ./run.sh all [args]      PM2 start: provide --all-operators --auto-register
  ./run.sh stop            Stop PM2 apps + installer service
  ./run.sh restart         PM2 restart
  ./run.sh logs [--lines N] [--nostream]
  ./run.sh status          provider status
  ./run.sh doctor          provider doctor
  ./run.sh dashboard       Local miner / project dashboard (http)
  ./run.sh setup|operators|auth|wallet|claim …
  ./run.sh wallet-set [ss58]
  ./run.sh build
  ./run.sh provider …      Pass-through
  ./run.sh help

Double-start: safe. If the miner is already online, start/all exit 0 and
do not launch a second process (avoids "active owner").

PM2 names: $PM2_NAME  /  $PM2_NAME_ALL
Binary:    ${PROVIDER_BIN:-"(not found)"}
Hotkey:    $HOTKEY_SEED_FILE
State:     ${URNETWORK_STATE_DIR:-$HOME/.urnetwork}
Dashboard: http://${DASHBOARD_HOST}:${DASHBOARD_PORT}/
Docs:      https://ur.xyz/docs/miner
EOF
}

main() {
  local cmd="${1:-}"
  [[ $# -gt 0 ]] && shift || true

  case "$cmd" in
    ""|up|ensure)          cmd_default "$@" ;;
    start|provide)         cmd_start "$@" ;;
    all|all-operators|operators-all) cmd_all "$@" ;;
    stop)                  cmd_stop ;;
    restart)               cmd_restart "$@" ;;
    logs|log)              cmd_logs "$@" ;;
    status)                show_status "$@" ;;
    doctor)                provider doctor "$@" ;;
    dashboard|ui|web)      cmd_dashboard "$@" ;;
    setup)                 provider setup "$@" ;;
    operators)             provider operators "$@" ;;
    auth|login)            provider auth "$@" ;;
    wallet)                provider wallet "$@" ;;
    wallet-set)            cmd_wallet_set "$@" ;;
    claim)                 provider claim "$@" ;;
    build)                 build_provider ;;
    provider|urnetwork)    provider "$@" ;;
    pm2)                   require_pm2; pm2 "$@" ;;
    help|-h|--help)        usage ;;
    *)
      err "unknown command: $cmd"
      usage
      exit 2
      ;;
  esac
}

main "$@"
