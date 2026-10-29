#!/usr/bin/env python3
"""
Push notification sender (native Expo push + social layer).

Sends push notifications for six events:

  --kind picks      new AI Picks since the last picks run (TIER-GATED: a
                    pick only reaches users whose tier rank >= the pick's
                    tier, so hidden picks never leak)
  --kind kickoff    matches starting in the next ~30 min (tier-gated the
                    same way — the message names the pick)
  --kind settled    AI Picks that settled correctly since the last run
                    (tier-gated like picks)
  --kind bookmarked matches a user BOOKMARKED starting in [5, 30] min —
                    targeted to exactly those users (user_favorites)
  --kind odds       odds moves (>= 5% relative) on upcoming AI Picks —
                    premium+ users, at most once per match per day
  --kind inbox      the notifications table (reply/mention alerts written
                    by the DB trigger) — pushed to the addressee and
                    stamped push_sent_at, rows age out after 24h

How it works
------------
ai/notify.py reads `push_tokens` (one row per Android install, written by
the app's lib/push.ts after expo-notifications registers a token) and
`user_profiles` (the per-user toggle plus current_tier for audience
gating), builds a payload, and sends it to Expo's push API
(https://exp.host/--/api/v2/push/send).  Tokens Expo reports as
DeviceNotRegistered are purged from `push_tokens`.

Web push was REMOVED with the PWA → Android conversion: `public/sw.js`,
the VAPID key, and the `push_subscriptions` sender are all gone (nothing
can register a browser endpoint anymore, so keeping the sender only added
a secret to leak).  The `push_tokens` table is the single delivery target.

Dedupe/state lives in app_settings.notify_state so runs never double-send:
each event carries a key (e.g. "picks:2026-10-26T12:00Z:espn_123") and a
sent set is kept per kind. `inbox` is different: the notifications table
itself is the dedupe (push_sent_at stamp), so state is not consulted.

Audiences: every event carries an optional audience —
  None            all subscribed devices
  {"user_ids":…}  exactly those users (bookmarked, inbox)
  {"min_rank": n} users with tier rank >= n (picks/kickoff/settled/odds)
Tiers rank free=0 < premium=1 < vip=2 < vvip=3.

Missing tables (migrations not run yet — push_tokens, user_favorites,
notifications)?  Exits 0 with a clear note — notifications are optional,
nothing else may break.

Usage:
    python ai/notify.py --kind kickoff
    python ai/notify.py --kind picks --dry-run
    python ai/notify.py --kind bookmarked --dry-run
    python ai/notify.py --kind odds
    python ai/notify.py --kind inbox --limit 50
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
#: Optional Expo access token (EAS Push). Anonymous push works without it at
#: a lower rate limit; set EXPO_ACCESS_TOKEN in the repo secrets to raise it.
EXPO_ACCESS_TOKEN = (os.environ.get("EXPO_ACCESS_TOKEN") or "").strip()
#: Absolute site origin — native push `data.url` must be a full URL, so a
#: relative payload path is absolutised against this before it reaches Expo.
SITE_BASE_URL = (os.environ.get("SITE_BASE_URL")
                 or os.environ.get("NEXT_PUBLIC_SITE_URL")
                 or "https://statwise-flax.vercel.app").strip()

#: How far ahead of kickoff the "match starting soon" alert fires.
KICKOFF_LEAD_MINUTES = 25
KICKOFF_MIN_MINUTES = 5   # don't alert for matches already under way

#: Keep at most this many sent-keys per kind in app_settings state.
MAX_SENT_KEYS = 500

#: Tier ladder for audience gating — a pick/kickoff/settled payload only
#: goes to users whose rank >= the pick's rank (predictions.tier is the
#: lowercase slug 'free'/'premium'/'vip'/'vvip'; profiles carry the
#: display form 'Free Tier'/'VVIP Tier'; both normalize here).
TIER_RANK = {"free": 0, "premium": 1, "vip": 2, "vvip": 3}
RANK_MIN = 0
RANK_PREMIUM = 1   # odds alerts are premium+ per the user's request

#: Odds alerts: relative move on the pick's decimal odds that counts as
#: worth saying (5%), and how long one match stays quiet afterwards —
#: the event key carries the UTC date, so <= 1 odds alert / match / day.
ODDS_MIN_MOVE = 0.05

#: inbox rows older than this are never pushed (stale replies are noise).
INBOX_MAX_AGE_HOURS = 24

#: Default rows pulled for kinds that take --limit (inbox).
DEFAULT_LIMIT = 100


def tier_rank(value: str | None) -> int:
    """Normalize a tier ('vvip', 'VVIP Tier', 'vvip tier', None) to a rank.

    Unknown/missing tiers rank as free — a gating bug must fail CLOSED
    (treat as least privileged), never open.
    """
    slug = (value or "").strip().lower().replace(" tier", "").strip()
    return TIER_RANK.get(slug, RANK_MIN)


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


#: How many device rows to pull per page — an unbounded read of the token
#: table grows forever and used to OOM the job once enough users subscribed.
SUBS_PAGE = 1000


# ── native (Expo) push ─────────────────────────────────────────────────────
# The Android app registers an Expo push token per install; ai/notify.py
# forwards every payload to Expo's HTTP push API.  audience_subs() gates the
# audience on `user_id`/`tier_rank`, the same filter the old web push used.
EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"
EXPO_TOKENS_TABLE = "push_tokens"
#: Expo accepts at most 100 messages per push request.
EXPO_BATCH = 100


def fetch_native_tokens() -> list[dict]:
    """Expo push tokens (one row per Android install) as device dicts.

    Missing table (native push migration not applied yet) → quiet [].
    """
    tokens: list[dict] = []
    page = 0
    while page < 500:
        try:
            chunk = (_rest("GET", EXPO_TOKENS_TABLE,
                           params={"select": "user_id,expo_token,platform",
                                   "order": "user_id.asc",
                                   "limit": str(SUBS_PAGE),
                                   "offset": str(page * SUBS_PAGE)}) or [])
        except FileNotFoundError:
            logger.info("%s table not found — run "
                        "statwise/supabase-native-push-migration.sql. "
                        "No native pushes (this is fine).", EXPO_TOKENS_TABLE)
            return []
        except Exception as e:  # noqa: BLE001
            logger.warning("Could not read %s: %s", EXPO_TOKENS_TABLE, e)
            return []
        tokens.extend(chunk)
        if len(chunk) < SUBS_PAGE:
            break
        page += 1
    devices = [{"user_id": t.get("user_id"), "token": t.get("expo_token"),
                "platform": "expo"} for t in tokens if t.get("expo_token")]
    logger.info("%d native (Expo) push token(s).", len(devices))
    return _with_profile_ranks(devices)


def _with_profile_ranks(devices: list[dict]) -> list[dict]:
    """Attach notification pref + tier rank to native token rows."""
    if not devices:
        return []
    try:
        profiles = _rest(
            "GET", "user_profiles",
            params={"select": "id,notifications,display_name,username,current_tier",
                    "id": f"in.({','.join(sorted({d['user_id'] for d in devices}))})"},
        ) or []
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not read user_profiles: %s", e)
        return devices
    by_id = {p["id"]: p for p in profiles}
    out = []
    for d in devices:
        prof = by_id.get(d.get("user_id")) or {}
        if prof.get("notifications") is False:
            continue
        out.append({
            **d,
            "name": prof.get("display_name") or prof.get("username") or "",
            "tier_rank": tier_rank(prof.get("current_tier")),
        })
    return out


# ── audiences (pure — unit tested) ──────────────────────────────────────────
#: Audience shapes an event can carry:
#:   None                       → every subscribed device (broadcast)
#:   {"user_ids": {...}}        → exactly those users (bookmarked, inbox)
#:   {"min_rank": n}            → users with tier rank >= n (picks-family, odds)
#:   {"user_ids", "min_rank"}   → intersection (odds: premium AND bookmarked)
#:   {"exclude_user_ids": {...}}→ minus these users (kickoff skips people who
#:                                get the personal bookmarked alert instead)
Audience = dict | None


def audience_subs(subs: list[dict], audience: Audience) -> list[dict]:
    """Filter subscriptions to an event's audience (pure — unit tested).

    Ranking fails CLOSED: a subscription whose profile was missing (no
    tier_rank) ranks RANK_MIN, so a VIP-only pick can never leak to an
    unknown tier. Empty result → the caller sends nothing for this event.
    """
    if not audience:
        return list(subs)
    out = []
    for s in subs:
        if "user_ids" in audience and s.get("user_id") not in audience["user_ids"]:
            continue
        if "exclude_user_ids" in audience and s.get("user_id") in audience["exclude_user_ids"]:
            continue
        if "min_rank" in audience and s.get("tier_rank", RANK_MIN) < audience["min_rank"]:
            continue
        out.append(s)
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


def bookmarked_message(p: dict, minutes: int) -> dict:
    """The personal "your bookmarked match is about to start" alert.

    Deliberately does NOT name the pick: this event is not tier-gated
    (the match may have been bookmarked at a higher tier), so echoing the
    pick here could leak a tier the user no longer pays for.
    """
    return {
        "title": f"Your match starts in {minutes} min",
        "body": f"{p.get('home_team', '?')} vs {p.get('away_team', '?')} kicks off soon",
        "tag": f"bookmarked:{p.get('match_id')}",
        "url": "/",
    }


def odds_message(p: dict, old: float, new: float) -> dict:
    move = abs(new - old) / old * 100 if old > 0 else 0.0
    direction = "📈" if new > old else "📉"
    return {
        "title": "Odds moved on your match",
        "body": (f"{p.get('home_team', '?')} vs {p.get('away_team', '?')} — "
                 f"odds {old:.2f} → {new:.2f} ({direction} {move:.0f}%)"),
        "tag": f"odds:{p.get('match_id')}",
        "url": "/",
    }


def inbox_message(r: dict) -> dict:
    """Push for one notifications row the DB trigger already wrote."""
    return {
        "title": r.get("title") or "StatWise",
        "body": (r.get("body") or "")[:200],
        "tag": f"inbox:{r.get('kind')}:{r.get('id')}",
        "url": r.get("url") or "/",
    }


# ── sending ────────────────────────────────────────────────────────────────

def send_payload(subs: list[dict], payload: dict, dry_run: bool) -> tuple[int, list[str]]:
    """Send one payload to every registered device (Expo native push).

    Returns ``(ok, dead_tokens)``.  Dead tokens (an Expo
    DeviceNotRegistered / MessageTooBig ticket) are collected so the caller
    can purge them from `push_tokens`; a stale row must never block future
    sends to the user's other devices.
    """
    if dry_run:
        logger.info("  [dry-run] would notify %d device(s): %s",
                    len(subs), json.dumps(payload, ensure_ascii=False)[:160])
        return len(subs), []

    return _send_expo(subs, payload)


#: Expo per-ticket error codes that mean "this token is gone for good".
EXPO_DEAD_ERRORS = {"DeviceNotRegistered", "InvalidCredentials",
                    "MessageTooBig", "MessageRateExceeded"}


def _expo_message(device: dict, payload: dict) -> dict:
    """One Expo push ticket.  `url` is a path on the site, so it is turned
    into an absolute https URL — Expo will not open a bare `/`."""
    url = (payload.get("url") or "/").strip() or "/"
    if not url.startswith(("http://", "https://")):
        url = f"{SITE_BASE_URL.rstrip('/')}{url}"
    return {
        "to": device["token"],
        "sound": "default",
        "title": payload.get("title") or "StatWise",
        "body": (payload.get("body") or "")[:400],
        "data": {"url": url, "tag": payload.get("tag")},
        # collapse by tag so a repeat alert replaces the previous one
        "collapseId": (payload.get("tag") or "statwise")[:64],
        "channelId": "default",
    }


def _send_expo(subs: list[dict], payload: dict) -> tuple[int, list[str]]:
    """Expo push half: batched HTTP tickets, dead tokens reported for purge."""
    if not subs:
        return 0, []
    import requests  # noqa: PLC0415

    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if EXPO_ACCESS_TOKEN:
        headers["Authorization"] = f"Bearer {EXPO_ACCESS_TOKEN}"
    ok, dead = 0, []
    for start in range(0, len(subs), EXPO_BATCH):
        batch = subs[start:start + EXPO_BATCH]
        messages = [_expo_message(d, payload) for d in batch]
        try:
            r = requests.post(EXPO_PUSH_URL, json=messages, headers=headers,
                              timeout=20)
        except Exception as e:  # noqa: BLE001
            logger.warning("  expo push error: %s", str(e)[:120])
            continue
        if r.status_code != 200:
            logger.warning("  expo push HTTP %s: %s", r.status_code,
                           (r.text or "")[:120])
            continue
        try:
            data = r.json()
        except Exception:  # noqa: BLE001
            logger.warning("  expo push returned non-JSON body")
            continue
        tickets = data.get("data") if isinstance(data, dict) else None
        if not isinstance(tickets, list):
            continue
        for dev, t in zip(batch, tickets):
            status = (t or {}).get("status")
            if status == "ok":
                ok += 1
                continue
            detail = ((t or {}).get("details") or {}).get("error")
            if detail in EXPO_DEAD_ERRORS:
                dead.append(dev.get("token"))
            else:
                logger.warning("  expo ticket rejected (%s): %s", status, detail)
    return ok, dead


def purge_dead_tokens(tokens: list[str]) -> None:
    """Drop Expo tokens Expo reported as permanently undeliverable."""
    if not tokens:
        return
    # Chunked: a long `in.(...)` list blows past PostgREST's URL limit.
    for start in range(0, len(tokens), 100):
        chunk = tokens[start:start + 100]
        try:
            _rest("DELETE", EXPO_TOKENS_TABLE,
                  params={"expo_token": f"in.({','.join(chunk)})"})
        except Exception as e:  # noqa: BLE001
            logger.warning("Could not purge dead Expo token: %s", e)
    logger.info("Purged %d dead Expo token(s).", len(tokens))


def stamp_inbox_pushed(row_ids: list[str]) -> None:
    """Mark notifications rows as pushed (the kind=inbox dedupe).

    Only rows that actually reached >= 1 device are stamped — an
    unsubscribed or transiently failing row stays eligible for the next
    run until it ages out at INBOX_MAX_AGE_HOURS.
    """
    if not row_ids:
        return
    now = dt.datetime.now(dt.timezone.utc).replace(
        microsecond=0).isoformat().replace("+00:00", "Z")
    try:
        _rest("PATCH", "notifications?push_sent_at=is.null",
              {"push_sent_at": now},
              params={"id": f"in.({','.join(row_ids)})"})
        logger.info("Stamped %d inbox notification(s) as pushed.", len(row_ids))
    except FileNotFoundError:
        logger.info("notifications table not found — nothing to stamp (fine).")
    except Exception as e:  # noqa: BLE001
        # Next run re-reads these rows (unstamped) — worst case a duplicate
        # push, never a lost one.
        logger.warning("Could not stamp inbox rows: %s", e)


def apply_odds_proposal(state: dict, proposal: OddsProposal, sent_keys: list[str],
                        conditional_only: bool) -> None:
    """Write proposed odds baselines into `state` (in place).

    conditional_only=True  (nothing new path): only first-sight entries —
        an alert that dedupe swallowed must NOT move its baseline, or
        tomorrow's diff would be measured from the blocked value and the
        move would vanish.
    conditional_only=False (after send): first-sight entries AND the
        ratchets whose alert key was actually among sent_keys.
    """
    values, conditional = proposal
    if not values:
        return
    snapshot = dict(state.get("odds_snapshot") or {})
    if conditional_only:
        movable = set()
    else:
        movable = {k.split(":")[1] for k in sent_keys
                   if k.startswith("odds:") and k.count(":") >= 2}
    for mid, val in values.items():
        if mid in conditional and mid not in movable:
            continue
        snapshot[mid] = val
    state["odds_snapshot"] = snapshot


# ── per-kind event gathering ───────────────────────────────────────────────

def kickoff_minutes(ko: str, now: dt.datetime) -> int | None:
    """Whole minutes until kickoff, or None when the timestamp won't parse."""
    try:
        return int((dt.datetime.fromisoformat(ko.replace("Z", "+00:00")) - now)
                   .total_seconds() // 60)
    except (ValueError, AttributeError, TypeError):
        return None


def fetch_favoriter_ids(pred_ids: list[str]) -> dict[str, list[str]]:
    """prediction_id → user_ids who bookmarked it (user_favorites).

    Returns {} with a clear note when the table doesn't exist yet (section
    4 of supabase-pending-migrations.sql not run) — bookmarked/odds alerts
    simply don't fire until then; nothing breaks.
    """
    ids = [i for i in pred_ids if i]
    if not ids:
        return {}
    try:
        rows = _rest("GET", "user_favorites",
                     params={"select": "user_id,prediction_id",
                             "prediction_id": f"in.({','.join(ids)})"}) or []
    except FileNotFoundError:
        logger.info("user_favorites table not found — run "
                    "statwise/supabase-pending-migrations.sql (section 4). "
                    "bookmarked/odds alerts stay quiet until then (fine).")
        return {}
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not read user_favorites: %s", e)
        return {}
    out: dict[str, list[str]] = {}
    for r in rows:
        out.setdefault(r.get("prediction_id") or "", []).append(r["user_id"])
    return out


def gather_kickoff(minutes_ahead: int) -> list[tuple[str, dict, Audience]]:
    """(key, payload, audience) for matches starting within the lead window.

    Both bounds are enforced server-side (PostgREST composite `and`) *and*
    re-checked below — a bare `gte` would sweep in every future fixture.

    Two audiences in one query:
      - everyone entitled to the pick's tier (the message names the pick);
      - MINUS users who bookmarked the match — they receive the personal
        "your match starts soon" alert from --kind bookmarked instead, so
        nobody gets two notifications about the same fixture. With the
        user_favorites table not yet migrated, the exclusion set is empty
        and behaviour is exactly the old broadcast.
    """
    now = dt.datetime.now(dt.timezone.utc)
    lo = (now + dt.timedelta(minutes=KICKOFF_MIN_MINUTES)).replace(
        microsecond=0).isoformat().replace("+00:00", "Z")
    hi = (now + dt.timedelta(minutes=minutes_ahead)).replace(
        microsecond=0).isoformat().replace("+00:00", "Z")
    rows = _rest(
        "GET", "predictions",
        params={
            "select": "id,match_id,home_team,away_team,prediction,confidence,kickoff_time,tier",
            "and": f"(kickoff_time.gte.{lo},kickoff_time.lt.{hi})",
            "status": "eq.upcoming",
            "order": "kickoff_time.asc",
            "limit": "50",
        },
    ) or []
    favoriters = fetch_favoriter_ids([r.get("id") or "" for r in rows])
    out = []
    for p in rows:
        minutes = kickoff_minutes(p.get("kickoff_time") or "", now)
        if minutes is None:
            continue
        if not (KICKOFF_MIN_MINUTES <= minutes < minutes_ahead):
            continue  # defensive: window may have moved while we queried
        audience: Audience = {"min_rank": tier_rank(p.get("tier"))}
        excluded = set(favoriters.get(p.get("id") or "") or [])
        if excluded:
            audience["exclude_user_ids"] = excluded
        out.append((f"kickoff:{p.get('match_id')}", kickoff_message(p, minutes),
                    audience))
    return out


def is_ai_pick(row: dict) -> bool:
    """AI Pick flag lives in the jsonb `stats` blob (there is no column)."""
    return bool((row.get("stats") or {}).get("is_ai_pick"))


def gather_picks(since: str | None) -> list[tuple[str, dict, Audience]]:
    """One grouped payload per TIER RANK for AI Picks published after `since`.

    `stats.is_ai_pick` is jsonb, so the flag is filtered here rather than
    in PostgREST (a phantom `is_ai_pick=eq.true` column filter 400s).

    Tier gate: a pick may only reach users with rank >= the pick's tier
    (free pick → everyone; vvip pick → vvip only), so a hidden pick is
    never announced to users who cannot open it. Grouping by rank keeps
    one payload per entitlement level.
    """
    params = {
        "select": "match_id,home_team,away_team,prediction,confidence,stats,kickoff_time,created_at,tier",
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
    by_rank: dict[int, list[dict]] = {}
    for r in rows:
        by_rank.setdefault(tier_rank(r.get("tier")), []).append(r)
    return [
        (f"picks:{rank}:" + ",".join(r["match_id"] for r in group),
         picks_message(group),
         {"min_rank": rank})
        for rank, group in sorted(by_rank.items())
    ]


def gather_settled(since: str | None) -> list[tuple[str, dict, Audience]]:
    """One payload per **AI Pick** that settled correctly after `since`.

    Only AI Picks (and only hits) — celebrating every correct tier-3-way
    prediction would be notification spam; misses are never celebrated.
    Tier-gated by the pick's tier (the message names the pick).
    """
    params = {
        "select": "match_id,home_team,away_team,prediction,actual_result,home_score,away_score,settled_at,stats,tier",
        "status": "eq.completed",
        "order": "settled_at.desc",
        "limit": "100",
    }
    if since:
        params["settled_at"] = f"gt.{since}"
    rows = [r for r in (_rest("GET", "predictions", params=params) or [])
            if is_ai_pick(r)][:10]
    return [(f"settled:{r['match_id']}", settled_message(r),
             {"min_rank": tier_rank(r.get("tier"))})
            for r in rows if r.get("actual_result") == r.get("prediction")]


def gather_bookmarked(minutes_ahead: int) -> list[tuple[str, dict, Audience]]:
    """Matches in the kickoff window that SOMEONE bookmarked — targeted to
    exactly those users (user request 2026-10-27).

    kickoff kind excludes these users so a bookmarker gets ONE alert (the
    personal one), not two for the same match. Missing user_favorites
    table → fetch_favoriter_ids returns {} → kickoff excludes nobody and
    this returns [] : pre-migration behaviour, unchanged.
    """
    now = dt.datetime.now(dt.timezone.utc)
    lo = (now + dt.timedelta(minutes=KICKOFF_MIN_MINUTES)).replace(
        microsecond=0).isoformat().replace("+00:00", "Z")
    hi = (now + dt.timedelta(minutes=minutes_ahead)).replace(
        microsecond=0).isoformat().replace("+00:00", "Z")
    rows = _rest(
        "GET", "predictions",
        params={
            "select": "id,match_id,home_team,away_team,prediction,confidence,kickoff_time,tier",
            "and": f"(kickoff_time.gte.{lo},kickoff_time.lt.{hi})",
            "status": "eq.upcoming",
            "order": "kickoff_time.asc",
            "limit": "50",
        },
    ) or []
    if not rows:
        return []
    favoriters = fetch_favoriter_ids([r["id"] for r in rows])
    out = []
    for p in rows:
        minutes = kickoff_minutes(p.get("kickoff_time") or "", now)
        if minutes is None:
            continue
        if not (KICKOFF_MIN_MINUTES <= minutes < minutes_ahead):
            continue
        users = favoriters.get(p["id"]) or []
        if not users:
            continue
        out.append((f"bookmarked:{p.get('match_id')}",
                    bookmarked_message(p, minutes),
                    {"user_ids": set(users)}))
    return out


#: Baseline updates that may be written even when no alert fired
#: (first sight of a match) vs updates that ride on a fired alert.
OddsProposal = tuple[dict, set]


def gather_odds(state: dict, limit: int = 200) -> tuple[list[tuple[str, dict, Audience]], OddsProposal]:
    """AI Pick odds that MOVED enough to be worth saying, premium+ only.

    Compares predictions.odds (decimal) against the last-alerted baseline
    kept in app_settings state["odds_snapshot"]:

      - first sight of a match  → set baseline, no alert;
      - |new - old| / old >= 5% → alert, key "odds:{match_id}:{UTC date}"
        (≤ 1 alert / match / day via the dedupe set) and ratchet baseline;
      - smaller move            → keep the OLD baseline so drift
        accumulates until it clears the threshold.

    Audience = users who bookmarked that match AND rank >= premium
    ({"user_ids", "min_rank"} intersect in audience_subs).

    Returns (events, (proposal, conditional_ids)): the proposal maps
    match_id → baseline to write; entries in conditional_ids only count
    if their alert key survived dedupe (main applies them after filtering
    — otherwise a same-day-blocked alert would silently move the baseline
    and swallow tomorrow's).
    """
    now_iso = dt.datetime.now(dt.timezone.utc).replace(
        microsecond=0).isoformat().replace("+00:00", "Z")
    hi = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=7)).replace(
        microsecond=0).isoformat().replace("+00:00", "Z")
    rows = _rest(
        "GET", "predictions",
        params={
            "select": "id,match_id,home_team,away_team,odds,tier,kickoff_time",
            "and": f"(kickoff_time.gte.{now_iso},kickoff_time.lt.{hi})",
            "status": "eq.upcoming",
            "odds": "not.is.null",
            "order": "kickoff_time.asc",
            "limit": str(limit),
        },
    ) or []
    if not rows:
        return [], ({}, set())

    snapshot: dict = dict(state.get("odds_snapshot") or {})
    favoriters = fetch_favoriter_ids([r["id"] for r in rows])
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()

    events: list[tuple[str, dict, Audience]] = []
    proposal: dict = {}
    conditional: set = set()
    for r in rows:
        mid = r.get("match_id")
        try:
            new = float(r.get("odds"))
        except (TypeError, ValueError):
            continue
        if not mid:
            continue
        old = snapshot.get(mid)
        if old is None:
            proposal[mid] = new          # first sight: silent baseline
            continue
        try:
            old = float(old)
        except (TypeError, ValueError):
            proposal[mid] = new
            continue
        if old <= 0 or abs(new - old) / old < ODDS_MIN_MOVE:
            continue                     # below threshold: baseline unchanged
        users = set(favoriters.get(r["id"]) or [])
        if not users:
            continue                     # nobody bookmarked → nobody to tell
        events.append((f"odds:{mid}:{today}",
                       odds_message(r, old, new),
                       {"user_ids": users, "min_rank": RANK_PREMIUM}))
        proposal[mid] = new              # ratchet — applied only if event sent
        conditional.add(mid)
    return events, (proposal, conditional)


