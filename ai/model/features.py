"""
Grandmaster Feature engineering pipeline — 126-feature matrix.
Optimized with Bulk C++ Engine v4 + League Embeddings.
"""
from __future__ import annotations
import logging, math
import numpy as np
import pandas as pd
from .cpp_bridge import (
    compute_elo_ratings_bulk,
    compute_attack_defense_elo_bulk,
    elo_probabilities,
    compute_all_features_bulk_v4,
)

logger = logging.getLogger(__name__)

MAX_TRAINING_SAMPLES = 120_000
_LOOKBACK            = 600

# Continuous features (0-124) + Categorical (League ID at 125)
FEATURE_NAMES = [
    # ... existing 125 features ...
    "elo_home", "elo_away", "elo_diff", "elo_prob_home", "elo_prob_draw", "elo_prob_away",
    "home_attack_elo", "home_defense_elo", "away_attack_elo", "away_defense_elo",
    "home_win_rate", "home_draw_rate", "home_loss_rate", "home_goals_scored", "home_goals_conceded",
    "home_goal_diff", "home_momentum", "home_ppg", "home_cs_rate", "home_scoring_rate",
    "away_win_rate", "away_draw_rate", "away_loss_rate", "away_goals_scored", "away_goals_conceded",
    "away_goal_diff", "away_momentum", "away_ppg", "away_cs_rate", "away_scoring_rate",
    "home_home_win_rate", "home_home_ppg", "home_home_goals_scored", "home_home_goals_conceded",
    "away_away_win_rate", "away_away_ppg", "away_away_goals_scored", "away_away_goals_conceded",
    "h2h_home_win", "h2h_draw", "h2h_away_win", "h2h_goals_home", "h2h_goals_away", "h2h_n_matches",
    "p_over15", "p_over25", "p_over35", "p_btts", "p_home_cs", "p_away_cs",
    "lambda_h", "lambda_a", "lambda_ratio", "total_expected_goals",
    "p_0_0", "p_1_0", "p_0_1", "p_1_1",
    "form_win_diff", "form_goals_diff", "momentum_diff", "ppg_diff",
    "odds_implied_home", "odds_implied_draw", "odds_implied_away", "market_overround",
    "ha_strength", "aa_strength", "hd_strength", "ad_strength",
    "home_unbeaten", "home_winless", "away_unbeaten", "away_winless",
    "home_streak", "away_streak", "home_trend", "away_trend", "home_consistency", "away_consistency",
    "h2h_avg_goals", "h2h_adv", "league_avg", "league_h_wr", "league_draw",
    "venue_ppg_diff", "h_att_vs_lg", "a_att_vs_lg", "h_def_vs_lg", "a_def_vs_lg",
    "h_gvar_s", "h_gvar_c", "a_gvar_s", "a_gvar_c", "h_l3s", "a_l3s", "h_l3c", "a_l3c",
    "days_h", "days_a", "season_stage", "h_dr", "a_dr", "has_odds",
    "h_win_mkt_diff", "a_win_mkt_diff", "h_s_a_c_diff", "a_s_h_c_diff", "h_mom_ppg_int", "a_mom_ppg_int",
    "home_elo_volatility", "away_elo_volatility", "home_ppg_accel", "away_ppg_accel",
    "elo_form_interaction", "market_volatility_interaction",
    "pad_1", "pad_2", "pad_3", "pad_4", "pad_5", "pad_6", "pad_7", "pad_8", "pad_9",
    # ── League Embedding Index (125)
    "league_id"
]
N_FEATURES = 126

