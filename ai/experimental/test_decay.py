"""Stage 3 tests: time-decayed fit + production-aligned population.

Covers the two structural realism fixes from ai/sim/reports/diagnosis.md §5:
  * exponential recency weights in fit_strengths / deviance / MatchData
    (mu, home_adv, league offsets, att/def and xi all refit under the SAME
    decay — one time scale, no internally inconsistent hybrid);
  * run.load_frame's production-aligned filters (club only, quality >= 40,
    >= 1993-08-01) fixing audit W1.

Run: .venv/bin/python ai/experimental/test_decay.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0]))  # ai/

from sim.model import (  # noqa: E402
    StrengthModel,
    decay_weights,
    deviance,
    fit_strengths,
)
from sim.sampler import MatchData, gradient, objective, pack  # noqa: E402

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'} {name}" + (f"  [{detail}]" if detail else ""),
          flush=True)
    if not ok:
        FAILURES.append(name)


def _regime_frame(n_per: int = 600, seed: int = 3) -> pd.DataFrame:
    """A league where the favourite COLLAPSES midway through the history.

    First half: 'OldFC' scores ~3 goals/game.  Recent half (within any sane
    half-life): 'OldFC' concedes ~3 and scores ~0.3.  A flat fit averages
    the two regimes; a decayed fit must follow the recent one.  A second,
    era-STABLE league ('test-cup') anchors the league offsets so their
    recency shift is measurable (one league would project to exactly 0).
    """
    rng = np.random.default_rng(seed)
    teams = ["OldFC", "Alpha", "Beta", "Gamma", "Delta"]
    rows = []
    d0 = pd.Timestamp("2019-01-01")
    for k in range(n_per):
        era_recent = k >= n_per // 2
        date = d0 + pd.Timedelta(days=k)
        home, away = "OldFC", teams[1 + k % 4]
        if era_recent:
            lam_h, lam_a = 0.3, 2.8        # OldFC collapsed
        else:
            lam_h, lam_a = 3.0, 0.6        # OldFC dominant
        rows.append({"date": date, "home_team": home, "away_team": away,
                     "home_goals": rng.poisson(lam_h),
                     "away_goals": rng.poisson(lam_a),
                     "league_slug": "test-pl"})
    # stable second league so league_offset (zero-mean projected) can move
    for k in range(n_per):
        date = d0 + pd.Timedelta(days=k)
        rows.append({"date": date, "home_team": "Steady",
                     "away_team": "Anchor" if k % 2 else "Bold",
                     "home_goals": rng.poisson(2.2),
                     "away_goals": rng.poisson(1.9),
                     "league_slug": "test-cup"})
    return pd.DataFrame(rows).sort_values("date").reset_index(drop=True)


def main() -> int:
    # ── decay_weights ─────────────────────────────────────────────────────
    dates = pd.Series(["2026-01-01", "2024-01-01", "2022-01-01", None])
    w = decay_weights(dates, half_life_days=730.0)
    check("decay: newest row weight 1", w[0] == 1.0, f"{w[0]}")
    check("decay: ~1 half-life old -> 0.5", abs(w[1] - 0.5) < 0.02, f"{w[1]:.4f}")
    check("decay: ~2 half-lives old -> 0.25", abs(w[2] - 0.25) < 0.02, f"{w[2]:.4f}")
    check("decay: unparseable date -> 0", w[3] == 0.0)
    w_ref = decay_weights(dates, half_life_days=730.0,
                          ref_date=pd.Timestamp("2027-01-01"))
    check("decay: explicit ref_date shifts the clock",
          0.65 < w_ref[0] < 0.75, f"{w_ref[0]:.4f} (~0.707 = 2^-0.5)")
    w_flat = decay_weights(dates, half_life_days=1e12)
    check("decay: huge half-life -> ~uniform", np.allclose(w_flat[:3], 1.0))

    # ── fit follows the recent regime under decay, not flat ───────────────
    df = _regime_frame()
    m_flat = fit_strengths(df, epochs=200, seed=1)
    m_dec = fit_strengths(df, epochs=200, seed=1, half_life_days=365.0,
                          ref_date=df["date"].max())
    m_dec2 = fit_strengths(df, epochs=200, seed=1, half_life_days=180.0,
                           ref_date=df["date"].max())
    i_old = m_flat.t2i["OldFC"]
    # recency: the collapse era must pull OldFC's att DOWN relative to the
    # flat fit, and a shorter half-life must pull it down further (monotone)
    check("decay: att(OldFC) follows the recent collapse",
          m_dec.att[i_old] < m_flat.att[i_old] - 0.05,
          f"dec {m_dec.att[i_old]:.3f} vs flat {m_flat.att[i_old]:.3f}")
    check("decay: shorter half-life -> stronger recency pull",
          m_dec2.att[i_old] < m_dec.att[i_old] < m_flat.att[i_old],
          f"180d {m_dec2.att[i_old]:.3f} < 365d {m_dec.att[i_old]:.3f} "
          f"< flat {m_flat.att[i_old]:.3f}")
    # home_adv is refit under the SAME clock: the recent era has OldFC
    # conceding heavily at home, so the global home boost must shrink
    check("decay: home_adv refit under the same clock",
          m_dec.home_adv < m_flat.home_adv,
          f"dec {np.exp(m_dec.home_adv):.3f} vs flat {np.exp(m_flat.home_adv):.3f}")
    check("decay: league offset refit under the same clock",
          not np.allclose(m_dec.league_offset, m_flat.league_offset),
          f"dec {np.round(m_dec.league_offset, 4).tolist()} vs "
          f"flat {np.round(m_flat.league_offset, 4).tolist()}")
    # both fits stay normalised (identifiability projection still holds)
    check("decay: strengths stay zero-mean",
          abs(m_dec.att.mean()) < 1e-9 and abs(m_dec.defn.mean()) < 1e-9)
    check("decay: xi refit under the same clock",
          m_dec.xi in (-0.12, -0.09, -0.06, -0.03, 0.0, 0.03, 0.06))

    # half_life=None keeps the legacy path exactly (uniform weights)
    m_none = fit_strengths(df, epochs=200, seed=1, half_life_days=None)
    check("decay: half_life=None matches explicit-flat fit",
          np.allclose(m_none.att, m_flat.att)
          and abs(m_none.mu - m_flat.mu) < 1e-12
          and m_none.xi == m_flat.xi)

    # ── deviance honours weights ──────────────────────────────────────────
    m = m_flat
    h = df["home_team"].astype(str).map(m.t2i).to_numpy()
    a = df["away_team"].astype(str).map(m.t2i).to_numpy()
    lg = df["league_slug"].astype(str).map(m.l2i).fillna(0).to_numpy()
    y_h = df["home_goals"].astype(float).to_numpy()
    y_a = df["away_goals"].astype(float).to_numpy()
    ww = decay_weights(df["date"], 365.0)
    d_u = deviance(m, h, a, lg, y_h, y_a)
    d_w = deviance(m, h, a, lg, y_h, y_a, w=ww)
    lam_h, lam_a = m.rates(h, a, lg)
    from sim.model import tau_factor, _LOGF
    per_row = (lam_h - y_h * np.log(np.clip(lam_h, 1e-9, None))
               + lam_a - y_a * np.log(np.clip(lam_a, 1e-9, None))
               - np.log(tau_factor(y_h.astype(int), y_a.astype(int), m.xi)))
    d_manual = float((per_row * ww).sum())
    check("deviance: weighted value equals manual weighted sum",
          abs(d_w - d_manual) < 1e-9, f"{d_w:.6f} vs {d_manual:.6f}")
    check("deviance: weights change the objective",
          abs(d_w - d_u) > 1.0, f"flat {d_u:.1f} vs decayed {d_w:.1f}")
    check("deviance: uniform weight == no weight",
          abs(deviance(m, h, a, lg, y_h, y_a, w=np.ones(len(h))) - d_u) < 1e-9)

    # ── MatchData plumbing ────────────────────────────────────────────────
    data_u = MatchData.from_frame(df, m)
    data_w = MatchData.from_frame(df, m, half_life_days=365.0,
                                  ref_date=df["date"].max())
    check("MatchData: no half-life -> w is None", data_u.w is None)
    check("MatchData: half-life -> w aligned to kept rows",
          data_w.w is not None and len(data_w.w) == len(data_w))
    check("MatchData: w_sum = sum(w)",
          abs(data_w.w_sum() - float(data_w.w.sum())) < 1e-9)
    check("MatchData: uniform w_sum = n", data_u.w_sum() == float(len(data_u)))
    check("MatchData: objective honours weights",
          abs(objective(m, data_w) - (d_w + 0.02 * float(
              (m.att ** 2).sum() + (m.defn ** 2).sum()
              + (m.league_offset ** 2).sum()))) < 1e-6)
    sub = data_w.subsample(200, np.random.default_rng(0))
    check("MatchData: subsample keeps rows and weights aligned",
          len(sub) == 200 and sub.w is not None and len(sub.w) == 200)
    check("MatchData: subsample rescales weight mass to the full frame",
          abs(sub.w_sum() - data_w.w_sum()) < 1e-6,
          f"{sub.w_sum():.3f} vs {data_w.w_sum():.3f}")
    sub_u = data_u.subsample(200, np.random.default_rng(0))
    check("MatchData: uniform subsample w stays None", sub_u.w is None)
    check("MatchData: subsample(0) returns self",
          data_w.subsample(0, np.random.default_rng(0)) is data_w)

    # ── gradient data-term is the true gradient of the WEIGHTED objective ─
    # The sampler's gradient is a per-effective-row proposal heuristic
    # (divided by w_sum, penalty term l2*att rather than 2*l2*att) — only
    # the MH *objective* must be an exact posterior.  So test the data term
    # with l2=0, scaling the analytic gradient back up by w_sum, and bypass
    # unpack()'s to_dict rounding (it would quantise 1e-6 perturbations).
    m_t = fit_strengths(df.iloc[:300], epochs=30, seed=5, half_life_days=500.0,
                        ref_date=df["date"].max())
    d_t = MatchData.from_frame(df.iloc[:300], m_t, half_life_days=500.0,
                               ref_date=df["date"].max())
    d_t.l2 = 0.0                      # isolate the data term

    def _nudge(theta: np.ndarray) -> StrengthModel:
        mm = StrengthModel(m_t.teams, m_t.leagues)
        mm.att = theta[:len(m_t.teams)].copy()
        mm.defn = theta[len(m_t.teams):2 * len(m_t.teams)].copy()
        mm.mu = float(theta[2 * len(m_t.teams)])
        mm.home_adv = float(theta[2 * len(m_t.teams) + 1])
        mm.league_offset = theta[2 * len(m_t.teams) + 2:
                                 2 * len(m_t.teams) + 2 + len(m_t.leagues)].copy()
        mm.xi = float(theta[-1])
        return mm

    g_an = gradient(m_t, d_t) * d_t.w_sum()   # undo per-row scaling
    theta = pack(m_t)
    g_fd = np.zeros_like(theta)
    eps = 1e-6
    for i in range(theta.size):
        tp, tm = theta.copy(), theta.copy()
        tp[i] += eps
        tm[i] -= eps
        g_fd[i] = (objective(_nudge(tp), d_t)
                   - objective(_nudge(tm), d_t)) / (2 * eps)
    check("gradient: analytic == finite-difference under weights",
          np.allclose(g_an, g_fd, atol=2e-3),
          f"max |diff| {np.max(np.abs(g_an - g_fd)):.2e}")

    # ── run.load_frame production alignment (audit W1) ────────────────────
    with tempfile.TemporaryDirectory() as td:
        csv = os.path.join(td, "2020_matches.csv")
        pd.DataFrame([
            # date, home, away, goals, league, is_intl, quality
            {"date": "2020-03-01", "home_team": "A", "away_team": "B",
             "home_goals": 1, "away_goals": 0, "league_slug": "pl",
             "is_international": 0, "quality_score": 80},
            {"date": "2020-03-02", "home_team": "C", "away_team": "D",
             "home_goals": 2, "away_goals": 2, "league_slug": "pl",
             "is_international": 1, "quality_score": 90},      # intl -> drop
            {"date": "2020-03-03", "home_team": "E", "away_team": "F",
             "home_goals": 0, "away_goals": 1, "league_slug": "pl",
             "is_international": 0, "quality_score": 20},      # quality -> drop
            {"date": "1990-01-05", "home_team": "G", "away_team": "H",
             "home_goals": 3, "away_goals": 3, "league_slug": "pl",
             "is_international": 0, "quality_score": 80},      # date floor -> drop
            {"date": "2020-03-04", "home_team": "I", "away_team": "J",
             "home_goals": 1, "away_goals": 2, "league_slug": "pl",
             "is_international": 0, "quality_score": 40},      # quality 40 kept
        ]).to_csv(csv, index=False)
        out = subprocess.run(
            [sys.executable, str(HERE.parents[0] / "sim" / "run.py"),
             "--data-glob", os.path.join(td, "????_matches.csv"),
             "--epochs", "1", "--worlds", "1", "--steps", "2", "--warmup", "1",
             "--generations", "1", "--population", "2", "--sims", "5",
             "--synth-fixtures", "10", "--holdout-days", "3650",
             "--out", os.path.join(td, "shard.json")],
            capture_output=True, text=True, timeout=120)
        check("run.py: tiny shard completes on filtered frame",
              out.returncode == 0, (out.stderr or "")[-300:])
        check("run.py: drops internationals",
              ",/5 rows" in out.stdout or "/5 rows" in out.stdout,
              [ln for ln in out.stdout.splitlines() if "population" in ln][:1])

    # frame-level check of the same filters without the full shard
    from sim.run import load_frame  # noqa: E402
    with tempfile.TemporaryDirectory() as td:
        csv = os.path.join(td, "2020_matches.csv")
        pd.DataFrame([
            {"date": "2020-03-01", "home_team": "A", "away_team": "B",
             "home_goals": 1, "away_goals": 0, "league_slug": "pl",
             "is_international": 0, "quality_score": 80},
            {"date": "2020-03-02", "home_team": "C", "away_team": "D",
             "home_goals": 2, "away_goals": 2, "league_slug": "pl",
             "is_international": 1, "quality_score": 90},
            {"date": "2020-03-03", "home_team": "E", "away_team": "F",
             "home_goals": 0, "away_goals": 1, "league_slug": "pl",
             "is_international": 0, "quality_score": 20},
            {"date": "1990-01-05", "home_team": "G", "away_team": "H",
             "home_goals": 3, "away_goals": 3, "league_slug": "pl",
             "is_international": 0, "quality_score": 80},
            {"date": "2020-03-04", "home_team": "I", "away_team": "J",
             "home_goals": 1, "away_goals": 2, "league_slug": "pl",
             "is_international": 0, "quality_score": 40},
        ]).to_csv(csv, index=False)
        fr = load_frame(os.path.join(td, "????_matches.csv"))
        check("load_frame: keeps club rows at quality >= 40 since 1993",
              len(fr) == 2 and set(fr["home_team"]) == {"A", "I"},
              f"{len(fr)} rows: {sorted(fr['home_team'])}")
        # missing filter columns -> nothing extra dropped (legacy files)
        csv2 = os.path.join(td, "2021_matches.csv")
        pd.DataFrame([
            {"date": "2021-03-01", "home_team": "K", "away_team": "L",
             "home_goals": 1, "away_goals": 0, "league_slug": "pl"},
        ]).to_csv(csv2, index=False)
        fr2 = load_frame(os.path.join(td, "2021_matches.csv"))
        check("load_frame: legacy files without filter columns survive",
              len(fr2) == 1)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {', '.join(FAILURES)}")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
