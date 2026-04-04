"""
Feature engineering pipeline — 64-feature matrix for the 5-model stacking ensemble.

Feature groups:
  Elo (6) · Home/Away overall form (10+10) · Head-to-head (6)
  Goal probabilities (2) · Differentials (4) · Market/odds (4)
  Poisson attack/defense (4) · Venue-specific form (4) · Streaks (2)
  Form trends (2) · Scoring consistency (2) · H2H extended (2)
  League context (1) · Venue PPG differential (1)
  [NEW] Recent 3-match goals scored/conceded (4)
  Total: 64 features

Speed: build_training_set uses joblib threads so C++ form-vector calls
       run in parallel (they release the GIL).
"""
from __future__ import annotations
import logging, math
import numpy as np
import pandas as pd
from joblib import Parallel, delayed
from .cpp_bridge import (
    compute_elo_ratings_bulk, form_vector, h2h_stats,
    goal_probability, elo_probabilities,
)

logger = logging.getLogger(__name__)

MAX_TRAINING_SAMPLES = 100_000   # hard cap on training set size
_LOOKBACK            = 400       # history window per sample

FEATURE_NAMES = [
    # Elo (6)  indices 0-5
    "elo_home", "elo_away", "elo_diff",
    "elo_prob_home", "elo_prob_draw", "elo_prob_away",
    # Home team overall form (10)  indices 6-15
    "home_win_rate", "home_draw_rate", "home_loss_rate",
    "home_goals_scored", "home_goals_conceded", "home_goal_diff",
    "home_momentum", "home_ppg", "home_cs_rate", "home_scoring_rate",
    # Away team overall form (10)  indices 16-25
    "away_win_rate", "away_draw_rate", "away_loss_rate",
    "away_goals_scored", "away_goals_conceded", "away_goal_diff",
    "away_momentum", "away_ppg", "away_cs_rate", "away_scoring_rate",
    # Head-to-head (6)  indices 26-31
    "h2h_home_win", "h2h_draw", "h2h_away_win",
    "h2h_goals_home", "h2h_goals_away", "h2h_n_matches",
    # Goal probabilities via Poisson (2)  indices 32-33
    "p_over25", "p_btts",
    # Differentials (4)  indices 34-37
    "form_win_diff", "form_goals_diff",
    "momentum_diff", "ppg_diff",
    # Market / implied odds (4)  indices 38-41
    "odds_implied_home", "odds_implied_draw", "odds_implied_away",
    "market_overround",
    # Poisson attack/defense strengths (4)  indices 42-45
    "home_attack_strength", "away_attack_strength",
    "home_defense_strength", "away_defense_strength",
    # Venue-specific form (4)  indices 46-49
    "home_venue_win_rate", "home_venue_ppg",
    "away_venue_win_rate", "away_venue_ppg",
    # Current streak (2)  indices 50-51
    "home_streak", "away_streak",
    # Form trend – recent 5 vs prev 5 (2)  indices 52-53
    "home_form_trend", "away_form_trend",
    # Scoring consistency – inverse std-dev (2)  indices 54-55
    "home_scoring_consistency", "away_scoring_consistency",
    # H2H extended (2)  indices 56-57
    "h2h_avg_goals", "h2h_home_adv_factor",
    # League context (1)  index 58
    "league_avg_goals",
    # Venue-adjusted PPG differential (1)  index 59
    "venue_ppg_diff",
    # [NEW] Recent 3-match scoring / conceding (4)  indices 60-63
    "home_last3_goals",    "away_last3_goals",
    "home_last3_conceded", "away_last3_conceded",
]
N_FEATURES = len(FEATURE_NAMES)   # 64


# ─── helper functions ─────────────────────────────────────────────────────────

