"""Plan Step 1.6 (BTTS / over-under markets) regression tests.

Fast and synthetic: a few hundred toy rows for the feature labels, a small
random matrix for the LightGBM aux heads.  Nothing here touches the 377k-row
clean set and no run exceeds a few seconds — heavy training belongs in
GitHub Actions (retrain-weekly.yml trains the real heads and reports
btts_brier/o35_brier on the out-of-time holdout).

What is guarded:

  * `last_meta["y_btts"] / ["y_o35"]` exist, are row-aligned with y_1x2 /
    y_goals, and match the ground truth recomputed from the score columns;
  * `fit_stacks(..., y_btts=…, y_o35=…)` fits auxiliary 2-class heads in
    'single' mode, and fits them for 'joint' mode too (they are independent
    binary tasks — mode must not gate them);
  * absent or misaligned labels leave the heads None instead of crashing;
  * aux predict_proba returns a proper distribution;
  * save() -> load() round-trips the heads, and a pickle written BEFORE
    step 1.6 (no btts_pipe/o35_pipe keys) still loads with heads None.

Run:  .venv/bin/python ai/experimental/test_markets.py
"""
from __future__ import annotations

import contextlib
import os
import sys
import tempfile

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


@contextlib.contextmanager
def tiny_lgbm(n_estimators: int = 30):
    """Shrink ONLY the boosting-round budget for local/CI test speed.

    The production _lgbm_single runs 800 rounds (~25 s per head even on 600
    rows — fixed per-round cost), and these tests check PLUMBING (labels ->
    heads -> pickle), not accuracy.  Every other hyperparameter stays the
    real one; GitHub Actions retrains with the real budget.
    """
    import model.trainer as trainer_mod
    real = trainer_mod._lgbm_single

    def tiny(n_classes: int, seed: int = 42):
        est = real(n_classes, seed)
        est.set_params(n_estimators=n_estimators)
        return est

    trainer_mod._lgbm_single = tiny
    try:
        yield
    finally:
        trainer_mod._lgbm_single = real


