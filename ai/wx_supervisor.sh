#!/bin/bash
# Overnight weather-backfill supervisor.
# Reruns the incremental backfill until every 0.5-degree city cache exists and
# ai/data/weather_by_match.csv has been written. Handles Open-Meteo 429
# cooldowns by sleeping 15 min between attempts.
set -u
PY=/home/t85491005/statwise/.venv/bin/python
DIR=/home/t85491005/statwise/ai
LOG=/tmp/wx_backfill.log

need=$($PY - <<'PY'
import json
v = json.load(open('/home/t85491005/statwise/ai/data/team_venues.json'))
cities = {(round(x['lat']*2)/2, round(x['lon']*2)/2)
          for x in v.values() if x and x.get('lat') is not None and x.get('lon') is not None}
print(len(cities))
PY
)

for i in $(seq 1 15); do
    echo "=== supervisor run $i of 15 — $(date -u +%FT%TZ) — need $need cities ===" >> "$LOG"
    (cd "$DIR" && "$PY" -u enrich_weather_history.py >> "$LOG" 2>&1)
    have=$(ls "$DIR"/data/weather_cache/*.json 2>/dev/null | wc -l)
    echo "=== caches: $have / $need ===" >> "$LOG"
    if [ "$have" -ge "$need" ] && [ -f "$DIR/data/weather_by_match.csv" ]; then
        echo "=== SUPERVISOR DONE: all cities cached, sidecar written ===" >> "$LOG"
        exit 0
    fi
    sleep 900
done
echo "=== SUPERVISOR EXHAUSTED 15 RUNS — rerun manually ===" >> "$LOG"
exit 1
