"""
Bridge to the C++ feature extraction library (libstatwise.so).
"""
from __future__ import annotations
import ctypes, os, math, logging
import numpy as np

logger = logging.getLogger(__name__)

_LIB_PATH = os.path.join(os.path.dirname(__file__), '..', 'libstatwise.so')
_lib: ctypes.CDLL | None = None

def _load_lib() -> ctypes.CDLL | None:
    global _lib
    if _lib is not None: return _lib
    path = os.path.realpath(_LIB_PATH)
    if not os.path.exists(path): return None
    try:
        _lib = ctypes.CDLL(path)
        _lib.compute_elo_ratings.restype          = None
        _lib.compute_form_vector.restype          = None
        _lib.compute_h2h_stats.restype            = None
        _lib.compute_goal_probability.restype     = None
        _lib.compute_elo_probabilities.restype    = None
        _lib.batch_compute_features.restype       = None
        _lib.compute_attack_defense_elo.restype   = None
        _lib.compute_poisson_score_matrix.restype = None
        _lib.compute_consecutive_runs.restype     = None
        _lib.compute_venue_split_form.restype     = None
        _lib.compute_goals_variance.restype       = None
        _lib.compute_form_trend.restype           = None
        _lib.compute_scoring_consistency.restype  = None
        _lib.compute_h2h_extended.restype         = None
        _lib.compute_last_n_goals.restype         = None
        _lib.compute_draw_rate.restype            = None
        _lib.compute_temporal_features.restype    = None
        _lib.compute_streak.restype               = None
        _lib.compute_all_features_v3.restype      = None
        _lib.compute_all_features_bulk.restype    = None
        _lib.compute_all_features_bulk_v4.restype = None
        logger.info("libstatwise.so loaded successfully")
        return _lib
    except Exception as e:
        logger.warning(f"Failed to load libstatwise.so: {e}")
        return None

def elo_probabilities(elo_home: float, elo_away: float, home_advantage: float = 100.0) -> tuple[float, float, float]:
    lib = _load_lib()
    if lib:
        ph = ctypes.c_double(); pd = ctypes.c_double(); pa = ctypes.c_double()
        lib.compute_elo_probabilities(ctypes.c_double(elo_home), ctypes.c_double(elo_away), ctypes.c_double(home_advantage), ctypes.byref(ph), ctypes.byref(pd), ctypes.byref(pa))
        return ph.value, pd.value, pa.value
    return 0.45, 0.25, 0.30

def compute_elo_ratings_bulk(home_teams, away_teams, home_goals, away_goals, k_factor: float = 32.0, home_advantage: float = 100.0) -> tuple[np.ndarray, np.ndarray]:
    n = len(home_teams); lib = _load_lib()
    if lib:
        enc_h = [s.encode() for s in home_teams]; enc_a = [s.encode() for s in away_teams]
        c_h = (ctypes.c_char_p * n)(*enc_h); c_a = (ctypes.c_char_p * n)(*enc_a)
        c_hg = (ctypes.c_int * n)(*home_goals); c_ag = (ctypes.c_int * n)(*away_goals)
        out_h, out_a = (ctypes.c_double * n)(), (ctypes.c_double * n)()
        lib.compute_elo_ratings(c_h, c_a, c_hg, c_ag, n, ctypes.c_double(k_factor), ctypes.c_double(home_advantage), out_h, out_a)
        return np.array(list(out_h)), np.array(list(out_a))
    return np.zeros(n), np.zeros(n)

def compute_attack_defense_elo_bulk(home_teams, away_teams, home_goals, away_goals, k_factor: float = 24.0, home_advantage: float = 100.0) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    n = len(home_teams); lib = _load_lib()
    if lib:
        enc_h = [s.encode() for s in home_teams]; enc_a = [s.encode() for s in away_teams]
        c_h = (ctypes.c_char_p * n)(*enc_h); c_a = (ctypes.c_char_p * n)(*enc_a)
        c_hg = (ctypes.c_int * n)(*home_goals); c_ag = (ctypes.c_int * n)(*away_goals)
        o_ha, o_hd, o_aa, o_ad = (ctypes.c_double * n)(), (ctypes.c_double * n)(), (ctypes.c_double * n)(), (ctypes.c_double * n)()
        lib.compute_attack_defense_elo(c_h, c_a, c_hg, c_ag, n, ctypes.c_double(k_factor), ctypes.c_double(home_advantage), o_ha, o_hd, o_aa, o_ad)
        return np.array(list(o_ha)), np.array(list(o_hd)), np.array(list(o_aa)), np.array(list(o_ad))
    return np.zeros(n), np.zeros(n), np.zeros(n), np.zeros(n)

def compute_all_features_bulk_v4(target_indices: np.ndarray, all_gh: np.ndarray, all_ga: np.ndarray, all_ts: np.ndarray, all_h_idx: np.ndarray, all_a_idx: np.ndarray, all_pre_elos: np.ndarray, all_pre_att_def: np.ndarray, all_odds: np.ndarray, all_league_stats: np.ndarray, team_matches_idx: np.ndarray, team_matches_ptr: np.ndarray, team_matches_cnt: np.ndarray, all_h_elo: np.ndarray, all_a_elo: np.ndarray, lookback: int, home_advantage: float) -> np.ndarray | None:
    lib = _load_lib()
    if lib:
        nt = len(target_indices); out = (ctypes.c_double * (nt * 125))()
        lib.compute_all_features_bulk_v4(target_indices.ctypes.data_as(ctypes.POINTER(ctypes.c_int)), nt, all_gh.ctypes.data_as(ctypes.POINTER(ctypes.c_int)), all_ga.ctypes.data_as(ctypes.POINTER(ctypes.c_int)), all_ts.ctypes.data_as(ctypes.POINTER(ctypes.c_double)), all_h_idx.ctypes.data_as(ctypes.POINTER(ctypes.c_int)), all_a_idx.ctypes.data_as(ctypes.POINTER(ctypes.c_int)), all_pre_elos.ctypes.data_as(ctypes.POINTER(ctypes.c_double)), all_pre_att_def.ctypes.data_as(ctypes.POINTER(ctypes.c_double)), all_odds.ctypes.data_as(ctypes.POINTER(ctypes.c_double)), all_league_stats.ctypes.data_as(ctypes.POINTER(ctypes.c_double)), team_matches_idx.ctypes.data_as(ctypes.POINTER(ctypes.c_int)), team_matches_ptr.ctypes.data_as(ctypes.POINTER(ctypes.c_int)), team_matches_cnt.ctypes.data_as(ctypes.POINTER(ctypes.c_int)), all_h_elo.ctypes.data_as(ctypes.POINTER(ctypes.c_double)), all_a_elo.ctypes.data_as(ctypes.POINTER(ctypes.c_double)), lookback, ctypes.c_double(home_advantage), out)
        return np.array(list(out)).reshape((nt, 125))
    return None
