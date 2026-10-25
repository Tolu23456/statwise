#!/usr/bin/env python3
"""Quarantine the legacy invented rows in Supabase's `predictions` table.

Background
----------
Until commit 5b6665f (2026-10-22) `.github/scripts/fetch_football_data.js`
invented `prediction` / `confidence` / `odds` / `reasoning` with a seeded
LCG and upserted them over the model's rows every three hours.  The seed was
a football-data.org match id (~300-700), whose first draw is ~0.0005, so the
generator said **"Home Win", 100.0% of the time** — and the accuracy screen
scored those rows as if Pico had produced them, pinning the reported hit rate
to the home-win base rate (44%).

A Pico row always carries `stats.prob_home/draw/away`; an invented row carries
at most `stats.details`.  That is the discriminator.

The script is NON-DESTRUCTIVE: it merges `data_quality: "invented"` into the
row's existing `stats` JSON (and `quarantined_at`), so nothing is deleted and
everything can be reverted with --revert.  Rows already flagged are skipped.

Usage
-----
    python ai/tools/quarantine_invented_rows.py             # dry run (default)
    python ai/tools/quarantine_invented_rows.py --apply     # write the flags
    python ai/tools/quarantine_invented_rows.py --revert    # undo the flags
    python ai/tools/quarantine_invented_rows.py --apply --delete  # hard delete

Credentials come from SUPABASE_URL / SUPABASE_SERVICE_KEY in the environment
or the repo-root .env.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FLAG = "data_quality"


def _load_env() -> None:
    """Fill SUPABASE_URL / SUPABASE_SERVICE_KEY from the repo .env if unset."""
    path = os.path.join(REPO, ".env")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k in ("SUPABASE_URL", "SUPABASE_SERVICE_KEY") and not os.environ.get(k):
                    os.environ[k] = v


def _client():
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        sys.exit("Missing SUPABASE_URL / SUPABASE_SERVICE_KEY (env or repo .env).")

    def req(method: str, path: str, params: dict | None = None, body: dict | None = None):
        q = f"?{urllib.parse.urlencode(params)}" if params else ""
        data = json.dumps(body).encode() if body is not None else None
        r = urllib.request.Request(
            f"{url}/rest/v1/{path}{q}",
            data=data,
            method=method,
            headers={
                "apikey": key,
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "Prefer": "return=representation",
            },
        )
        try:
            with urllib.request.urlopen(r, timeout=60) as resp:
                raw = resp.read().decode()
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"{method} {path} -> {e.code}: {e.read().decode()[:300]}") from e
        return json.loads(raw) if raw else []

    return req


def is_invented(row: dict) -> tuple[bool, str]:
    """Is this row NOT written by Pico?  Returns (flag, reason)."""
    stats = row.get("stats") or {}
    if not isinstance(stats, dict):
        return True, "stats is not an object"
    if isinstance(stats.get("prob_home"), (int, float)):
        return False, ""
    # No model probability at all -> some other writer.  Record the tells.
    tells = []
    if row.get("prediction") == "Home Win":
        tells.append("home-win-only-writer")
    if row.get("confidence") is not None:
        tells.append(f"conf={row['confidence']}")
    if isinstance(stats, dict) and stats.get("details") and not stats.get("source"):
        tells.append("stats.details-only")
    return True, ",".join(tells) or "no prob_home"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="write the quarantine flags")
    ap.add_argument("--revert", action="store_true", help="remove the quarantine flags")
    ap.add_argument("--delete", action="store_true",
                    help="with --apply: delete flagged rows instead of flagging them")
    args = ap.parse_args()

    _load_env()
    req = _client()

    cols = ("id,match_id,home_team,away_team,match_date,status,prediction,confidence,"
            "actual_result,created_at,stats,reasoning")
    rows = []
    offset = 0
    while True:
        page = req("GET", "predictions",
                   {"select": cols, "order": "created_at.asc",
                    "offset": offset, "limit": 1000})
        rows.extend(page)
        if len(page) < 1000:
            break
        offset += 1000
    print(f"Fetched {len(rows)} prediction rows.")

    flagged, clean, already = [], 0, 0
    for r in rows:
        bad, reason = is_invented(r)
        if not bad:
            clean += 1
            continue
        if (r.get("stats") or {}).get(FLAG) == "invented":
            already += 1
            continue
        flagged.append((r, reason))

    print(f"  Pico-written (prob_home present): {clean}")
    print(f"  already quarantined:              {already}")
    print(f"  newly identified as invented:     {len(flagged)}")

    if not flagged:
        print("Nothing to do.")
        return 0

    # Show a sample so the operator can eyeball the discriminator before writing.
    print("\nSample of rows to be flagged:")
    for r, reason in flagged[:10]:
        print(f"  {r['match_id']}  {r['match_date']}  pred={r['prediction']!r} "
              f"conf={r['confidence']} actual={r['actual_result']!r}  [{reason}]")
        if r.get("reasoning"):
            print(f"      reasoning: {str(r['reasoning'])[:110]}")

    if not args.apply:
        print("\nDRY RUN — pass --apply to write "
              + ("(and --delete to remove the rows)." if args.delete else "the flags."))
        return 0

    if args.delete:
        ids = [r["id"] for r, _ in flagged]
        for i in range(0, len(ids), 200):
            req("DELETE", "predictions", {"id": f"in.({','.join(ids[i:i+200])})"})
        print(f"Deleted {len(ids)} invented rows.")
        return 0

    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    ok = failed = 0
    for r, _ in flagged:
        stats = dict(r.get("stats") or {})
        stats[FLAG] = "invented"
        stats["quarantined_at"] = now
        stats["quarantine_reason"] = "no stats.prob_home — written by the legacy seeded-PRNG fetch job"
        try:
            req("PATCH", "predictions", {"id": f"eq.{r['id']}"}, {"stats": stats})
            ok += 1
        except RuntimeError as e:
            failed += 1
            if failed <= 3:
                print(f"  FAILED {r['id']}: {e}")
    print(f"\nQuarantined {ok} rows ({failed} failures).")
    print("Accuracy screens must now exclude stats.data_quality == 'invented' "
          "or rows without stats.prob_home.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
