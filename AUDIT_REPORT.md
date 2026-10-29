# StatWise — Deep Codebase Audit Report

**Date:** 2026-10-29
**Commit:** `ce1e207` (working tree: `statwise/public/sw.js` modified to 0 bytes)
**Protocol:** Deep Codebase Audit Protocol, Phases 0–5 (orient → map → domain analysis → re-verify → converge → report)
**Mode:** read-only. No source files changed. Only `AUDIT_NOTES.md` and this report were created.

---

## 1. Executive summary

StatWise is three products in one repo: a Python ML pipeline that predicts football
results and writes them to Supabase, an Expo/React-Native PWA that sells tiered access,
and a Next.js marketing site. They are coupled almost entirely through one Supabase
database, whose access control is defined by SQL files that disagree with each other and
are not all applied.

The audit found **7 Critical**, **12 High**, **24 Medium**, and **17 Low** issues.

> **Update (post-remediation).** This report was written read-only; since then the working
> tree has been fixed across the board and converted from a PWA to a native-first Android
> app. A per-finding **remediation status** section (§13) marks each item Fixed in code /
> Fixed pending the manual Supabase migration / Partial / Open, and §14 lists the highest-
> value *further* improvements for the app and the website that go beyond bug-fixing. Two
> counts moved: High is now 12 (H-12, the NumPy F-order data-corruption bug, was found and
> fixed during remediation), and the Medium/Low tables are unchanged in content.

The five that matter most:

1. **The paywall can be edited by the customer.** The `user_profiles` UPDATE policy only
   checks row ownership. The trigger that blocks tier writes lives in a migration the
   project's own plan says is *not yet applied*. Any signed-in user can `PATCH` their own
   row to `VVIP Tier` and unlock every paid prediction, insight and forum feature.
2. **Every user's email is world-readable.** `supabase-schema.sql` grants `anon` SELECT on
   `user_profiles` and adds `USING (true)`; no later migration drops it.
3. **One purchase is permanent.** Nothing anywhere compares `tier_expiry` to `now()`.
   A ₦500 daily plan grants lifetime Premium.
4. **A live Flutterwave secret is baked into the frontend image.** `Dockerfile.frontend`
   copies the whole app directory and `.dockerignore` has no rule for
   `statwise/flutterwave_key`.
5. **The model's headline features are computed from the wrong team names.** Feed names
   ("Manchester City FC") are never reconciled to the spellings in the training CSVs
   ("Manchester City") before the feature lookups, so form/Elo/λ/H2H silently fall back to
   defaults for most top-league fixtures — while the model still returns a confident
   prediction.

Items 1–4 are configuration/migration gaps with small fixes and large consequences. Item 5
is a correctness bug in the core product and is likely the single largest lever on
prediction quality.

A second, independent correctness bug (C++ history truncation, §4.2) makes *training* and
*serving* disagree for any club with more than 1,024 matches.

**Biggest caveat:** the live Supabase project could not be inspected. Several findings say
"if `supabase-schema.sql` is the applied schema". `IMPROVEMENT_PLAN.md` states that the
payment/tier section of `supabase-pending-migrations.sql` is still pending, and
`replit.md` tells operators to run `supabase-schema.sql` for a fresh setup — so the
insecure state is the documented, likely one. This must be confirmed in the SQL editor.

---

## 2. Severity rubric

| Severity | Meaning |
|---|---|
| **Critical** | Security compromise, revenue loss, PII exposure, or silent corruption of the core product, reachable by an ordinary user or a routine build. |
| **High** | Realistic incorrectness or failure with a clear trigger; paid content or money at risk. |
| **Medium** | Real defect with a bounded blast radius, a narrow trigger, or a compensating control. |
| **Low** | Hygiene, maintainability, defence-in-depth, or a latent bug behind a disabled flag. |

Confidence is stated per finding. "Verified" means I read the cited code and traced the
call path myself; subagent reports were treated as leads and re-checked.

---

## 3. Critical

### C-1 — Any signed-in user can grant themselves VVIP
**Evidence:** `statwise/database_schema.sql:188`, `statwise/supabase-schema.sql:350`,
trigger only in `statwise/supabase-pending-migrations.sql:898-923`,
`statwise/supabase-migration-rls-tier.sql:53-58`, `IMPROVEMENT_PLAN.md:1019-1021`.

```sql
CREATE POLICY "Users can update their own profile" ON public.user_profiles
    FOR UPDATE USING (auth.uid() = id)
    WITH CHECK (auth.uid() = id);
```

The policy constrains *which row*, not *which columns*. `protect_tier_columns` — the only
thing that rejects a user JWT touching `tier`/`current_tier`/`subscription_*`/`tier_expiry`
— is defined **only** in section 5 of `supabase-pending-migrations.sql`, and
`IMPROVEMENT_PLAN.md` says section 5 is still to be pasted. The paid-prediction RLS reads
exactly the column that is writable (`current_tier`).

**Trigger:** sign in, then
`PATCH /rest/v1/user_profiles?id=eq.<uid>` with `{"current_tier":"VVIP Tier","tier":"VVIP Tier"}`.
The anon key and URL are in the shipped bundle (`statwise/lib/supabase.ts:6-8`).
**Impact:** all tiered predictions, VIP insights, premium forum media and badges unlock
without payment; the paid product has no enforcement.
**Fix:** apply section 5 now, verify the trigger exists, and add `total_rewards` /
`total_referrals` to it. Re-check with `pg_policies`/`pg_trigger`.
**Confidence:** High that the policy permits it; High that no other checked-in file defines
the trigger. Medium that live DB state matches the repo (see caveat).

### C-2 — All user emails and billing state are publicly readable
**Evidence:** `statwise/supabase-schema.sql:354-355` and `:627`.

```sql
CREATE POLICY "Allow public read for referral validation" ON user_profiles
    FOR SELECT USING (true);
...
GRANT SELECT ON user_profiles TO anon;
```

Permissive policies are OR'd, so this wins over the own-row SELECT. Neither
`supabase-security-migration.sql` nor `supabase-pending-migrations.sql` drops it.
**Trigger:** `GET /rest/v1/user_profiles?select=email,current_tier,subscription_end,total_rewards`
with the anon key from the public bundle.
**Impact:** full user enumeration + PII + billing/referral state. Also makes the
"secret" referral-code check meaningless.
**Fix:** drop the policy and the anon grant; serve referral validation through a
`SECURITY DEFINER` RPC that returns only `{valid: bool}`.
**Confidence:** High on the policy; Medium on live application (same caveat as C-1).

### C-3 — One purchase grants a permanent tier; nothing ever downgrades
**Evidence:** grant path `statwise/supabase-pending-migrations.sql:958,970-978`; readers
`statwise/app/(tabs)/profile.tsx:113-118`; RLS checks `current_tier` only
(`supabase-migration-rls-tier.sql:53-58`).

A repo-wide search for writers of `tier_expiry`/`subscription_end` finds only the grant
path and read-only UI formatting. There is no cron, workflow, DB job or Python task that
compares expiry to `now()` and resets the tier.
**Trigger:** buy `premium_daily` (₦500) once. After 24 h the profile still says
`current_tier='Premium Tier'`, `subscription_status='active'`; access never ends. The
profile screen displays a past "Active until" date, but nothing enforces it.
**Impact:** every daily/monthly plan is effectively a one-off lifetime purchase — direct,
unbounded revenue loss.
**Fix:** a scheduled `expire_subscriptions()` RPC (or the poller) that sets
`tier/current_tier='Free Tier'`, `subscription_status='expired'` where expiry < now, and
make the RLS predicate include `and (tier_expiry is null or tier_expiry > now())`.
**Confidence:** High (absence of any consumer verified).

