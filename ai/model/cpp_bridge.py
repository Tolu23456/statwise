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

def form_vector(matches: list[dict], team: str) -> np.ndarray:
    lib = _load_lib()
    if lib:
        team_m = [m for m in matches if m['home_team'] == team or m['away_team'] == team][-25:]; n = len(team_m)
        if n == 0: return np.zeros(10)
        hg = (ctypes.c_int * n)(*[m['home_goals'] for m in team_m]); ag = (ctypes.c_int * n)(*[m['away_goals'] for m in team_m])
        wh = (ctypes.c_int * n)(*[1 if m['home_team'] == team else 0 for m in team_m]); out = (ctypes.c_double * 10)()
        lib.compute_form_vector(hg, ag, wh, n, None, out)
        return np.array(list(out))
    return np.zeros(10)

def h2h_stats(matches: list[dict], team_a: str, team_b: str) -> np.ndarray:
    lib = _load_lib()
    if lib:
        h2h = [m for m in matches if {m['home_team'], m['away_team']} == {team_a, team_b}][-25:]; n = len(h2h)
        if n == 0: return np.zeros(6)
        hg = (ctypes.c_int * n)(*[m['home_goals'] for m in h2h]); ag = (ctypes.c_int * n)(*[m['away_goals'] for m in h2h])
        wf = (ctypes.c_int * n)(*[1 if m['home_team'] == team_a else 0 for m in h2h]); out = (ctypes.c_double * 6)()
        lib.compute_h2h_stats(hg, ag, wf, n, out)
        return np.array(list(out))
    return np.zeros(6)

def goal_probability(attack_h, defense_a, attack_a, defense_h, league_avg, home_adv) -> tuple[float, float]:
    lib = _load_lib()
    if lib:
        p25, btts = ctypes.c_double(), ctypes.c_double()
        lib.compute_goal_probability(ctypes.c_double(attack_h), ctypes.c_double(defense_a), ctypes.c_double(attack_a), ctypes.c_double(defense_h), ctypes.c_double(league_avg), ctypes.c_double(home_adv), ctypes.byref(p25), ctypes.byref(btts))
        return p25.value, btts.value
    return 0.5, 0.5

def poisson_score_matrix(lambda_home: float, lambda_away: float, rho: float = -0.13) -> np.ndarray:
    lib = _load_lib()
    if lib:
        out = (ctypes.c_double * 10)()
        lib.compute_poisson_score_matrix(ctypes.c_double(lambda_home), ctypes.c_double(lambda_away), ctypes.c_double(rho), out)
        return np.array(list(out))
    return np.zeros(10)

def consecutive_runs(matches: list[dict], team: str) -> np.ndarray:
    lib = _load_lib()
    if lib:
        team_m = [m for m in matches if m.get('home_team') == team or m.get('away_team') == team][-15:]; n = len(team_m)
        if n == 0: return np.zeros(2)
        hg = (ctypes.c_int * n)(*[m['home_goals'] for m in team_m]); ag = (ctypes.c_int * n)(*[m['away_goals'] for m in team_m])
        wh = (ctypes.c_int * n)(*[1 if m['home_team'] == team else 0 for m in team_m]); out = (ctypes.c_double * 2)()
        lib.compute_consecutive_runs(hg, ag, wh, n, out)
        return np.array(list(out))
    return np.zeros(2)

def venue_split_form(matches: list[dict], team: str, as_home: bool, lookback: int = 20) -> np.ndarray:
    lib = _load_lib()
    if lib:
        team_m = [m for m in matches if m.get('home_team') == team or m.get('away_team') == team][-lookback:]; n = len(team_m)
        if n == 0: return np.zeros(4)
        hg = (ctypes.c_int * n)(*[m['home_goals'] for m in team_m]); ag = (ctypes.c_int * n)(*[m['away_goals'] for m in team_m])
        wh = (ctypes.c_int * n)(*[1 if m['home_team'] == team else 0 for m in team_m]); out = (ctypes.c_double * 4)()
        lib.compute_venue_split_form(hg, ag, wh, n, 1 if as_home else 0, out)
        return np.array(list(out))
    return np.zeros(4)

