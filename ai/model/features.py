"""
Feature engineering pipeline: builds the feature matrix used by the XGBoost + LightGBM
ensemble. Calls the C++ library for Elo + form computation when available,
falls back to pure Python otherwise.

Feature count: 60 (was 45). New additions:
  - Venue-specific form (home team's home win rate/PPG; away team's away win rate/PPG)
  - Win/loss streak (normalized)
  - Form trend (recent 5 vs previous 5 PPG)
  - Scoring consistency (inverse std-dev of goals scored)
  - H2H average total goals & home advantage factor
  - League average goals
  - Venue-adjusted PPG differential
  - Market overround (bookmaker margin signal)
"""
from __future__ import annotations
import logging, math
import numpy as np
import pandas as pd
from .cpp_bridge import (
    compute_elo_ratings_bulk, form_vector, h2h_stats,
    goal_probability, elo_probabilities,
)

logger = logging.getLogger(__name__)

FEATURE_NAMES = [
    # Elo (6)
    "elo_home", "elo_away", "elo_diff",
    "elo_prob_home", "elo_prob_draw", "elo_prob_away",
    # Home team overall form (10)
    "home_win_rate", "home_draw_rate", "home_loss_rate",
    "home_goals_scored", "home_goals_conceded", "home_goal_diff",
    "home_momentum", "home_ppg", "home_cs_rate", "home_scoring_rate",
    # Away team overall form (10)
    "away_win_rate", "away_draw_rate", "away_loss_rate",
    "away_goals_scored", "away_goals_conceded", "away_goal_diff",
    "away_momentum", "away_ppg", "away_cs_rate", "away_scoring_rate",
    # Head-to-head (6)
    "h2h_home_win", "h2h_draw", "h2h_away_win",
    "h2h_goals_home", "h2h_goals_away", "h2h_n_matches",
    # Goal probabilities via Poisson (2)
    "p_over25", "p_btts",
    # Differentials (4)
    "form_win_diff", "form_goals_diff",
    "momentum_diff", "ppg_diff",
    # Market / implied odds (4)
    "odds_implied_home", "odds_implied_draw", "odds_implied_away",
    "market_overround",
    # Poisson attack/defense strengths (4)
    "home_attack_strength", "away_attack_strength",
    "home_defense_strength", "away_defense_strength",
    # NEW: Venue-specific form (4)
    "home_venue_win_rate", "home_venue_ppg",
    "away_venue_win_rate", "away_venue_ppg",
    # NEW: Current streak (2)
    "home_streak", "away_streak",
    # NEW: Form trend – recent 5 vs prev 5 (2)
    "home_form_trend", "away_form_trend",
    # NEW: Scoring consistency – inverse std-dev (2)
    "home_scoring_consistency", "away_scoring_consistency",
    # NEW: H2H extended (2)
    "h2h_avg_goals", "h2h_home_adv_factor",
    # NEW: League context (1)
    "league_avg_goals",
    # NEW: Venue-adjusted PPG differential (1)
    "venue_ppg_diff",
]
N_FEATURES = len(FEATURE_NAMES)   # 60


# ─────────────────────── helper functions (new features) ─────────────── #

def _venue_form(matches: list[dict], team: str, as_home: bool,
                lookback: int = 20) -> tuple[float, float]:
    """
    Returns (win_rate, ppg) for the team playing specifically at home or away.
    Neutral defaults: win_rate=0.333, ppg=1.0
    """
    results = []
    for m in matches[-lookback:]:
        if as_home and m.get('home_team') == team:
            scored, conceded = int(m.get('home_goals', 0)), int(m.get('away_goals', 0))
        elif not as_home and m.get('away_team') == team:
            scored, conceded = int(m.get('away_goals', 0)), int(m.get('home_goals', 0))
        else:
            continue
        results.append((scored, conceded))

    if not results:
        return 0.333, 1.0

    wins   = sum(1 for s, c in results if s > c)
    points = sum(3 if s > c else (1 if s == c else 0) for s, c in results)
    return wins / len(results), points / len(results)