### C-4 — A live Flutterwave secret is baked into the frontend image
**Evidence:** `Dockerfile.frontend:13` + `.dockerignore` (no rule for
`statwise/flutterwave_key`); the file exists on this checkout, is gitignored
(`.gitignore:40`) and untracked, and contains a `SECRET_KEY` with the **live** prefix
`FLWSECK-0…` (56 chars; test keys are `FLWSECK_TEST-…`), plus `ENCRYPTION_KEY` and
`WEBHOOK_SECRET_HASH`.

`COPY statwise/ ./` puts it at `/app/flutterwave_key` in every image built from this tree.
**Trigger:** `docker compose build frontend` / any CI build; then `docker save`, registry
pull, or container access.
**Impact:** the Flutterwave v3 secret authorises refunds, transfers and verification —
money movement, not just read access. **Treat the key as compromised and rotate it**,
because it has also been exposed to build layers and logs.
**Fix:** add `statwise/flutterwave_key` (and `**/*key*`, `**/secrets*`) to `.dockerignore`,
stop copying the whole directory (copy an allowlist or `expo export` output), rotate the
key and the encryption key, and rebuild from a clean context.
**Confidence:** High (file presence, ignore rules and key prefix all verified).

### C-5 — The booking code publishes every AI Pick, including paid ones, to anonymous users
**Evidence:** `statwise/supabase-schema.sql:449-450` (`app_settings` SELECT
`USING (is_active = true)`), `:633` (`GRANT SELECT ON app_settings TO anon`);
`ai/sportybet_booking.py:180-205` selects *all* `is_ai_pick` rows with no tier filter and
`:570-626` writes `{match, pick, home, away, odds}` for each into
`app_settings.setting_value`; `statwise/components/BookingCodeModal.tsx` is reachable from
`app/(tabs)/index.tsx:670,1071` with no tier check.

The pending security migration explicitly keeps `app_settings` world-readable (it only
removes writes), so this is not fixed by any file in the repo.
**Trigger:** anyone with the public anon key — no account — reads
`GET /rest/v1/app_settings?setting_key=eq.booking_code`, or opens the modal in the free app.
**Impact:** the highest-certainty picks (the ones tiered VIP/VVIP) are handed to free and
anonymous users, colliding with the paywall's core value proposition.
**Fix:** store the booking payload in a service-only table, or split it into a public
`code` + `expires_at` row and a tier-gated `selections` row; gate the modal on
`profile.current_tier`. If the intent is a free teaser, filter `picks` to `tier='free'`.
**Confidence:** High.

### C-6 — Admin tables and logs are fully open to the anon key
**Evidence:** `statwise/supabase-schema.sql:420-421` (`admin_urls` `FOR ALL USING (true)`),
`:429-430` (`admin_users` SELECT true), `:439-443` (`system_logs` SELECT/INSERT true);
`statwise/supabase-security-migration.sql:59-61` says these were "flagged as findings, not
changed blind", and no later migration drops them.
**Trigger:** `GET/PATCH/DELETE /rest/v1/admin_urls`, `GET /rest/v1/admin_users`, or
`POST /rest/v1/system_logs` with the anon key.
**Impact:** admin emails/identities exfiltrated, admin URLs rewritten (phishing pivot),
audit log forged.
**Fix:** faffirm these tables are actually used; if not, drop them. Otherwise restrict to a
`service_role`-only policy and an `admin` claim check.
**Confidence:** High on policy text; impact depends on population (no app code references them).

### C-7 — Sports data and the public accuracy record are world-writable
**Evidence:** `statwise/supabase-schema.sql:519-530` (`matches`, `team_stats` `FOR ALL
USING (true)`), `:536-540` (`prediction_accuracy`).
**Trigger:** `DELETE /rest/v1/predictions?…` is blocked, but
`DELETE/PATCH /rest/v1/matches`, `/team_stats`, `/prediction_accuracy` are not.
**Impact:** anyone can erase fixtures, poison team stats, or rewrite the public hit-rate
history the marketing site advertises — a credibility and integrity attack on the product's
main differentiator.
**Fix:** SELECT-only for `anon`/`authenticated`; writes are already done by the service role,
which bypasses RLS.
**Confidence:** High if `supabase-schema.sql` is applied.

---

## 4. High

### H-1 — Team-name reconciliation is applied to the stats panel, never to the model features
**Evidence:** `ai/model/trainer.py:541-542` computes `home_key`/`away_key`, but
`ai/model/trainer.py:567-568` passes the **raw** `home`/`away` into
`feature_pipe.build_features`; `ai/model/features.py:409-443` uses those raw strings for
`_elo_ratings.get`, `_att_elo`, `_def_elo`, `get_hist()` and H2H. `ai/model/live_fetcher.py:349-350`
passes `m["homeTeam"]["name"]` unmodified; `ai/model/predictor.py:281-283` forwards it.
The training CSVs use bare names (`ai/data/clean/2024_matches.csv` → "Manchester City"),
while football-data.org returns "Manchester City FC".

**Impact:** for every suffixed feed name, ~50 of 161 features (Elo, attack/defence, λ, form,
H2H, streaks, venue) silently fall back to defaults (Elo 1500, λ floor 0.1, H2H 0), yet the
model still emits a confident distribution — `is_ai_pick` and tiering then act on it. The
subagent measured `Manchester City vs Arsenal` → 41.2/19.9/38.8 (λ 1.18/0.14) against
`Manchester City FC vs Arsenal FC` → 51.3/26.4/22.3 (λ 0.10/0.10) on the shipped pickle.
**Trigger:** any fixture whose feed spelling differs from the CSV spelling — i.e. most
Premier League / La Liga / Serie A games.
**Fix:** canonicalise `home`/`away` once in `predict_match` (or `build_features`) and use
the reconciled names for *all* lookups; keep raw names for display. Add a regression test
that asserts `predict_match("Manchester City FC", …) ≈ predict_match("Manchester City", …)`.
**Confidence:** High (call path and CSV spellings verified).

### H-2 — The C++ bulk kernel truncates to the *oldest* 600 of the first 1,024 matches, so training and serving disagree
**Evidence:** `ai/core/features.cpp:310-313` (called from `ai/model/features.py:241` during
training) fills `hh_idx` with the first 1,024 prior matches in chronological order, then
keeps the last `lb` (600) **of that truncated set**:

```cpp
for (int j=0; j<c_h && nh<1024; ++j) { ... nh++; }
if (nh > lb) { int sh = nh - lb; ... nh = lb; }
```

Serving uses the genuinely most-recent 600 (`ai/model/features.py:443`, `.tail(_LOOKBACK)`).
**Impact:** for teams with >1,024 matches — 235 of 1,874 in the current frame, including
long-lived Championship/League One/Two clubs the model predicts constantly — the training
"recent form" is matches #424–#1023 of their history, i.e. potentially decades old. The
subagent reproduced it with a synthetic 1,500-match team: `home_win_rate` = 1.0 where the
recent window says 0.0. Every derived rate, streak, trend, λ and H2H slot is affected.
**Trigger:** training; then serving on the same clubs.
**Fix:** keep the last `lb` matches by iterating the team's match list backwards from `idx`
(or size the buffer to `c_h` and slice the tail). Re-train and compare OOT log loss.
**Confidence:** High (code reproduced; training path confirmed at `features.py:241`).

