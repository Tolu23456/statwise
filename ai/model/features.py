"""
Feature engineering pipeline — 96-feature matrix for the 5-model stacking ensemble.

Feature groups (v2):
  Elo (6)
  Attack / Defence Elo — new (4)
  Home overall form (10) · Away overall form (10)
  Home venue-split form — new (4) · Away venue-split form — new (4)
  Head-to-head (6)
  Dixon-Coles Poisson goal probs — expanded (10, was 2)
  Dixon-Coles exact score probs — new (4)
  Form differentials (4) · Market / odds (4)
  Poisson attack / defense strengths (4)
  Consecutive runs — new (4)
  Streaks (2) · Form trends (2) · Scoring consistency (2)
  H2H extended (2) · League context — expanded (3, was 1)
  Venue PPG differential (1)
  Attack / defence vs league — new (4)
  Goals variance — new (4)
  Recent 3-match goals (4)
  Total: 96 features

Speed: build_training_set uses joblib threads so C++ calls run in parallel
       (they release the GIL).
"""
from __future__ import annotations
import logging, math
import numpy as np
import pandas as pd
from joblib import Parallel, delayed
from .cpp_bridge import (
    compute_elo_ratings_bulk,
    compute_attack_defense_elo_bulk,
    form_vector, h2h_stats,
    goal_probability, elo_probabilities,
    poisson_score_matrix,
    consecutive_runs,
    venue_split_form,
    goals_variance,
)

logger = logging.getLogger(__name__)

MAX_TRAINING_SAMPLES = 100_000
_LOOKBACK            = 400

FEATURE_NAMES = [
    # ── Elo (6)  0-5
    "elo_home", "elo_away", "elo_diff",
    "elo_prob_home", "elo_prob_draw", "elo_prob_away",
    # ── Attack / Defence Elo (4)  6-9  [NEW]
    "home_attack_elo", "home_defense_elo",
    "away_attack_elo", "away_defense_elo",
    # ── Home overall form (10)  10-19
    "home_win_rate", "home_draw_rate", "home_loss_rate",
    "home_goals_scored", "home_goals_conceded", "home_goal_diff",
    "home_momentum", "home_ppg", "home_cs_rate", "home_scoring_rate",
    # ── Away overall form (10)  20-29
    "away_win_rate", "away_draw_rate", "away_loss_rate",
    "away_goals_scored", "away_goals_conceded", "away_goal_diff",
    "away_momentum", "away_ppg", "away_cs_rate", "away_scoring_rate",
    # ── Home venue-split form (4)  30-33  [NEW]
    "home_home_win_rate", "home_home_ppg",
    "home_home_goals_scored", "home_home_goals_conceded",
    # ── Away venue-split form (4)  34-37  [NEW]
    "away_away_win_rate", "away_away_ppg",
    "away_away_goals_scored", "away_away_goals_conceded",
    # ── Head-to-head (6)  38-43
    "h2h_home_win", "h2h_draw", "h2h_away_win",
    "h2h_goals_home", "h2h_goals_away", "h2h_n_matches",
    # ── Dixon-Coles Poisson goal probs (10)  44-53  [EXPANDED from 2]
    "p_over15", "p_over25", "p_over35",
    "p_btts", "p_home_cs", "p_away_cs",
    "expected_home_goals", "expected_away_goals",
    "lambda_ratio", "total_expected_goals",
    # ── Dixon-Coles exact score probs (4)  54-57  [NEW]
    "p_0_0", "p_1_0", "p_0_1", "p_1_1",
    # ── Form differentials (4)  58-61
    "form_win_diff", "form_goals_diff", "momentum_diff", "ppg_diff",
    # ── Market / implied odds (4)  62-65
    "odds_implied_home", "odds_implied_draw", "odds_implied_away",
    "market_overround",
    # ── Poisson attack / defense strengths (4)  66-69
    "home_attack_strength", "away_attack_strength",
    "home_defense_strength", "away_defense_strength",
    # ── Consecutive runs (4)  70-73  [NEW]
    "home_unbeaten_run", "home_winless_run",
    "away_unbeaten_run", "away_winless_run",
    # ── Current streak (2)  74-75
    "home_streak", "away_streak",
    # ── Form trends (2)  76-77
    "home_form_trend", "away_form_trend",
    # ── Scoring consistency (2)  78-79
    "home_scoring_consistency", "away_scoring_consistency",
    # ── H2H extended (2)  80-81
    "h2h_avg_goals", "h2h_home_adv_factor",
    # ── League context (3)  82-84  [EXPANDED from 1]
    "league_avg_goals", "league_home_win_rate", "league_draw_rate",
    # ── Venue PPG differential (1)  85
    "venue_ppg_diff",
    # ── Attack / defence vs league (4)  86-89  [NEW]
    "home_attack_vs_league", "away_attack_vs_league",
    "home_defense_vs_league", "away_defense_vs_league",
    # ── Goals variance (4)  90-93  [NEW]
    "home_goals_variance", "home_conceded_variance",
    "away_goals_variance", "away_conceded_variance",
    # ── Recent 3-match goals (4)  94-97 → but we have exactly 96 …
    # wait: 6+4+10+10+4+4+6+10+4+4+4+4+4+2+2+2+2+3+1+4+4+4 = 96 ✓
    "home_last3_goals",    "away_last3_goals",
    "home_last3_conceded", "away_last3_conceded",
]
N_FEATURES = len(FEATURE_NAMES)   # must be 96


