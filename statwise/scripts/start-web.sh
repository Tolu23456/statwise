#!/usr/bin/env bash
# Start the StatWise Expo web app in the background.
#
# Expo CLI crashes on this machine because $BROWSER points at a missing
# binary (/usr/bin/garcon-url-handler). BROWSER=none stops the CLI from
# trying to open a browser, which is what was killing the dev server.
set -u

PORT="${PORT:-5000}"
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_FILE="${LOG_FILE:-/tmp/statwise-web.log}"
PID_FILE="/tmp/statwise-web.pid"
READY_TIMEOUT="${READY_TIMEOUT:-300}"

cd "$APP_DIR" || exit 1

# Stop anything already bound to the port so restarts are always clean.
bash "$APP_DIR/scripts/stop-web.sh" >/dev/null 2>&1 || true

# --clear matches the package.json dev/start scripts: a Metro cache left over
# from a killed session can make the bundler spin at 0% until it exhausts the
# default JS heap.  Give node enough headroom for the initial bundle.
#
# setsid detaches the server from the calling shell's process group so it
# survives the wrapper exiting (nohup alone only handles SIGHUP).
echo "Starting StatWise web on http://localhost:${PORT} ..."
BROWSER=none EXPO_NO_DEVTOOLS=1 NODE_OPTIONS="${NODE_OPTIONS:---max-old-space-size=1408}" \
  setsid nohup node node_modules/.bin/expo start --web --clear --port "$PORT" \
  >"$LOG_FILE" 2>&1 < /dev/null &
echo $! >"$PID_FILE"

# Deadline-based wait: each attempt may block up to CURL_TIMEOUT seconds
# (long enough for a cold Metro build), while the total wait stays bounded
# by READY_TIMEOUT. Aborting a request mid-build makes Metro restart it.
CURL_TIMEOUT="${CURL_TIMEOUT:-240}"
DEADLINE=$(( $(date +%s) + READY_TIMEOUT ))

while [ "$(date +%s)" -lt "$DEADLINE" ]; do
  if curl -sf --max-time "$CURL_TIMEOUT" "http://localhost:${PORT}" >/dev/null 2>&1; then
    echo "StatWise web is up: http://localhost:${PORT}"
    exit 0
  fi
  if ! kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "Expo exited before becoming ready. Last log lines:" >&2
    tail -30 "$LOG_FILE" >&2
    exit 1
  fi
  sleep 2
done

echo "Timed out after ${READY_TIMEOUT}s waiting for http://localhost:${PORT}" >&2
tail -30 "$LOG_FILE" >&2
exit 1