def _current_streak(matches: list[dict], team: str) -> float:
    """
    Returns the current win/loss streak normalized to [-1, 1].
    Positive = winning, negative = losing.  Max streak counted: 5.
    """
    streak = 0
    sign = 0
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
            sign = outcome
        elif outcome == sign:
            streak += sign
        else:
            break

    return max(-1.0, min(1.0, streak / 5.0))


def _form_trend(matches: list[dict], team: str) -> float:
    """
    Recent-5 PPG minus previous-5 PPG for the given team.
    Positive = improving, negative = declining.
    """
    team_matches = [
        m for m in matches
        if m.get('home_team') == team or m.get('away_team') == team
    ][-12:]

    if len(team_matches) < 6:
        return 0.0

    def _ppg(ms: list[dict]) -> float:
        if not ms:
            return 0.0
        pts = []
        for m in ms:
            if m.get('home_team') == team:
                hg, ag = int(m.get('home_goals', 0)), int(m.get('away_goals', 0))
            else:
                hg, ag = int(m.get('away_goals', 0)), int(m.get('home_goals', 0))
            pts.append(3 if hg > ag else (1 if hg == ag else 0))
        return sum(pts) / len(pts)

    recent = team_matches[-5:]
    older  = team_matches[-10:-5] if len(team_matches) >= 10 else team_matches[:5]
    return _ppg(recent) - _ppg(older)


def _scoring_consistency(matches: list[dict], team: str,
                          lookback: int = 15) -> float:
    """
    Returns inverse std-dev of goals scored (higher = more consistent).
    Range approximately [0.2, 1.0].
    """
    goals = []
    for m in matches[-lookback:]:
        if m.get('home_team') == team:
            goals.append(int(m.get('home_goals', 0)))
        elif m.get('away_team') == team:
            goals.append(int(m.get('away_goals', 0)))

    if len(goals) < 3:
        return 0.5

    std = float(np.std(goals))
    return 1.0 / (1.0 + std)


def _h2h_extended(matches: list[dict], home: str, away: str) -> tuple[float, float]:
    """
    Returns (avg_total_goals, home_advantage_factor) for H2H matches.
    home_advantage_factor > 1 means home team tends to win; < 1 means away.
    """
    h2h_matches = [
        m for m in matches
        if {m.get('home_team'), m.get('away_team')} == {home, away}
    ][-15:]

    if not h2h_matches:
        return 2.6, 1.25

    totals = [int(m.get('home_goals', 0)) + int(m.get('away_goals', 0))
              for m in h2h_matches]
    avg_goals = sum(totals) / len(totals)

    home_wins = sum(
        1 for m in h2h_matches
        if ((m.get('home_team') == home and int(m.get('home_goals', 0)) > int(m.get('away_goals', 0))) or
            (m.get('away_team') == home and int(m.get('away_goals', 0)) > int(m.get('home_goals', 0))))
    )
    away_wins = len(h2h_matches) - home_wins - sum(
        1 for m in h2h_matches
        if int(m.get('home_goals', 0)) == int(m.get('away_goals', 0))
    )
    adv = (home_wins + 0.5) / (away_wins + 0.5)
    return avg_goals, float(adv)


# ─────────────────────── main pipeline ─────────────────────────────── #