def goals_variance(matches: list[dict], team: str, lookback: int = 30) -> np.ndarray:
    lib = _load_lib()
    if lib:
        team_m = [m for m in matches if m.get('home_team') == team or m.get('away_team') == team][-lookback:]; n = len(team_m)
        if n == 0: return np.array([0.5, 0.5])
        hg = (ctypes.c_int * n)(*[m['home_goals'] for m in team_m]); ag = (ctypes.c_int * n)(*[m['away_goals'] for m in team_m])
        wh = (ctypes.c_int * n)(*[1 if m['home_team'] == team else 0 for m in team_m]); out = (ctypes.c_double * 2)()
        lib.compute_goals_variance(hg, ag, wh, n, out)
        return np.array(list(out))
    return np.array([0.5, 0.5])

def compute_all_features_v3(pre_elos: np.ndarray, pre_att_def: np.ndarray, match_goals: list[int], odds: list[float], current_ts: float, league_stats: list[float], h_gh, h_ga, h_wh, h_ts, a_gh, a_ga, a_wh, a_ts, h2h_gh, h2h_ga, h2h_wh, home_advantage: float = 100.0) -> np.ndarray | None:
    lib = _load_lib()
    if lib:
        nh, na, n2 = len(h_gh), len(a_gh), len(h2h_gh); out = (ctypes.c_double * 110)()
        c_pe = (ctypes.c_double * 6)(*pre_elos); c_pad = (ctypes.c_double * 4)(*pre_att_def); c_mg = (ctypes.c_int * 2)(*match_goals); c_od = (ctypes.c_double * 3)(*odds); c_ls = (ctypes.c_double * 6)(*league_stats)
        c_hgh = (ctypes.c_int * nh)(*h_gh); c_hga = (ctypes.c_int * nh)(*h_ga); c_hwh = (ctypes.c_int * nh)(*h_wh); c_hts = (ctypes.c_double * nh)(*h_ts)
        c_agh = (ctypes.c_int * na)(*a_gh); c_aga = (ctypes.c_int * na)(*a_ga); c_awh = (ctypes.c_int * na)(*a_wh); c_ats = (ctypes.c_double * na)(*a_ts)
        c_2gh = (ctypes.c_int * n2)(*h2h_gh); c_2ga = (ctypes.c_int * n2)(*h2h_ga); c_2wh = (ctypes.c_int * n2)(*h2h_wh)
        lib.compute_all_features_v3(c_pe, c_pad, c_mg, c_od, ctypes.c_double(current_ts), c_ls, nh, c_hgh, c_hga, c_hwh, c_hts, na, c_agh, c_aga, c_awh, c_ats, n2, c_2gh, c_2ga, c_2wh, ctypes.c_double(home_advantage), out)
        return np.array(list(out))
    return None

def compute_all_features_bulk(target_indices: np.ndarray, all_gh: np.ndarray, all_ga: np.ndarray, all_ts: np.ndarray, all_h_idx: np.ndarray, all_a_idx: np.ndarray, all_pre_elos: np.ndarray, all_pre_att_def: np.ndarray, all_odds: np.ndarray, all_league_stats: np.ndarray, team_matches_idx: np.ndarray, team_matches_ptr: np.ndarray, team_matches_cnt: np.ndarray, lookback: int, home_advantage: float) -> np.ndarray | None:
    lib = _load_lib()
    if lib:
        nt = len(target_indices); out = (ctypes.c_double * (nt * 110))()
        lib.compute_all_features_bulk(target_indices.ctypes.data_as(ctypes.POINTER(ctypes.c_int)), nt, all_gh.ctypes.data_as(ctypes.POINTER(ctypes.c_int)), all_ga.ctypes.data_as(ctypes.POINTER(ctypes.c_int)), all_ts.ctypes.data_as(ctypes.POINTER(ctypes.c_double)), all_h_idx.ctypes.data_as(ctypes.POINTER(ctypes.c_int)), all_a_idx.ctypes.data_as(ctypes.POINTER(ctypes.c_int)), all_pre_elos.ctypes.data_as(ctypes.POINTER(ctypes.c_double)), all_pre_att_def.ctypes.data_as(ctypes.POINTER(ctypes.c_double)), all_odds.ctypes.data_as(ctypes.POINTER(ctypes.c_double)), all_league_stats.ctypes.data_as(ctypes.POINTER(ctypes.c_double)), team_matches_idx.ctypes.data_as(ctypes.POINTER(ctypes.c_int)), team_matches_ptr.ctypes.data_as(ctypes.POINTER(ctypes.c_int)), team_matches_cnt.ctypes.data_as(ctypes.POINTER(ctypes.c_int)), lookback, ctypes.c_double(home_advantage), out)
        return np.array(list(out)).reshape((nt, 110))
    return None
