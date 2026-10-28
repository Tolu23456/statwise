# StatWise — Deep Codebase Audit (working notes)

Protocol: Deep Codebase Audit Protocol (Phases 0–5).
Started: 2026-10-29. Repo: /home/t85491005/statwise @ ce1e207.

## Scope

Included (hand-written source only):
- `ai/` — Python prediction/training/serving backend (~26k LOC)
- `statwise/` — Expo/React Native PWA (~15k LOC TS/TSX, excl. node_modules)
- `website/` — Next.js marketing + live predictions site
- `.github/workflows/`, `.github/scripts/` — CI/CD + Node jobs
- `analysis/` — research scripts
- `statwise/database_schema.sql`, `docker-compose.yml`, Dockerfiles, `pyproject.toml`, `replit.md`

Excluded: `node_modules/`, `.venv/`, `website/.next/`, `website/out/`, `oss/` (empty),
`attached_assets/`, `ai/data/` datasets, `ai/models/` binaries, `uv.lock`.

## Phase 0 — Orientation (draft)

StatWise = sports-betting prediction product.
- Python backend: downloads historical football data, trains LightGBM ensemble +
  optional attention model, predicts upcoming fixtures, writes to Supabase, settles.
- Expo PWA: consumes Supabase directly with anon key + RLS; payments via Flutterwave inline.
- Next.js site: static marketing/live-predictions pages, built from Supabase at build time.
- CI: ~26 GitHub Actions workflows do fetch/predict/settle/retrain/notify/audit.

## Findings log (raw)

(consolidated at report time; each entry must have file:line evidence)

### F-001 CRITICAL-ish [security/config] git remote credential
`.git/config` remote URL embeds a GitHub PAT (`ghp_…`). Not committed, but stored in
plaintext in the working tree/host. Rotate + use credential helper.

### F-002 [correctness/data] schema drift
`statwise/database_schema.sql` defines `predictions` without `match_id`,
`match_title`, `league_slug`, `stats`, `match_date`, `tier_required`,
`settled_at`, `actual_result` — yet `ai/model/predictor.py:384-418` writes exactly
those and upserts `on_conflict="match_id"`. Applying the checked-in schema cannot
work; no migration files exist. (verify: grep migrations)

### F-003 [security/data] RLS tier predicate uses legacy `user_profiles.tier` column
`database_schema.sql:196-202` gates paid predictions on the *subquery's* `tier`
column as well as `current_tier`. Two columns exist (19-20) and can drift.

### F-004 [security] `GRANT ALL ON ALL TABLES ... TO anon` (schema.sql:291) — RLS is the
only barrier; any future table added without RLS is world-readable/writable.

### F-005 [correctness/error-handling] predictor.py:18-31 hardcoded Supabase URL + anon
JWT fallback; if `SUPABASE_SERVICE_KEY` missing, writes silently fail row-by-row.

### F-006 [data-loss risk] predictor.py:450-508 prune_duplicate_predictions deletes
upcoming rows grouped only by (normalized teams, date[:10]) — no kickoff time,
no competition. Distinct fixtures can be pruned.

### F-007 [perf] predictor.py:266-302 sequential per-fixture weather HTTP call inside
prediction loop (verify caching in team_venues).

### F-008 [config drift] docker-compose.yml default SUPABASE_URL
`pdrcyuzfdqjnsltqqxvr` vs hardcoded `fwpriiugfjhtoymcbmso` in code/replit.md.

### F-009 [supply chain] pyproject.toml 90KB / hundreds of unrelated
`tool.uv.sources` entries (AA-module, DALL-E, InvokeAI…) — auto-generated junk.

### F-010 [build] root package.json placeholder test script (`exit 1`); no repo-level
lint/typecheck/test entry for Python.

### F-011 [secrets hygiene] tracked `statwise/env.js` hardcodes anon JWT + project URL;
`window.localStorage` makes it browser-only (likely dead). Verify imports.