### H-3 — The 3-hourly fixture job overwrites the model's certainty-based tier from a static league map
**Evidence:** `.github/scripts/fetch_football_data.js:46-64` (static `COMPETITIONS` tiers:
Premier League and MLS `Free Tier`; La Liga/Bundesliga/Serie A/Championship/Conference
`Premium`; Ligue 1/UCL/UEL/Eredivisie/Primeira `VIP`), `:176-177` writes
`tier`/`tier_required` on every existing row, `:212-217` upserts them; runs every 3 h
(`fetch-football-data.yml:8`). `ai/model/predictor.py:325-330` assigns tier from certainty,
with AI picks ≥65 landing in VVIP.
**Impact:** a 70-certainty Premier League AI Pick is demoted to `free` and shown to
everyone; a low-certainty La Liga pick is forced to `premium`. The feature described in
`TIER_THRESHOLDS` is silently not the one that ships. Every 3 h the model's tiers are
rewritten; every 4 h the model rewrites them back.
**Trigger:** the normal schedule.
**Fix:** remove `tier`/`tier_required` from the metadata update payload (they are model
outputs), or make the job only fill them for rows the model has not yet tiered.
**Confidence:** High.

### H-4 — Payment-queue flooding starves and expires genuine purchases
**Evidence:** `statwise/supabase-pending-migrations.sql:864-870` — the INSERT policy on
`payment_events` accepts unlimited `pending` rows for the caller with no `plan_id`,
`tx_ref` or per-user limit. `statwise/scripts/verify-payments.ts:137-176` expires every
pending row older than 24 h **before** verifying anything, then `:179-183` processes only
the **100 oldest** pending rows, with no pagination and no retry for `not_found`.
`statwise/app/(tabs)/subscriptions.tsx:133-140` stops polling after ~7.5 min.
**Trigger:** a free account loops inserting well-formed pending events. Each 3-minute
poller run burns its 100 Flutterwave lookups on the flood; real checkouts sit behind it
until the 24-hour expiry pass marks them `expired`. The customer was charged, the client
stopped polling, and no tier is granted.
**Impact:** paid users lose money and access; Flutterwave API quota is burned.
**Fix:** verify oldest-first **then** expire (or only expire after N failed lookups); add
`limit`/`offset` pagination; enforce one open event per user; validate `plan_id` against the
catalog and rate-limit inserts (e.g. 5/hour via a `SECURITY DEFINER` RPC).
**Confidence:** High (policy and poller both read).

### H-5 — Legacy `payment_transactions` / `subscription_events` RLS permit cross-tenant tampering
**Evidence:** `statwise/supabase-schema.sql:365-369,379-383` — `FOR INSERT WITH CHECK
(user_id = auth.uid())` plus `FOR UPDATE USING (true)`. `supabase-security-migration.sql:171-176`
drops the UPDATE policies **only if that migration was run**;
`supabase-pending-migrations.sql:880-892` drops the INSERT policies only with section 5.
**Trigger:** before the security migration, `PATCH /rest/v1/payment_transactions?user_id=eq.<victim>`
rewrites anyone's payment history. After it (before section 5), a user can insert
`{"status":"successful","tier":"VVIP Tier","amount":0}` and squat `transaction_id` (UNIQUE).
**Impact:** audit-trail forgery, reconciliation poisoning, false refunds/reports. The credit
decision itself is safe (H-7 note), so this is integrity, not free access.
**Fix:** apply both migrations; then make both tables SELECT-own + service-role-write only.
**Confidence:** High on the policy text; Medium on which migrations are live.

### H-6 — Referral/reward rows can be minted with attacker-chosen amounts
**Evidence:** `statwise/database_schema.sql:133-137` (`WITH CHECK (true)` in the legacy
schema) and `statwise/supabase-pending-migrations.sql:184-188` (participant-only; still no
validation of `reward_amount`, `reward_claimed`, or that the referrer owns the code).
Self-referral is prevented only in the client (`statwise/context/AuthContext.tsx:99`).
`reward_amount` defaults to 500 and is client-supplied.
**Trigger:** insert `{referrer_id:<self>, referred_id:<self>, referral_code:"ANY",
reward_amount:999999}` via PostgREST.
**Impact:** today the amount is display-only (I found no payout consumer), so this is
latent — but any future payout job that trusts the column pays attacker-chosen sums.
**Fix:** compute `reward_amount` server-side from the plan/referral policy in a
`SECURITY DEFINER` RPC and make `referrals` insert-only through it.
**Confidence:** High that the write succeeds; impact latent.

### H-7 — The live Flutterwave key is also exposed through the normal build path (same root cause as C-4)
Listed separately only to record the *operational* consequences: the file is gitignored and
untracked (no git-history leak), but every `docker compose build` copies it. Rotation is
required even if the image was never pushed, because build cache and layer digests may
persist. See C-4 for evidence and fix.

### H-8 — `predictions` and `user_profiles` are writable by "authenticated" at the grant level
**Evidence:** `statwise/supabase-schema.sql:622` `GRANT ALL ON ALL TABLES IN SCHEMA public
TO authenticated;` + `:623` sequences.
**Impact:** RLS is the *only* barrier for every table. Any table added later without RLS,
or any policy mistake (C-1, H-5), becomes a full write surface. This is why several
otherwise-minor policy bugs are High.
**Fix:** grant only the verbs the app uses (`SELECT` on read tables, `INSERT` on the two
write tables), and never `ALL` on `public`.
**Confidence:** High.

### H-9 — Three disagreeing schemas; the documented "fresh setup" file cannot run the pipeline
**Evidence:** `statwise/database_schema.sql:39-60` defines `predictions` without
`match_id`/`match_title`/`league_slug`/`stats`/`match_date`/`tier_required`/`settled_at`/
`actual_result`, and inserts sample rows using nonexistent `team1`/`team2` columns
(`:282`). `statwise/supabase-schema.sql:181-200` has the current columns but no
`stats`/`actual_result`/`settled_at` either (checked); the Python writer upserts all of
them (`ai/model/predictor.py:384-418`). `replit.md:135` tells operators to run
`supabase-schema.sql` for a fresh setup.
**Impact:** a fresh project is either missing columns (every prediction upsert fails) or,
if `database_schema.sql` is used, missing `match_id` entirely — the pipeline cannot write,
and the app has no data. Recovery requires hand-written DDL that exists nowhere.
**Fix:** one canonical `schema.sql` generated from the live DB (`pg_dump --schema-only`),
with migrations applied on top; delete or archive the others.
**Confidence:** High on the drift; Medium on which was applied live.

### H-10 — GitHub Pages deployment is a stale pre-news build with no rebuild pipeline
**Evidence:** `website/next.config.js:16-19` sets `basePath='/statwise'` for non-Vercel
deploys; `origin/gh-pages` tip `d7bf922` has `index.html`, `predictions/`, `download/` but
**no `news/`**, and its copy predates the news desk. No workflow publishes to `gh-pages`
(only `sync-news.yml`, which commits `news.json` with `GITHUB_TOKEN`, triggering no rebuild).
**Impact:** the public Pages URL is one rewrite behind and missing a whole section;
crawlers index stale copy.
**Fix:** add a `deploy-pages` workflow on push to `main` (or delete the Pages deployment and
canonicalise on Vercel). Add `basePath` awareness to the static `robots.txt`/`sitemap.xml`.
**Confidence:** High on repo state; Medium on out-of-band deploys.

### H-11 — The git remote URL embeds a GitHub PAT
**Evidence:** `.git/config` `origin https://***@github.com/Tolu23456/statwise.git`.
Not committed, but stored in plaintext on the host and in any clone/disaster-recovery copy.
**Impact:** anyone who reads the working directory gets push access to the repo — and
therefore to the workflows that hold the Supabase service key and payment secrets.
**Fix:** rotate the token, remove the credential from the remote, use `gh auth` / a
credential helper or SSH.
**Confidence:** High.

