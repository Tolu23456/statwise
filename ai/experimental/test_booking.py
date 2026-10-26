"""Checks for booking-code hardening (IMPROVEMENT_PLAN Step 2.3).

What silently breaks booking:

  1. No cache — every predict cycle re-asks SportyBet for a code even when
     the AI Picks haven't changed, burning API calls and churning codes
     users may have copied. selection_signature/should_regenerate gate it.
  2. No retry — a single 5xx from the share endpoint killed the whole run.
  3. Failures were invisible — no counters anywhere, so nobody notices a
     week of failed generation. bump_stats writes rolling rates.
  4. Opaque errors — internal exceptions surfaced verbatim. friendly_error
     maps failure kinds to actionable sentences the app shows to users.

Everything here is tiny synthetic payloads — no network, no Supabase.

Run:  .venv/bin/python ai/experimental/test_booking.py
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

# sportybet_booking loads .env + env_loader on import; dummies keep the pure
# helpers importable identically on dev machines and CI.
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-key")

AI_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, AI_DIR)

import sportybet_booking as sb  # noqa: E402

ok = True


def check(name, cond, extra=""):
    global ok
    print(f"{'PASS' if cond else 'FAIL'}  {name} {extra}")
    ok = ok and bool(cond)


NOW = datetime(2026, 10, 26, 18, 0, 0, tzinfo=timezone.utc)

# ── 1. selection signature: stable, order-independent, pick-sensitive ──────
sel_a = [{"eventId": "111", "outcomeId": "1"}, {"eventId": "222", "outcomeId": "3"}]
sel_b = [{"eventId": "222", "outcomeId": "3"}, {"eventId": "111", "outcomeId": "1"}]
check("signature is order-independent",
      sb.selection_signature(sel_a) == sb.selection_signature(sel_b))
check("signature is deterministic",
      sb.selection_signature(sel_a) == sb.selection_signature(list(sel_a)))
sel_c = [{"eventId": "111", "outcomeId": "2"}, {"eventId": "222", "outcomeId": "3"}]
check("different outcome -> different signature",
      sb.selection_signature(sel_a) != sb.selection_signature(sel_c))
sel_d = [{"eventId": "111", "outcomeId": "1"}]
check("subset -> different signature",
      sb.selection_signature(sel_a) != sb.selection_signature(sel_d))
check("empty selections have a signature (never equal to a real one)",
      sb.selection_signature([]) == sb.selection_signature([]) and
      sb.selection_signature([]) != sb.selection_signature(sel_d))
check("None treated as empty", sb.selection_signature(None) == sb.selection_signature([]))
check("signature is a short hex string",
      len(sb.selection_signature(sel_a)) == 16, sb.selection_signature(sel_a))

SIG = sb.selection_signature(sel_a)
FUTURE = (NOW + timedelta(days=2)).isoformat()
PAST = (NOW - timedelta(hours=1)).isoformat()


def payload(**kw):
    base = {"code": "ABC123", "signature": SIG, "expires_at": FUTURE,
            "selections": sel_a}
    base.update(kw)
    return base


# ── 2. should_regenerate: reuse only identical + not expiring soon ────────
check("no stored payload -> regenerate", sb.should_regenerate(None, SIG, NOW) is True)
check("payload without code -> regenerate",
      sb.should_regenerate({"signature": SIG, "expires_at": FUTURE}, SIG, NOW) is True)
check("same picks + fresh deadline -> reuse",
      sb.should_regenerate(payload(), SIG, NOW) is False)
check("different picks -> regenerate",
      sb.should_regenerate(payload(), sb.selection_signature(sel_c), NOW) is True)
check("expired code -> regenerate",
      sb.should_regenerate(payload(expires_at=PAST), SIG, NOW) is True)
soon = (NOW + timedelta(hours=5)).isoformat()
check("expiring within margin (5h < 6h) -> regenerate",
      sb.should_regenerate(payload(expires_at=soon), SIG, NOW) is True)
edge = (NOW + timedelta(hours=7)).isoformat()
check("beyond margin (7h > 6h) -> reuse",
      sb.should_regenerate(payload(expires_at=edge), SIG, NOW) is False)
check("missing deadline -> regenerate",
      sb.should_regenerate(payload(expires_at=None), SIG, NOW) is True)
check("garbage deadline -> regenerate",
      sb.should_regenerate(payload(expires_at="soon"), SIG, NOW) is True)

# ── 3. friendly errors: actionable sentences, details capped ──────────────
KNOWN = ["no_picks", "playwright", "blocked", "no_events", "no_match",
         "share_rejected", "no_code", "publish", "unknown"]
for kind in KNOWN:
    msg = sb.friendly_error(kind)
    check(f"friendly_error({kind}) is a sentence", len(msg) > 20 and msg[0].isupper(), msg[:40])
check("unknown kind falls back to the generic message",
      "copyable selections" in sb.friendly_error("brand_new_kind"))
check("detail appended in parentheses",
      "(HTTP 403)" in sb.friendly_error("blocked", "HTTP 403"))
long = sb.friendly_error("blocked", "x" * 500)
capped = sb.friendly_error("blocked", "x" * 120)
check("detail capped at 120 chars (idempotent at the cap)", long == capped,
      f"len={len(long)}")

# ── 4. bump_stats: rolling counters the app can display ───────────────────
s0 = {}
s1 = sb.bump_stats(s0, True, code="AAA", now=NOW)
check("first success counts", s1["attempts"] == 1 and s1["successes"] == 1)
check("success stamps last_success_at", s1["last_success_at"] == NOW.isoformat())
check("success records last_code", s1["last_code"] == "AAA")
s2 = sb.bump_stats(s1, False, "blocked", now=NOW + timedelta(minutes=5))
check("failure counts separately",
      s2["attempts"] == 2 and s2["failures"] == 1 and s2["successes"] == 1)
check("failure stores friendly error", sb.friendly_error("blocked") in s2["last_error"])
check("failure stamps last_error_at",
      s2["last_error_at"] == (NOW + timedelta(minutes=5)).isoformat())
check("stale error cleared on next success", "last_error" in s2)
s3 = sb.bump_stats(s2, True, code="BBB", now=NOW + timedelta(minutes=10))
check("error cleared after a success",
      "last_error" not in s3 and "last_error_at" not in s3)
check("attempts keep rolling", s3["attempts"] == 3 and s3["successes"] == 2)
check("bump_stats returns a copy, not the input", s2.get("attempts") == 2)
check("None stats -> fresh dict",
      sb.bump_stats(None, False, "no_picks", now=NOW)["failures"] == 1)

# ── 5. retry policy shape: create_share_code retries 5xx, not 4xx ────────
# A fake ctx: first two POSTs raise (network), third returns a good body.
class FakeResp:
    def __init__(self, status, body=None, text=""):
        self.status = status
        self._body = body or {}
        self._text = text

    def json(self):
        return self._body

    def text(self):
        return self._text


class FakeCtx:
    def __init__(self, responses):
        self.responses = list(responses)
        self.posts = 0

    @property
    def request(self):
        return self

    def post(self, *a, **kw):
        self.posts += 1
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


import time as _time  # noqa: E402

_orig_sleep = _time.sleep
_time.sleep = lambda *_a, **_k: None  # keep the test instant


try:
    ctx = FakeCtx([ConnectionError("blip"), ConnectionError("blip"),
                   FakeResp(200, {"bizCode": 10000, "data": {"shareCode": "XYZ"}})])
    data = sb.create_share_code(ctx, sel_a)
    check("transient network errors retried, third attempt wins",
          data.get("shareCode") == "XYZ" and ctx.posts == 3, f"posts={ctx.posts}")

    ctx = FakeCtx([FakeResp(502), FakeResp(502), FakeResp(502), FakeResp(200, {})])
    try:
        sb.create_share_code(ctx, sel_a)
        check("5xx retried then exhausted raises", False)
    except RuntimeError as e:
        check("5xx retried then exhausted raises", "3 attempts" in str(e), str(e)[:60])

    ctx = FakeCtx([FakeResp(403, text="Forbidden")])
    try:
        sb.create_share_code(ctx, sel_a)
        check("4xx NOT retried (fails fast)", False)
    except RuntimeError as e:
        check("4xx NOT retried (fails fast)",
              "HTTP 403" in str(e) and ctx.posts == 1, f"posts={ctx.posts}")

    ctx = FakeCtx([FakeResp(200, {"bizCode": 10001, "message": "invalid selections"})])
    try:
        sb.create_share_code(ctx, sel_a)
        check("bizCode rejection raises immediately (no retry)", False)
    except RuntimeError as e:
        check("bizCode rejection raises immediately (no retry)",
              "rejected" in str(e) and ctx.posts == 1, f"posts={ctx.posts}")
finally:
    _time.sleep = _orig_sleep

print()
print("ALL PASS" if ok else "FAILURES PRESENT")
sys.exit(0 if ok else 1)
