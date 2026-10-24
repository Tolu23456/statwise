#!/usr/bin/env bash
# Stop the StatWise Expo web dev server started by scripts/start-web.sh.
set -u

PORT="${PORT:-8081}"
PID_FILE="/tmp/statwise-web.pid"

if [ -f "$PID_FILE" ]; then
  PID="$(cat "$PID_FILE")"
  if kill -0 "$PID" 2>/dev/null; then
    kill "$PID" 2>/dev/null || true
    sleep 1
    kill -9 "$PID" 2>/dev/null || true
  fi
  rm -f "$PID_FILE"
fi

# Also clear any stray process still holding the port.
if command -v fuser >/dev/null 2>&1; then
  fuser -k "${PORT}/tcp" >/dev/null 2>&1 || true
fi
pkill -f "expo start --web --port ${PORT}" >/dev/null 2>&1 || true

echo "StatWise web stopped (port ${PORT})."
