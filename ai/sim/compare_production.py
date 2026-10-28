"""Apples-to-apples: deployed LightGBM vs lab ensemble vs market vs blends.

Every previous lab-vs-production number in this repo compared figures from
DIFFERENT runs on DIFFERENT row populations (lab holdout = 5,123 loose-filter
rows incl. internationals; production gate = 2,250 production-filtered eval
rows; stored reports before the audit carried a non-proper binary "log
loss").  That is the measurement error this script removes: one frame, one
split, one set of eval rows, proper log loss for every system, and paired
bootstrap CIs on the differences so a gap is either real or noise.

Protocol — deliberately identical to ai/retrain.py's gate:

  * data: production filters (_load_clean_data: club, quality >= 40,
    >= 1993-08-01), features built through the deployed pickle's own
    feature pipe;
  * split: last 180 days = holdout, FIRST half of the holdout is skipped
    (production fits alpha/calibration there), SECOND half = eval rows;
  * market: de-vigged CLOSING consensus (_devig / _valid_odds), the source
    production's alpha blends against;
  * blends: log-loss-optimal alpha on a 0.05 grid, damped toward the
    production prior 0.35 with n/(n+1000) weight — the exact damp_alpha
    production uses (fitting alpha on the eval rows themselves is
    optimistic; the endpoints lgbm/market/lab are always shown next to the
    blend so the optimism is visible).

Systems:

  lgbm    the DEPLOYED pickle's outcome pipe (v7 mode=single; raw probs,
          calibration_used=false).  The recomputed eval LL is compared to
          the stored 1.01129 — a non-trivial delta means the data moved
          since training and is reported, never hidden.
  lab     the Monte Carlo recipe's world ensemble, exact grid averaging
          (posterior_markets, no sampling noise); unknown teams padded to
          league average (same rule as realism.py) with the count reported
          and a known-teams-only LL alongside.
  market  de-vigged closing consensus, on rows that have odds.
  blends  lgbm+market (should reproduce ~0.99491 when faithful),
          lab+market, and lgbm+lab — the H7 test: if the lab carried
          information orthogonal to the feature matrix, stacking it on the
          LightGBM would beat the LightGBM alone.

Hypothesis verdicts printed and stored:

  H1  "the DC ensemble is simply a weaker model" — lab LL vs lgbm LL on
      identical rows with the paired CI.
  H7  "the lab's information is already in the feature matrix" — lgbm+lab
      vs lgbm alone with the paired CI (plus probability correlations).

Usage (CI, .github/workflows/simulate.yml -> mode compare):

    python ai/sim/compare_production.py --out-dir ai/sim/reports

Local smoke (seconds — small slice, features refit on the slice):

    python ai/sim/compare_production.py --limit-rows 4000 \
        --fresh-feature-state --out-dir /tmp/opencode/compare
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))          # ai/ -> `import sim...`

from retrain import _devig, _load_clean_data, _valid_odds, damp_alpha  # noqa: E402
from sim.model import StrengthModel                                     # noqa: E402
from sim.realism import map_teams, pad_worlds                           # noqa: E402
from sim.simulate import posterior_markets                              # noqa: E402

COMPARE_SCHEMA = "statwise.sim.compare.v1"
EPS = 1e-9
STORED_V7_EVAL_LL = 1.01129        # ai/data/model_info.json v7 gate number
STORED_V7_BLEND_LL = 0.99491       # ...its lgbm+market blend
STORED_V7_MARKET_LL = 0.99796      # ...its closing-market baseline


# ── scoring primitives ─────────────────────────────────────────────────────
def ll_rows(p: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Per-row proper log loss against the observed class."""
    return -np.log(np.clip(p[np.arange(len(y)), y], EPS, 1.0))


def ll3(p: np.ndarray, y: np.ndarray) -> float:
    return float(ll_rows(p, y).mean())


def best_alpha(pa: np.ndarray, pb: np.ndarray, y: np.ndarray,
               mask: np.ndarray) -> float | None:
    """Log-loss-optimal weight on `pa` (production's grid search, 0.05 step)."""
    m = np.asarray(mask, dtype=bool)
    if int(m.sum()) < 50:
        return None
    best_a, best_ll = None, float("inf")
    for a in np.arange(0.0, 1.001, 0.05):
        ll = ll3(a * pa[m] + (1.0 - a) * pb[m], y[m])
        if ll < best_ll:
            best_ll, best_a = ll, float(a)
    return float(best_a)


