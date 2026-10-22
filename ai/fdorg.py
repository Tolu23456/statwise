"""
football-data.org v4 client with a rotating token pool.

The free tier allows **10 requests/minute** and **10,000 requests/month per
token**.  The fixture fetcher walks ten leagues back-to-back, so sharing a
single token tripped HTTP 429 and killed entire runs.

This module:

* keeps a pool of tokens (``FOOTBALL_DATA_TOKENS=a,b,c …``, plus the legacy
  single-token ``FOOTBALL_DATA_TOKEN`` / ``FOOTBALL_API_TOKEN`` names);
* spaces requests with a sliding per-minute window so one key never exceeds
  its allowance, sleeping until the earliest key frees up rather than failing;
* rotates to the next key on HTTP 429 (rate limit) and retires a key on
  HTTP 403 (invalid token or monthly quota exhausted);
* reads ``X-RequestLimit-Remaining`` / ``X-Quota-Remaining`` so a key that has
  just hit its ceiling is parked immediately instead of being retried blindly.

Usage::

    import fdorg

    data = fdorg.get("/matches", {"dateFrom": d, "dateTo": d1})
    # raises fdorg.FdorgError on failure
"""
from __future__ import annotations

import collections
import logging
import os
import sys
import threading
import time

import requests

logger = logging.getLogger(__name__)

# Make the module usable standalone (CI step, ad-hoc script) without every
# caller having to remember to load the repo-root .env first.
_AI_DIR = os.path.dirname(os.path.abspath(__file__))
if _AI_DIR not in sys.path:
    sys.path.insert(0, _AI_DIR)
try:                                        # normal: ai/ is on sys.path
    from env_loader import load_dotenv
except ModuleNotFoundError:                 # pragma: no cover - package-style import
    from ai.env_loader import load_dotenv
load_dotenv()

BASE = os.environ.get("FDORG_BASE", "https://api.football-data.org/v4")

#: Requests a single key may make inside one 60s provider window.  Defaults to
#: 8 rather than the hard 10 so a burst from another job cannot push us over.
PER_KEY_PER_MINUTE = int(os.environ.get("FDORG_PER_MINUTE", "8"))

#: Length of our sliding window — a hair over the provider's 60s so a key that
#: was rate-limited is not re-used the instant the provider resets.
_WINDOW = 62.0


class FdorgError(RuntimeError):
    """Raised when football-data.org cannot serve a request."""

    def __init__(self, status: int, detail: str, url: str = ""):
        super().__init__(f"football-data.org HTTP {status}: {detail}")
        self.status = status
        self.detail = detail
        self.url = url


def _read_tokens() -> list[str]:
    """Key pool from the environment, order preserved, duplicates dropped."""
    raw: list[str] = []
    bulk = os.environ.get("FOOTBALL_DATA_TOKENS", "")
    if bulk.strip():
        raw.extend(p.strip() for p in bulk.replace(";", ",").split(",") if p.strip())
    # Legacy single-token names kept working so existing deployments and the
    # GitHub secrets (`FOOTBALL_DATA_TOKEN`, `FOOTBALL_API_TOKEN`) still apply.
    for name in ("FOOTBALL_DATA_TOKEN", "FOOTBALL_API_TOKEN"):
        value = os.environ.get(name, "").strip()
        if value:
            raw.append(value)

    keys, seen = [], set()
    for key in raw:
        if key not in seen:
            seen.add(key)
            keys.append(key)
    return keys


_LOCK = threading.Lock()
_KEYS: list[str] = []
_STATE: dict[str, dict] = {}


def init_keys() -> list[str]:
    """Load the token pool once and return it."""
    with _LOCK:
        if not _KEYS:
            _KEYS.extend(_read_tokens())
            for key in _KEYS:
                _STATE.setdefault(key, {
                    "calls": collections.deque(),   # timestamps, oldest first
                    "cooldown": 0.0,                # parked until (epoch)
                    "dead": None,                   # reason string once retired
                })
        return list(_KEYS)


def available() -> bool:
    """True when at least one token is configured."""
    return bool(init_keys())


def _trim(key: str, now: float) -> None:
    """Drop call timestamps that have aged out of the sliding window."""
    calls = _STATE[key]["calls"]
    while calls and now - calls[0] > _WINDOW:
        calls.popleft()


def _next_free(key: str, now: float) -> float:
    """Earliest epoch at which ``key`` may be used again (inf when retired)."""
    state = _STATE[key]
    if state["dead"]:
        return float("inf")
    _trim(key, now)
    calls = state["calls"]
    if len(calls) >= PER_KEY_PER_MINUTE:
        return max(state["cooldown"], calls[0] + _WINDOW)
    return max(state["cooldown"], now)