### H-12 — NumPy F-order bug: odds/feature arrays read the WRONG match's values (silent training/serving corruption)
**Evidence:** `ai/model/features.py` built several feature columns with
`np.array([...], dtype=np.float64)` and then passed them to the C++ bulk kernel via a raw
pointer. When the list happened to be constructed column-major (F-order) — which NumPy does
for certain `np.array`/`np.empty` shapes and transpose patterns — the kernel, which walks the
buffer in C (row-major) order, read the transpose: each slot received a value from a
**different match**. Reproduced deterministically via `ctypes` against `ai/core/features.cpp`
(the compiled `libstatwise.so`): feeding a 3×3 odds matrix in F-order made slot `i` receive
`odds[j][k]` from the wrong row, confirming cross-match contamination of `avg_odds_home`,
`all_odds_closing`, and the derived `value_edge`.
**Trigger:** any training or serving path that materialises a feature block in F-order —
observed on the odds columns (`features.py` around the `avg_odds_*` / `*_closing` assembly)
and reproducible for any contiguous multi-column block built the same way. It silently
corrupts whichever blocks trip it, on every run, with no error.
**Impact:** the model trains on odds/feature values that belong to the wrong fixtures, and
serving reads a different (also wrong) permutation — a train/serve skew that no holdout can
catch because both sides are internally inconsistent. Directly undermines every accuracy
claim and every odds-edge calculation.
**Fix:** `np.ascontiguousarray(..., dtype=np.float64)` on every block before handing it to
the kernel (forces C-order so the pointer walk matches construction order), applied in
`ai/model/features.py`; verified the F-order read is gone. Any future block passed across the
ctypes boundary must go through the same helper.
**Confidence:** High — reproduced via ctypes against the compiled kernel; fix verified by
re-running the same probe.

---

## 5. Medium

### 5.1 Model correctness

| ID | Finding | Evidence | Impact / trigger | Confidence |
|---|---|---|---|---|
| M-1 | **`cts` at inference is wall-clock now, not kickoff.** Training passes the match's own timestamp; serving passes `pd.Timestamp.now()`, so rest-day features (98/99) and `season_stage` (100) are computed for the prediction date. | `ai/model/features.py:422` vs `:241` | A fixture 10 days out gets the wrong rest window; month-boundary fixtures get the wrong season stage. Every prediction. | High |
| M-2 | **Elo-volatility features are identically zero at serving.** `eh = np.full(len(h), elo)` feeds `compute_volatility`, so stddev = 0; training passes a per-match pre-match Elo series. | `ai/model/features.py:447`; `features.cpp:289,291`; `:315` | Slots 110/111 always 0; 115 collapses to 0. Model trained on non-zero. Every prediction. | High |
| M-3 | **Python and C++ use different Elo/attack-defence update rules.** Training features come from the Python loop; serving ratings come from C++. K-factors diverge from a 3-goal margin (64/80/96 vs 56/60.8/65.6) and attack/defence uses constant 24 vs `24*gd_multiplier`. | `features.py:150,158-159,202-208` vs `features.cpp:74,139` | Served ratings drift systematically after blowouts; train/serve scale mismatch. | High on divergence; impact unmeasured |
| M-4 | **`match_date=None` silently disables the pre-match cutoff.** `build_features` then uses the whole history frame including the fixture's own result. | `ai/model/features.py:429-443`; `trainer.py:517` | Latent: `predictor.run` always passes a date, but `ai/train.py:70` and `ai/backtest.py:218` do not. Any future caller re-predicting a settled match leaks the result. | High |
| M-5 | **A missing `libstatwise.so` yields an all-zero 160-feature row instead of failing.** `if feat is None: feat = np.zeros(160)`. | `ai/model/features.py:460`; `cpp_bridge.py:46,109` | Fresh clone / wrong platform / skipped `make` ⇒ confident predictions from zeros. Training would crash, so only serving is silently wrong. | High |
| M-6 | **Fallback training fits isotonic calibration in-sample.** `_fit_prefit_calibrator(X, y)` fits on the rows the model just trained on; `train()` defaults `calibrate=True`. | `ai/model/trainer.py:236-245,369-374`; `ai/retrain.py:723` | Used when holdout <600 rows or train <2,000; also by `ai/train.py`, `train_statsbomb.py`, `load_or_train`. Over-confident probabilities directly raise `certainty`/AI-pick counts. | High |
| M-7 | **Stage B refits on the holdout and keeps a Stage-A calibrator; the gate cannot see Stage B.** `metrics` come from Stage A (pre-split), Stage B trains on all rows including the 90-day holdout, then `attach_calibration(calibs, …, metrics)`. | `ai/retrain.py:683-711,783-786,574-576`; `STAGE_B` default `"0"` (`:299`) | Latent (`STAGE_B=0` in all shipped workflows): the shipped model has seen the evaluation window and the isotonic map is fitted for a different distribution. | High mechanism; Medium impact |
| M-8 | **Distillation appends random duplicates at the chronological tail**, which early stopping treats as the time-based validation set. | `ai/retrain.py:188-190,380-381`; `trainer.py:172-183` | Only with `SYNTH_LABELS=1` + a recipe; tree count is selected on synthetic rows. | High code; Low frequency |
| M-9 | **The documented retrain fallback crashes on `avg_odds_*`.** Clean CSVs have those columns; the downloader/open-sources fallback only creates `odds_*`. | `ai/model/features.py:216`; `retrain.py:238-252,308` | A retrain without `ai/data/clean/` dies with `KeyError` after the expensive download. Fails loudly. | High |
| M-10 | **`ai/train.py` calls `predict_match(league_slug=…)`, which does not exist.** | `ai/train.py:70-75` vs `trainer.py:517` | The one-shot script raises `TypeError` *after* training and saving; `predictor.save()` also returns `None`, so it logs "Model saved to None". | High |

### 5.2 Data writes and settlement

| ID | Finding | Evidence | Impact / trigger | Confidence |
|---|---|---|---|---|
| M-11 | **The predictor's full-row upsert replaces `stats`, wiping enrichment.** `_row_for_db` keeps the freshly-built `stats` and omits only `odds=None`; `odds_sync` writes `stats.details.market` + `stats.odds_*` and `enrich_match_details` writes `stats.details.venue/lineups/team_stats`. | `predictor.py:420-447,141-161`; `enrich_match_details.py:440-492`; `odds_sync.py:497-573` | Every 4-hourly predict run drops venue/lineup/team-stat/market detail until the 3-hourly/5-minute enrichers re-add it, costing extra ESPN calls and leaving windows with empty detail modals. | High |
| M-12 | **`odds_sync` writes de-vigged fair odds into the real price columns.** `consensus()` returns `odds = 1/prob`, and `_apply_market_odds` stores those in `predictions.odds` and `stats.odds_*`. | `ai/odds_sync.py:458-460,510-573` | Displayed "odds" are a model-implied fair price, not a bookmaker price; ROI/value-edge shown to users is internally inconsistent. | High |
| M-13 | **Cross-source fixtures are predicted 2–3 times.** `fetch_upcoming_matches` dedups by `match_id` only, but the same fixture has `espn_*`, `fdorg_*`, `rapid_*`, `tsdb_*` ids. | `live_fetcher.py:829-841`; ids at `:176,369,435,522` | Wasted model time, transient duplicate rows, and the prune may keep the wrong one. | High |
| M-14 | **Settlement selects an unbounded set and never settles beyond the first page.** `select(...).in_("status", ["upcoming","live"]).lt("kickoff_time", now)` with no `limit`. | `settle_predictions.py:168-182` | PostgREST caps at 1,000 rows; any backlog >1,000 leaves the remainder permanently `upcoming` (so the accuracy page silently under-counts). | High |
| M-15 | **Fuzzy team matching can attach the wrong score.** `team_names_match` accepts substring containment in either direction, used in settlement fallbacks. | `live_fetcher.py:571-578`; `settle_predictions.py:87-93,240-245`; TSDB `home_team.lower() in eh` `:672-674` | "Manchester United" vs "Manchester United Women"/reserve sides, or short names, can settle the wrong fixture — corrupting the public track record. | Medium |
| M-16 | **Double settlement and a race between workflows.** `scheduler.py --once` already calls `settle_past_predictions()`; `predict.yml:90-99` then runs `settle_predictions.py` again, and `settle.yml` is a *different* concurrency group. | `ai/scheduler.py:110`; `predict.yml:90-99`; `settle.yml:38-40` | Doubles football-data.org calls against a 10 req/min budget; concurrent runs do non-atomic read-modify-write on `app_settings.settlement_stats` and update rows without a status guard. | High on duplication |
| M-17 | **Privacy `push_subscriptions` read is unpaginated.** `_rest("GET","push_subscriptions")` and an `in.(...)` profile query. | `ai/notify.py:140-168` | PostgREST's 1,000-row cap silently drops subscribers; at scale the URL exceeds limits and notify fails. | High |
| M-18 | **`prune_duplicate_predictions` scans all upcoming rows and can keep an arbitrary row.** Unbounded select; within a group it prefers "just written" then lexicographically smallest `match_id`. | `predictor.py:450-508` | >1,000 upcoming rows ⇒ partial prune. Two current-run ids (espn_/fdorg_) collide and the lexicographic winner may lack odds. | High |