def _venue_form(matches: list, team: str, as_home: bool,
                lookback: int = 20) -> tuple:
    results = []
    for m in matches[-lookback:]:
        if as_home and m.get('home_team') == team:
            results.append((int(m.get('home_goals', 0)), int(m.get('away_goals', 0))))
        elif not as_home and m.get('away_team') == team:
            results.append((int(m.get('away_goals', 0)), int(m.get('home_goals', 0))))
    if not results:
        return 0.333, 1.0
    wins   = sum(1 for s, c in results if s > c)
    points = sum(3 if s > c else (1 if s == c else 0) for s, c in results)
    return wins / len(results), points / len(results)


def _current_streak(matches: list, team: str) -> float:
    streak = 0
    sign   = 0
    for m in reversed(matches[-12:]):
        if m.get('home_team') == team:
            s, c = int(m.get('home_goals', 0)), int(m.get('away_goals', 0))
        elif m.get('away_team') == team:
            s, c = int(m.get('away_goals', 0)), int(m.get('home_goals', 0))
        else:
            continue
        outcome = 1 if s > c else (-1 if s < c else 0)
        if streak == 0:
            streak = outcome
            sign   = outcome
        elif outcome == sign:
            streak += sign
        else:
            break
    return max(-1.0, min(1.0, streak / 5.0))


def _form_trend(matches: list, team: str) -> float:
    team_m = [m for m in matches if m.get('home_team') == team or m.get('away_team') == team][-12:]
    if len(team_m) < 6:
        return 0.0
    def _ppg(ms):
        if not ms: return 0.0
        pts = []
        for m in ms:
            hg = int(m.get('home_goals', 0))
            ag = int(m.get('away_goals', 0))
            if m.get('home_team') == team:
                pts.append(3 if hg > ag else (1 if hg == ag else 0))
            else:
                pts.append(3 if ag > hg else (1 if ag == hg else 0))
        return sum(pts) / len(pts)
    recent = team_m[-5:]
    older  = team_m[-10:-5] if len(team_m) >= 10 else team_m[:5]
    return _ppg(recent) - _ppg(older)


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
    h2h_m = [m for m in matches if {m.get('home_team'), m.get('away_team')} == {home, away}][-15:]
    if not h2h_m:
        return 2.6, 1.25
    totals    = [int(m.get('home_goals', 0)) + int(m.get('away_goals', 0)) for m in h2h_m]
    avg_goals = sum(totals) / len(totals)
    home_wins = sum(
        1 for m in h2h_m
        if ((m.get('home_team') == home and int(m.get('home_goals', 0)) > int(m.get('away_goals', 0))) or
            (m.get('away_team') == home and int(m.get('away_goals', 0)) > int(m.get('home_goals', 0))))
    )
    draws     = sum(1 for m in h2h_m if int(m.get('home_goals', 0)) == int(m.get('away_goals', 0)))
    away_wins = len(h2h_m) - home_wins - draws
    adv       = (home_wins + 0.5) / (away_wins + 0.5)
    return avg_goals, float(adv)


def _last_n_goals(matches: list, team: str, n: int = 3, scored: bool = True) -> float:
    """Average goals scored (or conceded) by `team` in the most recent n matches."""
    team_m = [m for m in reversed(matches[-12:])
              if m.get('home_team') == team or m.get('away_team') == team][:n]
    if not team_m:
        return 1.2
    vals = []
    for m in team_m:
        hg = int(m.get('home_goals', 0))
        ag = int(m.get('away_goals', 0))
        if m.get('home_team') == team:
            vals.append(hg if scored else ag)
        else:
            vals.append(ag if scored else hg)
    return sum(vals) / len(vals)


# ─── main pipeline ────────────────────────────────────────────────────────────

