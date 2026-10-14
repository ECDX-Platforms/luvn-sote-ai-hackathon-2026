#!/usr/bin/env bash
# LUVN dev launcher — starts the FastAPI backend + Vite frontend on the first
# free ports, wires the UI to the backend, waits for readiness, and prints URLs.
#
# Leak-proof: writes the real server PIDs to .dev.pids and offers `--stop`, so a
# server can always be stopped even where signal traps do not fire.
#
# Usage:
#   ./dev.sh                 # backend from :8000, frontend from :5173 (auto-increments)
#   BACKEND_PORT=8010 ./dev.sh
#   ./dev.sh --no-frontend   # backend only
#   ./dev.sh --stop          # stop the servers started by the last ./dev.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIDFILE="$ROOT/.dev.pids"

stop_all() {
  [ -f "$PIDFILE" ] || return 0
  local pid port p any
  while read -r pid port; do
    [ -n "${pid:-}" ] && kill "$pid" 2>/dev/null || true
  done < "$PIDFILE"
  # retry: Vite/uvicorn can take a beat to release (and Vite may fork), so keep
  # killing whatever is still bound to our ports until they are actually free.
  for _ in 1 2 3 4 5 6; do
    any=0
    while read -r pid port; do
      [ -n "${port:-}" ] || continue
      p=$(lsof -ti "tcp:$port" 2>/dev/null) || true
      for p in $p; do kill "$p" 2>/dev/null && any=1; done
    done < "$PIDFILE"
    [ "$any" = 0 ] && break
    sleep 0.4
  done
  rm -f "$PIDFILE"
}

if [ "${1:-}" = "--stop" ]; then
  stop_all
  echo "stopped (if anything was running)."
  exit 0
fi

BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"
START_FRONTEND=1
[ "${1:-}" = "--no-frontend" ] && START_FRONTEND=0

need() { command -v "$1" >/dev/null 2>&1 || { echo "missing: $1 ($2)"; exit 1; }; }
need python3 "https://www.python.org/"
need lsof "macOS/BSD lsof (usually present)"

# first free TCP port at or above $1 (busy on IPv4 or IPv6 -> increment)
free_port() {
  local p="$1"
  while python3 -c '
import socket, sys
p = int(sys.argv[1])
for host, fam in (("127.0.0.1", socket.AF_INET), ("::1", socket.AF_INET6)):
    s = socket.socket(fam)
    s.settimeout(0.2)
    if s.connect_ex((host, p)) == 0:
        sys.exit(0)  # busy
sys.exit(1)  # free
' "$p"; do
    p=$((p + 1))
  done
  echo "$p"
}

BACKEND_PORT="$(free_port "$BACKEND_PORT")"
BACKEND_URL="http://localhost:${BACKEND_PORT}"

if [ ! -f "$ROOT/backend/.env" ]; then
  echo "warn: backend/.env missing — Foundry narration will fall back (app still runs)."
fi

# prefer the project venv (a real, killable process); fall back to uv.
if [ -x "$ROOT/backend/.venv/bin/python" ]; then
  BACKEND_CMD=( "$ROOT/backend/.venv/bin/python" -m uvicorn app.main:app )
else
  need uv "https://docs.astral.sh/uv/"
  BACKEND_CMD=( uv run --env-file .env uvicorn app.main:app )
fi

: > "$PIDFILE"
trap 'stop_all' EXIT INT TERM

echo "→ backend  $BACKEND_URL"
(
  cd "$ROOT/backend"
  if [ -f .env ]; then set -a; . ./.env; set +a; fi
  exec "${BACKEND_CMD[@]}" --host 127.0.0.1 --port "$BACKEND_PORT"
) &
echo "$! $BACKEND_PORT" >> "$PIDFILE"

for _ in $(seq 1 60); do
  curl -sf -m 2 "$BACKEND_URL/health/live" >/dev/null && break
  sleep 0.5
done
curl -sf -m 2 "$BACKEND_URL/health/live" >/dev/null || { echo "backend failed to start — see output above"; exit 1; }
echo "  backend ready"

if [ "$START_FRONTEND" = "1" ]; then
  need node "https://nodejs.org/"
  need npm "https://nodejs.org/"
  [ -d "$ROOT/frontend/node_modules" ] || ( cd "$ROOT/frontend" && npm install )
  FRONTEND_PORT="$(free_port "$FRONTEND_PORT")"
  echo "→ frontend http://localhost:${FRONTEND_PORT}   (VITE_API=${BACKEND_URL})"
  (
    cd "$ROOT/frontend"
    exec env VITE_API="$BACKEND_URL" ./node_modules/.bin/vite --port "$FRONTEND_PORT" --strictPort
  ) &
  echo "$! $FRONTEND_PORT" >> "$PIDFILE"
  echo
  echo "Open:  http://localhost:${FRONTEND_PORT}"
else
  echo
  echo "API:  ${BACKEND_URL}/api/encounters"
fi
echo "Ctrl-C to stop, or run: ./dev.sh --stop"
wait
