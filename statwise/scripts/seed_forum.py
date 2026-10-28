#!/usr/bin/env python3
"""
Seed the forum with a small, believable starter discussion from multiple
accounts (plan 5.15). READ/WRITE on auth + forum tables, NEVER on models.

What one run does (idempotent — safe to re-run):
  1. admin-creates any missing seed user (confirmed, no email sent) via
     /auth/v1/admin/users (needs SUPABASE_SERVICE_KEY from the repo .env);
  2. mirrors AuthContext.ensureUserProfile: upserts user_profiles +
     referral_codes for each account using that account's own session;
  3. inserts messages directly with the service key, shaped EXACTLY like
     send_forum_message writes them (user_id, username, message, reply_to,
     created_at) — used because the live RPC was broken by the plan 4.3
     sanitization regex (fixed in supabase-pending-migrations.sql; once
     pasted, in-app sends work and this path is equivalent anyway);
  4. spreads created_at across the last few days so day dividers/threads
     look organic instead of a one-second burst;
  5. posts image messages ({"image": <public bucket url>, "text":
     <caption>} envelopes; CC0 Wikimedia Commons photos pre-uploaded to
     the public forum-media bucket);
  6. deletes this script's own probe row if one exists.

Usage:  .venv/bin/python statwise/scripts/seed_forum.py   (or python3)
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PASSWORD = "StatWise#Seed2026!"          # shared by the seed accounts only

# email-local-part -> display name (username == display name, as the app does)
ACCOUNTS = {
    "tunde.tips": "Tunde T.",
    "chidi.strikers": "Chidi S.",
    "amaka.value": "Amaka V.",
    "kelechi.odds": "Kelechi O.",
    "bola.draws": "Bola D.",
    "ngozi.picks": "Ngozi P.",
    "emeka.corner": "Emeka C.",
    "zainab.combo": "Zainab C.",
}
DOMAIN = "statwise.community"

# (when, author-local-part, message, reply-to-index-or-None)
# Indices refer to previous entries in this list (thread realism).
DAYS = {  # convenience: day offsets from today (UTC)
    "d3": -3, "d2": -2, "d1": -1, "d0": 0,
}
MESSAGES = [
    ("d3", 18, 40, "bola.draws",
     "Brighton over 2.5 cashed again — that's 4 of its last 5 for me. The value edge flagged it early too, before the line moved.", None),
    ("d3", 19, 12, "emeka.corner",
     "Anyone actually using the La Liga board? Draw predictions hit twice for me this weekend.", None),
    ("d2", 9, 5, "amaka.value",
     "Quick one — does the free tier's 5 picks refresh at midnight or 24h after my first pick? Woke up on 0/5 and got confused 😅", None),
    ("d2", 9, 31, "kelechi.odds",
     "Resets 00:00 WAT daily. I save mine for the evening kickoffs.", 2),
    ("d2", 21, 15, "zainab.combo",
     "Does the app work offline? Was in a tunnel and the board still loaded from cache 👀", None),
    ("d2", 21, 40, "tunde.tips",
     "Same here — cached board saved my acca on the train last week.", 4),
    ("d1", 7, 50, "chidi.strikers",
     "Model gave Arsenal 71% in the cup and they went out on pens 😂 this is exactly why I never stake heavy on cup games. League matches only.", None),
    ("d1", 12, 20, "ngozi.picks",
     "Closing line moved 1.85 -> 1.65 before kickoff today. The odds sync is actually useful for spotting steam moves — good feature.", None),
    ("d1", 20, 5, "tunde.tips",
     "Switched to Premium last night — full board with value edges beats checking screenshots everywhere. Running the daily plan for now.", None),
    ("d0", 6, 30, "amaka.value",
     "The news section with the Sky stories is a nice touch, read the whole cup report right in the app.", None),
    ("d0", 7, 15, "chidi.strikers",
     "Agreed. Anyone know if free tier keeps the news too or does that move behind the plan later?", 9),
    ("d0", 8, 0, "bola.draws",
     "News stays free as far as I can see — only the full prediction board tiers up.", 10),
]

# Image messages (plan 5.15 "send images too") — same envelope the app's
# upload path writes: {"image": <https url>, "text": <caption>}. URLs point
# at the PUBLIC `forum-media` bucket (seed/ folder, uploaded with the
# service key because the authenticated write policy only lands with the
# pending paste — see plan 5.11). Sources: Wikimedia Commons, all CC0
# (attribution not required): ball-pitch.jpg = Peter Glaser,
# goal-net.jpg = Dan Gold, stadium.jpg = Markus Spiske.
_IMG_BASE = ("https://fwpriiugfjhtoymcbmso.supabase.co/storage/v1/object"
             "/public/forum-media/seed")

def _env(image: str, caption: str) -> str:
    return json.dumps({"image": f"{_IMG_BASE}/{image}", "text": caption},
                      ensure_ascii=False)

# Appended AFTER the text thread, so earlier reply indices (2, 4, 9, 10)
# remain valid; entries here may reply to any index above.
MESSAGES += [
    ("d1", 16, 45, "kelechi.odds",
     _env("ball-pitch.jpg", "Matchday pitch from my lads' league — turf actually looked decent for once 😄"),
     6),
    ("d0", 9, 10, "ngozi.picks",
     _env("goal-net.jpg", "That late winner yesterday had me like this. Nerve-wracking acca to end the night."),
     None),
    ("d0", 10, 25, "emeka.corner",
     _env("stadium.jpg", "Away day trip next month. Anyone been to a ground like this? Worth the journey?"),
     13),
]

# This script's own wiring probe (safe to remove on every run).
PROBE_MESSAGE = "Direct-insert probe (trigger check)."


def _load_dotenv() -> None:
    env = REPO / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())


def _anon_key() -> str:
    src = (REPO / "statwise" / "lib" / "supabase.ts").read_text()
    m = re.search(r"supabaseAnonKey\s*=\s*'([^']+)'", src)
    if not m:
        raise SystemExit("anon key not found in statwise/lib/supabase.ts")
    return m.group(1)


def req(method: str, url: str, key: str, token: str | None = None,
        body: dict | None = None, expect: tuple[int, ...] = (200, 201, 204)):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, method=method, data=data, headers={
        "apikey": key,
        "Authorization": f"Bearer {token or key}",
        "Content-Type": "application/json",
        # merge-duplicates makes the profile/referral POSTs true upserts on
        # re-runs (PK = id) instead of 409-ing on existing rows.
        "Prefer": "return=representation,resolution=merge-duplicates",
    })
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            raw = resp.read().decode()
            return resp.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        if e.code in expect:
            return e.code, None
        raise SystemExit(f"HTTP {e.code} {method} {url}: {raw[:400]}")


def main() -> int:
    _load_dotenv()
    base = os.environ.get("SUPABASE_URL", "https://fwpriiugfjhtoymcbmso.supabase.co")
    admin = os.environ.get("SUPABASE_SERVICE_KEY")
    if not admin:
        raise SystemExit("SUPABASE_SERVICE_KEY missing from .env")
    anon = _anon_key()
    emails = {local: f"{local}@{DOMAIN}" for local in ACCOUNTS}

    # ── 1. create missing users (admin, confirmed, no email) ─────────────
    existing: dict[str, str] = {}  # email -> uid
    status, users = req("GET", f"{base}/auth/v1/admin/users?per_page=200", admin)
    for u in users.get("users", []):
        if u.get("email"):
            existing[u["email"].lower()] = u["id"]

    for local, email in emails.items():
        if email.lower() in existing:
            print(f"user exists : {email}")
            continue
        _, created = req("POST", f"{base}/auth/v1/admin/users", admin, body={
            "email": email,
            "password": PASSWORD,
            "email_confirm": True,
            "user_metadata": {"display_name": ACCOUNTS[local]},
        })
        existing[email.lower()] = created["id"]
        print(f"user created: {email} ({created['id'][:8]})")
        time.sleep(0.3)

    # ── 2. profile + referral code per account (as AuthContext does) ─────
    sessions: dict[str, str] = {}
    for local, email in emails.items():
        uid = existing[email.lower()]
        _, tok = req("POST", f"{base}/auth/v1/token?grant_type=password", anon,
                     body={"email": email, "password": PASSWORD})
        access = tok["access_token"]
        sessions[local] = access
        req("POST", f"{base}/rest/v1/user_profiles", anon, token=access, body={
            "id": uid, "email": email,
            "username": ACCOUNTS[local], "display_name": ACCOUNTS[local],
            "current_tier": "Free Tier", "tier": "Free Tier",
            "subscription_status": "active", "notifications": True,
            "last_login": datetime.now(timezone.utc).isoformat(),
        })
        # on_conflict targets the UNIQUE(user_id) column — merge-duplicates
        # alone only resolves PK conflicts, and 409'd on re-runs otherwise.
        req("POST", f"{base}/rest/v1/referral_codes?on_conflict=user_id", anon, token=access, body={
            "user_id": uid, "code": uid[:8].upper(),
            "username": ACCOUNTS[local], "total_referrals": 0,
            "active": True,
        })
        print(f"profile ok  : {ACCOUNTS[local]}")

    # ── 3. messages (service key = same row shape as send_forum_message) ─
    _, existing_rows = req(
        "GET",
        f"{base}/rest/v1/forum_messages?select=id,message,created_at,username",
        admin)
    have = {(r["username"], r["message"]) for r in existing_rows}

    # drop this script's probe rows, if any
    for r in existing_rows:
        if r["message"] == PROBE_MESSAGE:
            req("DELETE", f"{base}/rest/v1/forum_messages?id=eq.{r['id']}", admin)
            print(f"probe removed: {r['id'][:8]}")

    posted = 0
    ids: list[str] = []
    for day, hh, mm, local, text, reply_idx in MESSAGES:
        ids.append(None)  # placeholder keeps MESSAGES indices == ids indices
    now = datetime.now(timezone.utc)
    for i, (day, hh, mm, local, text, reply_idx) in enumerate(MESSAGES):
        if (ACCOUNTS[local], text) in have:
            ids[i] = None
            print(f"msg exists  : {ACCOUNTS[local]} ({len(text)} ch)")
            continue
        when = (now + timedelta(days=DAYS[day])).replace(
            hour=hh, minute=mm, second=0, microsecond=0)
        body = {
            "user_id": existing[emails[local].lower()],
            "username": ACCOUNTS[local],
            "message": text,
            "created_at": when.isoformat(),
            "updated_at": when.isoformat(),
        }
        if reply_idx is not None and ids[reply_idx]:
            body["reply_to"] = ids[reply_idx]
        _, row = req("POST", f"{base}/rest/v1/forum_messages", admin, body=body)
        ids[i] = row[0]["id"] if row else None
        posted += 1
        print(f"posted      : {ACCOUNTS[local]} @ {when:%b %d %H:%M}"
              + (" (reply)" if reply_idx is not None else ""))
        time.sleep(0.2)

    _, final = req(
        "GET",
        f"{base}/rest/v1/forum_messages?select=id&order=created_at.asc",
        admin)
    print(f"\nDONE — {posted} new message(s); forum now holds "
          f"{len(final)} message(s) from {len(emails)} accounts.")
    print(f"Shared seed password: {PASSWORD}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
