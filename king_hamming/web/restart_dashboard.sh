#!/usr/bin/env bash
# Restart the King Hamming dashboard detached from any terminal, keeping the
# production arguments (behind cloudflared on 127.0.0.1:8070). Sessions live in
# sessions.sqlite, so signed-in users stay signed in.
#
# Usage: king_hamming/web/restart_dashboard.sh [extra serve arguments...]
# Example: king_hamming/web/restart_dashboard.sh
set -euo pipefail

SANDBOX="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PORT="${KH_DASHBOARD_PORT:-8070}"
STATE="${KH_DASHBOARD_STATE:-$HOME/.local/share/king_hamming/web}"
LOG="$STATE/dashboard.log"
mkdir -p "$STATE"

# Stop only a dashboard server process listening on our port.
for pid in $(ss -ltnpH "sport = :$PORT" 2>/dev/null | grep -o 'pid=[0-9]*' | cut -d= -f2 | sort -u); do
    if tr '\0' ' ' < "/proc/$pid/cmdline" | grep -q 'web/server.py serve'; then
        echo "stopping dashboard pid $pid"
        kill "$pid"
        for _ in $(seq 50); do kill -0 "$pid" 2>/dev/null || break; sleep 0.1; done
        kill -0 "$pid" 2>/dev/null && { echo "pid $pid did not exit" >&2; exit 1; }
    else
        echo "port $PORT is held by a non-dashboard process (pid $pid); refusing" >&2
        exit 1
    fi
done

cd "$SANDBOX"
PYTHONPATH="$SANDBOX" setsid nohup python3 king_hamming/web/server.py serve \
    --trust-proxy --secure-cookies "$@" >>"$LOG" 2>&1 < /dev/null &
echo "started dashboard pid $! (log $LOG)"

for _ in $(seq 100); do
    if curl -s -o /dev/null "http://127.0.0.1:$PORT/"; then
        echo "dashboard answering on 127.0.0.1:$PORT"
        exit 0
    fi
    sleep 0.1
done
echo "dashboard did not answer; see $LOG" >&2
exit 1
