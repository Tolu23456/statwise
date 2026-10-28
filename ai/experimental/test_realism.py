"""Realism validation suite tests — fast, synthetic, no heavy work.

Guards ai/sim/realism.py, the mandatory sim-vs-real validation suite:

  * the scoreline lookups (total-goals / goal-difference binning) are total
    and correct — every grid cell maps to exactly one bin;
  * event probabilities from the averaged grid agree with the canonical
    market marginals (same numbers evaluate() gates on);
  * grid moments are right, including var(total) — the fix for the missing
    var_t (var_h + var_a ignores the covariance the Dixon-Coles tau adds);
  * scoring primitives behave: z_cal, Wilson, W1, split-half floor, RPS,
    proper log loss, ECE;
  * baselines are honest: the league prior comes from TRAIN rows only, the
    market block de-vigs and masks missing odds instead of inventing them;
  * make_windows is strictly chronological — no evaluation row ever appears
    in its own window's training set (the leak guard);
  * load_frame applies the production row filters (club football, quality
    >= 40, >= 1993-08-01) — audit W1's population fix;
  * evaluate_window / run_windows produce a FULL report that is strictly
    JSON-serialisable (allow_nan=False — numpy scalars and NaN floors were
    the failure mode), with every verdict key present;
  * unknown teams map to the league-average padded slot and are counted,
    never silently dropped.

Data: a few hundred synthetic matches generated in-process.  Nothing here
loads the 377k-row clean set — heavy runs belong in GitHub Actions
(.github/workflows/simulate.yml, mode realism).

Run:  .venv/bin/python ai/experimental/test_realism.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))          # .../statwise
sys.path.insert(0, os.path.join(ROOT, "ai"))

from sim.model import fit_strengths                       # noqa: E402
from sim.model import market_probs as grid_market_probs   # noqa: E402
from sim.model import scoreline_probs                     # noqa: E402
from sim.realism import (                                # noqa: E402
    HEADLINE_EVENTS, N_TOTAL, _data_facts, _ece_conf, _gd_pmf_rows, _num,
    _total_pmf_rows, build_report, compare_reports, ece_binary, evaluate_window,
    find_previous, grid_moments, grids_event_probs, league_prior_probs,
    load_frame, make_windows, map_teams, market_probs, pad_worlds, render_md,
    rps_mean, run_windows, split_half_floor, wasserstein1, wilson, z_cal,
)

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILURES.append(name)


def _frame(n: int = 900, seed: int = 7) -> pd.DataFrame:
    """Synthetic season: 2 leagues, 12 teams, home advantage, HT/red/odds data."""
    rng = np.random.default_rng(seed)
    leagues = {
        "realism-a": [f"Alpha United {i}" for i in range(6)],
        "realism-b": [f"Beta City {i}" for i in range(6)],
    }
    att = {t: rng.normal(0, 0.25) for ts in leagues.values() for t in ts}
    dfn = {t: rng.normal(0, 0.25) for ts in leagues.values() for t in ts}
    rows = []
    start = pd.Timestamp("2024-01-01")
    for k in range(n):
        name = "realism-a" if k % 2 == 0 else "realism-b"
        h, a = rng.choice(leagues[name], size=2, replace=False)
        lam_h = np.exp(np.log(1.35) + np.log(1.25) + att[h] - dfn[a])
        lam_a = np.exp(np.log(1.35) + att[a] - dfn[h])
        hg = int(rng.poisson(lam_h))
        ag = int(rng.poisson(lam_a))
        rows.append({
            "date": start + pd.Timedelta(days=int(k // 2)),
            "league_slug": name, "home_team": h, "away_team": a,
            "home_goals": hg, "away_goals": ag,
            "quality_score": 60, "is_international": 0,
            "is_neutral": int(rng.random() < 0.01),
            "reds_home": int(rng.random() < 0.05),
            "reds_away": int(rng.random() < 0.03),
            "halftime_home": int(rng.binomial(hg, 0.5)),
            "halftime_away": int(rng.binomial(ag, 0.5)),
            "odds_home": 2.1 * float(rng.uniform(0.9, 1.1)),
            "odds_draw": 3.4 * float(rng.uniform(0.9, 1.1)),
            "odds_away": 3.5 * float(rng.uniform(0.9, 1.1)),
            "avg_odds_home": 2.1 * float(rng.uniform(0.9, 1.1)),
            "avg_odds_draw": 3.4 * float(rng.uniform(0.9, 1.1)),
            "avg_odds_away": 3.5 * float(rng.uniform(0.9, 1.1)),
        })
    return pd.DataFrame(rows)


def _quiet(*_a, **_k) -> None:
    pass


# ── 1. bin lookups are total and correct ───────────────────────────────────
def test_bin_lookups() -> None:
    tm, gm = _total_pmf_rows(), _gd_pmf_rows()
    check("total lookup shape (G*G, 8)", tm.shape == (121, N_TOTAL), str(tm.shape))
    check("total lookup rows sum to 1 (total function)",
          bool(np.allclose(tm.sum(axis=1), 1.0)))
    check("gd lookup shape (G*G, 9)", gm.shape == (121, 9), str(gm.shape))
    check("gd lookup rows sum to 1", bool(np.allclose(gm.sum(axis=1), 1.0)))
    # cell (i, j) -> flattened index i*G + j
    cell = lambda i, j: i * 11 + j
    check("total bin 0-0 -> 0", tm[cell(0, 0)].argmax() == 0)
    check("total bin 2-3 -> 5", tm[cell(2, 3)].argmax() == 5)
    check("total bin 6-0 -> 6", tm[cell(6, 0)].argmax() == 6)
    check("total bin folds 7+ (10-10 -> 7)", tm[cell(10, 10)].argmax() == 7)
    check("gd bin 0-0 -> centre (4)", gm[cell(0, 0)].argmax() == 4)
    check("gd bin 3-0 -> 7", gm[cell(3, 0)].argmax() == 7)
    check("gd clips 10-0 into top bin", gm[cell(10, 0)].argmax() == 8)


# ── 2. event probs == canonical market marginals ──────────────────────────
def test_event_probs_match_market_probs() -> None:
    grid = scoreline_probs(1.7, 1.1, xi=-0.06)
    check("scoreline grid sums to 1", abs(grid.sum() - 1.0) < 1e-9)
    ev = grids_event_probs(grid[None, ...],
                           ("home_win", "draw", "away_win", "btts",
                            "over15", "over25", "over35"))
    mp = grid_market_probs(grid)
    for key, mk in (("home_win", "home"), ("draw", "draw"), ("away_win", "away"),
                    ("btts", "btts"), ("over15", "over15"),
                    ("over25", "over25"), ("over35", "over35")):
        check(f"event {key} == market_probs[{mk}]",
              abs(float(ev[key][0]) - mp[mk]) < 1e-9,
              f"{float(ev[key][0]):.6f} vs {mp[mk]:.6f}")
    check("1X2 events sum to 1",
          abs(float(ev["home_win"][0] + ev["draw"][0] + ev["away_win"][0]) - 1) < 1e-9)


# ── 3. grid moments incl. var(total) ───────────────────────────────────────
def test_grid_moments() -> None:
    lam_h, lam_a = 1.7, 1.1
    g0 = scoreline_probs(lam_h, lam_a, xi=0.0)
    m0 = grid_moments(g0[None, ...])
    check("moment E[home] ~ lambda (truncated at 10 goals)",
          abs(float(m0["lam_h"][0]) - lam_h) < 1e-4,
          f"{float(m0['lam_h'][0]):.5f}")
    check("moment E[total] ~ sum of lambdas",
          abs(float(m0["lam_t"][0]) - (lam_h + lam_a)) < 1e-4)
    # independent Poisson => zero covariance => var_t == var_h + var_a exactly
    check("var(total) = var_h + var_a when independent (xi=0)",
          abs(float(m0["var_t"][0]) - float(m0["var_h"][0] + m0["var_a"][0])) < 1e-9)
    g1 = scoreline_probs(lam_h, lam_a, xi=-0.06)
    m1 = grid_moments(g1[None, ...])
    check("var(total) positive and finite with DC tau",
          bool(np.isfinite(m1["var_t"][0])) and float(m1["var_t"][0]) > 0)
    check("DC tau creates covariance (var_t != var_h + var_a)",
          abs(float(m1["var_t"][0]) - float(m1["var_h"][0] + m1["var_a"][0])) > 1e-9)


# ── 4. scoring primitives ──────────────────────────────────────────────────
def test_scoring_primitives() -> None:
    check("z_cal zero when y == p",
          abs(z_cal(np.full(50, 0.4), np.full(50, 0.4))) < 1e-12)
    z = z_cal(np.ones(100), np.full(100, 0.5))
    check("z_cal = sqrt(n) for all-positive vs p=0.5", abs(z - 10.0) < 1e-9,
          f"z={z:.3f}")
    lo, hi = wilson(50, 100)
    check("wilson interval ordered and contains p", lo <= 0.5 <= hi,
          f"[{lo:.3f}, {hi:.3f}]")
    check("wilson degenerate at n=0", wilson(0, 0) == (0.0, 1.0))
    p = np.array([0.4, 0.3, 0.2, 0.1])
    check("w1 identical pmfs = 0", wasserstein1(p, p) < 1e-12)
    check("w1 one-bin shift = mass",
          abs(wasserstein1(np.array([1.0, 0.0]), np.array([0.0, 1.0])) - 1.0) < 1e-9)
    vals = np.random.default_rng(0).integers(0, 8, 1000)
    floor = split_half_floor(vals, 8, n_boot=50, seed=1)
    check("split-half floor finite and non-negative",
          bool(np.isfinite(floor)) and floor >= 0, f"{floor:.4f}")
    check("split-half floor ~0 for constant data",
          split_half_floor(np.zeros(200, dtype=int), 8, n_boot=30, seed=1) < 1e-9)
    probs = np.array([[0.0, 1.0, 0.0], [0.2, 0.7, 0.1]])
    y = np.array([1, 1])
    check("rps = 0 for perfect first row and small for second",
          0 <= rps_mean(probs, y) < 0.1, f"{rps_mean(probs, y):.4f}")
    check("rps ordered classes away<draw<home",
          abs(rps_mean(np.array([[1 / 3, 1 / 3, 1 / 3]]), np.array([0]))
              - rps_mean(np.array([[1 / 3, 1 / 3, 1 / 3]]), np.array([2]))) < 1e-12)
    check("ece_binary = 0 for perfect probs",
          ece_binary(np.array([0.0, 1.0, 1.0, 0.0]),
                     np.array([0, 1, 1, 0])) == 0.0)
    check("_ece_conf = 0 for perfect probs",
          _ece_conf(np.eye(3)[[0, 1, 2]], np.array([0, 1, 2])) == 0.0)
    check("ece_binary returns plain float",
          isinstance(ece_binary(np.array([0.5]), np.array([1])), float))

# ── 5. baselines are honest ────────────────────────────────────────────────
def test_baselines() -> None:
    # league prior: train rows only, per league, pooled fallback
    train = pd.DataFrame({
        "league_slug": ["lg-a"] * 90 + ["lg-b"] * 10,
        "home_goals": [1, 0] * 45 + [0, 1] * 5,
        "away_goals": [0, 1] * 45 + [1, 0] * 5,
    })
    ev = pd.DataFrame({
        "league_slug": ["lg-a", "lg-b", "mystery"],
        "home_goals": [1, 0, 1], "away_goals": [0, 1, 0],
    })
    prior, y_ev = league_prior_probs(train, ev)
    check("prior shape over eval rows", prior.shape == (3, 3), str(prior.shape))
    check("prior rows sum to 1", bool(np.allclose(prior.sum(axis=1), 1.0)))
    check("lg-a prior = train frequency of lg-a",
          abs(prior[0][0] - 0.5) < 1e-9, f"{prior[0][0]:.4f}")
    # lg-a: home win when hg>ag -> 45/90 = 0.5 home, 0 draw? rows alternate
    # (1,0) and (0,1): home=0.5, draw=0.0, away=0.5
    check("lg-b prior reflects its own rows", abs(prior[1][0] - 0.5) < 1e-9)
    check("unknown league falls back to pooled",
          bool(np.allclose(prior[2], [0.5, 0.0, 0.5])),
          f"{prior[2].tolist()}")
    check("prior returns observed y", list(y_ev) == [0, 2, 0], str(list(y_ev)))

    # market: de-vig, prefer avg odds, mask missing
    mk = pd.DataFrame({
        "odds_home": [2.0, 4.0, np.nan],
        "odds_draw": [4.0, 4.0, np.nan],
        "odds_away": [4.0, 4.0, 3.0],
        "avg_odds_home": [2.5, np.nan, np.nan],     # differs from odds_home
        "avg_odds_draw": [4.0, np.nan, np.nan],
        "avg_odds_away": [4.0, np.nan, np.nan],
    })
    mv, good = market_probs(mk)
    check("market de-vigs and PREFERS avg odds",
          np.allclose(mv[0], np.array([0.4, 0.25, 0.25]) / 0.9),
          f"{mv[0].tolist()}")
    check("market falls back to single-book odds when avg missing",
          bool(good[0]) and bool(good[1])
          and np.allclose(mv[1], np.full(3, 1 / 3)))
    check("market masks incomplete odds rows",
          not bool(good[2]) and bool((mv[2] == 0).all()))
    mv2, good2 = market_probs(pd.DataFrame({"home_goals": [1, 2]}))
    check("market with no odds columns -> all masked",
          not bool(good2.any()) and bool((mv2 == 0).all()))

    # proper scores: -log p assigned to the OBSERVED class
    from sim.realism import ll_multiclass
    p1 = np.array([[0.9, 0.05, 0.05]])
    check("log loss is proper (-log p_true)",
          abs(ll_multiclass(p1, np.array([0])) - (-np.log(0.9))) < 1e-12)
    check("log loss punishes wrong class",
          ll_multiclass(p1, np.array([2])) > ll_multiclass(p1, np.array([0])) + 1.0)


# ── 6. chronology: eval rows never in their own train ──────────────────────
def test_windows_are_chronological() -> None:
    df = _frame()
    wins = make_windows(df, holdout_days=60, n_windows=3,
                        min_train=400, min_eval=60)
    check("3 usable windows", len(wins) == 3, str(len(wins)))
    for w in wins:
        tr, ev = w["train"], w["eval"]
        check(f"{w['k']}: eval strictly after window start",
              bool((ev["date"] > w["start"]).all()) and bool((ev["date"] <= w["end"]).all()))
        check(f"{w['k']}: no eval row inside its train (leak guard)",
              len(tr.merge(ev[["date", "home_team", "away_team"]],
                           on=["date", "home_team", "away_team"])) == 0)
        check(f"{w['k']}: train never reaches into eval window",
              bool((tr["date"] <= w["start"]).all()))
    check("windows are nested backwards (w0 most recent)",
          wins[0]["start"] > wins[1]["start"] > wins[2]["start"])


# ── 7. production row filters (audit W1) ───────────────────────────────────
def test_load_frame_filters() -> None:
    cols = ["date", "home_team", "away_team", "home_goals", "away_goals",
            "league_slug", "is_international", "quality_score"]
    rows = [
        # valid club match
        ["2020-08-01", "H", "A", 2, 1, "lg", 0, 60],
        # international -> dropped
        ["2020-08-02", "H", "A", 2, 1, "lg", 1, 60],
        # low quality -> dropped
        ["2020-08-03", "H", "A", 2, 1, "lg", 0, 30],
        # pre-1993 -> dropped
        ["1990-01-01", "H", "A", 2, 1, "lg", 0, 60],
        # valid, second file without league_slug
        ["2021-08-01", "B", "C", 1, 1, None, 0, 60],
    ]
    with tempfile.TemporaryDirectory() as tmp:
        pd.DataFrame(rows, columns=cols).to_csv(os.path.join(tmp, "a.csv"),
                                                index=False)
        pd.DataFrame([["2021-08-01", "B", "C", 1, 1, 0, 60]],
                     columns=["date", "home_team", "away_team", "home_goals",
                              "away_goals", "is_international",
                              "quality_score"]).to_csv(
            os.path.join(tmp, "b.csv"), index=False)
        df = load_frame(os.path.join(tmp, "*.csv"))
    check("load_frame keeps only production-eligible rows",
          len(df) == 3, f"kept {len(df)} (want 3: 2 valid + 1 legacy)")
    check("no internationals survive",
          bool((pd.to_numeric(df["is_international"], errors="coerce")
                .fillna(0) == 0).all()))
    check("no quality<40 survives",
          bool((pd.to_numeric(df["quality_score"], errors="coerce")
                .fillna(0) >= 40).all()))
    check("date floor 1993-08-01 applied",
          bool((df["date"] >= pd.Timestamp("1993-08-01")).all()))
    d = df["date"].diff()
    check("rows sorted by date",
          bool((d.isna() | (d >= pd.Timedelta(0))).all()))


# ── 8. unknown teams are padded, counted, never dropped ────────────────────
def test_unknown_teams_padded() -> None:
    df = _frame(n=300)
    model = fit_strengths(df.iloc[:250], epochs=40, seed=1)
    worlds = pad_worlds([model])
    ev = df.iloc[250:].copy()
    ev.loc[ev.index[0], "home_team"] = "Newcomers FC"
    h, a, _lg, n_unknown = map_teams(ev, worlds)
    check("unknown team mapped to padded slot",
          n_unknown >= 1, f"n_unknown={n_unknown}")
    check("no row dropped", len(h) == len(ev) == len(a))
    dummy = len(model.teams)
    check("dummy index is the appended slot",
          int(h.min()) <= dummy <= int(max(h.max(), a.max())))
    check("known teams keep their index",
          int(h[1:].max()) < dummy and int(h[1:].min()) >= 0)


# ── 9. half-time + data facts sections ─────────────────────────────────────
def test_half_time_and_facts() -> None:
    from sim.realism import _half_time_section
    df = _frame(n=800)
    train, ev = df.iloc[:650].reset_index(drop=True), df.iloc[650:].reset_index(drop=True)
    model = fit_strengths(train, epochs=40, seed=2)
    halves = _half_time_section(pad_worlds([model]), train, ev)
    check("halftime coverage reported", 0.0 <= halves.get("coverage", 0) <= 1.0)
    check("halftime z finite and JSON-safe",
          all(isinstance(v, float) and np.isfinite(v)
              for v in halves.get("z", {}).values()),
          str(halves.get("z")))
    check("halftime pred probabilities sum to ~1",
          abs(halves["pred"]["ht_home_win"] + halves["pred"]["ht_draw"]
              + halves["pred"]["ht_away_win"] - 1) < 2e-4)   # 3 values rounded to 4dp
    check("p_first_half in (0,1)", 0.0 < halves.get("p_first_half", 0) < 1.0)
    no_ht = ev.drop(columns=["halftime_home", "halftime_away"])
    halves2 = _half_time_section(pad_worlds([model]), train, no_ht)
    check("missing halftime columns -> note, no crash",
          "note" in halves2 and "z" not in halves2)

    facts = _data_facts(ev)
    check("data facts quantify red cards",
          "red_cards" in facts and facts["red_cards"]["modelled"] is False)
    check("data facts quantify neutral venues",
          "neutral_venue" in facts and "share_neutral" in facts["neutral_venue"])
    facts2 = _data_facts(ev.drop(columns=["reds_home", "reds_away"]))
    check("missing reds columns -> zero share, no crash",
          facts2["red_cards"]["share_matches_with_red"] == 0.0)
    check("_num returns all-NaN for missing column",
          bool(_num(ev, "nope").isna().all()))


# ── 10. evaluate_window: full report, strictly JSON-serialisable ───────────
def test_evaluate_window_report() -> None:
    df = _frame(n=900)
    train, ev = df.iloc[:700].reset_index(drop=True), df.iloc[700:].reset_index(drop=True)
    model = fit_strengths(train, epochs=80, seed=1)
    rep = evaluate_window("T0", [model], train, ev,
                          model_source="test", seed=5, n_boot=150)
    try:
        json.dumps(rep, allow_nan=False)          # strict: numpy scalars/NaN bug out
        check("window report strictly JSON-serialisable", True)
    except (TypeError, ValueError) as e:
        check("window report strictly JSON-serialisable", False, str(e)[:200])
    check("all headline events reported",
          set(rep["global"]) == set(HEADLINE_EVENTS)
          | {"score_0_0", "score_1_0", "score_1_1", "score_2_1", "high_scoring"},
          str(sorted(rep["global"])))
    check("R1 z finite for every headline event",
          all(np.isfinite(rep["global"][e]["z"]) for e in HEADLINE_EVENTS))
    check("observed frequencies in [0,1] with ordered CIs",
          all(0 <= rep["global"][e]["obs"] <= 1
              and rep["global"][e]["obs_ci"][0] <= rep["global"][e]["obs"]
              <= rep["global"][e]["obs_ci"][1] for e in HEADLINE_EVENTS))
    check("predicted 1X2 sums to ~1",
          abs(sum(rep["global"][e]["pred"] for e in
                  ("home_win", "draw", "away_win")) - 1) < 2e-3)
    for key in ("home_goals", "away_goals", "total_goals"):
        check(f"moments report {key} with finite z",
              key in rep["moments"] and np.isfinite(rep["moments"][key]["z"]))
    check("moments include total variance (var_t fix)",
          "pred_var" in rep["moments"]["total_goals"])
    d = rep["distances"]
    check("W1 vs measured noise floor reported",
          all(k in d for k in ("w1_total", "w1_total_floor95",
                               "w1_gd", "w1_gd_floor95"))
          and np.isfinite(d["w1_total"]) and np.isfinite(d["w1_total_floor95"]))
    s = rep["scores"]
    check("proper scores for sim and league prior",
          s["sim"]["ll_1x2"] > 0 and s["league_prior"]["ll_1x2"] > 0)
    check("market baseline scored on identical rows",
          "market" in s and s["market"]["n"] > 50
          and s["market"]["ll_1x2"] > 0)
    check("LL difference carries a CI",
          len(s["ll_diff_sim_minus_prior_ci90"]) == 2
          and s["ll_diff_sim_minus_market_ci90"][0]
          <= s["ll_diff_sim_minus_market_ci90"][1])
    check("conditional groups computed (league n>=100 present)",
          any(g["n"] >= 100 for g in rep["groups"]["league"]),
          str([g["n"] for g in rep["groups"]["league"]]))
    check("tercile groups ordered low->mid->high",
          [g["value"] for g in rep["groups"]["pred_total_tercile"]]
          == ["low", "mid", "high"])
    check("verdict carries all four rules",
          set(rep["verdict"]) >= {"R1_calibration", "R2_shape", "R3_beats_prior",
                                  "R4_conditional", "window_pass",
                                  "worst_group_abs_z"})
    check("verdict values are plain bools",
          all(isinstance(rep["verdict"][k], bool) for k in
              ("R1_calibration", "R2_shape", "R3_beats_prior",
               "R4_conditional", "window_pass")))
    check("half-time section present with finite z",
          isinstance(rep["halves"].get("z", {}).get("ht_draw"), float))
    check("data facts present and marked unmodelled",
          rep["data_facts"]["red_cards"]["modelled"] is False)

    # unknown team inside the eval window is counted, not dropped
    ev2 = ev.copy()
    ev2.loc[ev2.index[0], "home_team"] = "Newcomers FC"
    rep2 = evaluate_window("T1", [model], train, ev2,
                           model_source="test", seed=5, n_boot=50)
    check("unknown-team rows counted in report",
          rep2["n_unknown_team_rows"] >= 1 and rep2["n_eval"] == len(ev2),
          f"unknown={rep2['n_unknown_team_rows']} n_eval={rep2['n_eval']}")


# ── 11. run_windows -> build_report -> render end-to-end ───────────────────
def test_run_windows_end_to_end() -> None:
    df = _frame(n=900)
    windows = run_windows(df, holdout_days=60, n_windows=3, recipe_path=None,
                          epochs=40, capdown_worlds=0, capdown_steps=0,
                          seed=3, n_boot=80, min_train=400, min_eval=60,
                          log=_quiet)
    check("run_windows produced 3 reports", len(windows) == 3, str(len(windows)))
    check("fresh-fit source recorded",
          all(w["model_source"] == "fresh_fit" for w in windows))
    report = build_report(tag="t0", windows=windows, df_len=len(df),
                          holdout_days=60, seed=3, epochs=40,
                          capdown_worlds=0, capdown_steps=0,
                          recipe_path=None, duration_s=0.1)
    try:
        json.dumps(report, allow_nan=False)
        check("full report strictly JSON-serialisable", True)
    except (TypeError, ValueError) as e:
        check("full report strictly JSON-serialisable", False, str(e)[:200])
    check("report carries schema + thresholds",
          report["schema"].startswith("statwise.sim.realism.")
          and set(report["meta"]["thresholds"]) == {"R1", "R2", "R3", "R4"})
    check("verdict counts windows",
          report["verdict"]["n_windows"] == 3
          and 0 <= report["verdict"]["passed"] <= 3)
    md = render_md(report, None)
    check("markdown renders with all sections",
          "Realism validation" in md and "## W0_" in md
          and "verdict: R1=" in md and "half-time" in md)

    # comparison against an identical report -> zero deltas
    comp = compare_reports(report, report)
    check("comparison yields zero deltas on identical reports",
          all(abs(v["ll_1x2"]) < 1e-12 and v["pass"] == 0
              for v in comp["deltas"].values()))
    md2 = render_md(report, comp)
    check("markdown renders with comparison table",
          "Comparison vs previous simulator version" in md2)

    # find_previous picks up another tag's report, never the current one
    with tempfile.TemporaryDirectory() as tmp:
        other = os.path.join(tmp, "realism_baseline.json")
        with open(other, "w", encoding="utf-8") as fh:
            json.dump({"meta": {"tag": "baseline"}}, fh)
        cur = os.path.join(tmp, "realism_t0.json")
        with open(cur, "w", encoding="utf-8") as fh:
            json.dump({"meta": {"tag": "t0"}}, fh)
        check("find_previous ignores the current tag",
              find_previous(tmp, "t0") == other)

    # cap-down wiring inside run_windows (tiny chain, seconds)
    windows2 = run_windows(df, holdout_days=60, n_windows=1, recipe_path=None,
                           epochs=30, capdown_worlds=1, capdown_steps=15,
                           seed=4, n_boot=40, min_train=400, min_eval=60,
                           log=_quiet)
    check("cap-down path wired into run_windows",
          len(windows2) == 1 and windows2[0]["model_source"].startswith(
              "fresh_fit+capdown"),
          windows2[0]["model_source"] if windows2 else "no windows")


# ── 12. empty windows never vacuously pass ─────────────────────────────────
def test_empty_report_fails() -> None:
    report = build_report(tag="x", windows=[], df_len=0, holdout_days=180,
                          seed=0, epochs=0, capdown_worlds=0, capdown_steps=0,
                          recipe_path=None, duration_s=0.0)
    check("0 windows is a FAIL, not a vacuous pass",
          report["verdict"]["all_windows_pass"] is False
          and report["verdict"]["n_windows"] == 0)


def main() -> int:
    print("Realism suite tests (synthetic, tiny, fast)")
    for fn in (
        test_bin_lookups,
        test_event_probs_match_market_probs,
        test_grid_moments,
        test_scoring_primitives,
        test_baselines,
        test_windows_are_chronological,
        test_load_frame_filters,
        test_unknown_teams_padded,
        test_half_time_and_facts,
        test_evaluate_window_report,
        test_run_windows_end_to_end,
        test_empty_report_fails,
    ):
        print(f"\n{fn.__name__}:")
        fn()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {', '.join(FAILURES)}")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