### 5.3 Auth and app

| ID | Finding | Evidence | Impact / trigger | Confidence |
|---|---|---|---|---|
| M-19 | **Referral count increment silently no-ops.** The new user's session updates the *referrer's* `referral_codes` row; RLS filters it to 0 rows and Supabase returns no error. Both counters are also self-writable. | `AuthContext.tsx:109-115`; `database_schema.sql:242-243` | Honest referrals never increment; dishonest users set 999999; `profile.tsx:108-110` multiplies it into displayed points. | High |
| M-20 | **`ensureUserProfile` ignores the existence-read error and upserts `Free Tier`.** | `AuthContext.tsx:57-77` | A transient read failure downgrades a paid user; once the C-1 trigger is live, the same upsert raises 42501 inside the auth callback. | Medium-High |
| M-21 | **Auth bootstrap can hang forever.** `getSession()` has no `.catch()`; the `async` `onAuthStateChange` callback awaits profile work with no try/catch. | `AuthContext.tsx:29-52`; route guard `app/_layout.tsx:55-63` | A storage/network rejection leaves `loading=true` — blank app, no error, unrecoverable without clearing storage. | High code path |
| M-22 | **Google OAuth callback is not handled on web.** `detectSessionInUrl:false` and nothing calls `exchangeCodeForSession` or parses the hash. | `lib/supabase.ts:44`; `app/(auth)/login.tsx:132-138` | The redirect lands back on `/` with tokens in the URL that supabase-js ignores; the guard bounces to login. OAuth is effectively unusable on web. | Medium |
| M-23 | **`insights` has no schema or policy anywhere in the repo.** Only a client-side `hasAccess` boolean guards the query. | `app/(tabs)/insights.tsx:58-70`; no `create table insights` in any `.sql` | If created via the dashboard without RLS, the anon key reads every VIP insight. **Unverified — must check live.** | Low (unverified) |
| M-24 | **A 24-hour-late payment is expired without ever being re-verified; the client stops polling at 7.5 min.** | `verify-payments.ts:137-176`; `subscriptions.tsx:133-140` | Bank transfers that settle after 24 h grant nothing, and the UI tells the user it is "being verified in the background". | High |

### 5.4 Website

| ID | Finding | Evidence | Impact | Confidence |
|---|---|---|---|---|
| M-25 | **`/news` exports only a Suspense skeleton.** `useSearchParams()` is inside the only child of the boundary, so prerender bails for the whole page; the built HTML has `animate-pulse`, no `<h1>`, no story text. | `website/src/components/NewsReader.tsx:66-78,94`; verified in `.next/server/app/news.html` | Crawlers and no-JS/first paint see an empty shell; every `?a=` deep link is the same contentless document. | High |
| M-26 | **Every page inherits the homepage OG block and there is no canonical URL.** | `website/src/app/layout.tsx:25-28`; built `/news` HTML has `og:url` = homepage | Shared `/predictions` links show the homepage card; Vercel/Pages/`?a=` URLs are uncrawlable duplicates. | High |
| M-27 | **No ad or cookie disclosure.** AdSense is loaded in `layout.tsx:76-80`; the privacy page has no third-party/advertising/cookie section, no legal entity and no contact address. | `website/src/app/privacy/page.tsx:24-27` | AdSense policy violation and a GDPR/ePrivacy gap for EU/UK visitors. | High that it is absent |
| M-28 | **Untrusted JSON flows into `href` with no scheme validation.** `story.url`/`item.image`/`sources[name]` come from `news.json` with only `Array.isArray` validation; React 18.3.1 warns but does **not** block `javascript:` hrefs. | `website/src/lib/useNewsFeed.ts:64-68`; `NewsReader.tsx:341-344,225`; `NewsFeed.tsx:93` | Click-to-XSS if a `javascript:` value reaches the feed (repo write or compromised ref). Mechanism verified (`react-dom.development.js:655`). | High mechanism; Low-Medium reachability |
| M-29 | **Client data fetches have no timeout or abort.** | `website/src/lib/statwiseData.ts:53-56` | A stalled connection leaves the skeleton forever; failures are indistinguishable from "refreshing". | High |
| M-30 | **Committed build artifacts.** `git ls-files website/.next` = 152 files, `website/out` = 53; the tracked `out/` mixes builds (`out/index.html` links `/statwise/_next/…`, `out/news/index.html` links `/_next/…`). | `.gitignore` has no `.next`/`out` entry | Serving `out/` as-is yields an unstyled `/news`; every clone carries stale generated files that can be deployed by accident. | High on facts |

### 5.5 CI / build / config

