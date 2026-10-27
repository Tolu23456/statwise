# StatWise Web Start Scripts

Expo CLI tries to launch a browser and fails in this environment (missing `/usr/bin/garcon-url-handler`).
To avoid that crash, always use these scripts.

## Start the app
```bash
cd statwise
npm run web:bg
# or
bash scripts/start-web.sh
```

## Stop the app
```bash
npm run web:stop
# or
bash scripts/stop-web.sh
```

The app will be available at http://localhost:5000. Logs go to `/tmp/statwise-web.log`.

## Daily metrics report (plan 4.4/4.5)
`scripts/metrics_report.py` (stdlib only, read-only) prints a Markdown
dashboard: prediction volume + AI Picks share, settled accuracy using
the production rule (`actual_result == prediction`), booking-code
pipeline stats, client telemetry by kind, slow-query p50/p95, and the
A/B experiment funnels (exposures / CTR / conversions per variant).

```bash
python3 scripts/metrics_report.py                 # report only, always exit 0
python3 scripts/metrics_report.py --enforce-alerts # CI mode: exit 1 on alerts
python3 scripts/metrics_report.py --prune         # 90-day client_events retention
```

Credentials: `SUPABASE_URL` + `SUPABASE_SERVICE_KEY` (env, or the
repo-root `.env` locally). It runs daily in `.github/workflows/metrics-daily.yml`
(step summary = dashboard; failure emails = alerts). The telemetry
sections need `statwise/supabase-telemetry-migration.sql` run in the
Supabase SQL editor; everything else works without it.

## Bundle analysis (plan 4.2)
`scripts/bundle_report.py` + `scripts/measure_load.py` — production
export size report and headless (Playwright) load measurement with
interleaved A/B runs. Run via `.github/workflows/bundle-report.yml`
(workflow_dispatch only; `measure_load.py` refuses non-127.0.0.1 hosts).
