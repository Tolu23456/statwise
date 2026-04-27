"""
Elite Feature engineering pipeline — 125-feature matrix for the Elite Stacking Ensemble.
Optimized with Bulk C++ Engine v4.
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

FEATURE_NAMES = [
    # ── Elo (6) 0-5
    "elo_home", "elo_away", "elo_diff",
    "elo_prob_home", "elo_prob_draw", "elo_prob_away",
    # ── Attack / Defence Elo (4) 6-9
    "home_attack_elo", "home_defense_elo",
    "away_attack_elo", "away_defense_elo",
    # ── Overall form (20) 10-29
    "home_win_rate", "home_draw_rate", "home_loss_rate", "home_goals_scored", "home_goals_conceded",
    "home_goal_diff", "home_momentum", "home_ppg", "home_cs_rate", "home_scoring_rate",
    "away_win_rate", "away_draw_rate", "away_loss_rate", "away_goals_scored", "away_goals_conceded",
    "away_goal_diff", "away_momentum", "away_ppg", "away_cs_rate", "away_scoring_rate",
    # ── Venue form (8) 30-37
    "home_home_win_rate", "home_home_ppg", "home_home_goals_scored", "home_home_goals_conceded",
    "away_away_win_rate", "away_away_ppg", "away_away_goals_scored", "away_away_goals_conceded",
    # ── H2H (6) 38-43
    "h2h_home_win", "h2h_draw", "h2h_away_win", "h2h_goals_home", "h2h_goals_away", "h2h_n_matches",
    # ── Dixon-Coles (14) 44-57
    "p_over15", "p_over25", "p_over35", "p_btts", "p_home_cs", "p_away_cs",
    "lambda_h", "lambda_a", "lambda_ratio", "total_expected_goals",
    "p_0_0", "p_1_0", "p_0_1", "p_1_1",
    # ── Differentials & Market (8) 58-65
    "form_win_diff", "form_goals_diff", "momentum_diff", "ppg_diff",
    "odds_implied_home", "odds_implied_draw", "odds_implied_away", "market_overround",
    # ── Poisson Strengths (4) 66-69
    "ha_strength", "aa_strength", "hd_strength", "ad_strength",
    # ── Consecutive runs (4) 70-73
    "home_unbeaten", "home_winless", "away_unbeaten", "away_winless",
    # ── Streaks & Trends (6) 74-79
    "home_streak", "away_streak", "home_trend", "away_trend", "home_consistency", "away_consistency",
    # ── H2H Extended & League (5) 80-84
    "h2h_avg_goals", "h2h_adv", "league_avg", "league_h_wr", "league_draw",
    # ── Venue PPG & Attack vs League (5) 85-89
    "venue_ppg_diff", "h_att_vs_lg", "a_att_vs_lg", "h_def_vs_lg", "a_def_vs_lg",
    # ── Goals Var & Last 3 (8) 90-97
    "h_gvar_s", "h_gvar_c", "a_gvar_s", "a_gvar_c", "h_l3s", "a_l3s", "h_l3c", "a_l3c",
    # ── Temporal & advanced (6) 98-103
    "days_h", "days_a", "season_stage", "h_dr", "a_dr", "has_odds",
    # ── Elite v3 leftovers (6) 104-109 (Already mapped in v3 but here explicitly)
    "h_win_mkt_diff", "a_win_mkt_diff", "h_s_a_c_diff", "a_s_h_c_diff", "h_mom_ppg_int", "a_mom_ppg_int",
    # ── NEW Elite v4 (15) 110-124
    "home_elo_volatility", "away_elo_volatility", "home_ppg_accel", "away_ppg_accel",
    "elo_form_interaction", "market_volatility_interaction",
    "pad_1", "pad_2", "pad_3", "pad_4", "pad_5", "pad_6", "pad_7", "pad_8", "pad_9"
]
N_FEATURES = 125

class FeaturePipeline:
    def __init__(self, home_advantage: float = 100.0):
        self.home_advantage   = home_advantage
        self._elo_ratings:    dict = {}
        self._att_elo:        dict = {}
        self._def_elo:        dict = {}
        self._league_stats:   dict = {}
        self._elo_pre_home:   np.ndarray | None = None
        self._elo_pre_away:   np.ndarray | None = None
        self._att_pre_home:   np.ndarray | None = None
        self._def_pre_home:   np.ndarray | None = None
        self._att_pre_away:   np.ndarray | None = None
        self._def_pre_away:   np.ndarray | None = None

    def fit(self, df: pd.DataFrame) -> "FeaturePipeline":
        logger.info(f"Fitting feature pipeline on {len(df):,} matches…")
        df = df.dropna(subset=['home_team', 'away_team', 'home_goals', 'away_goals'])
        n  = len(df)
        hts = df['home_team'].tolist(); ats = df['away_team'].tolist()
        hgs = df['home_goals'].astype(int).tolist()
        ags = df['away_goals'].astype(int).tolist()

        HA = self.home_advantage
        K_ELO = 32.0; K_AD = 24.0; SCALE = 300.0; NORM = 2.5
        DEFAULT = 1500.0

        ratings: dict[str, float] = {}
        att_r:   dict[str, float] = {}
        def_r:   dict[str, float] = {}

        pre_elo_h = np.zeros(n, dtype=np.float32)
        pre_elo_a = np.zeros(n, dtype=np.float32)
        pre_att_h = np.zeros(n, dtype=np.float32)
        pre_def_h = np.zeros(n, dtype=np.float32)
        pre_att_a = np.zeros(n, dtype=np.float32)
        pre_def_a = np.zeros(n, dtype=np.float32)

        for i in range(n):
            h, a = hts[i], ats[i]
            hg, ag = hgs[i], ags[i]
            rh = ratings.get(h, DEFAULT); ra = ratings.get(a, DEFAULT)
            pre_elo_h[i] = rh; pre_elo_a[i] = ra
            adj_rh = rh + HA
            eh = 1.0 / (1.0 + 10.0 ** ((ra - adj_rh) / 400.0))
            sh = 1.0 if hg > ag else (0.5 if hg == ag else 0.0)
            gm = min(1.0 + 0.5 * max(abs(hg-ag) - 1, 0), 3.0)
            ek = K_ELO * gm
            ratings[h] = rh + ek * (sh - eh)
            ratings[a] = ra + ek * ((1.0-sh) - (1.0-eh))
            att_h, def_h = att_r.get(h, DEFAULT), def_r.get(h, DEFAULT)
            att_a, def_a = att_r.get(a, DEFAULT), def_r.get(a, DEFAULT)
            pre_att_h[i], pre_def_h[i] = att_h, def_h
            pre_att_a[i], pre_def_a[i] = att_a, def_a
            e_ha = 1.0 / (1.0 + 10.0 ** ((def_a - att_h - 30.0) / SCALE))
            e_aa = 1.0 / (1.0 + 10.0 ** ((def_h - att_a + 30.0) / SCALE))
            att_r[h] = att_h + K_AD * (min(hg/NORM, 1.0) - e_ha)
            def_r[h] = def_h + K_AD * (max(0.0, 1.0-ag/NORM) - (1.0-e_aa))
            att_r[a] = att_a + K_AD * (min(ag/NORM, 1.0) - e_aa)
            def_r[a] = def_a + K_AD * (max(0.0, 1.0-hg/NORM) - (1.0-e_ha))

        self._elo_pre_home, self._elo_pre_away = pre_elo_h, pre_elo_a
        self._att_pre_home, self._def_pre_home = pre_att_h, pre_def_h
        self._att_pre_away, self._def_pre_away = pre_att_a, pre_def_a

        elo_h_bulk, elo_a_bulk = compute_elo_ratings_bulk(hts, ats, hgs, ags, k_factor=K_ELO, home_advantage=HA)
        for t, e in zip(hts, elo_h_bulk): self._elo_ratings[t] = e
        for t, e in zip(ats, elo_a_bulk): self._elo_ratings[t] = e
        ha, hd, aa, ad = compute_attack_defense_elo_bulk(hts, ats, hgs, ags, k_factor=K_AD, home_advantage=HA)
        for t, v in zip(hts, ha): self._att_elo[t] = v
        for t, v in zip(hts, hd): self._def_elo[t] = v
        for t, v in zip(ats, aa): self._att_elo[t] = v
        for t, v in zip(ats, ad): self._def_elo[t] = v

        groupby_col = 'league_slug' if 'league_slug' in df.columns else None
        groups = df.groupby(groupby_col) if groupby_col else [('all', df)]
        for slug, grp in groups:
            avg_goals  = (grp['home_goals'] + grp['away_goals']).mean()
            home_avg, away_avg = grp['home_goals'].mean(), grp['away_goals'].mean()
            self._league_stats[slug] = {
                'avg_goals': avg_goals,
                'home_attack': home_avg / max(avg_goals / 2, 1e-6),
                'away_attack': away_avg / max(avg_goals / 2, 1e-6),
                'home_adv_factor': home_avg / max(away_avg, 1e-6),
                'home_win_rate': (grp['home_goals'] > grp['away_goals']).mean(),
                'draw_rate': (grp['home_goals'] == grp['away_goals']).mean(),
            }
        logger.info("Feature pipeline fitted.")
        return self

    def _get_league(self, league_slug: str) -> dict:
        return self._league_stats.get(league_slug, {'avg_goals': 2.6, 'home_attack': 1.0, 'away_attack': 1.0, 'home_adv_factor': 1.25, 'home_win_rate': 0.46, 'draw_rate': 0.24})

    def build_features(self, matches: list, history: pd.DataFrame) -> np.ndarray:
        return np.zeros((len(matches), N_FEATURES))

    def build_training_set(self, df: pd.DataFrame, max_samples: int = MAX_TRAINING_SAMPLES):
        total = len(df)
        all_idx = list(range(20, total))
        if len(all_idx) > max_samples: all_idx = all_idx[-max_samples:]
        n_targets = len(all_idx)
        logger.info(f"Building elite training set: {n_targets:,} samples (BULK C++ v4)")

        teams = pd.concat([df['home_team'], df['away_team']]).unique()
        team_map = {name: i for i, name in enumerate(teams)}
        n_teams = len(teams)
        all_gh, all_ga = df['home_goals'].values.astype(np.int32), df['away_goals'].values.astype(np.int32)
        all_ts = pd.to_datetime(df['date']).values.astype(np.int64) / 1e9
        all_h_idx, all_a_idx = df['home_team'].map(team_map).values.astype(np.int32), df['away_team'].map(team_map).values.astype(np.int32)

        all_pre_elos = np.zeros((len(df), 6), dtype=np.float64)
        all_pre_elos[:, 0] = self._elo_pre_home; all_pre_elos[:, 1] = self._elo_pre_away; all_pre_elos[:, 2] = self._elo_pre_home - self._elo_pre_away
        for i in range(len(df)):
            all_pre_elos[i, 3:6] = elo_probabilities(self._elo_pre_home[i], self._elo_pre_away[i], self.home_advantage)

        all_pre_att_def = np.zeros((len(df), 4), dtype=np.float64)
        all_pre_att_def[:, 0] = self._att_pre_home; all_pre_att_def[:, 1] = self._def_pre_home; all_pre_att_def[:, 2] = self._att_pre_away; all_pre_att_def[:, 3] = self._def_pre_away
        all_odds = df[['odds_home', 'odds_draw', 'odds_away']].fillna(0).values.astype(np.float64)
        all_league_stats = np.zeros((len(df), 6), dtype=np.float64)
        for i, slug in enumerate(df['league_slug'] if 'league_slug' in df.columns else ['all']*len(df)):
            ls = self._get_league(slug)
            all_league_stats[i] = [ls['avg_goals'], ls['home_attack'], ls['away_attack'], ls['home_adv_factor'], ls['home_win_rate'], ls['draw_rate']]

        team_matches = [[] for _ in range(n_teams)]
        for i in range(len(df)):
            team_matches[all_h_idx[i]].append(i); team_matches[all_a_idx[i]].append(i)
        flat_team_matches, team_ptrs, team_cnts, curr_ptr = [], [], [], 0
        for m_list in team_matches:
            team_ptrs.append(curr_ptr); team_cnts.append(len(m_list)); flat_team_matches.extend(m_list); curr_ptr += len(m_list)

        # Elo history for volatility
        h_elo_hist = self._elo_pre_home; a_elo_hist = self._elo_pre_away

        X = compute_all_features_bulk_v4(np.array(all_idx, dtype=np.int32), all_gh, all_ga, all_ts, all_h_idx, all_a_idx, all_pre_elos, all_pre_att_def, all_odds, all_league_stats, np.array(flat_team_matches, dtype=np.int32), np.array(team_ptrs, dtype=np.int32), np.array(team_cnts, dtype=np.int32), h_elo_hist, a_elo_hist, _LOOKBACK, self.home_advantage)

        y_1x2 = np.where(all_gh[all_idx] > all_ga[all_idx], 0, np.where(all_gh[all_idx] == all_ga[all_idx], 1, 2)).astype(np.int32)
        y_goals = ((all_gh[all_idx] + all_ga[all_idx]) > 2.5).astype(np.int32)
        dates = df['date'].values[all_idx]
        logger.info(f"Elite Feature matrix ready: {X.shape}")
        return X, y_1x2, y_goals, dates
