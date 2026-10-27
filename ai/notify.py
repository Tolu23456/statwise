#!/usr/bin/env python3
"""
Web push notification sender (IMPROVEMENT_PLAN Step 3.2).

Sends browser push notifications for three events:

  --kind picks     new AI Picks since the last picks run (high confidence)
  --kind kickoff   matches starting in the next ~30 minutes
  --kind settled   picks that settled since the last settled run

How it works
------------
ai/notify.py reads `push_subscriptions` (one row per browser, written by
the app's lib/push.ts) and `user_profiles.notifications` (the existing
per-user toggle in the profile screen), builds a Web Push payload and
sends it with pywebpush + the VAPID private key from the
VAPID_PRIVATE_KEY secret (env).

Dedupe/state lives in app_settings.notify_state so runs never double-send:
each event carries a key (e.g. "picks:2026-10-26T12:00Z:espn_123") and a
sent set is kept per kind.

No `push_subscriptions` table yet (migration not run)? Exits 0 with a
clear note — notifications are optional, nothing else may break.

Usage:
    python ai/notify.py --kind kickoff
    python ai/notify.py --kind picks --dry-run
    python ai/notify.py --kind settled --limit 50
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from env_loader import load_dotenv  # noqa: E402

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-5s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("notify")

SUPABASE_URL = (os.environ.get("SUPABASE_URL") or "").strip()
SUPABASE_KEY = (
    os.environ.get("SUPABASE_SERVICE_KEY")
    or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    or ""
).strip()
VAPID_PRIVATE_KEY = (os.environ.get("VAPID_PRIVATE_KEY") or "").strip()
#: pywebpush needs a contact claim; the repo is the honest contact.
VAPID_CLAIMS = {"sub": "https://github.com/Tolu23456/statwise"}

#: How far ahead of kickoff the "match starting soon" alert fires.
KICKOFF_LEAD_MINUTES = 25
KICKOFF_MIN_MINUTES = 5   # don't alert for matches already under way

#: Keep at most this many sent-keys per kind in app_settings state.
MAX_SENT_KEYS = 500


# ── Supabase (REST via requests — no client SDK needed here) ───────────────

def _rest(method: str, path: str, payload=None, params=None):
    import requests  # local import: only used at runtime
    url = f"{SUPABASE_URL.rstrip('/')}/rest/v1/{path}"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }
    r = requests.request(method, url, json=payload, params=params or {},
                         headers=headers, timeout=30)
    if r.status_code == 404:
        raise FileNotFoundError(path)
    r.raise_for_status()
    return r.json() if r.text.strip() else None


def fetch_subscriptions() -> list[dict]:
    """All browser subscriptions joined with the user's notification pref.

    Returns [] with a clear log when the table doesn't exist yet.
    """
    try:
        subs = _rest("GET", "push_subscriptions",
                     params={"select": "user_id,endpoint,p256dh,auth"})
    except FileNotFoundError:
        logger.info("push_subscriptions table not found — run "
                    "statwise/supabase-notifications-migration.sql. "
                    "No notifications sent (this is fine).")
        return []
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not read push_subscriptions: %s", e)
        return []
    if not subs:
        return []
    try:
        profiles = _rest(
            "GET", "user_profiles",
            params={"select": "id,notifications,display_name,username",
                    "id": f"in.({','.join(sorted({s['user_id'] for s in subs}))})"},
        ) or []
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not read user_profiles: %s", e)
        return []
    by_id = {p["id"]: p for p in profiles}
    out = []
    for s in subs:
        prof = by_id.get(s["user_id"]) or {}
        if prof.get("notifications") is False:
            continue  # user turned notifications off in the profile screen
        out.append({**s, "name": prof.get("display_name") or prof.get("username") or ""})
    logger.info("%d subscription(s) with notifications enabled.", len(out))
    return out


# ── dedupe state (app_settings.notify_state) ───────────────────────────────

def load_state() -> dict:
    try:
        rows = _rest("GET", "app_settings",
                     params={"select": "setting_value",
                             "setting_key": "eq.notify_state", "limit": "1"})
    except Exception:  # noqa: BLE001
        return {}
    val = (rows[0] if rows else {}).get("setting_value")
    if isinstance(val, str):
        try:
            val = json.loads(val)
        except Exception:  # noqa: BLE001
            val = {}
    return val if isinstance(val, dict) else {}


def save_state(state: dict) -> None:
    row = {
        "setting_value": state,
        "description": "Web push dedupe state per notification kind (plan 3.2)",
        "is_active": True,
    }
    try:
        updated = _rest("PATCH", "app_settings?setting_key=eq.notify_state", row)
        if not updated:
            _rest("POST", "app_settings", {**row, "setting_key": "notify_state"})
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not save notify state: %s", e)


def new_keys(kind: str, keys: list[str], state: dict) -> list[str]:
    """Keys not yet sent for this kind (pure — unit tested)."""
    sent = set((state.get(kind) or {}).get("sent") or [])
    return [k for k in keys if k not in sent]


def mark_sent(kind: str, keys: list[str], state: dict,
              now: dt.datetime | None = None) -> dict:
    """Return updated state with keys marked sent (pure — unit tested)."""
    now = now or dt.datetime.now(dt.timezone.utc)
    entry = dict(state.get(kind) or {})
    sent = list(entry.get("sent") or [])
    sent.extend(keys)
    entry["sent"] = sent[-MAX_SENT_KEYS:]
    entry["last_run_at"] = now.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    out = dict(state)
    out[kind] = entry
    return out


# ── message builders (pure — unit tested) ──────────────────────────────────

def kickoff_message(p: dict, minutes: int) -> dict:
    home, away = p.get("home_team", ""), p.get("away_team", "")
    return {
        "title": f"Kickoff in {minutes} min",
        "body": f"{home} vs {away} — Pico picked {p.get('prediction', '?')}"
                + (f" ({p.get('confidence')}% conf.)" if p.get("confidence") else ""),
        "tag": f"kickoff:{p.get('match_id')}",
        "url": "/",
    }


def picks_message(picks: list[dict]) -> dict:
    if len(picks) == 1:
        p = picks[0]
        body = (f"{p.get('home_team')} vs {p.get('away_team')} → "
                f"{p.get('prediction')} ({p.get('confidence')}%)")
    else:
        body = (f"{len(picks)} new AI Pick(s): "
                + ", ".join(f"{p.get('home_team')} v {p.get('away_team')}" for p in picks[:3])
                + ("…" if len(picks) > 3 else ""))
    return {
        "title": "New AI Pick" + ("s" if len(picks) != 1 else ""),
        "body": body,
        "tag": "picks",
        "url": "/",
    }


def settled_message(p: dict) -> dict:
    hit = p.get("actual_result") == p.get("prediction")
    return {
        "title": "Pick settled: " + ("✅ correct" if hit else "❌ missed"),
        "body": (f"{p.get('home_team')} {p.get('home_score')}-{p.get('away_score')} "
                 f"{p.get('away_team')} — Pico said {p.get('prediction')}"),
        "tag": f"settled:{p.get('match_id')}",
        "url": "/",
    }


# ── sending ────────────────────────────────────────────────────────────────

def send_payload(subs: list[dict], payload: dict, dry_run: bool) -> tuple[int, list[str]]:
    """Send one payload to every subscription. Returns (ok, dead_endpoints).

    Dead endpoints (404/410 — browser unsubscribed) are collected so the
    caller can purge them; a stale row must never block future sends.
    """
    if dry_run:
        logger.info("  [dry-run] would notify %d device(s): %s",
                    len(subs), json.dumps(payload, ensure_ascii=False)[:160])
        return len(subs), []
    if not VAPID_PRIVATE_KEY:
        logger.error("VAPID_PRIVATE_KEY not set — cannot send. "
                     "Set it as a repository secret (plan 3.2).")
        return 0, []
    from pywebpush import WebPushException, webpush  # noqa: PLC0415

    ok, dead = 0, []
    for s in subs:
        try:
            webpush(
                subscription_info={
                    "endpoint": s["endpoint"],
                    "keys": {"p256dh": s["p256dh"], "auth": s["auth"]},
                },
                data=json.dumps(payload, ensure_ascii=False),
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims=VAPID_CLAIMS,
            )
            ok += 1
        except WebPushException as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status in (404, 410):
                dead.append(s["endpoint"])
            else:
                logger.warning("  push failed (HTTP %s): %s", status, str(e)[:120])
        except Exception as e:  # noqa: BLE001
            logger.warning("  push error: %s", str(e)[:120])
    return ok, dead


def purge_dead(endpoints: list[str]) -> None:
    if not endpoints:
        return
    try:
        _rest("DELETE", "push_subscriptions",
              params={"endpoint": f"in.({','.join(endpoints)})"})
        logger.info("Purged %d dead subscription(s).", len(endpoints))
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not purge dead subscriptions: %s", e)


# ── per-kind event gathering ───────────────────────────────────────────────

def gather_kickoff(minutes_ahead: int) -> list[tuple[str, dict]]:
    """(key, payload) for matches starting within the lead window.

    Both bounds are enforced server-side (PostgREST composite `and`) *and*
    re-checked below — a bare `gte` would sweep in every future fixture.
    """
    now = dt.datetime.now(dt.timezone.utc)
    lo = (now + dt.timedelta(minutes=KICKOFF_MIN_MINUTES)).replace(
        microsecond=0).isoformat().replace("+00:00", "Z")
    hi = (now + dt.timedelta(minutes=minutes_ahead)).replace(
        microsecond=0).isoformat().replace("+00:00", "Z")
    rows = _rest(
        "GET", "predictions",
        params={
            "select": "match_id,home_team,away_team,prediction,confidence,kickoff_time",
            "and": f"(kickoff_time.gte.{lo},kickoff_time.lt.{hi})",
            "status": "eq.upcoming",
            "order": "kickoff_time.asc",
            "limit": "50",
        },
    ) or []
    out = []
    for p in rows:
        ko = p.get("kickoff_time") or ""
        try:
            minutes = int((dt.datetime.fromisoformat(ko.replace("Z", "+00:00")) - now)
                          .total_seconds() // 60)
        except ValueError:
            continue
        if not (KICKOFF_MIN_MINUTES <= minutes < minutes_ahead):
            continue  # defensive: window may have moved while we queried
        out.append((f"kickoff:{p.get('match_id')}", kickoff_message(p, minutes)))
    return out


def is_ai_pick(row: dict) -> bool:
    """AI Pick flag lives in the jsonb `stats` blob (there is no column)."""
    return bool((row.get("stats") or {}).get("is_ai_pick"))


def gather_picks(since: str | None) -> list[tuple[str, dict]]:
    """One grouped payload for AI Picks published after `since`.

    `stats.is_ai_pick` is jsonb, so the flag is filtered here rather than
    in PostgREST (a phantom `is_ai_pick=eq.true` column filter 400s).
    """
    params = {
        "select": "match_id,home_team,away_team,prediction,confidence,stats,kickoff_time,created_at",
        "status": "eq.upcoming",
        "order": "kickoff_time.asc",
        "limit": "100",
    }
    if since:
        params["created_at"] = f"gt.{since}"
    rows = [r for r in (_rest("GET", "predictions", params=params) or [])
            if is_ai_pick(r)][:20]
    if not rows:
        return []
    return [("picks:" + ",".join(r["match_id"] for r in rows), picks_message(rows))]


def gather_settled(since: str | None) -> list[tuple[str, dict]]:
    """One payload per **AI Pick** that settled correctly after `since`.

    Only AI Picks (and only hits) — celebrating every correct tier-3-way
    prediction would be notification spam; misses are never celebrated.
    """
    params = {
        "select": "match_id,home_team,away_team,prediction,actual_result,home_score,away_score,settled_at,stats",
        "status": "eq.completed",
        "order": "settled_at.desc",
        "limit": "100",
    }
    if since:
        params["settled_at"] = f"gt.{since}"
    rows = [r for r in (_rest("GET", "predictions", params=params) or [])
            if is_ai_pick(r)][:10]
    return [(f"settled:{r['match_id']}", settled_message(r))
            for r in rows if r.get("actual_result") == r.get("prediction")]


# ── main ───────────────────────────────────────────────────────────────────

def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--kind", choices=["picks", "kickoff", "settled"], required=True)
    ap.add_argument("--dry-run", action="store_true",
                    help="gather + log payloads, send nothing")
    ap.add_argument("--minutes-ahead", type=int, default=KICKOFF_LEAD_MINUTES + 5,
                    help="kickoff kind: upper window bound in minutes (30)")
    args = ap.parse_args(argv)

    if not SUPABASE_URL or not SUPABASE_KEY:
        logger.error("SUPABASE_URL / SUPABASE_SERVICE_KEY not set — cannot run.")
        return 2

    gather = {"kickoff": lambda: gather_kickoff(args.minutes_ahead),
              "picks": lambda: gather_picks((load_state().get("picks") or {}).get("last_run_at")),
              "settled": lambda: gather_settled((load_state().get("settled") or {}).get("last_run_at"))}[args.kind]

    try:
        events = gather()
    except Exception as e:  # noqa: BLE001
        logger.error("Could not gather %s events: %s", args.kind, e)
        return 1

    state = load_state()
    pending = new_keys(args.kind, [k for k, _ in events], state)
    events = [(k, m) for k, m in events if k in set(pending)]
    if not events:
        logger.info("Nothing new for kind=%s — no notifications sent.", args.kind)
        # still advance last_run_at so the next window is bounded
        save_state(mark_sent(args.kind, [], state))
        return 0
    logger.info("%d new %s notification(s).", len(events), args.kind)

    subs = fetch_subscriptions()
    if not subs and not args.dry_run:
        logger.info("No subscribed devices — marking keys sent anyway.")
        save_state(mark_sent(args.kind, [k for k, _ in events], state))
        return 0

    total_ok, all_dead = 0, []
    for _key, payload in events:
        ok, dead = send_payload(subs, payload, args.dry_run)
        total_ok += ok
        all_dead.extend(dead)

    purge_dead(all_dead)
    if not args.dry_run:
        save_state(mark_sent(args.kind, [k for k, _ in events], state))
    logger.info("Done. delivered=%d%s dead=%d",
                total_ok, " (dry-run)" if args.dry_run else "", len(all_dead))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
