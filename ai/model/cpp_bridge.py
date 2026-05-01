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
        _lib.compute_all_features_v4.restype      = None
        _lib.compute_all_features_v6.restype      = None
        _lib.compute_all_features_bulk_v6.restype = None
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

def compute_all_features_v4(pe: np.ndarray, pad: np.ndarray, mg: np.ndarray, od: np.ndarray, cts: float, ls: np.ndarray, nh: int, ghh: np.ndarray, gah: np.ndarray, whh: np.ndarray, tsh: np.ndarray, ehh: np.ndarray, na: int, gha: np.ndarray, gaa: np.ndarray, wha: np.ndarray, tsa: np.ndarray, eha: np.ndarray, n2: int, gh2: np.ndarray, ga2: np.ndarray, wh2: np.ndarray, ha: float) -> np.ndarray | None:
    lib = _load_lib()
    if lib:
        out = (ctypes.c_double * 125)()
        lib.compute_all_features_v4(
            pe.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
            pad.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
            mg.ctypes.data_as(ctypes.POINTER(ctypes.c_int)),
            od.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
            ctypes.c_double(cts),
            ls.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
            nh, ghh.ctypes.data_as(ctypes.POINTER(ctypes.c_int)), gah.ctypes.data_as(ctypes.POINTER(ctypes.c_int)), whh.ctypes.data_as(ctypes.POINTER(ctypes.c_int)), tsh.ctypes.data_as(ctypes.POINTER(ctypes.c_double)), ehh.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
            na, gha.ctypes.data_as(ctypes.POINTER(ctypes.c_int)), gaa.ctypes.data_as(ctypes.POINTER(ctypes.c_int)), wha.ctypes.data_as(ctypes.POINTER(ctypes.c_int)), tsa.ctypes.data_as(ctypes.POINTER(ctypes.c_double)), eha.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
            n2, gh2.ctypes.data_as(ctypes.POINTER(ctypes.c_int)), ga2.ctypes.data_as(ctypes.POINTER(ctypes.c_int)), wh2.ctypes.data_as(ctypes.POINTER(ctypes.c_int)),
            ctypes.c_double(ha), out
        )
        return np.array(list(out))
    return None

def compute_all_features_v6(pe: np.ndarray, pad: np.ndarray, mg: np.ndarray, od: np.ndarray, cts: float, ls: np.ndarray, nh: int, ghh: np.ndarray, gah: np.ndarray, whh: np.ndarray, tsh: np.ndarray, ehh: np.ndarray, na: int, gha: np.ndarray, gaa: np.ndarray, wha: np.ndarray, tsa: np.ndarray, eha: np.ndarray, n2: int, gh2: np.ndarray, ga2: np.ndarray, wh2: np.ndarray, ha: float, asv: np.ndarray) -> np.ndarray | None:
    lib = _load_lib()
    if lib:
        out = (ctypes.c_double * 160)()
        lib.compute_all_features_v6(
            pe.ctypes.data_as(ctypes.POINTER(ctypes.c_double)), pad.ctypes.data_as(ctypes.POINTER(ctypes.c_double)), mg.ctypes.data_as(ctypes.POINTER(ctypes.c_int)), od.ctypes.data_as(ctypes.POINTER(ctypes.c_double)), ctypes.c_double(cts), ls.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
            nh, ghh.ctypes.data_as(ctypes.POINTER(ctypes.c_int)), gah.ctypes.data_as(ctypes.POINTER(ctypes.c_int)), whh.ctypes.data_as(ctypes.POINTER(ctypes.c_int)), tsh.ctypes.data_as(ctypes.POINTER(ctypes.c_double)), ehh.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
            na, gha.ctypes.data_as(ctypes.POINTER(ctypes.c_int)), gaa.ctypes.data_as(ctypes.POINTER(ctypes.c_int)), wha.ctypes.data_as(ctypes.POINTER(ctypes.c_int)), tsa.ctypes.data_as(ctypes.POINTER(ctypes.c_double)), eha.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
            n2, gh2.ctypes.data_as(ctypes.POINTER(ctypes.c_int)), ga2.ctypes.data_as(ctypes.POINTER(ctypes.c_int)), wh2.ctypes.data_as(ctypes.POINTER(ctypes.c_int)),
            ctypes.c_double(ha), asv.ctypes.data_as(ctypes.POINTER(ctypes.c_double)), out
        )
        return np.array(list(out))
    return None

def compute_all_features_bulk_v6(ti: np.ndarray, agh: np.ndarray, aga: np.ndarray, ats: np.ndarray, ahi: np.ndarray, aai: np.ndarray, ape: np.ndarray, apad: np.ndarray, ao: np.ndarray, als: np.ndarray, tmi: np.ndarray, tmp: np.ndarray, tmc: np.ndarray, ahe: np.ndarray, aae: np.ndarray, asv: np.ndarray, lb: int, ha: float) -> np.ndarray | None:
    lib = _load_lib()
    if lib:
        nt = len(ti); out = (ctypes.c_double * (nt * 160))()
        lib.compute_all_features_bulk_v6(ti.ctypes.data_as(ctypes.POINTER(ctypes.c_int)), nt, agh.ctypes.data_as(ctypes.POINTER(ctypes.c_int)), aga.ctypes.data_as(ctypes.POINTER(ctypes.c_int)), ats.ctypes.data_as(ctypes.POINTER(ctypes.c_double)), ahi.ctypes.data_as(ctypes.POINTER(ctypes.c_int)), aai.ctypes.data_as(ctypes.POINTER(ctypes.c_int)), ape.ctypes.data_as(ctypes.POINTER(ctypes.c_double)), apad.ctypes.data_as(ctypes.POINTER(ctypes.c_double)), ao.ctypes.data_as(ctypes.POINTER(ctypes.c_double)), als.ctypes.data_as(ctypes.POINTER(ctypes.c_double)), tmi.ctypes.data_as(ctypes.POINTER(ctypes.c_int)), tmp.ctypes.data_as(ctypes.POINTER(ctypes.c_int)), tmc.ctypes.data_as(ctypes.POINTER(ctypes.c_int)), ahe.ctypes.data_as(ctypes.POINTER(ctypes.c_double)), aae.ctypes.data_as(ctypes.POINTER(ctypes.c_double)), asv.ctypes.data_as(ctypes.POINTER(ctypes.c_double)), lb, ctypes.c_double(ha), out)
        return np.array(list(out)).reshape((nt, 160))
    return None