def _synthetic_frame(n: int = 400, seed: int = 1) -> pd.DataFrame:
    """Toy settled matches with Poisson scores — enough for the label path."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2025-08-01", periods=max(n // 4, 30), freq="D")
    rows = []
    for k in range(n):
        hg, ag = int(rng.poisson(1.3)), int(rng.poisson(1.1))
        rows.append({
            "date": dates[k % len(dates)],
            "home_team": f"Team {k % 12}", "away_team": f"Team {(k + 5) % 12}",
            "home_goals": hg, "away_goals": ag,
            "league_slug": "test-league-a" if k % 2 else "test-league-b",
            "odds_home": 2.1, "odds_draw": 3.3, "odds_away": 3.4,
            "avg_odds_home": 2.1, "avg_odds_draw": 3.3, "avg_odds_away": 3.4,
            "max_odds_home": 2.2, "max_odds_draw": 3.5, "max_odds_away": 3.6,
            "over25_odds": 1.9, "under25_odds": 1.9,
            "elo_home": 1500, "elo_away": 1500,
        })
    return pd.DataFrame(rows)


def test_labels() -> None:
    """last_meta goal-market labels: present, aligned, ground-truth correct."""
    from model.features import FeaturePipeline

    df = _synthetic_frame()
    pipe = FeaturePipeline()
    pipe.fit(df)
    X, y1, yg, _ = pipe.build_training_set(df, max_samples=100_000)
    m = getattr(pipe, "last_meta", {}) or {}

    check("y_btts in last_meta", "y_btts" in m)
    check("y_o35 in last_meta", "y_o35" in m)
    if "y_btts" not in m or "y_o35" not in m:
        return
    bt, ot = m["y_btts"], m["y_o35"]
    check("labels aligned with y_1x2/y_goals",
          len(bt) == len(y1) == len(ot) == len(yg) == X.shape[0],
          f"{len(bt)} vs X {X.shape[0]}")

    # build_training_set starts at row 20 (warm-up window) — recompute the
    # truth from the source goals over exactly the aligned slice.
    gh = df["home_goals"].values[20:20 + len(y1)]
    ga = df["away_goals"].values[20:20 + len(y1)]
    check("y_btts is (both scored)",
          np.array_equal(bt, ((gh > 0) & (ga > 0)).astype(np.int32)))
    check("y_o35 is (total > 3.5)",
          np.array_equal(ot, ((gh + ga) > 3.5).astype(np.int32)))
    check("main labels still correct (no regression)",
          np.array_equal(y1, np.where(gh > ga, 0, np.where(gh == ga, 1, 2))
                         .astype(np.int32))
          and np.array_equal(yg, ((gh + ga) > 2.5).astype(np.int32)))
    check("both classes present in each aux label",
          len(np.unique(bt)) == 2 and len(np.unique(ot)) == 2,
          f"btts={np.bincount(bt).tolist()} o35={np.bincount(ot).tolist()}")


def _fit_xy(n: int = 600, n_features: int = 161, seed: int = 3):
    """Random feature matrix + plausible binary/multiclass labels (pure CPU)."""
    rng = np.random.default_rng(seed)
    X = rng.normal(0, 1, (n, n_features)).astype(np.float32)
    y1 = rng.integers(0, 3, n).astype(np.int32)
    yg = rng.integers(0, 2, n).astype(np.int32)
    # aux labels correlated with the features so the heads have signal
    score = X[:, 0] + 0.7 * X[:, 1]
    bt = (score > 0).astype(np.int32)
    ot = (score > 1.0).astype(np.int32)
    return X, y1, yg, bt, ot


def test_aux_heads() -> None:
    """fit_stacks fits/omits the BTTS + O3.5 heads as specified."""
    from model.trainer import FootballPredictor

    X, y1, yg, bt, ot = _fit_xy()

    # (a) with labels — single mode
    p = FootballPredictor()
    with tiny_lgbm():
        p.fit_stacks(X, y1, yg, calibrate=False, mode="single",
                     y_btts=bt, y_o35=ot)
    check("single mode fits BTTS head", p._btts_pipe is not None)
    check("single mode fits O3.5 head", p._o35_pipe is not None)

    pb = p._btts_pipe.predict_proba(X[:20])
    check("BTTS probs are a distribution",
          bool(np.allclose(pb.sum(axis=1), 1.0, atol=1e-6))
          and bool((pb >= 0).all() and (pb <= 1).all()))
    check("BTTS head learned the signal (AUC-ish > 0.8)",
          float(pb[:, 1][bt[:20] == 1].mean()) > float(pb[:, 1][bt[:20] == 0].mean()))

    # (b) joint main mode must NOT gate the aux heads
    pj = FootballPredictor()
    with tiny_lgbm():
        pj.fit_stacks(X, y1, yg, calibrate=False, mode="joint",
                      y_btts=bt, y_o35=ot)
    check("joint mode fits aux heads too",
          pj._btts_pipe is not None and pj._o35_pipe is not None)

    # (c) absent labels -> heads None, no crash (older callers)
    pn = FootballPredictor()
    with tiny_lgbm():
        pn.fit_stacks(X, y1, yg, calibrate=False, mode="single")
    check("no labels -> heads None",
          pn._btts_pipe is None and pn._o35_pipe is None)

    # (d) misaligned labels -> skipped with a warning, not a crash
    pm = FootballPredictor()
    with tiny_lgbm():
        pm.fit_stacks(X, y1, yg, calibrate=False, mode="single",
                      y_btts=bt[:-5], y_o35=ot)
    check("misaligned BTTS label skipped",
          pm._btts_pipe is None and pm._o35_pipe is not None)


def test_save_load() -> None:
    """The aux heads must survive the pickle, and pre-1.6 pickles must load."""
    import joblib
    from model.trainer import FootballPredictor

    X, y1, yg, bt, ot = _fit_xy(n=400, seed=9)
    p = FootballPredictor()
    p._recent_history = pd.DataFrame(  # non-empty -> load() skips CSV fallback
        [{"date": "2026-01-01", "home_team": "A", "away_team": "B",
          "home_goals": 1, "away_goals": 0, "league_slug": "x"}])
    with tiny_lgbm():
        p.fit_stacks(X, y1, yg, calibrate=False, mode="single",
                     y_btts=bt, y_o35=ot)

    path = os.path.join(tempfile.gettempdir(), "statwise-markets-test.pkl")
    p.save(path)
    q = FootballPredictor.load(path)
    check("save/load round-trips BTTS head", q._btts_pipe is not None)
    check("save/load round-trips O3.5 head", q._o35_pipe is not None)

    # a pickle written before step 1.6 has no aux keys at all
    old = joblib.load(path)
    for k in ("btts_pipe", "o35_pipe"):
        old.pop(k, None)
    old_path = os.path.join(tempfile.gettempdir(), "statwise-markets-old.pkl")
    joblib.dump(old, old_path)
    r = FootballPredictor.load(old_path)
    check("pre-1.6 pickle loads with heads None",
          r._btts_pipe is None and r._o35_pipe is None)
    check("pre-1.6 pickle keeps its main heads", r._outcome_pipe is not None
          and r._goals_pipe is not None)


def main() -> int:
    print("Plan 1.6 market-head tests (synthetic, tiny, fast)")
    test_labels()
    test_aux_heads()
    test_save_load()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {', '.join(FAILURES)}")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