class FeaturePipeline:
    def __init__(self, home_advantage: float = 100.0):
        self.home_advantage = home_advantage
        self._elo_ratings:  dict = {}
        self._league_stats: dict = {}

    # ── fit ───────────────────────────────────────────────────────────────────

    def fit(self, df: pd.DataFrame) -> "FeaturePipeline":
        logger.info(f"Fitting feature pipeline on {len(df):,} matches…")
        df = df.dropna(subset=['home_team', 'away_team', 'home_goals', 'away_goals'])

        elo_h, elo_a = compute_elo_ratings_bulk(
            df['home_team'].tolist(), df['away_team'].tolist(),
            df['home_goals'].astype(int).tolist(), df['away_goals'].astype(int).tolist(),
            k_factor=32.0, home_advantage=self.home_advantage,
        )
        for team, elo in zip(df['home_team'].tolist(), elo_h):
            self._elo_ratings[team] = elo
        for team, elo in zip(df['away_team'].tolist(), elo_a):
            self._elo_ratings[team] = elo

        groupby_col = 'league_slug' if 'league_slug' in df.columns else None
        groups = df.groupby(groupby_col) if groupby_col else [('all', df)]
        for league_slug, grp in groups:
            avg_goals  = (grp['home_goals'] + grp['away_goals']).mean()
            home_avg   = grp['home_goals'].mean()
            away_avg   = grp['away_goals'].mean()
            self._league_stats[league_slug] = {
                'avg_goals':       avg_goals,
                'home_attack':     home_avg / max(avg_goals / 2, 1e-6),
                'away_attack':     away_avg / max(avg_goals / 2, 1e-6),
                'home_adv_factor': home_avg / max(away_avg, 1e-6),
            }
        logger.info("Feature pipeline fitted.")
        return self

    def _get_elo(self, team: str) -> float:
        return self._elo_ratings.get(team, 1500.0)

    def _get_league(self, league_slug: str) -> dict:
        return self._league_stats.get(
            league_slug,
            {'avg_goals': 2.6, 'home_attack': 1.0,
             'away_attack': 1.0, 'home_adv_factor': 1.25},
        )

    # ── single-match feature builder (list-based, no DataFrame overhead) ──────

    def _build_one(self, match: dict, history_list: list) -> np.ndarray:
        """Compute a single 64-d feature vector from a match dict + history list."""
        home   = match.get('home_team', '')
        away   = match.get('away_team', '')
        league = match.get('league_slug', 'all')
        ls     = self._get_league(league)

        # Elo
        elo_h = self._get_elo(home)
        elo_a = self._get_elo(away)
        ph, pd_, pa = elo_probabilities(elo_h, elo_a, self.home_advantage)

        # Form vectors (C++ / Python fallback)
        hform = form_vector(history_list, home)
        aform = form_vector(history_list, away)
        h2h   = h2h_stats(history_list, home, away)

        # Poisson strengths
        _half = max(ls['avg_goals'] / 2, 0.1)
        ha_str = hform[3] / _half
        hd_str = max(_half - hform[4], 0.1) / _half
        aa_str = aform[3] / _half
        ad_str = max(_half - aform[4], 0.1) / _half

        p25, pbtts = goal_probability(
            ha_str, ad_str, aa_str, hd_str,
            ls['avg_goals'], ls['home_adv_factor'],
        )

        # Market / implied odds
        odds_h = match.get('odds_home')
        odds_d = match.get('odds_draw')
        odds_a = match.get('odds_away')
        imp_h  = (1 / odds_h) if odds_h and odds_h > 1 else ph
        imp_d  = (1 / odds_d) if odds_d and odds_d > 1 else pd_
        imp_a  = (1 / odds_a) if odds_a and odds_a > 1 else pa
        overround = imp_h + imp_d + imp_a
        s = overround if overround > 1e-9 else 1.0
        imp_h /= s; imp_d /= s; imp_a /= s

        # Venue-specific form
        hv_wr, hv_ppg = _venue_form(history_list, home, as_home=True)
        av_wr, av_ppg = _venue_form(history_list, away, as_home=False)

        # Streaks
        h_str = _current_streak(history_list, home)
        a_str = _current_streak(history_list, away)

        # Form trends
        h_trend = _form_trend(history_list, home)
        a_trend = _form_trend(history_list, away)

        # Scoring consistency
        h_cons = _scoring_consistency(history_list, home)
        a_cons = _scoring_consistency(history_list, away)

        # H2H extended
        h2h_avg_g, h2h_adv = _h2h_extended(history_list, home, away)

        # League context
        league_avg = ls['avg_goals']

        # Venue PPG diff
        venue_ppg_d = hv_ppg - av_ppg

        # [NEW] Recent 3-match goals
        h_l3_scored   = _last_n_goals(history_list, home, n=3, scored=True)
        a_l3_scored   = _last_n_goals(history_list, away, n=3, scored=True)
        h_l3_conceded = _last_n_goals(history_list, home, n=3, scored=False)
        a_l3_conceded = _last_n_goals(history_list, away, n=3, scored=False)

        return np.array([
            # Elo (6)
            elo_h, elo_a, elo_h - elo_a, ph, pd_, pa,
            # Home form (10)
            hform[0], hform[1], hform[2], hform[3], hform[4],
            hform[5], hform[6], hform[7], hform[8], hform[9],
            # Away form (10)
            aform[0], aform[1], aform[2], aform[3], aform[4],
            aform[5], aform[6], aform[7], aform[8], aform[9],
            # H2H (6)
            h2h[0], h2h[1], h2h[2], h2h[3], h2h[4], h2h[5],
            # Goal probs (2)
            p25, pbtts,
            # Differentials (4)
            hform[0] - aform[0], hform[3] - aform[3],
            hform[6] - aform[6], hform[7] - aform[7],
            # Market (4)
            imp_h, imp_d, imp_a, overround,
            # Strengths (4)
            ha_str, aa_str, hd_str, ad_str,
            # Venue form (4)
            hv_wr, hv_ppg, av_wr, av_ppg,
            # Streaks (2)
            h_str, a_str,
            # Trends (2)
            h_trend, a_trend,
            # Consistency (2)
            h_cons, a_cons,
            # H2H extended (2)
            h2h_avg_g, h2h_adv,
            # League (1)
            league_avg,
            # Venue PPG diff (1)
            venue_ppg_d,
            # [NEW] Recent 3-match goals (4)
            h_l3_scored, a_l3_scored, h_l3_conceded, a_l3_conceded,
        ], dtype=np.float64)

    # ── public API: build_features (DataFrame input, for predict_match) ────────

    def build_features(self, matches: list, history: pd.DataFrame) -> np.ndarray:
        history_list = history.to_dict('records') if not history.empty else []
        return np.array([self._build_one(m, history_list) for m in matches])

    # ── public API: build_training_set (parallel, capped at MAX_TRAINING_SAMPLES)

    def build_training_set(self, df: pd.DataFrame,
                           max_samples: int = MAX_TRAINING_SAMPLES):
        """
        Build (X, y_1x2, y_goals) from historical DataFrame.

        Uses up to `max_samples` evenly-spaced samples across the full dataset.
        Feature computation runs in parallel threads (C++ releases GIL).
        """
        total   = len(df)
        records = df.to_dict('records')   # convert once — no per-sample DataFrame

        # Even subsample capped at max_samples
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

        # Worker: compute one sample — called from joblib threads
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
            hg     = int(match.get('home_goals', 0))
            ag     = int(match.get('away_goals', 0))
            label  = 0 if hg > ag else (1 if hg == ag else 2)
            goals  = 1 if (hg + ag) > 2.5 else 0
            return feat, label, goals

        # Parallel execution — threads share `records` and `self` read-only attributes
        results = Parallel(n_jobs=-1, prefer='threads', verbose=0)(
            delayed(_worker)(idx) for idx in all_idx
        )

        X      = np.array([r[0] for r in results], dtype=np.float64)
        y_1x2  = np.array([r[1] for r in results], dtype=np.int32)
        y_goals= np.array([r[2] for r in results], dtype=np.int32)

        logger.info(f"Feature matrix ready: {X.shape}")
        return X, y_1x2, y_goals