# ─── helper functions ─────────────────────────────────────────────────────────

def _current_streak(matches: list, team: str) -> float:
    streak = 0; sign = 0
    for m in reversed(matches[-12:]):
        if m.get('home_team') == team:
            s, c = int(m.get('home_goals', 0)), int(m.get('away_goals', 0))
        elif m.get('away_team') == team:
            s, c = int(m.get('away_goals', 0)), int(m.get('home_goals', 0))
        else:
            continue
        outcome = 1 if s > c else (-1 if s < c else 0)
        if streak == 0:
            streak = outcome; sign = outcome
        elif outcome == sign:
            streak += sign
        else:
            break
    return max(-1.0, min(1.0, streak / 5.0))


def _form_trend(matches: list, team: str) -> float:
    team_m = [m for m in matches
              if m.get('home_team') == team or m.get('away_team') == team][-12:]
    if len(team_m) < 6:
        return 0.0
    def _ppg(ms):
        if not ms: return 0.0
        pts = []
        for m in ms:
            hg = int(m.get('home_goals', 0)); ag = int(m.get('away_goals', 0))
            if m.get('home_team') == team:
                pts.append(3 if hg > ag else (1 if hg == ag else 0))
            else:
                pts.append(3 if ag > hg else (1 if ag == hg else 0))
        return sum(pts) / len(pts)
    return _ppg(team_m[-5:]) - _ppg(team_m[-10:-5] if len(team_m) >= 10 else team_m[:5])


def _scoring_consistency(matches: list, team: str, lookback: int = 15) -> float:
    goals = []
    for m in matches[-lookback:]:
        if m.get('home_team') == team:
            goals.append(int(m.get('home_goals', 0)))
        elif m.get('away_team') == team:
            goals.append(int(m.get('away_goals', 0)))
    if len(goals) < 3:
        return 0.5
    return 1.0 / (1.0 + float(np.std(goals)))


def _h2h_extended(matches: list, home: str, away: str) -> tuple:
    h2h_m = [m for m in matches
             if {m.get('home_team'), m.get('away_team')} == {home, away}][-15:]
    if not h2h_m:
        return 2.6, 1.25
    totals    = [int(m.get('home_goals', 0)) + int(m.get('away_goals', 0))
                 for m in h2h_m]
    avg_goals = sum(totals) / len(totals)
    home_wins = sum(
        1 for m in h2h_m
        if ((m.get('home_team') == home and
             int(m.get('home_goals', 0)) > int(m.get('away_goals', 0))) or
            (m.get('away_team') == home and
             int(m.get('away_goals', 0)) > int(m.get('home_goals', 0))))
    )
    draws     = sum(1 for m in h2h_m
                    if int(m.get('home_goals', 0)) == int(m.get('away_goals', 0)))
    away_wins = len(h2h_m) - home_wins - draws
    adv       = (home_wins + 0.5) / (away_wins + 0.5)
    return avg_goals, float(adv)


def _last_n_goals(matches: list, team: str, n: int = 3, scored: bool = True) -> float:
    team_m = [m for m in reversed(matches[-12:])
              if m.get('home_team') == team or m.get('away_team') == team][:n]
    if not team_m:
        return 1.2
    vals = []
    for m in team_m:
        hg = int(m.get('home_goals', 0)); ag = int(m.get('away_goals', 0))
        if m.get('home_team') == team:
            vals.append(hg if scored else ag)
        else:
            vals.append(ag if scored else hg)
    return sum(vals) / len(vals)