def paired_ll_ci(d: np.ndarray, *, n_boot: int = 1000, seed: int = 0,
                 q: float = 0.05) -> dict:
    """Bootstrap CI of a mean per-row log-loss difference (system A − B)."""
    d = np.asarray(d, dtype=float)
    n = len(d)
    if n == 0:
        return {"n": 0, "mean": None, "ci90": [None, None],
                "p_a_better": None}
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, n, size=(n_boot, n))
    means = d[idx].mean(axis=1)
    return {
        "n": int(n),
        "mean": round(float(d.mean()), 5),
        "ci90": [round(float(np.quantile(means, q)), 5),
                 round(float(np.quantile(means, 1 - q)), 5)],
        # fraction of bootstrap draws where A beats B (LL difference < 0)
        "p_a_better": round(float((means < 0).mean()), 3),
    }


def _corr(a: np.ndarray, b: np.ndarray) -> float:
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    if a.std() < EPS or b.std() < EPS:
        return 0.0
    return round(float(np.corrcoef(a, b)[0, 1]), 4)


def _fmt(v, spec: str = ".5f") -> str:
    return "-" if v is None else format(v, spec)


def _ci_str(lo, hi) -> str:
    if lo is None or hi is None:
        return "-"
    return f"[{lo:+.5f}, {hi:+.5f}]"


def _signed(v) -> str:
    return "n/a" if v is None else f"{v:+.5f}"


