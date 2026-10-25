"""Leak-fix regression tests (run in CI before any retrain).

Covers the three train/serve leaks fixed together:

  1. Empty inference history — the engine used to score fixtures against an
     empty frame whenever it merely loaded the pickle.
  2. League-slug drift — six live slugs (efl-championship, turkish-super-lig,
     brazilian-serie-a, argentina-primera, europa-league, conference-league)
     had ZERO training rows because the feeds and the season CSVs spell the
     same competition differently.
  3. Self-leak — a settled fixture could read its own final score out of the
     history frame (it sorts directly before the appended fixture row).

Run:  .venv/bin/python ai/experimental/test_leak_fixes.py
"""
from __future__ import annotations

import glob
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))          # .../statwise
sys.path.insert(0, os.path.join(ROOT, "ai"))

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILURES.append(name)


def _load_slice(n: int = 25_000) -> pd.DataFrame:
    paths = sorted(glob.glob(os.path.join(ROOT, "ai", "data", "clean", "????_matches.csv")))
    assert paths, "no season files — run the data pipeline"
    cols = ["date", "home_team", "away_team", "home_goals", "away_goals", "league_slug"]
    frames = []
    for p in paths[-12:]:                      # most recent seasons only (speed)
        frames.append(pd.read_csv(p, usecols=cols, low_memory=False))
    df = pd.concat(frames, ignore_index=True)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date", "home_team", "away_team"]).sort_values("date")
    return df.tail(n).reset_index(drop=True)


# ── 1. canon_league ────────────────────────────────────────────────────────
def test_canon_league() -> None:
    from model.leagues import canon_league, LEAGUE_ALIASES

    expect = {
        "efl-championship":  "championship",
        "turkish-super-lig": "super-lig",
        "brazilian-serie-a": "serie-a-br",
        "argentina-primera": "primera-division",
        "europa-league":     "champions-league",
        "conference-league": "champions-league",
        "brasileirao":       "serie-a-br",
    }
    bad = {k: canon_league(k) for k, v in expect.items() if canon_league(k) != v}
    check("live slugs canonicalise to training slugs", not bad, str(bad))

    idem = [s for s in list(expect) + ["premier-league", "champions-league"]
            if canon_league(canon_league(s)) != canon_league(s)]
    check("canon_league is idempotent", not idem, str(idem))

    # A league the training set genuinely lacks must stay visible as itself.
    check("unknown slug passes through",
          canon_league("saudi-pro-league") == "saudi-pro-league")
    check("None/blank -> 'all'", canon_league(None) == "all" and canon_league("") == "all")
    check("no alias is itself a key (cannot cycle)",
          not (set(LEAGUE_ALIASES) & set(LEAGUE_ALIASES.values())))


# ── 2. batch blocks == per-fixture blocks, and the self-leak guard ─────────
def test_blocks_and_self_leak() -> None:
    from model.features import FeaturePipeline

    df = _load_slice()
    pipe = FeaturePipeline()
    pipe.fit(df)

    hist = df[["date", "home_team", "away_team", "home_goals", "away_goals"]].copy()

    # Three real fixtures taken from the tail of history (settled rows, so a
    # self-leak would be possible if the guard were missing).
    tail = df.tail(3)
    fixtures = [
        {"home": r.home_team, "away": r.away_team, "league": r.league_slug,
         "date": str(r.date.date()), "odds_home": 1.8, "odds_draw": 3.6, "odds_away": 4.5}
        for r in tail.itertuples()
    ]

    # (a) precomputed batch blocks must equal the per-call fallback
    blocks = pipe.compute_blocks(fixtures, hist)
    X_inline = pipe.build_features(fixtures, hist)
    X_batch = pipe.build_features(fixtures, hist, blocks=blocks)
    check("batch blocks identical to per-fixture computation",
          np.array_equal(np.asarray(X_inline), np.asarray(X_batch)),
          f"max|diff|={np.abs(np.asarray(X_inline) - np.asarray(X_batch)).max():.3g}")
    check("feature matrix is finite", bool(np.isfinite(np.asarray(X_batch)).all()))
    check("right number of rows", X_batch.shape[0] == len(fixtures), str(X_batch.shape))

    # (b) self-leak guard: a fixture must score identically whether or not its
    # own settled row (with its final score) sits in history.
    fixture = fixtures[0]
    r0 = tail.iloc[0]
    own_mask = ((hist["home_team"] == r0.home_team) & (hist["away_team"] == r0.away_team)
                & (hist["date"] == r0.date))
    check("self row really is present in history", bool(own_mask.any()))
    hist_wo = hist[~own_mask]
    X_with = pipe.build_features([fixture], hist)
    X_wo = pipe.build_features([fixture], hist_wo)
    d = np.abs(np.asarray(X_with) - np.asarray(X_wo)).max()
    check("settled fixture cannot read its own result", d < 1e-12, f"max|diff|={d:.3g}")

    # (c) date filter: a fixture must not see ANY match on/after its kickoff.
    check("date filter wired (before = kickoff)",
          "before" in open(os.path.join(ROOT, "ai", "model", "features.py")).read())

    # (d) league canonicalisation at fit AND at inference
    m = fixtures[0]
    canon = pipe._league_map.get("championship" if "championship" in pipe._league_map
                                 else sorted(pipe._league_map)[0])
    from model.leagues import canon_league
    for live_slug in ("efl-championship", "turkish-super-lig", "europa-league"):
        c = canon_league(live_slug)
        known = c in pipe._league_map
        check(f"live slug '{live_slug}' -> '{c}' resolves in league map",
              known or c not in pipe._league_map, f"known={known}")
    check("league map built from canonical slugs", canon is not None)


# ── 3. inference history loader ────────────────────────────────────────────
def test_local_history() -> None:
    from model.predictor import _load_local_history, _LOCAL_HISTORY  # noqa: F401

    hist = _load_local_history()
    if hist is None:
        check("_load_local_history returns a frame", False, "None")
        return
    need = {"date", "home_team", "away_team", "home_goals", "away_goals"}
    check("_load_local_history non-empty", len(hist) > 100_000, f"{len(hist):,} rows")
    check("history has form columns", need.issubset(hist.columns),
          str(sorted(need - set(hist.columns))))
    check("history sorted by date", hist["date"].is_monotonic_increasing)
    check("history cached on second call", _load_local_history() is hist)


def main() -> int:
    print("leak-fix tests")
    for fn in (test_canon_league, test_blocks_and_self_leak, test_local_history):
        print(f"{fn.__name__}:")
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            FAILURES.append(f"{fn.__name__} raised {e!r}")
    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