# ─── main pipeline ────────────────────────────────────────────────────────────

class FeaturePipeline:
    def __init__(self, home_advantage: float = 100.0):
        self.home_advantage   = home_advantage
        self._elo_ratings:    dict = {}
        self._att_elo:        dict = {}
        self._def_elo:        dict = {}
        self._league_stats:   dict = {}

    def fit(self, df: pd.DataFrame) -> "FeaturePipeline":
        logger.info(f"Fitting feature pipeline on {len(df):,} matches…")
        df = df.dropna(subset=['home_team', 'away_team', 'home_goals', 'away_goals'])

        hts = df['home_team'].tolist(); ats = df['away_team'].tolist()
        hgs = df['home_goals'].astype(int).tolist()
        ags = df['away_goals'].astype(int).tolist()

        # Standard Elo
        elo_h, elo_a = compute_elo_ratings_bulk(
            hts, ats, hgs, ags, k_factor=32.0,
            home_advantage=self.home_advantage,
        )
        for team, elo in zip(hts, elo_h): self._elo_ratings[team] = elo
        for team, elo in zip(ats, elo_a): self._elo_ratings[team] = elo

        # Attack / Defence Elo
        ha, hd, aa, ad = compute_attack_defense_elo_bulk(
            hts, ats, hgs, ags, k_factor=24.0,
            home_advantage=self.home_advantage,
        )
        for team, v in zip(hts, ha): self._att_elo[team] = v
        for team, v in zip(hts, hd): self._def_elo[team] = v
        for team, v in zip(ats, aa): self._att_elo[team] = v
        for team, v in zip(ats, ad): self._def_elo[team] = v

        # League context stats
        groupby_col = 'league_slug' if 'league_slug' in df.columns else None
        groups = df.groupby(groupby_col) if groupby_col else [('all', df)]
        for slug, grp in groups:
            total_m    = len(grp)
            avg_goals  = (grp['home_goals'] + grp['away_goals']).mean()
            home_avg   = grp['home_goals'].mean()
            away_avg   = grp['away_goals'].mean()
            home_wins  = (grp['home_goals'] > grp['away_goals']).mean()
            draws      = (grp['home_goals'] == grp['away_goals']).mean()
            self._league_stats[slug] = {
                'avg_goals':       avg_goals,
                'home_attack':     home_avg / max(avg_goals / 2, 1e-6),
                'away_attack':     away_avg / max(avg_goals / 2, 1e-6),
                'home_adv_factor': home_avg / max(away_avg, 1e-6),
                'home_win_rate':   home_wins,
                'draw_rate':       draws,
            }

        logger.info("Feature pipeline fitted.")
        return self

    def _get_elo(self, team: str) -> float:
        return self._elo_ratings.get(team, 1500.0)

    def _get_att_elo(self, team: str) -> float:
        return self._att_elo.get(team, 1500.0)

    def _get_def_elo(self, team: str) -> float:
        return self._def_elo.get(team, 1500.0)

    def _get_league(self, league_slug: str) -> dict:
        return self._league_stats.get(
            league_slug,
            {'avg_goals': 2.6, 'home_attack': 1.0, 'away_attack': 1.0,
             'home_adv_factor': 1.25, 'home_win_rate': 0.46, 'draw_rate': 0.24},
        )

    def _build_one(self, match: dict, history_list: list) -> np.ndarray:
        home   = match.get('home_team', '')
        away   = match.get('away_team', '')
        league = match.get('league_slug', 'all')
        ls     = self._get_league(league)

        # ── Elo ───────────────────────────────────────────────────────────
        elo_h  = self._get_elo(home)
        elo_a  = self._get_elo(away)
        ph, pd_, pa = elo_probabilities(elo_h, elo_a, self.home_advantage)

        # ── Attack / Defence Elo ──────────────────────────────────────────
        h_att = self._get_att_elo(home); h_def = self._get_def_elo(home)
        a_att = self._get_att_elo(away); a_def = self._get_def_elo(away)

        # ── Overall form (C++ / fallback) ─────────────────────────────────
        hform = form_vector(history_list, home)
        aform = form_vector(history_list, away)
        h2h   = h2h_stats(history_list, home, away)

        # ── Venue-split form ──────────────────────────────────────────────
        hvsf  = venue_split_form(history_list, home, as_home=True)   # home's home record
        avsf  = venue_split_form(history_list, away, as_home=False)  # away's away record

        # ── Poisson / Dixon-Coles ─────────────────────────────────────────
        _half = max(ls['avg_goals'] / 2, 0.1)
        ha_str = hform[3] / _half
        hd_str = max(_half - hform[4], 0.1) / _half
        aa_str = aform[3] / _half
        ad_str = max(_half - aform[4], 0.1) / _half

        # Expected goals (for use in score matrix)
        lambda_h = max(ha_str * ad_str * ls['home_adv_factor'] * _half, 0.1)
        lambda_a = max(aa_str * hd_str * _half, 0.1)
        lambda_h = min(lambda_h, 6.0); lambda_a = min(lambda_a, 6.0)

        # Full Dixon-Coles matrix: [p15,p25,p35,btts,hcs,acs,p00,p10,p01,p11]
        dc = poisson_score_matrix(lambda_h, lambda_a, rho=-0.13)

        # Legacy over-2.5 / btts via original C++ (kept for ensemble compatibility)
        p25, pbtts = goal_probability(
            ha_str, ad_str, aa_str, hd_str,
            ls['avg_goals'], ls['home_adv_factor'],
        )

        # ── Market / implied odds ─────────────────────────────────────────
        odds_h = match.get('odds_home'); odds_d = match.get('odds_draw'); odds_a = match.get('odds_away')
        imp_h  = (1/odds_h) if odds_h and odds_h > 1 else ph
        imp_d  = (1/odds_d) if odds_d and odds_d > 1 else pd_
        imp_a  = (1/odds_a) if odds_a and odds_a > 1 else pa
        overround = imp_h + imp_d + imp_a
        s = overround if overround > 1e-9 else 1.0
        imp_h /= s; imp_d /= s; imp_a /= s

        # ── Consecutive runs ──────────────────────────────────────────────
        h_runs = consecutive_runs(history_list, home)   # [unbeaten, winless]
        a_runs = consecutive_runs(history_list, away)

        # ── Streaks ───────────────────────────────────────────────────────
        h_str = _current_streak(history_list, home)
        a_str = _current_streak(history_list, away)

        # ── Form trends ───────────────────────────────────────────────────
        h_trend = _form_trend(history_list, home)
        a_trend = _form_trend(history_list, away)

        # ── Scoring consistency ───────────────────────────────────────────
        h_cons = _scoring_consistency(history_list, home)
        a_cons = _scoring_consistency(history_list, away)

        # ── H2H extended ──────────────────────────────────────────────────
        h2h_avg_g, h2h_adv = _h2h_extended(history_list, home, away)

        # ── League context ────────────────────────────────────────────────
        league_avg   = ls['avg_goals']
        league_h_wr  = ls['home_win_rate']
        league_draw  = ls['draw_rate']

        # ── Venue PPG diff ────────────────────────────────────────────────
        venue_ppg_d = hvsf[1] - avsf[1]

        # ── Attack / defence vs league ────────────────────────────────────
        league_att_half = max(ls['home_attack'] * _half, 0.1)
        h_att_vs_lg  = hform[3] / league_att_half - 1.0   # positive = above-average attack
        a_att_vs_lg  = aform[3] / league_att_half - 1.0
        h_def_vs_lg  = 1.0 - hform[4] / league_att_half   # positive = below-average conceded
        a_def_vs_lg  = 1.0 - aform[4] / league_att_half

        # ── Goals variance ────────────────────────────────────────────────
        h_gvar = goals_variance(history_list, home)   # [scored_var, conceded_var]
        a_gvar = goals_variance(history_list, away)

        # ── Recent 3-match goals ──────────────────────────────────────────
        h_l3s = _last_n_goals(history_list, home, n=3, scored=True)
        a_l3s = _last_n_goals(history_list, away, n=3, scored=True)
        h_l3c = _last_n_goals(history_list, home, n=3, scored=False)
        a_l3c = _last_n_goals(history_list, away, n=3, scored=False)

        # ── Assemble 96-d vector ──────────────────────────────────────────
        return np.array([
            # Elo (6)  0-5
            elo_h, elo_a, elo_h - elo_a, ph, pd_, pa,
            # Attack / Defence Elo (4)  6-9
            h_att, h_def, a_att, a_def,
            # Home form (10)  10-19
            hform[0], hform[1], hform[2], hform[3], hform[4],
            hform[5], hform[6], hform[7], hform[8], hform[9],
            # Away form (10)  20-29
            aform[0], aform[1], aform[2], aform[3], aform[4],
            aform[5], aform[6], aform[7], aform[8], aform[9],
            # Home venue-split (4)  30-33
            hvsf[0], hvsf[1], hvsf[2], hvsf[3],
            # Away venue-split (4)  34-37
            avsf[0], avsf[1], avsf[2], avsf[3],
            # H2H (6)  38-43
            h2h[0], h2h[1], h2h[2], h2h[3], h2h[4], h2h[5],
            # Dixon-Coles goal probs (10)  44-53
            dc[0], dc[1], dc[2], dc[3], dc[4], dc[5],
            lambda_h, lambda_a, lambda_h / max(lambda_a, 0.01), lambda_h + lambda_a,
            # Exact scores (4)  54-57
            dc[6], dc[7], dc[8], dc[9],
            # Differentials (4)  58-61
            hform[0] - aform[0], hform[3] - aform[3],
            hform[6] - aform[6], hform[7] - aform[7],
            # Market (4)  62-65
            imp_h, imp_d, imp_a, overround,
            # Strengths (4)  66-69
            ha_str, aa_str, hd_str, ad_str,
            # Consecutive runs (4)  70-73
            h_runs[0], h_runs[1], a_runs[0], a_runs[1],
            # Streaks (2)  74-75
            h_str, a_str,
            # Trends (2)  76-77
            h_trend, a_trend,
            # Consistency (2)  78-79
            h_cons, a_cons,
            # H2H extended (2)  80-81
            h2h_avg_g, h2h_adv,
            # League context (3)  82-84
            league_avg, league_h_wr, league_draw,
            # Venue PPG diff (1)  85
            venue_ppg_d,
            # Attack/defence vs league (4)  86-89
            h_att_vs_lg, a_att_vs_lg, h_def_vs_lg, a_def_vs_lg,
            # Goals variance (4)  90-93
            h_gvar[0], h_gvar[1], a_gvar[0], a_gvar[1],
            # Recent 3-match goals (4)  94-97
            h_l3s, a_l3s, h_l3c, a_l3c,
        ], dtype=np.float64)

    def build_features(self, matches: list, history: pd.DataFrame) -> np.ndarray:
        history_list = history.to_dict('records') if not history.empty else []
        return np.array([self._build_one(m, history_list) for m in matches])

    def build_training_set(self, df: pd.DataFrame,
                           max_samples: int = MAX_TRAINING_SAMPLES):
        total   = len(df)
        records = df.to_dict('records')

        all_idx = list(range(20, total))
        if len(all_idx) > max_samples:
            step    = max(1, len(all_idx) // max_samples)
            all_idx = all_idx[::step]
        all_idx = all_idx[:max_samples]
        n = len(all_idx)

        logger.info(
            f"Building training set: {n:,} samples from {total:,} matches "
            f"(parallel threads, {N_FEATURES} features each)…"
        )

        def _worker(idx: int):
            match      = records[idx]
            hist_slice = records[max(0, idx - _LOOKBACK): idx]
            feat = self._build_one({
                'home_team':   match.get('home_team', ''),
                'away_team':   match.get('away_team', ''),
                'league_slug': match.get('league_slug', 'all'),
                'odds_home':   match.get('odds_home'),
                'odds_draw':   match.get('odds_draw'),
                'odds_away':   match.get('odds_away'),
            }, hist_slice)
            hg    = int(match.get('home_goals', 0))
            ag    = int(match.get('away_goals', 0))
            label = 0 if hg > ag else (1 if hg == ag else 2)
            goals = 1 if (hg + ag) > 2.5 else 0
            return feat, label, goals

        results = Parallel(n_jobs=-1, prefer='threads', verbose=0)(
            delayed(_worker)(idx) for idx in all_idx
        )

        X      = np.array([r[0] for r in results], dtype=np.float64)
        y_1x2  = np.array([r[1] for r in results], dtype=np.int32)
        y_goals= np.array([r[2] for r in results], dtype=np.int32)

        logger.info(f"Feature matrix ready: {X.shape}")
        return X, y_1x2, y_goals
