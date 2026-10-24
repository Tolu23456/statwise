#!/usr/bin/env bash
# Start the StatWise Expo web app in the background.
#
# Expo CLI crashes on this machine because $BROWSER points at a missing
# binary (/usr/bin/garcon-url-handler). BROWSER=none stops the CLI from
# trying to open a browser, which is what was killing the dev server.
set -u

PORT="${PORT:-8081}"
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_FILE="${LOG_FILE:-/tmp/statwise-web.log}"
PID_FILE="/tmp/statwise-web.pid"
READY_TIMEOUT="${READY_TIMEOUT:-180}"

cd "$APP_DIR" || exit 1

# Stop anything already bound to the port so restarts are always clean.
bash "$APP_DIR/scripts/stop-web.sh" >/dev/null 2>&1 || true

echo "Starting StatWise web on http://localhost:${PORT} ..."
BROWSER=none EXPO_NO_DEVTOOLS=1 \
  nohup node node_modules/.bin/expo start --web --port "$PORT" \
  >"$LOG_FILE" 2>&1 &
echo $! >"$PID_FILE"

for _ in $(seq 1 "$READY_TIMEOUT"); do
  if curl -sf --max-time 2 "http://localhost:${PORT}" >/dev/null 2>&1; then
    echo "StatWise web is up: http://localhost:${PORT}"
    exit 0
  fi
  if ! kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "Expo exited before becoming ready. Last log lines:" >&2
    tail -30 "$LOG_FILE" >&2
    exit 1
  fi
  sleep 1
done

echo "Timed out after ${READY_TIMEOUT}s waiting for http://localhost:${PORT}" >&2
tail -30 "$LOG_FILE" >&2
exit 1
