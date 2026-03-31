"""
Feature engineering pipeline: builds the feature matrix used by the XGBoost
ensemble. Calls the C++ library for Elo + form computation when available,
falls back to pure Python otherwise.
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
    "elo_home", "elo_away", "elo_diff",
    "elo_prob_home", "elo_prob_draw", "elo_prob_away",
    "home_win_rate", "home_draw_rate", "home_loss_rate",
    "home_goals_scored", "home_goals_conceded", "home_goal_diff",
    "home_momentum", "home_ppg", "home_cs_rate", "home_scoring_rate",
    "away_win_rate", "away_draw_rate", "away_loss_rate",
    "away_goals_scored", "away_goals_conceded", "away_goal_diff",
    "away_momentum", "away_ppg", "away_cs_rate", "away_scoring_rate",
    "h2h_home_win", "h2h_draw", "h2h_away_win",
    "h2h_goals_home", "h2h_goals_away", "h2h_n_matches",
    "p_over25", "p_btts",
    "form_win_diff", "form_goals_diff",
    "momentum_diff", "ppg_diff",
    "odds_implied_home", "odds_implied_draw", "odds_implied_away",
    "home_attack_strength", "away_attack_strength",
    "home_defense_strength", "away_defense_strength",
]
N_FEATURES = len(FEATURE_NAMES)


class FeaturePipeline:
    def __init__(self, home_advantage: float = 100.0):
        self.home_advantage = home_advantage
        self._elo_ratings: dict[str, float] = {}
        self._league_stats: dict[str, dict] = {}

    def fit(self, df: pd.DataFrame) -> "FeaturePipeline":
        """Compute Elo ratings and league statistics from historical data."""
        logger.info(f"Fitting feature pipeline on {len(df)} matches…")
        df = df.dropna(subset=['home_team', 'away_team', 'home_goals', 'away_goals'])

        # Compute rolling Elo
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
        for league_slug, grp in df.groupby('league_slug') if 'league_slug' in df.columns else [('all', df)]:
            avg_goals = (grp['home_goals'] + grp['away_goals']).mean()
            home_avg  = grp['home_goals'].mean()
            away_avg  = grp['away_goals'].mean()
            home_attack = home_avg / max(avg_goals / 2, 1e-6)
            away_attack = away_avg / max(avg_goals / 2, 1e-6)
            self._league_stats[league_slug] = {
                'avg_goals':    avg_goals,
                'home_attack':  home_attack,
                'away_attack':  away_attack,
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
            home = match.get('home_team', '')
            away = match.get('away_team', '')
            league = match.get('league_slug', 'all')
            ls = self._get_league(league)

            elo_h = self._get_elo(home)
            elo_a = self._get_elo(away)
            ph, pd_, pa = elo_probabilities(elo_h, elo_a, self.home_advantage)

            hform = form_vector(history_list, home)
            aform = form_vector(history_list, away)
            h2h   = h2h_stats(history_list, home, away)

            ha_strength = hform[3] / max(ls['avg_goals'] / 2, 0.1)
            hd_strength = max(ls['avg_goals'] / 2 - hform[4], 0.1) / (ls['avg_goals'] / 2)
            aa_strength = aform[3] / max(ls['avg_goals'] / 2, 0.1)
            ad_strength = max(ls['avg_goals'] / 2 - aform[4], 0.1) / (ls['avg_goals'] / 2)

            p25, pbtts = goal_probability(
                ha_strength, ad_strength,
                aa_strength, hd_strength,
                ls['avg_goals'], ls['home_adv_factor'],
            )

            odds_h = match.get('odds_home')
            odds_d = match.get('odds_draw')
            odds_a = match.get('odds_away')
            imp_h = (1 / odds_h) if odds_h and odds_h > 1 else ph
            imp_d = (1 / odds_d) if odds_d and odds_d > 1 else pd_
            imp_a = (1 / odds_a) if odds_a and odds_a > 1 else pa
            # normalise implied probs
            s = imp_h + imp_d + imp_a
            imp_h /= s; imp_d /= s; imp_a /= s

            X[i] = [
                elo_h, elo_a, elo_h - elo_a,
                ph, pd_, pa,
                hform[0], hform[1], hform[2],
                hform[3], hform[4], hform[5],
                hform[6], hform[7], hform[8], hform[9],
                aform[0], aform[1], aform[2],
                aform[3], aform[4], aform[5],
                aform[6], aform[7], aform[8], aform[9],
                h2h[0], h2h[1], h2h[2],
                h2h[3], h2h[4], h2h[5],
                p25, pbtts,
                hform[0] - aform[0],
                hform[3] - aform[3],
                hform[6] - aform[6],
                hform[7] - aform[7],
                imp_h, imp_d, imp_a,
                ha_strength, aa_strength,
                hd_strength, ad_strength,
            ]

        return X

    def build_training_set(self, df: pd.DataFrame, sample_every: int = 6):
        """
        Build (X, y_1x2, y_goals) for training from historical DataFrame.
        sample_every: only process every Nth match to keep O(n) manageable.
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
            # Use a fixed-size lookback window (last 300 matches) instead of
            # the full growing history — same informational value, much faster.
            history_df = df.iloc[max(0, idx - 300): idx]

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