class FeaturePipeline:
    def __init__(self, home_advantage: float = 100.0):
        self.home_advantage = home_advantage
        self._league_stats = {}
        self._league_map = {} # Maps slug to ID
        self._elo_ratings = {}
        self._att_elo = {}
        self._def_elo = {}
        self._elo_pre_home = None
        self._elo_pre_away = None
        self._att_pre_home = None
        self._def_pre_home = None
        self._att_pre_away = None
        self._def_pre_away = None

    def fit(self, df: pd.DataFrame) -> "FeaturePipeline":
        logger.info(f"Fitting Grandmaster pipeline on {len(df):,} matches…")
        df = df.dropna(subset=['home_team', 'away_team', 'home_goals', 'away_goals'])

        # League mapping
        all_leagues = sorted(df['league_slug'].fillna('all').unique())
        self._league_map = {slug: i for i, slug in enumerate(all_leagues)}

        n = len(df)
        hts, ats = df['home_team'].tolist(), df['away_team'].tolist()
        hgs, ags = df['home_goals'].astype(int).tolist(), df['away_goals'].astype(int).tolist()

        ratings, att_r, def_r = {}, {}, {}
        pre_elo_h, pre_elo_a = np.zeros(n, dtype=np.float32), np.zeros(n, dtype=np.float32)
        pre_att_h, pre_def_h = np.zeros(n, dtype=np.float32), np.zeros(n, dtype=np.float32)
        pre_att_a, pre_def_a = np.zeros(n, dtype=np.float32), np.zeros(n, dtype=np.float32)

        for i in range(n):
            h, a = hts[i], ats[i]; hg, ag = hgs[i], ags[i]
            rh, ra = ratings.get(h, 1500.0), ratings.get(a, 1500.0)
            pre_elo_h[i], pre_elo_a[i] = rh, ra
            eh = 1.0 / (1.0 + 10.0 ** ((ra - (rh + self.home_advantage)) / 400.0))
            sh = 1.0 if hg > ag else (0.5 if hg == ag else 0.0)
            ek = 32.0 * min(1.0 + 0.5 * max(abs(hg-ag)-1, 0), 3.0)
            ratings[h], ratings[a] = rh + ek*(sh-eh), ra + ek*((1-sh)-(1-eh))

            ah, dh = att_r.get(h, 1500.0), def_r.get(h, 1500.0)
            aa, da = att_r.get(a, 1500.0), def_r.get(a, 1500.0)
            pre_att_h[i], pre_def_h[i], pre_att_a[i], pre_def_a[i] = ah, dh, aa, da
            e_ha = 1.0 / (1.0 + 10.0 ** ((da - ah - 30.0) / 300.0))
            e_aa = 1.0 / (1.0 + 10.0 ** ((dh - aa + 30.0) / 300.0))
            att_r[h], def_r[h] = ah + 24.0*(min(hg/2.5, 1.0)-e_ha), dh + 24.0*(max(0.0, 1.0-ag/2.5)-(1-e_aa))
            att_r[a], def_r[a] = aa + 24.0*(min(ag/2.5, 1.0)-e_aa), da + 24.0*(max(0.0, 1.0-hg/2.5)-(1-e_ha))

        self._elo_pre_home, self._elo_pre_away = pre_elo_h, pre_elo_a
        self._att_pre_home, self._def_pre_home = pre_att_h, pre_def_h
        self._att_pre_away, self._def_pre_away = pre_att_a, pre_def_a

        elo_h_bulk, elo_a_bulk = compute_elo_ratings_bulk(hts, ats, hgs, ags, 32.0, self.home_advantage)
        for t, e in zip(hts, elo_h_bulk): self._elo_ratings[t] = e
        for t, e in zip(ats, elo_a_bulk): self._elo_ratings[t] = e

        groups = df.groupby('league_slug') if 'league_slug' in df.columns else [('all', df)]
        for slug, grp in groups:
            self._league_stats[slug] = {
                'avg_goals': (grp['home_goals'] + grp['away_goals']).mean(),
                'home_attack': grp['home_goals'].mean() / max((grp['home_goals'] + grp['away_goals']).mean() / 2, 1e-6),
                'away_attack': grp['away_goals'].mean() / max((grp['home_goals'] + grp['away_goals']).mean() / 2, 1e-6),
                'home_adv_factor': grp['home_goals'].mean() / max(grp['away_goals'].mean(), 1e-6),
                'home_win_rate': (grp['home_goals'] > grp['away_goals']).mean(),
                'draw_rate': (grp['home_goals'] == grp['away_goals']).mean(),
            }
        return self

    def build_training_set(self, df: pd.DataFrame, max_samples: int = MAX_TRAINING_SAMPLES):
        total = len(df)
        all_idx = list(range(20, total))
        if len(all_idx) > max_samples: all_idx = all_idx[-max_samples:]
        n_targets = len(all_idx)
        logger.info(f"Building Grandmaster set: {n_targets:,} samples (BULK C++ v4 + League IDs)")

        teams = pd.concat([df['home_team'], df['away_team']]).unique()
        team_map = {name: i for i, name in enumerate(teams)}
        all_gh, all_ga = df['home_goals'].values.astype(np.int32), df['away_goals'].values.astype(np.int32)
        all_ts = pd.to_datetime(df['date']).values.astype(np.int64) / 1e9
        all_h_idx, all_a_idx = df['home_team'].map(team_map).values.astype(np.int32), df['away_team'].map(team_map).values.astype(np.int32)

        # Pre-ELO snapshots for bulk engine
        all_pre_elos = np.zeros((len(df), 6), dtype=np.float64)
        all_pre_elos[:, 0] = self._elo_pre_home; all_pre_elos[:, 1] = self._elo_pre_away; all_pre_elos[:, 2] = self._elo_pre_home - self._elo_pre_away
        for i in range(len(df)):
            all_pre_elos[i, 3:6] = elo_probabilities(self._elo_pre_home[i], self._elo_pre_away[i], self.home_advantage)

        all_pre_att_def = np.zeros((len(df), 4), dtype=np.float64)
        all_pre_att_def[:, 0] = self._att_pre_home; all_pre_att_def[:, 1] = self._def_pre_home; all_pre_att_def[:, 2] = self._att_pre_away; all_pre_att_def[:, 3] = self._def_pre_away
        all_odds = df[['odds_home', 'odds_draw', 'odds_away']].fillna(0).values.astype(np.float64)
        all_league_stats = np.zeros((len(df), 6), dtype=np.float64)
        for i, slug in enumerate(df['league_slug'].fillna('all')):
            ls = self._league_stats.get(slug, {'avg_goals': 2.6, 'home_attack': 1.0, 'away_attack': 1.0, 'home_adv_factor': 1.25, 'home_win_rate': 0.46, 'draw_rate': 0.24})
            all_league_stats[i] = [ls['avg_goals'], ls['home_attack'], ls['away_attack'], ls['home_adv_factor'], ls['home_win_rate'], ls['draw_rate']]

        team_matches = [[] for _ in range(len(teams))]
        for i in range(len(df)):
            team_matches[all_h_idx[i]].append(i); team_matches[all_a_idx[i]].append(i)
        flat_m, t_ptr, t_cnt, curr = [], [], [], 0
        for ml in team_matches:
            t_ptr.append(curr); t_cnt.append(len(ml)); flat_m.extend(ml); curr += len(ml)

        # 1. Continuous block (125)
        X_cont = compute_all_features_bulk_v4(np.array(all_idx, dtype=np.int32), all_gh, all_ga, all_ts, all_h_idx, all_a_idx, all_pre_elos, all_pre_att_def, all_odds, all_league_stats, np.array(flat_m, dtype=np.int32), np.array(t_ptr, dtype=np.int32), np.array(t_cnt, dtype=np.int32), self._elo_pre_home, self._elo_pre_away, _LOOKBACK, self.home_advantage)

        # 2. League IDs (1)
        league_ids = df['league_slug'].fillna('all').map(self._league_map).values[all_idx].reshape(-1, 1)

        # 3. Concatenate to 126 features
        X = np.hstack([X_cont, league_ids])

        y_1x2 = np.where(all_gh[all_idx] > all_ga[all_idx], 0, np.where(all_gh[all_idx] == all_ga[all_idx], 1, 2)).astype(np.int32)
        y_goals = ((all_gh[all_idx] + all_ga[all_idx]) > 2.5).astype(np.int32)
        return X, y_1x2, y_goals, df['date'].values[all_idx]

    def build_features(self, matches: list, history: pd.DataFrame) -> np.ndarray:
        return np.zeros((len(matches), N_FEATURES))