def gather_inbox(limit: int) -> list[tuple[str, dict, Audience]]:
    """Notification rows the DB trigger wrote (replies/mentions) that no
    device has pushed yet, younger than 24h — one event per row, targeted
    at its addressee. The table IS the dedupe (push_sent_at stamp); the
    notify_state sent-set is NOT consulted for this kind.

    Missing notifications table (social migration not run) → exits quiet.
    """
    cutoff = (dt.datetime.now(dt.timezone.utc)
              - dt.timedelta(hours=INBOX_MAX_AGE_HOURS)).replace(
        microsecond=0).isoformat().replace("+00:00", "Z")
    try:
        rows = _rest(
            "GET", "notifications",
            params={
                "select": "id,user_id,kind,title,body,url,created_at",
                "push_sent_at": "is.null",
                "created_at": f"gt.{cutoff}",
                "order": "created_at.asc",
                "limit": str(limit),
            },
        ) or []
    except FileNotFoundError:
        logger.info("notifications table not found — run "
                    "statwise/supabase-pending-migrations.sql (section 4). "
                    "No inbox pushes (this is fine).")
        return []
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not read notifications: %s", e)
        return []
    return [
        (f"inbox:{r['id']}",
         inbox_message(r),
         {"user_ids": {r["user_id"]}})
        for r in rows if r.get("user_id")
    ]