# ── the comparison ─────────────────────────────────────────────────────────
def load_production(model_path: str, df: pd.DataFrame, *, holdout_days: int,
                    fresh_feature_state: bool):
    """Rebuild the gate's feature matrix and the deployed predictions.

    Returns (p_lgb (h,3), eval slices, meta).  Uses the pickle's own fitted
    feature state by default — that is the state v7 trained with, so the
    recomputed numbers are v7's numbers unless the data moved.
    """
    from model.trainer import FootballPredictor

    predictor = FootballPredictor.load(model_path)
    if fresh_feature_state:
        pipe = FootballPredictor().feature_pipe
        pipe.fit(df)
        predictor.feature_pipe = pipe
    pipe = predictor.feature_pipe

    X, y, _g, d = pipe.build_training_set(df)
    X = np.nan_to_num(X, nan=0.0, posinf=1e6, neginf=-1e6).astype(
        np.float32, copy=False)
    meta = getattr(pipe, "last_meta", {}) or {}

    dates = pd.to_datetime(d, errors="coerce")
    split_at = dates.max() - pd.Timedelta(days=holdout_days)
    m_tr = dates <= split_at
    m_ho = ~m_tr
    Xh, yh = X[m_ho], y[m_ho]
    mid = max(len(yh) // 2, 100)

    raw = predictor._outcome_pipe.predict_proba(Xh)
    calibs = getattr(predictor, "_calibrators", None)
    if calibs:
        # honour the pickle: only trained when the isotonic won out-of-sample
        q = np.column_stack([iso.predict(raw[:, k]) for k, iso in enumerate(calibs)])
        q = np.clip(q, 1e-6, None)
        p_ho = q / q.sum(axis=1, keepdims=True)
        calibration = "isotonic"
    else:
        p_ho = raw
        calibration = "raw"
    return p_ho[mid:], yh[mid:], split_at, mid, m_ho, meta, calibration


def lab_probs(worlds: list[StrengthModel], home: np.ndarray, away: np.ndarray,
              leagues: np.ndarray) -> tuple[np.ndarray, np.ndarray, int]:
    """World-ensemble 1X2 for eval rows -> (probs, known-teams mask, n_unknown)."""
    frame = pd.DataFrame({"home_team": home, "away_team": away,
                          "league_slug": leagues})
    worlds = pad_worlds(worlds)
    known = np.array([
        str(h) in worlds[0].t2i and str(a) in worlds[0].t2i
        for h, a in zip(home, away)], dtype=bool)
    h, a, lg, n_unknown = map_teams(frame, worlds)
    pm = posterior_markets(worlds, h, a, lg)
    p = np.column_stack([pm["home"], pm["draw"], pm["away"]])
    p = p / p.sum(axis=1, keepdims=True)
    return p, known, n_unknown


def compare(df: pd.DataFrame, model_path: str, recipe_path: str,
            *, holdout_days: int, fresh_feature_state: bool,
            n_boot: int, seed: int) -> dict:
    t0 = time.time()

    # ── deployed side (identical protocol to the gate) ────────────────────
    p_lgb, y_ev, split_at, mid, m_ho, meta, calibration = load_production(
        model_path, df, holdout_days=holdout_days,
        fresh_feature_state=fresh_feature_state)
    n_eval = len(y_ev)

    def _meta(key: str, default=None):
        v = meta.get(key)
        if v is None:
            return default
        return np.asarray(v)[m_ho][mid:]

    home = _meta("home")
    away = _meta("away")
    leagues = _meta("leagues", np.array(["all"] * n_eval))
    odds_c = _meta("odds_closing", np.zeros((n_eval, 3)))

    # ── lab side ──────────────────────────────────────────────────────────
    from sim.merge import recipe_models
    with open(recipe_path, encoding="utf-8") as fh:
        recipe = json.load(fh)
    worlds = recipe_models(recipe)
    p_lab, known, n_unknown = lab_probs(worlds, home, away, leagues)

    # ── market side (closing consensus, production's devig) ───────────────
    mkt = _devig(odds_c)
    valid = _valid_odds(odds_c)

    # ── system log losses on their row sets ───────────────────────────────
    ll_lgb = ll3(p_lgb, y_ev)
    ll_lab = ll3(p_lab, y_ev)
    ll_lab_known = ll3(p_lab[known], y_ev[known]) if known.any() else None
    ll_unif = float(np.log(3))
    systems = {
        "n_eval": n_eval,
        "split_at": str(pd.Timestamp(split_at).date()),
        "eval_second_half_rows": int(n_eval),
        "first_holdout_half_skipped": int(mid),
        "lgbm": {"ll_1x2": round(ll_lgb, 5),
                 "accuracy": round(float((p_lgb.argmax(1) == y_ev).mean()), 4),
                 "calibration": calibration},
        "lab": {"ll_1x2": round(ll_lab, 5),
                "accuracy": round(float((p_lab.argmax(1) == y_ev).mean()), 4),
                "known_teams_only_ll_1x2": (round(ll_lab_known, 5)
                                            if ll_lab_known is not None else None),
                "n_unknown_team_rows": int(n_unknown),
                "unknown_team_rate": round(n_unknown / max(n_eval, 1), 4),
                "worlds": len(worlds)},
        "uniform": {"ll_1x2": round(ll_unif, 5)},
    }
    if int(valid.sum()) > 50:
        systems["market"] = {
            "ll_1x2": round(ll3(mkt[valid], y_ev[valid]), 5),
            "rows": int(valid.sum()),
        }

    # ── blends: two protocols, kept apart on purpose ──────────────────────
    #  * `production` — lgbm+market, alpha grid damped to prior 0.35 fitted
    #    on the FULL eval rows: exactly how the stored 0.99491 was produced.
    #    Used only as the faithfulness number (alpha fitted on the rows it
    #    scores is optimistic — production accepts that with damping).
    #  * `honest` — alpha fit on the FIRST half of the eval rows, scored on
    #    the SECOND half (rows the alpha never saw), damped as well.  The
    #    hypothesis blends (lab+market, lgbm+lab) use this, so "the blend
    #    wins" can never be alpha overfitting.  lgbm+lab damps toward
    #    prior 1.0 — the null that the lab gets NO weight unless the fit
    #    half earns it.
    blends: dict = {}
    n_eval_half = n_eval // 2
    fit_mask = np.arange(n_eval) < n_eval_half
    test_mask = ~fit_mask

    def _blend_row(pa, pb, y, score_mask, a, protocol) -> dict:
        m = np.asarray(score_mask, dtype=bool)
        return {
            "protocol": protocol,
            "alpha_used": round(float(a), 4),
            "scoring_rows": int(m.sum()),
            "ll_1x2": round(ll3(a * pa[m] + (1 - a) * pb[m], y[m]), 5),
            "endpoint_pa": round(ll3(pa[m], y[m]), 5),
            "endpoint_pb": round(ll3(pb[m], y[m]), 5),
        }

    if int(valid.sum()) >= 50:
        a_fit = best_alpha(p_lgb, mkt, y_ev, valid)
        blends["lgbm_market"] = _blend_row(
            p_lgb, mkt, y_ev, valid,
            damp_alpha(a_fit, int(valid.sum())),
            "production protocol: alpha grid + damp(0.35) on full eval "
            "(reproduces the stored 0.99491 when faithful)")

    fit_valid = fit_mask & valid
    test_valid = test_mask & valid
    if int(fit_valid.sum()) >= 50 and int(test_valid.sum()) >= 50:
        a_fit = best_alpha(p_lab, mkt, y_ev, fit_valid)
        blends["lab_market"] = _blend_row(
            p_lab, mkt, y_ev, test_valid,
            damp_alpha(a_fit, int(fit_valid.sum())),
            "honest: alpha fit on first eval half, scored on second")
    if int(fit_mask.sum()) >= 50 and int(test_mask.sum()) >= 50:
        a_fit = best_alpha(p_lgb, p_lab, y_ev, fit_mask)
        blends["lgbm_lab"] = _blend_row(
            p_lgb, p_lab, y_ev, test_mask,
            damp_alpha(a_fit, int(fit_mask.sum()), prior=1.0),
            "honest: alpha fit on first eval half (prior 1.0 = lab earns "
            "its weight), scored on second")

    # ── paired CIs on the differences that decide the hypotheses ──────────
    d_h1 = ll_rows(p_lab, y_ev) - ll_rows(p_lgb, y_ev)      # lab − lgbm
    h1 = paired_ll_ci(d_h1, n_boot=n_boot, seed=seed)
    diffs = {"lab_minus_lgbm": h1}
    if "lgbm_lab" in blends:
        a_gl = blends["lgbm_lab"]["alpha_used"]
        diffs["lgbm_lab_blend_minus_lgbm"] = paired_ll_ci(
            ll_rows(a_gl * p_lgb + (1 - a_gl) * p_lab, y_ev)[test_mask]
            - ll_rows(p_lgb, y_ev)[test_mask],
            n_boot=n_boot, seed=seed + 1)
    if int(valid.sum()) >= 50:
        diffs["lgbm_minus_market"] = paired_ll_ci(
            ll_rows(p_lgb[valid], y_ev[valid]) - ll_rows(mkt[valid], y_ev[valid]),
            n_boot=n_boot, seed=seed + 2)
    if "lab_market" in blends:
        a_lm = blends["lab_market"]["alpha_used"]
        diffs["lab_market_blend_minus_market"] = paired_ll_ci(
            (ll_rows(a_lm * p_lab + (1 - a_lm) * mkt, y_ev)
             - ll_rows(mkt, y_ev))[test_valid],
            n_boot=n_boot, seed=seed + 3)
    h7 = diffs.get("lgbm_lab_blend_minus_lgbm", {
        "n": 0, "mean": None, "ci90": [None, None], "p_a_better": None})

    # ── information overlap (H7's second leg) ─────────────────────────────
    overlap = {
        "corr_home_prob": _corr(p_lgb[:, 0], p_lab[:, 0]),
        "corr_draw_prob": _corr(p_lgb[:, 1], p_lab[:, 1]),
        "corr_margin_ph_minus_pa": _corr(p_lgb[:, 0] - p_lgb[:, 2],
                                         p_lab[:, 0] - p_lab[:, 2]),
        "corr_row_ll": _corr(ll_rows(p_lgb, y_ev), ll_rows(p_lab, y_ev)),
    }

    # ── reproduction check vs the stored v7 gate numbers ──────────────────
    delta = ll_lgb - STORED_V7_EVAL_LL
    reproduction = {
        "used_pickle_feature_state": bool(not fresh_feature_state),
        "stored_v7_eval_ll": STORED_V7_EVAL_LL,
        "recomputed_lgbm_eval_ll": round(ll_lgb, 5),
        "eval_ll_delta": round(delta, 5),
        "stored_v7_blend_ll": STORED_V7_BLEND_LL,
        "recomputed_lgbm_market_blend_ll": blends.get("lgbm_market", {}).get("ll_1x2"),
        "stored_v7_market_ll": STORED_V7_MARKET_LL,
        "recomputed_market_ll": systems.get("market", {}).get("ll_1x2"),
        # only meaningful when the pickle's own fitted state was used; a
        # --fresh-feature-state smoke run compares against nothing
        "data_moved_since_training": (None if fresh_feature_state
                                      else bool(abs(delta) > 0.002)),
    }

    # ── hypothesis verdicts ───────────────────────────────────────────────
    # H1: "the DC ensemble is simply a weaker predictor" -> confirmed when
    #     the paired CI for (lab − lgbm) sits above 0.
    # H7: "the lab's information is already in the feature matrix" ->
    #     confirmed when stacking cannot improve the LightGBM (the fit half
    #     refuses the lab any weight, or the blend is significantly worse);
    #     refuted only when the blend significantly BEATS the LightGBM.
    h1_gap, h1_lo, h1_hi = h1["mean"], h1["ci90"][0], h1["ci90"][1]
    h7_gap, h7_lo, h7_hi = h7["mean"], h7["ci90"][0], h7["ci90"][1]
    alpha_gl = blends.get("lgbm_lab", {}).get("alpha_used")
    if h1_gap is None:
        h1_res = "inconclusive"
    elif (h1_lo or 0) > 0:
        h1_res = "confirmed"
    elif (h1_hi or 0) < 0:
        h1_res = "refuted"
    else:
        h1_res = "inconclusive"
    if h7_gap is None:
        h7_res = "inconclusive"
    elif (h7_hi or 0) < 0:
        h7_res = "refuted"        # blend beats lgbm => lab DOES add signal
    elif alpha_gl is not None and alpha_gl >= 0.999:
        h7_res = "confirmed"      # fit half gave the lab zero weight
    elif (h7_lo or 0) > 0:
        h7_res = "confirmed"      # blend significantly worse than lgbm alone
    else:
        h7_res = "inconclusive"
    verdicts = {
        "H1": {"claim": "the DC ensemble is a weaker predictor than the "
                        "deployed LightGBM on identical rows",
               "result": h1_res, "gap_ll": h1_gap, "ci90": [h1_lo, h1_hi]},
        "H7": {"claim": "the lab carries no signal beyond the production "
                        "feature matrix (stacking cannot improve it)",
               "result": h7_res, "gap_ll": h7_gap, "ci90": [h7_lo, h7_hi],
               "lgbm_weight_alpha": alpha_gl},
        "note": "gaps are log-loss differences: positive = first system "
                "worse.  H1 'confirmed' = lab significantly weaker.  H7 "
                "'confirmed' = stacking gains nothing; 'refuted' would mean "
                "the lab deserves the effort.",
    }
    return {
        "schema": COMPARE_SCHEMA,
        "meta": {
            "generated_at": pd.Timestamp.now("UTC").isoformat(),
            "data_rows": len(df),
            "holdout_days": holdout_days,
            "model_path": model_path,
            "recipe": recipe_path,
            "fresh_feature_state": fresh_feature_state,
            "n_boot": n_boot,
            "seed": seed,
            "duration_s": round(time.time() - t0, 1),
        },
        "systems": systems,
        "blends": blends,
        "diffs": diffs,
        "overlap": overlap,
        "reproduction": reproduction,
        "verdicts": verdicts,
    }


# ── report rendering ───────────────────────────────────────────────────────
def render_md(rep: dict) -> str:
    s, b, d, r, v = (rep["systems"], rep["blends"], rep["diffs"],
                     rep["reproduction"], rep["verdicts"])
    m = rep["meta"]
    lines = [
        "# Production comparison — LightGBM vs lab vs market",
        "",
        f"> generated {m['generated_at']} · schema {rep['schema']} · "
        f"{s['n_eval']:,} eval rows (second half of the "
        f"{m['holdout_days']}d holdout, split {s['split_at']}) · "
        f"runtime {m['duration_s']}s",
        "",
        "| system | 1X2 log loss (proper) | rows | notes |",
        "|---|---|---|---|",
        f"| deployed LightGBM | **{s['lgbm']['ll_1x2']:.5f}** | {s['n_eval']:,} "
        f"| {s['lgbm']['calibration']} probs |",
        f"| lab ensemble | **{s['lab']['ll_1x2']:.5f}** | {s['n_eval']:,} "
        f"| {s['lab']['worlds']} worlds, known-only "
        f"{s['lab']['known_teams_only_ll_1x2']} "
        f"({s['lab']['unknown_team_rate']:.1%} unknown-team rows) |",
        f"| uniform | {s['uniform']['ll_1x2']:.5f} | {s['n_eval']:,} | ln 3 |",
    ]
    if "market" in s:
        lines.append(f"| closing market | **{s['market']['ll_1x2']:.5f}** "
                     f"| {s['market']['rows']:,} | de-vigged consensus |")
    lines += ["", "## Blends", "",
              "| blend | alpha | LL (scoring rows) | endpoints | protocol |",
              "|---|---|---|---|---|"]
    for name, row in b.items():
        lines.append(f"| {name} | {row['alpha_used']:.3f} "
                     f"| **{row['ll_1x2']:.5f}** "
                     f"({row['scoring_rows']:,} rows) "
                     f"| {row['endpoint_pa']:.5f} / {row['endpoint_pb']:.5f} "
                     f"| {row['protocol']} |")
    lines += ["", "## Paired differences (A − B, bootstrap CI90)", "",
              "| difference | mean | CI90 | P(A better) |", "|---|---|---|---|"]
    for name, row in d.items():
        lo, hi = row["ci90"]
        if row["mean"] is None:
            lines.append(f"| {name} | n/a | n/a | n/a |")
            continue
        lines.append(f"| {name} | {row['mean']:+.5f} | [{lo:+.5f}, {hi:+.5f}] "
                     f"| {row['p_a_better']} |")
    o = rep["overlap"]
    lines += [
        "",
        "## Information overlap (H7)",
        "",
        f"- corr(home prob): {o['corr_home_prob']} · corr(draw): "
        f"{o['corr_draw_prob']} · corr(margin): "
        f"{o['corr_margin_ph_minus_pa']} · corr(per-row LL): "
        f"{o['corr_row_ll']}",
        "",
        "## Reproduction vs the stored v7 gate",
        "",
        f"- eval LL: stored {r['stored_v7_eval_ll']:.5f} vs recomputed "
        f"{r['recomputed_lgbm_eval_ll']:.5f} (delta "
        f"{r['eval_ll_delta']:+.5f})"
        + (" — fresh feature state, delta not a data-drift signal"
           if r["data_moved_since_training"] is None else
           (" — DATA MOVED since training" if r["data_moved_since_training"]
            else " — faithful")),
        f"- market LL: stored {r['stored_v7_market_ll']:.5f} vs recomputed "
        f"{r['recomputed_market_ll']}",
        f"- blend LL: stored {r['stored_v7_blend_ll']:.5f} vs recomputed "
        f"{r['recomputed_lgbm_market_blend_ll']}",
        "",
        "## Verdicts",
        "",
        f"- **H1** ({v['H1']['claim']}): **{v['H1']['result'].upper()}** "
        f"(gap {_signed(v['H1']['gap_ll'])}, CI90 "
        f"{_ci_str(*v['H1']['ci90'])})",
        f"- **H7** ({v['H7']['claim']}): **{v['H7']['result'].upper()}** "
        f"(stacking gap {_signed(v['H7']['gap_ll'])}, CI90 "
        f"{_ci_str(*v['H7']['ci90'])}, lgbm weight alpha "
        f"{_fmt(v['H7'].get('lgbm_weight_alpha'))})",
        f"- {v['note']}",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model-path", default=os.path.join(
        os.path.dirname(HERE), "models", "football_predictor.pkl"))
    ap.add_argument("--recipe", default=os.path.join(
        os.path.dirname(HERE), "data", "sim", "worlds.json"))
    ap.add_argument("--holdout-days", type=int, default=180)
    ap.add_argument("--limit-rows", type=int, default=0,
                    help="tail-slice the frame for a local smoke run")
    ap.add_argument("--fresh-feature-state", action="store_true",
                    help="refit the feature pipe on the frame instead of "
                         "using the deployed pickle's fitted state")
    ap.add_argument("--n-boot", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=20261028)
    ap.add_argument("--out-dir", default=os.path.join(HERE, "reports"))
    ap.add_argument("--tag", default="compare_v0")
    args = ap.parse_args()

    t0 = time.time()
    df = _load_clean_data()
    if df.empty:
        print("no production data", file=sys.stderr)
        return 1
    if args.limit_rows:
        df = df.tail(args.limit_rows).reset_index(drop=True)
    print(f"loaded {len(df):,} production rows "
          f"({df['date'].min().date()} .. {df['date'].max().date()})", flush=True)

    rep = compare(df, args.model_path, args.recipe,
                  holdout_days=args.holdout_days,
                  fresh_feature_state=args.fresh_feature_state,
                  n_boot=args.n_boot, seed=args.seed)

    os.makedirs(args.out_dir, exist_ok=True)
    jpath = os.path.join(args.out_dir, f"{args.tag}.json")
    mpath = os.path.join(args.out_dir, f"{args.tag}.md")
    with open(jpath, "w", encoding="utf-8") as fh:
        json.dump(rep, fh, indent=1)
    md = render_md(rep)
    with open(mpath, "w", encoding="utf-8") as fh:
        fh.write(md)
    print(md, flush=True)
    print(f"wrote {jpath} and {mpath} "
          f"(total {time.time() - t0:.1f}s)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