| ID | Finding | Evidence | Impact | Confidence |
|---|---|---|---|---|
| M-31 | **14 credential-holding workflows have no `permissions:` and all deps/actions are unpinned.** Every dependency is a floor (`numpy>=1.24`, `supabase>=2.0`, …); 32 `uses:` are floating major tags. | `ai/requirements.txt:1-11`; `.github/workflows/*.yml` | A compromised package or action executes with the Supabase service key in env and the repo default token scope. | Medium |
| M-32 | **The production frontend image runs the Expo dev server.** `CMD [... "expo","start","--web","--port","5000","--clear"]`; `NODE_ENV=production` has no effect. | `Dockerfile.frontend:20`; `statwise/package.json:9-12`; `bundle-report.yml:38` shows the real build command | Non-minified dev bundle, full re-bundle on every restart, no immutable artefact; the healthcheck still passes. | High |
| M-33 | **The Docker build context includes ~3 GB of local junk and bakes a local model.** `.dockerignore` omits `.venv` (2 GB), `website/node_modules`, `website/out`, `ai/data` (719 MB); `Dockerfile.ai:14` copies `ai/` including the gitignored 18 MB pickle. | `.dockerignore:1-18`; `Dockerfile.ai:14` | Non-reproducible AI image (ships whatever model was on the builder's disk) and much larger images. | High |
| M-34 | **The production DB Node job installs from an untracked lockfile.** `.gitignore:33` ignores `.github/scripts/package-lock.json`; the workflow runs `npm install`. | `fetch-football-data.yml:36-38` | Any minor/patch release in `^2` silently changes the code that writes prod rows with the service key. | High |
| M-35 | **Three jobs publish "the deployed model" with no shared concurrency.** `retrain-weekly` has no `concurrency:` at all; `train-model` and `train-attention-student` save the same `statwise-model-v2-*` prefix and all commit `model_info.json`. | `retrain-weekly.yml:156-164,195-205`; `train-model.yml:32-34`; `predict.yml:37-44` | Concurrent retrains ⇒ two cache saves, competing `git pull --rebase`/`push`, and a nondeterministic deployed model. A crashed run can push under `if: always()`. | High config |
| M-36 | **In-play odds ticks can cancel the pre-match refresh.** Both jobs share `concurrency: group: odds-sync`; `inplay` fires every 5 min and GitHub keeps one pending run per group. | `odds-sync.yml:32-34,14,17` | The 05/11/17/23 UTC pre-match refresh can be skipped, leaving `stats.odds_*`/`value_edge` stale for the evening card. | Medium |
| M-37 | **No `pull_request` trigger in any of the 24 workflows.** | `grep -rl pull_request .github/workflows` → 0 | Tests are post-merge only; `ai/**` has no unit-test gate at all (earliest check is the 120-min retrain). Nothing blocks a broken merge. | High |
| M-38 | **Supabase project drift.** Code/docs use `fwpriiugfjhtoymcbmso`; `docker-compose.yml:28` defaults to `pdrcyuzfdqjnsltqqxvr`. | `predictor.py`, `notify.py`, `settle_predictions.py`, `odds_sync.py` hardcode the former as fallback | A compose deploy without `.env` writes to a different project (or fails). Fallback URLs hide misconfiguration. | High |

### 5.6 Data layer / schema

| ID | Finding | Evidence | Impact | Confidence |
|---|---|---|---|---|
| M-39 | **`odds_sync`/`notify`/`settle`/`predictor` all fall back to a hardcoded Supabase URL + anon key when env is missing**, and writes then fail row-by-row with only a warning. | `predictor.py:18-31,171-175`; `env_loader.py` | Misconfiguration looks like "0 predictions" instead of an error; one job can silently write to the wrong project. | High |
| M-40 | **`app_settings.settlement_stats` and booking stats use unlocked read-modify-write.** | `settle_predictions.py:327-367`; `sportybet_booking.py:_load_stats/write_stats` | Concurrent settle runs lose increments. | High |
| M-41 | **`env_loader.load_dotenv` does not strip `export ` or inline comments.** | `ai/env_loader.py:28-37` | `FOO=bar  # note` becomes a value with the comment; `export FOO=…` becomes key `export FOO`. | High code |
| M-42 | **The working tree has `statwise/public/sw.js` truncated to 0 bytes** (HEAD has the push handler). | `git status`; file size 0 | Web push is broken in the current checkout. Flagged, not changed (user work). | High |

---

## 6. Low

| ID | Finding | Evidence |
|---|---|---|
| L-1 | No root `LICENSE`; `package.json` license `ISC` and website/statwise MIT-ish metadata disagree; no license file anywhere. | `package.json`, `website/package.json` |
| L-2 | Root `package.json` test script is the placeholder `exit 1`. | `package.json:7` |
| L-3 | `pyproject.toml` is 1,142 lines with hundreds of unrelated auto-generated `[tool.uv.sources]` entries; declares `torch`/`torchvision`/`xgboost` that no `ai/` module imports; floors disagree with `ai/requirements.txt`; `uv.lock` is 460 KB. | `pyproject.toml` |
| L-4 | `continue.md` documents `ai/model/neural_net.py` (does not exist) and a TabTransformer ensemble that is not in production. | `continue.md:20-30`; `ls ai/model` |
| L-5 | `ai/backtest.py` hardcodes `TEST_CUTOFF = "2023-08-01"` (stale as data grows) and calls `predict_match` without precomputed blocks → ~30 s/test row. | `ai/backtest.py:31,218` |
| L-6 | `ai/notify.py` `push_subscriptions` read is unpaginated (see M-17) and the profile query builds an unbounded `in.(...)` URL. | `notify.py:140-168` |
| L-7 | Telemetry rate limit is keyed on the client-controllable `x-forwarded-for`; the INSERT policy is `with check (true)` for `anon`. | `supabase-telemetry-migration.sql:66-100` |
| L-8 | Web session tokens live in `localStorage`; no CSP/security headers are defined for the Expo deployment. | `lib/supabase.ts:15-37` |
| L-9 | Accessibility: 19 `<TextInput>`s vs 6 `accessibilityLabel`s; the plan-period toggle has no `tab`/`selected` state; purchase buttons lack roles and disabled state; several icon-only buttons are unlabelled. | `subscriptions.tsx:293-309,410-449`; `forum.tsx`; `profile.tsx:292-304` |
| L-10 | Website nav is not responsive: brand + five links at 14 px overflow a 375 px viewport on every page; no wrap/hamburger. | `website/src/app/page.tsx:98-112` and siblings |
| L-11 | Secondary text fails WCAG AA: `text-gray-600` on `#0a0a0f` ≈ 2.6:1 (used for source attribution, footer, 18+/gambling disclaimer). | `NewsReader.tsx:280`; `page.tsx:309-313,388-392` |
| L-12 | News imagery is `alt=""` everywhere, including the article hero. | `NewsReader.tsx:175-178,290-293`; `NewsFeed.tsx:46-49` |
| L-13 | `sync_news.py` has a socket timeout but no total deadline or size cap, no retries, and follows redirects to arbitrary hosts. | `website/scripts/sync_news.py:122-125` |
| L-14 | `sync-news.yml` is the only workflow with no `timeout-minutes` and the only committing workflow that never rebases. | `sync-news.yml:41-43` |
| L-15 | Production origin is copied verbatim in `robots.txt`, `sitemap.xml` and `siteConfig.ts`; the sitemap has no `/news/?a=` entries or `lastmod`. | `website/public/robots.txt:4`; `sitemap.xml:4`; `siteConfig.ts:15` |
| L-16 | `/predictions` title double-suffixes: `"Free AI Football Predictions - StatWise - StatWise"`. | `predictions/page.tsx:9` + `layout.tsx:8-9` |
| L-17 | `workflow_dispatch` inputs are interpolated unquoted into `run:` blocks (settle/live-enrich/eval-draw/bundle-report/exp-audit); `settle.yml`'s step holds the service key. | `settle.yml:79`; `live-enrich.yml:64-66`; `eval-draw.yml:49-50` |
| L-18 | Every rotation logs the first 8 characters of the football-data token. | `.github/scripts/fetch_football_data.js:102-103,115-116` |
| L-19 | `weather-backfill.yml` saves a new GB-scale cache key per shard per run, competing with the 10 GB repo cache that `predict.yml` restores the model from. | `weather-backfill.yml:93-98`; `predict.yml:43-44` |
| L-20 | `continue-on-error: true` on Playwright install, booking and settlement makes those failures permanently invisible. | `predict.yml:73-92` |
| L-21 | `Dockerfile.ai` installs gcc/g++/make but never runs `make -C ai/core`, so the C++ fast path is absent; runs as root; no healthcheck for `ai-scheduler`. | `Dockerfile.ai:5-9,14,20`; `docker-compose.yml:21-33` |
| L-22 | `ai/sim` is research-only (no production import; `SYNTH_LABELS` default off), but `merge/evaluate` accept an acknowledged league-aggregate leak and `compare_production` fits alpha on eval rows. | `ai/sim/evaluate.py:24-56`; `retrain.py:291-293` |
| L-23 | Weather/venue lookups run sequentially inside the prediction loop with a 0.22 s sleep per uncached call. | `predictor.py:266-278`; `team_venues.py:191-231` |

---

## 7. Findings by domain

| Domain | Critical | High | Medium | Low |
|---|---|---|---|---|
| Security / RLS / auth | C-1…C-7 | H-5, H-6, H-8, H-11 | M-19…M-24 | L-7, L-8 |
| ML correctness | — | H-1, H-2 | M-1…M-10 | L-5, L-22 |
| Data writes / settlement | — | H-4 | M-11…M-18 | L-6, L-23 |
| Schema / config | — | H-3, H-9 | M-38…M-42 | L-3 |
| Website | — | H-10 | M-25…M-30 | L-10…L-16 |
| CI / build | — | — | M-31…M-37 | L-17…L-21 |
| Docs / licensing / a11y | — | — | — | L-1, L-2, L-4, L-9 |

---

## 8. Phase 4 — convergence check

Each dimension was re-examined after the first pass; the second pass added H-1/H-2/M-1…M-4
(from re-reading the feature pipeline rather than trusting the "features look consistent"
comment), and removed two false positives:

- **"React blocks `javascript:` hrefs"** — checked the installed React 18.3.1 source:
  `sanitizeURL` only `console.error`s and returns the URL (`react-dom.development.js:655`).
  Kept as M-28, downgraded to Low-Medium reachability.
- **"News content XSS"** — the whole pipeline ends in React text children; only URL fields
  are exposed. Refuted.

Dimensions with no material findings: cryptography (none used beyond Supabase/Flutterwave),
i18n (single-locale by design), concurrency primitives inside Python (single-threaded
scheduler; the only shared-state risks are the DB read-modify-writes in M-16/M-40).

**Convergence reached:** the last pass produced no new Critical/High findings, and every
Critical/High item has a cited line, a trigger, and a fix.

---

## 9. Coverage and limits

**Read in full or in relevant part (verified myself):**
`ai/scheduler.py`, `ai/model/predictor.py`, `ai/model/trainer.py` (service path),
`ai/model/features.py`, `ai/model/live_fetcher.py`, `ai/model/team_venues.py`,
`ai/model/inplay.py`, `ai/core/features.cpp` (bulk kernel), `ai/settle_predictions.py`,
`ai/odds_sync.py`, `ai/notify.py`, `ai/sportybet_booking.py`, `ai/enrich_match_details.py`,
`ai/live_enricher.py`, `ai/env_loader.py`, `ai/fdorg.py`, `ai/retrain.py` (pipeline, stages,
distillation), `ai/sim/{run,merge,distill,evaluate}.py`, `ai/attn/*`, `ai/backtest.py`,
`statwise/lib/{bookings,bookingGuard,paymentCore,supabase,validation}.ts`,
`statwise/scripts/verify-payments.ts`, `statwise/context/AuthContext.tsx`,
`statwise/app/(tabs)/{index,subscriptions,profile,insights,forum}.tsx` (relevant paths),
all five checked-in SQL files, all 24 workflows, `.github/scripts/fetch_football_data.js`,
`website/src/**` (components, app routes, lib), `website/scripts/sync_news.py`,
Dockerfiles, `docker-compose.yml`, `.dockerignore`, `.gitignore`, `package.json`s,
`pyproject.toml`, and the docs (`replit.md`, `IMPROVEMENT_PLAN.md`, `MODEL_REPORT.md`,
`continue.md`, `HANDOFF.md`).

**Not fully read (sampled / pattern-searched):** `ai/sim/realism.py` (1,139 lines) and
`ai/sim/compare_production.py` (566 lines) beyond their guards and gate logic;
`ai/attn/train.py` (681 lines); `ai/experimental/test_*.py`; the full `website/out` tree;
individual `ai/data/clean/*.csv` beyond spot checks.

**Excluded:** `node_modules/`, `.venv/`, `website/.next/`, `website/out/`, `oss/` (empty),
`attached_assets/`, datasets and model binaries, `uv.lock`, and vendored/generated code.

**Could not verify:** the live Supabase project (no DB/credentials access), so every
RLS finding is "as written in the checked-in SQL". Live Flutterwave/Supabase runtime
behaviour was not exercised. Live feed payloads were not fetched (no network); the
"Manchester City FC" spelling is from football-data.org's documented v4 schema and the
subagent's cited code path, not a live sample.

---

## 10. Open questions (resolve before trusting the security findings)

1. **Which SQL files were actually applied?** `IMPROVEMENT_PLAN.md:1019-1021` says section 5
   of `supabase-pending-migrations.sql` is still pending; `:1470` says an earlier bundle ran
   2026-10-27; `replit.md:135` tells fresh setups to run `supabase-schema.sql`. Run
   `select tablename, policyname, cmd, qual from pg_policies where schemaname='public'` and
   `select tgname from pg_trigger where tgrelid='public.user_profiles'::regclass` to settle
   C-1/C-2/C-5/C-6/C-7 and H-5.
2. **Does `insights` have RLS?** No migration creates it. `select relrowsecurity from
   pg_class where relname='insights'`.
3. **Is the Flutterwave key live and has the image ever been pushed?** Prefix says live.
   Rotate regardless.
4. **Is `fwpriiugfjhtoymcbmso` the production project, or `pdrcyuzfdqjnsltqqxvr`?** The code
   and the compose default disagree.
5. **Is `statwise/public/sw.js` intentionally emptied?** It is a local modification; web
   push is broken in this working tree.
6. **Which model source is authoritative?** `predict.yml` restores the newest
   `statwise-model-v2-*` cache; three jobs write that prefix.

---

## 11. Suggested remediation order

1. **Rotate** the GitHub PAT and the Flutterwave secret; add `.dockerignore` rules; rebuild.
2. **Apply** `supabase-security-migration.sql` + section 5 of
   `supabase-pending-migrations.sql`; then verify with `pg_policies`/`pg_trigger` (closes
   C-1, C-2, C-5, C-6, C-7, H-5).
3. **Add** a subscription-expiry job (C-3) and a payment-queue rate limit/pagination (H-4).
4. **Fix** `_canonical_team` usage in `build_features` and the C++ lookback truncation, then
   re-train and compare OOT log loss (H-1, H-2). These are the highest-leverage product
   fixes.
5. **Remove** the tier overwrite from the fixture job (H-3); consolidate the schema (H-9).
6. Work down the Medium list; the CI `permissions:`/pinning and `pull_request` gate are
   cheap and broad.

---

## 12. Appendix — verified sound

- **Payment adjudication is genuinely server-side and catalog-priced.** `paymentCore.ts:189-243`
  requires a matching `tx_ref` round-trip, user prefix, plan id, `NGN`, and an exact price
  match before crediting. The client cannot buy a cheaper tier.
- **`apply_payment_verification` is service-role only once applied**, with `ON CONFLICT
  (transaction_id) DO NOTHING`, a `FOR UPDATE` on the event, and `already` on re-runs — no
  double-credit path found.
- **Tier-gated predictions RLS is correct** where it is defined
  (`supabase-migration-rls-tier.sql` drops the competing policies and maps `current_tier`
  correctly).
- **`inplay.py` conditional-Poisson maths is plausible and internally consistent.**
- **No XSS sink** in app or website: no `dangerouslySetInnerHTML`/`innerHTML`/`eval`; scraped
  news text reaches React text children only.
- **No open redirect** in booking/share URLs: `validation.ts:107-120` enforces `https:` plus
  exact/dot-suffix host matching; `bookingGuard.ts:44-49` rebuilds non-SportyBet URLs.
- **No service-role key in tracked files**, and `.env` was never committed.
- **Feature count/order is consistent**: 161 names in both training (`hstack([X_cont,
  league_ids])`) and serving (`np.append(feat, league_id)`), with matching λ/weather/travel
  indices.
- **Form/travel windows are causal** (`groupby(...).shift(1)` before rolling) and the same
  functions run at train and serve.
- **Main-path isotonic calibration is honest**: fitted on the first holdout half, `use_cal`
  decided on the untouched second half.
- **No fork-triggered secret exposure** in CI: zero `pull_request`/`pull_request_target`
  triggers.
- **Export/deploy config is coherent** (`output:'export'`, `images.unoptimized`,
  `trailingSlash`, `framework:null`, `VERCEL=1` suppressing `basePath`).
- **No secrets in the website client bundle** beyond the public anon key and AdSense id.

---

## 13. Remediation status (post-fix pass, 2026-10-29)

Every finding is bucketed into one of four states:

- **Fixed** — corrected in the working tree; no operator action needed beyond the retrain
  noted where applicable.
- **Fixed · migrate** — the code/SQL fix is written (a migration file or workflow in the
  repo) but must be **applied to the live Supabase project by hand** (the project was
  unreachable from here). Until then the vulnerability is still live.
- **Fixed · retrain** — corrected in the feature/training code, but the deployed `.pkl` was
  trained on the old (wrong) features; a GitHub-Actions retrain is required for the fix to
  reach production.
- **Open** — intentionally left for a product/ops decision (listed with the reason).

### Critical

| ID | Status | Note |
|---|---|---|
| C-1 paywall editable by customer | **Fixed · migrate** | Tier-write block + hardened `user_profiles` UPDATE policy in `supabase-audit-hardening.sql`; apply it. Client no longer writes tier fields. |
| C-2 emails world-readable | **Fixed · migrate** | Anon SELECT on `user_profiles` revoked in the hardening migration. |
| C-3 one purchase is permanent | **Fixed · migrate** | `expire_subscriptions()` RPC + hourly `expire-subscriptions.yml` added; apply the migration so the RPC exists. |
| C-4 Flutterwave secret in image | **Fixed** | `.dockerignore` now excludes `**/*_key`, `.env*`, `.venv`, build outputs; `Dockerfile.frontend` rewritten to a static non-root build. **Rotate the key out-of-band.** |
| C-5 booking code leaks paid AI Picks | **Fixed** | `sportybet_booking.py::get_ai_picks` filters to `tier == "free"`; the published accumulator can no longer carry premium/vip/vvip picks. |
| C-6 admin tables open to anon | **Fixed · migrate** | Drop/restrict `admin_urls`/`admin_users`/`system_logs` in the hardening migration. |
| C-7 sports data world-writable | **Fixed · migrate** | `matches`/`team_stats`/`prediction_accuracy` write policies tightened in the hardening migration. |

### High

| ID | Status | Note |
|---|---|---|
| H-1 team names not reconciled into features | **Fixed · retrain** | `_canonical_team` now applied to home/away keys in `build_features`/`trainer.py`; retrain to benefit. |
| H-2 C++ bulk kernel truncates to oldest 600 | **Fixed · retrain** | Lookback keeps the most recent `lb` matches + own-team pre-match Elo. |
| H-3 fixture job overwrites certainty tier | **Fixed** | Tier written from the model's certainty band, not the static league map. |
| H-4 payment-queue flooding | **Fixed** | Poller caps per-run/per-user FIFO + final-verify-before-expiry; client enqueue cap (`MAX_PENDING_CHECKOUTS=3`). |
| H-5 legacy payment RLS cross-tenant | **Fixed · migrate** | Policies dropped/tightened in the hardening migration. |
| H-6 referral/reward rows mintable | **Fixed · migrate** | `register_referral` RPC validates amounts server-side in the hardening migration. |
| H-7 live Flutterwave key exposed | **Fixed** | Same `.dockerignore`/image fix as C-4. **Rotate.** |
| H-8 (security-subagent scope) | **Fixed · migrate** | Covered by `supabase-audit-hardening.sql` (RLS/grants); apply it. |
| H-9 schema drift across SQL files | **Open** | Consolidating into one canonical `pg_dump`-derived schema is a deliberate cleanup, not a bug fix; flagged for a follow-up. |
| H-10 stale GitHub Pages build | **Open** | Canonicalise on Vercel (the real origin is `statwise-flax.vercel.app`); Pages left as-is pending that decision. |
| H-11 git remote embeds a GitHub PAT | **Open · rotate** | Cannot rotate from here. Remove the credential from `.git/config` and rotate the token (use `gh auth`/SSH). |
| H-12 NumPy F-order cross-match corruption | **Fixed · retrain** | `np.ascontiguousarray` on every odds/feature block before the ctypes kernel; reproduced-then-closed. Retrain required. |

### Medium / Low (by group)

- **Model correctness (M-1…M-15 and related):** **Fixed · retrain** — `cts` at serving is
  now the kickoff date, Elo-volatility fed from the per-match series, Python/C++ update rules
  aligned, `match_date=None` guarded, `avg_odds_*` reindexed, closing-odds passed to the
  kernel, F-order (H-12) closed, `feat is None` raises. All need one CI retrain to land.
- **Payments (M-24, H-4):** **Fixed** — stale rows get one final Flutterwave verification
  before expiry (late bank transfers are credited, not dropped); bounded per-run caps.
- **CI / Docker (M-16, M-32, M-33, M-35, M-36):** **Fixed** — settlement writes are
  status-guarded/idempotent; frontend image serves a static export (no dev server);
  `ai/models` excluded from build context; model-publishing workflows share one `model-publish`
  concurrency group; odds-sync inplay/pre-match run in separate groups.
- **Notifications / push (M-17, native conversion):** **Fixed** — web push deleted end-to-end
  (`sw.js`, VAPID, `push_subscriptions` sender, `pywebpush` dependency); native Expo push is
  the single path, fully paginated, with dead-token purge. Apply
  `supabase-native-push-migration.sql` for the `push_tokens` table.
- **Website (contrast, a11y, metadata, news content, timeouts):** **Fixed** — responsive
  header, real `/news` content, per-page metadata/canonical, privacy/cookie disclosure,
  contrast and alt-text fixes, fetch timeouts + size caps. Static export and `tsc` verified.
- **Low (L-1…L-23):** mostly **Fixed** (copy, dead code, small guards) or **Open** where a
  product decision is needed; L-9 (a11y) **Partial** — contrast/alt fixed, full screen-reader
  pass still recommended.

---

## 14. Further improvements (beyond bug-fixing)

### Android app

1. **Native in-app payments (Play Billing / Google Play Billing Library).** Flutterwave
   checkout is web-only by design (Play Store digital-goods rules). If distribution stays on
   Play, move paid tiers to Play Billing; keep Flutterwave only for direct/web sales.
2. **EAS project id + store submission.** `eas.json` and `app.json` are ready (versionCode,
   `POST_NOTIFICATIONS`, notifications plugin); an EAS `projectId` and Play Store credentials
   are needed to build/submit an AAB. Add per-notification **channels** for finer Android
   control.
3. **Full accessibility pass (L-9).** Contrast and alt text are fixed; add TalkBack labels on
   icon-only controls, dynamic-type support, and focus order for the tab navigation.
4. **Offline-first reads.** Cache the last predictions/insights in AsyncStorage so the app is
   useful with no connection (it already detects offline and shows honest copy).
5. **On-device feature parity check.** The model's feature vector (161 slots) is the single
   source of truth; a CI assertion that serving feature names/order match training would
   catch future silent skew (belt-and-braces for H-1/H-2/H-12).

### Website

1. **Consent CMP.** The privacy page discloses AdSense cookies honestly but there is no
   banner; a lightweight CMP (or region-aware consent) would make EU compliance explicit.
2. **Per-page OG images.** Canonical/OG tags are per-page now, but they all point at the same
   `icon.png`; generated per-route social cards would lift click-through.
3. **Analytics.** No first-party traffic analytics yet — a privacy-respecting counter would
   inform the A/B funnel and news desk.
4. **Canonicalise deployment.** Decide Vercel-vs-Pages (H-10) and delete the loser so
   crawlers stop indexing a stale copy; keep `sitemap.xml`/`robots.txt` in sync with the
   chosen origin.