def _pick_key(wait: bool = True) -> str:
    """Choose the least-recently-used key that is inside its budget.

    When every token is inside its per-minute window, sleep until the earliest
    one frees up instead of failing the caller.  Recursion depth is bounded:
    after the sleep some key is guaranteed free, or every key is retired and
    we raise.
    """
    init_keys()
    now = time.time()
    live = [k for k in _KEYS if not _STATE[k]["dead"]]
    if not live:
        raise FdorgError(403, "every football-data.org token is retired — "
                              "add more to FOOTBALL_DATA_TOKENS")

    free = [k for k in live if _next_free(k, now) <= now]
    if free:
        # Least recently used first, so the monthly quota is spread evenly.
        return min(free, key=lambda k: _STATE[k]["calls"][-1] if _STATE[k]["calls"] else 0.0)
    if not wait:
        return min(live, key=lambda k: _next_free(k, now))

    horizon = min(_next_free(k, now) for k in live)
    time.sleep(min(max(0.05, horizon - now), _WINDOW + 1.0))
    return _pick_key(wait=True)


def _note_limits(key: str, resp: requests.Response) -> None:
    """Park or retire a key based on the provider's own rate-limit headers."""
    state = _STATE.get(key)
    if state is None:
        return
    try:
        remaining = resp.headers.get("X-RequestLimit-Remaining")
        if remaining is not None and int(remaining) <= 0:
            state["cooldown"] = max(state["cooldown"], time.time() + _WINDOW)
    except (TypeError, ValueError):
        pass
    try:
        quota = resp.headers.get("X-Quota-Remaining")
        if quota is not None and int(quota) <= 0:
            state["dead"] = "monthly quota exhausted"
            logger.warning("football-data.org token %s… retired: monthly quota "
                           "exhausted", key[:8])
    except (TypeError, ValueError):
        pass


def get(path: str, params: dict | None = None, timeout: float = 20.0) -> dict:
    """GET ``path`` against the v4 API and return the decoded JSON body.

    Rotates across the token pool on 429/403.  Raises :class:`FdorgError` when
    no token can serve the request.
    """
    init_keys()
    if not _KEYS:
        raise FdorgError(401, "no football-data.org token configured — set "
                              "FOOTBALL_DATA_TOKENS in .env")

    url = f"{BASE}{path}"
    last: FdorgError | None = None

    for _ in range(len(_KEYS) * 2 + 2):
        key = _pick_key()
        with _LOCK:
            _STATE[key]["calls"].append(time.time())

        try:
            resp = requests.get(url, params=params,
                                headers={"X-Auth-Token": key}, timeout=timeout)
        except requests.RequestException as exc:
            last = FdorgError(0, str(exc), url)
            time.sleep(1.5)
            continue

        _note_limits(key, resp)

        if resp.status_code == 200:
            try:
                return resp.json()
            except ValueError:
                raise FdorgError(200, "response was not valid JSON", url)

        detail = (resp.text or "").strip()[:300]
        last = FdorgError(resp.status_code, detail, url)

        if resp.status_code == 429:
            with _LOCK:
                _STATE[key]["cooldown"] = max(_STATE[key]["cooldown"],
                                              time.time() + _WINDOW)
            logger.warning("football-data.org rate limit on token %s… — rotating",
                           key[:8])
            continue
        if resp.status_code == 403:
            low = detail.lower()
            # 403 has two very different meanings. An exhausted allowance is the
            # key's problem — park it and let another token try. But a restricted
            # RESOURCE (a competition outside the caller's plan, e.g. the Europa
            # League on the free tier) would 403 every key identically, so retire
            # nothing and fail fast instead.
            if "quota" in low or "exceed" in low or "rate limit" in low:
                with _LOCK:
                    _STATE[key]["dead"] = f"HTTP 403: {detail[:120]}"
                logger.warning("football-data.org token %s… retired (HTTP 403): %s",
                               key[:8], detail[:120])
                continue
            logger.debug("football-data.org HTTP 403 for %s — restricted resource, "
                         "not a key problem", url)
            raise last
        if resp.status_code in (500, 502, 503):
            time.sleep(2.0)
            continue
        # 400/404/422 — a different token will not change the answer.
        raise last

    raise last or FdorgError(0, "no usable token", url)


def stats() -> dict:
    """Per-key health, for diagnostics and CI logs."""
    init_keys()
    now = time.time()
    out = {}
    for key in _KEYS:
        _trim(key, now)
        state = _STATE[key]
        out[f"{key[:8]}…"] = {
            "used_last_minute": len(state["calls"]),
            "free_in": round(max(0.0, _next_free(key, now) - now), 1),
            "retired": state["dead"],
        }
    return out