class FeaturePipeline:
    def __init__(self, home_advantage: float = 100.0):
        self.home_advantage = home_advantage
        self._elo_ratings: dict[str, float] = {}
        self._league_stats: dict[str, dict] = {}

    def fit(self, df: pd.DataFrame) -> "FeaturePipeline":
        """Compute Elo ratings and league statistics from historical data."""
        logger.info(f"Fitting feature pipeline on {len(df)} matches…")
        df = df.dropna(subset=['home_team', 'away_team', 'home_goals', 'away_goals'])

        # Compute rolling Elo using C++ (or Python fallback)
        elo_h, elo_a = compute_elo_ratings_bulk(
            df['home_team'].tolist(),
            df['away_team'].tolist(),
            df['home_goals'].astype(int).tolist(),
            df['away_goals'].astype(int).tolist(),
            k_factor=32.0,
            home_advantage=self.home_advantage,
        )
        # Store final Elo per team
        for team, elo in zip(df['home_team'].tolist(), elo_h):
            self._elo_ratings[team] = elo
        for team, elo in zip(df['away_team'].tolist(), elo_a):
            self._elo_ratings[team] = elo

        # League-level stats
        groupby_col = 'league_slug' if 'league_slug' in df.columns else None
        groups = df.groupby(groupby_col) if groupby_col else [('all', df)]
        for league_slug, grp in groups:
            avg_goals  = (grp['home_goals'] + grp['away_goals']).mean()
            home_avg   = grp['home_goals'].mean()
            away_avg   = grp['away_goals'].mean()
            home_attack = home_avg / max(avg_goals / 2, 1e-6)
            away_attack = away_avg / max(avg_goals / 2, 1e-6)
            self._league_stats[league_slug] = {
                'avg_goals':       avg_goals,
                'home_attack':     home_attack,
                'away_attack':     away_attack,
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
             'away_attack': 1.0, 'home_adv_factor': 1.25}
        )

    def build_features(self, matches: list[dict],
                       history: pd.DataFrame) -> np.ndarray:
        """
        Build feature matrix for a list of upcoming (or training) matches.
        Each match dict: home_team, away_team, league_slug, [odds_home, odds_draw, odds_away]
        history: DataFrame of past matches for form/H2H lookups.
        """
        history_list = history.to_dict('records') if not history.empty else []
        X = np.zeros((len(matches), N_FEATURES))

        for i, match in enumerate(matches):
            home   = match.get('home_team', '')
            away   = match.get('away_team', '')
            league = match.get('league_slug', 'all')
            ls     = self._get_league(league)

            # ── Elo ────────────────────────────────────────────────────
            elo_h = self._get_elo(home)
            elo_a = self._get_elo(away)
            ph, pd_, pa = elo_probabilities(elo_h, elo_a, self.home_advantage)

            # ── Form vectors (C++ or Python, last 20 matches) ──────────
            hform = form_vector(history_list, home)
            aform = form_vector(history_list, away)
            h2h   = h2h_stats(history_list, home, away)

            # ── Poisson attack/defense strengths ───────────────────────
            _half_avg = max(ls['avg_goals'] / 2, 0.1)
            ha_strength = hform[3] / _half_avg
            hd_strength = max(_half_avg - hform[4], 0.1) / _half_avg
            aa_strength = aform[3] / _half_avg
            ad_strength = max(_half_avg - aform[4], 0.1) / _half_avg

            p25, pbtts = goal_probability(
                ha_strength, ad_strength,
                aa_strength, hd_strength,
                ls['avg_goals'], ls['home_adv_factor'],
            )

            # ── Market / implied odds ─────────────────────────────────
            odds_h = match.get('odds_home')
            odds_d = match.get('odds_draw')
            odds_a = match.get('odds_away')
            imp_h = (1 / odds_h) if odds_h and odds_h > 1 else ph
            imp_d = (1 / odds_d) if odds_d and odds_d > 1 else pd_
            imp_a = (1 / odds_a) if odds_a and odds_a > 1 else pa
            overround = imp_h + imp_d + imp_a  # before normalising
            s = overround if overround > 1e-9 else 1.0
            imp_h /= s; imp_d /= s; imp_a /= s

            # ── NEW: Venue-specific form ───────────────────────────────
            home_venue_wr, home_venue_ppg_ = _venue_form(history_list, home, as_home=True)
            away_venue_wr, away_venue_ppg_ = _venue_form(history_list, away, as_home=False)

            # ── NEW: Streaks ───────────────────────────────────────────
            home_str = _current_streak(history_list, home)
            away_str = _current_streak(history_list, away)

            # ── NEW: Form trends ───────────────────────────────────────
            home_trend = _form_trend(history_list, home)
            away_trend = _form_trend(history_list, away)

            # ── NEW: Scoring consistency ───────────────────────────────
            home_cons = _scoring_consistency(history_list, home)
            away_cons = _scoring_consistency(history_list, away)

            # ── NEW: H2H extended ──────────────────────────────────────
            h2h_avg_g, h2h_home_adv = _h2h_extended(history_list, home, away)

            # ── NEW: League avg goals ──────────────────────────────────
            league_avg = ls['avg_goals']

            # ── NEW: Venue PPG differential ────────────────────────────
            venue_ppg_d = home_venue_ppg_ - away_venue_ppg_

            X[i] = [
                # Elo (6)
                elo_h, elo_a, elo_h - elo_a,
                ph, pd_, pa,
                # Home form (10)
                hform[0], hform[1], hform[2],
                hform[3], hform[4], hform[5],
                hform[6], hform[7], hform[8], hform[9],
                # Away form (10)
                aform[0], aform[1], aform[2],
                aform[3], aform[4], aform[5],
                aform[6], aform[7], aform[8], aform[9],
                # H2H (6)
                h2h[0], h2h[1], h2h[2],
                h2h[3], h2h[4], h2h[5],
                # Goal probs (2)
                p25, pbtts,
                # Differentials (4)
                hform[0] - aform[0],
                hform[3] - aform[3],
                hform[6] - aform[6],
                hform[7] - aform[7],
                # Market (4)
                imp_h, imp_d, imp_a,
                overround,
                # Strengths (4)
                ha_strength, aa_strength,
                hd_strength, ad_strength,
                # Venue form (4)
                home_venue_wr, home_venue_ppg_,
                away_venue_wr, away_venue_ppg_,
                # Streaks (2)
                home_str, away_str,
                # Trends (2)
                home_trend, away_trend,
                # Consistency (2)
                home_cons, away_cons,
                # H2H extended (2)
                h2h_avg_g, h2h_home_adv,
                # League (1)
                league_avg,
                # Venue PPG diff (1)
                venue_ppg_d,
            ]

        return X

    def build_training_set(self, df: pd.DataFrame, sample_every: int = 3):
        """
        Build (X, y_1x2, y_goals) for training from historical DataFrame.
        sample_every: only process every Nth match (3 = use 2x more data than before).
        """
        total_matches = len(df)
        logger.info(
            f"Building training set from {total_matches} historical matches "
            f"(sampling every {sample_every}th → ~{total_matches // sample_every} samples)…"
        )
        records = df.to_dict('records')
        X_list, y_1x2, y_goals = [], [], []
        indices = list(range(20, total_matches, sample_every))
        n = len(indices)

        for count, idx in enumerate(indices):
            match = records[idx]
            # Use a lookback window of 400 matches (was 300) for richer context
            history_df = df.iloc[max(0, idx - 400): idx]

            feat = self.build_features([{
                'home_team':   match['home_team'],
                'away_team':   match['away_team'],
                'league_slug': match.get('league_slug', 'all'),
                'odds_home':   match.get('odds_home'),
                'odds_draw':   match.get('odds_draw'),
                'odds_away':   match.get('odds_away'),
            }], history_df)[0]

            hg, ag = int(match['home_goals']), int(match['away_goals'])
            result = 0 if hg > ag else (1 if hg == ag else 2)
            total  = hg + ag
            y_1x2.append(result)
            y_goals.append(1 if total > 2.5 else 0)
            X_list.append(feat)

            if (count + 1) % 200 == 0 or (count + 1) == n:
                pct = (count + 1) / n * 100
                logger.info(
                    f"  Feature engineering: {count + 1}/{n} samples "
                    f"({pct:.0f}%) — {len(X_list)} rows built"
                )

        X = np.array(X_list)
        logger.info(f"Feature matrix ready: {X.shape}")
        return X, np.array(y_1x2), np.array(y_goals)