# ── main ───────────────────────────────────────────────────────────────────

def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--kind", choices=["picks", "kickoff", "settled",
                                        "bookmarked", "odds", "inbox"],
                    required=True)
    ap.add_argument("--dry-run", action="store_true",
                    help="gather + log payloads, send nothing")
    ap.add_argument("--minutes-ahead", type=int, default=KICKOFF_LEAD_MINUTES + 5,
                    help="kickoff/bookmarked: upper window bound in minutes (30)")
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                    help="inbox/odds: max rows gathered (100)")
    args = ap.parse_args(argv)

    if not SUPABASE_URL or not SUPABASE_KEY:
        logger.error("SUPABASE_URL / SUPABASE_SERVICE_KEY not set — cannot run.")
        return 2

    state = load_state()
    gather = {
        "kickoff": lambda: gather_kickoff(args.minutes_ahead),
        "picks": lambda: gather_picks((state.get("picks") or {}).get("last_run_at")),
        "settled": lambda: gather_settled((state.get("settled") or {}).get("last_run_at")),
        "bookmarked": lambda: gather_bookmarked(args.minutes_ahead),
        "odds": lambda: gather_odds(state, args.limit),
        "inbox": lambda: gather_inbox(args.limit),
    }[args.kind]

    try:
        result = gather()
    except Exception as e:  # noqa: BLE001
        logger.error("Could not gather %s events: %s", args.kind, e)
        return 1

    # gather_odds also proposes baseline writes (see its docstring).
    odds_proposal: OddsProposal | None = result[1] if args.kind == "odds" else None
    events: list[tuple[str, dict, Audience]] = result[0] if args.kind == "odds" else result

    # Dedupe: state sent-set for every kind EXCEPT inbox — for inbox the
    # notifications table is the dedupe (push_sent_at stamp after send), so
    # a half-crashed run is retried instead of silently swallowed.
    if args.kind == "inbox":
        pending_set = {k for k, _, _ in events}
    else:
        pending_set = set(new_keys(args.kind, [k for k, _, _ in events], state))
    events = [e for e in events if e[0] in pending_set]

    def finish_early(keys: list[str]) -> int:
        """Common path: persist what must be persisted and stop."""
        if args.kind == "odds" and odds_proposal is not None:
            apply_odds_proposal(state, odds_proposal, keys, conditional_only=True)
        if not args.dry_run and args.kind != "inbox":
            save_state(mark_sent(args.kind, keys, state))
        return 0

    if not events:
        logger.info("Nothing new for kind=%s — no notifications sent.", args.kind)
        # still advance last_run_at so the next window is bounded
        finish_early([])
        return 0
    logger.info("%d new %s notification(s).", len(events), args.kind)

    # Devices: the app's Expo push tokens, each carrying user_id/tier_rank
    # so audience_subs() can gate them on entitlement.
    subs = fetch_native_tokens()
    if not subs and not args.dry_run:
        logger.info("No subscribed devices — marking keys sent anyway.")
        return finish_early([k for k, _, _ in events])

    total_ok = 0
    dead_tokens: list[str] = []
    inbox_stamped: list[str] = []
    for key, payload, audience in events:
        target = audience_subs(subs, audience)
        if not target and not args.dry_run:
            # Nobody entitled/subscribed for THIS event — not a failure of
            # the others. Log and move on: non-inbox keys are still marked
            # sent below (they were processed — retrying would re-skip
            # forever); inbox rows stay unpushed so they retry next run
            # until their 24h age-out.
            logger.info("  %s: no eligible device — skipped.", key)
            continue
        ok, dead = send_payload(target, payload, args.dry_run)
        total_ok += ok
        dead_tokens.extend(dead)
        if args.kind == "inbox" and not args.dry_run and ok > 0:
            inbox_stamped.append(key.split(":", 1)[1])

    purge_dead_tokens(dead_tokens)
    if not args.dry_run:
        if args.kind == "inbox":
            stamp_inbox_pushed(inbox_stamped)
        else:
            # Baseline FIRST — save_state serializes the same dict, so a
            # ratchet applied after the save would be silently dropped.
            if args.kind == "odds" and odds_proposal is not None:
                apply_odds_proposal(state, odds_proposal,
                                    [k for k, _, _ in events], conditional_only=False)
            save_state(mark_sent(args.kind, [k for k, _, _ in events], state))
    logger.info("Done. delivered=%d%s purged_dead_tokens=%d",
                total_ok, " (dry-run)" if args.dry_run else "",
                len(dead_tokens))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
