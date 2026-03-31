"""
Bridge to the C++ feature extraction library (libstatwise.so).
Falls back gracefully to pure-Python implementations if the library
hasn't been compiled yet.
"""
from __future__ import annotations
import ctypes, os, math, logging
import numpy as np

logger = logging.getLogger(__name__)

_LIB_PATH = os.path.join(os.path.dirname(__file__), '..', 'libstatwise.so')
_lib: ctypes.CDLL | None = None


def _load_lib() -> ctypes.CDLL | None:
    global _lib
    if _lib is not None:
        return _lib
    path = os.path.realpath(_LIB_PATH)
    if not os.path.exists(path):
        logger.warning("libstatwise.so not found – using pure-Python fallback")
        return None
    try:
        _lib = ctypes.CDLL(path)
        _lib.compute_elo_ratings.restype = None
        _lib.compute_form_vector.restype = None
        _lib.compute_h2h_stats.restype = None
        _lib.compute_goal_probability.restype = None
        _lib.compute_elo_probabilities.restype = None
        _lib.batch_compute_features.restype = None
        logger.info("libstatwise.so loaded successfully")
        return _lib
    except Exception as e:
        logger.warning(f"Failed to load libstatwise.so: {e} – using pure-Python fallback")
        return None


# ─────────────────────────── Python fallbacks ─────────────────────────── #

def _py_elo_probabilities(elo_home: float, elo_away: float,
                           home_advantage: float = 100.0):
    adj = elo_home + home_advantage
    e_home = 1.0 / (1.0 + 10 ** ((elo_away - adj) / 400.0))
    elo_diff = abs(adj - elo_away)
    draw_prob = max(0.05, min(0.35, 0.28 * math.exp(-0.0015 * elo_diff)))
    remainder = 1.0 - draw_prob
    ph = e_home * remainder
    pa = (1.0 - e_home) * remainder
    total = ph + draw_prob + pa
    return ph / total, draw_prob / total, pa / total


def _py_form_vector(matches: list[dict], team: str) -> np.ndarray:
    """
    matches: list of dicts with keys home_team, away_team, home_goals, away_goals
    Returns a 10-element form vector.
    """
    results = []
    for m in reversed(matches[-15:]):
        if m.get('home_team') == team:
            scored, conceded = m['home_goals'], m['away_goals']
        elif m.get('away_team') == team:
            scored, conceded = m['away_goals'], m['home_goals']
        else:
            continue
        results.append((scored, conceded))

    if not results:
        return np.zeros(10)

    decay = 0.85
    n = len(results)
    wins = draws = losses = 0.0
    goals_s = goals_c = momentum = cs = sg = 0.0
    w_sum = 0.0
    for i, (s, c) in enumerate(results):
        w = decay ** (n - 1 - i)
        w_sum += w
        goals_s += w * s
        goals_c += w * c
        if s > c:
            wins += w; momentum += w * 3.0
        elif s == c:
            draws += w; momentum += w * 1.0
        else:
            losses += w
        if c == 0: cs += w
        if s > 0:  sg += w

    if w_sum < 1e-9:
        return np.zeros(10)

    return np.array([
        wins / w_sum, draws / w_sum, losses / w_sum,
        goals_s / w_sum, goals_c / w_sum,
        (goals_s - goals_c) / w_sum,
        momentum / w_sum,
        (wins * 3 + draws) / (w_sum * 3),
        cs / w_sum, sg / w_sum,
    ])


def _py_h2h_stats(matches: list[dict], team_a: str, team_b: str) -> np.ndarray:
    wins = draws = losses = 0.0
    ga = gb = n = 0.0
    for m in matches:
        if m['home_team'] == team_a and m['away_team'] == team_b:
            s, c = m['home_goals'], m['away_goals']
        elif m['home_team'] == team_b and m['away_team'] == team_a:
            s, c = m['away_goals'], m['home_goals']
        else:
            continue
        n += 1; ga += s; gb += c
        if s > c:   wins += 1
        elif s == c: draws += 1
        else:        losses += 1
    if n < 1:
        return np.zeros(6)
    return np.array([wins / n, draws / n, losses / n, ga / n, gb / n, n])


def _py_goal_probability(attack_h, defense_a, attack_a, defense_h,
                          league_avg, home_adv):
    from scipy.stats import poisson
    lh = min(max(attack_h * defense_a * home_adv, 0.3), 6.0)
    la = min(max(attack_a * defense_h,             0.2), 6.0)
    p_over25 = 1.0 - sum(
        poisson.pmf(gh, lh) * poisson.pmf(ga, la)
        for gh in range(3) for ga in range(3 - gh)
    )
    p_btts = (1 - poisson.pmf(0, lh)) * (1 - poisson.pmf(0, la))
    return float(p_over25), float(p_btts)


# ─────────────────────────── Public API ─────────────────────────── #

def elo_probabilities(elo_home: float, elo_away: float,
                      home_advantage: float = 100.0) -> tuple[float, float, float]:
    lib = _load_lib()
    if lib:
        ph = ctypes.c_double(); pd = ctypes.c_double(); pa = ctypes.c_double()
        lib.compute_elo_probabilities(
            ctypes.c_double(elo_home),
            ctypes.c_double(elo_away),
            ctypes.c_double(home_advantage),
            ctypes.byref(ph), ctypes.byref(pd), ctypes.byref(pa)
        )
        return ph.value, pd.value, pa.value
    return _py_elo_probabilities(elo_home, elo_away, home_advantage)


def compute_elo_ratings_bulk(home_teams, away_teams, home_goals, away_goals,
                              k_factor: float = 32.0,
                              home_advantage: float = 100.0
                              ) -> tuple[np.ndarray, np.ndarray]:
    n = len(home_teams)
    lib = _load_lib()
    if lib:
        enc_home = [s.encode() for s in home_teams]
        enc_away = [s.encode() for s in away_teams]
        c_home = (ctypes.c_char_p * n)(*enc_home)
        c_away = (ctypes.c_char_p * n)(*enc_away)
        c_hg = (ctypes.c_int * n)(*home_goals)
        c_ag = (ctypes.c_int * n)(*away_goals)
        out_h = (ctypes.c_double * n)()
        out_a = (ctypes.c_double * n)()
        lib.compute_elo_ratings(
            c_home, c_away, c_hg, c_ag, n,
            ctypes.c_double(k_factor), ctypes.c_double(home_advantage),
            out_h, out_a
        )
        return np.array(list(out_h)), np.array(list(out_a))

    # Python fallback: compute sequentially
    ratings: dict[str, float] = {}
    elo_h_out, elo_a_out = [], []
    DEFAULT = 1500.0
    for i in range(n):
        h, a = home_teams[i], away_teams[i]
        if h not in ratings: ratings[h] = DEFAULT
        if a not in ratings: ratings[a] = DEFAULT
        rh = ratings[h] + home_advantage
        ra = ratings[a]
        e_h = 1.0 / (1.0 + 10 ** ((ra - rh) / 400.0))
        e_a = 1.0 - e_h
        s_h = 1.0 if home_goals[i] > away_goals[i] else (0.5 if home_goals[i] == away_goals[i] else 0.0)
        gd = abs(home_goals[i] - away_goals[i])
        gd_m = min(1.0 + 0.5 * max(gd - 1, 0), 3.0)
        ek = k_factor * gd_m
        ratings[h] += ek * (s_h - e_h)
        ratings[a] += ek * ((1 - s_h) - e_a)
        elo_h_out.append(ratings[h])
        elo_a_out.append(ratings[a])
    return np.array(elo_h_out), np.array(elo_a_out)


def form_vector(matches: list[dict], team: str) -> np.ndarray:
    lib = _load_lib()
    if lib:
        team_matches = [m for m in matches
                        if m['home_team'] == team or m['away_team'] == team][-15:]
        n = len(team_matches)
        if n == 0:
            return np.zeros(10)
        hg = (ctypes.c_int * n)(*[m['home_goals'] for m in team_matches])
        ag = (ctypes.c_int * n)(*[m['away_goals'] for m in team_matches])
        wh = (ctypes.c_int * n)(*[1 if m['home_team'] == team else 0 for m in team_matches])
        out = (ctypes.c_double * 10)()
        lib.compute_form_vector(hg, ag, wh, n, None, out)
        return np.array(list(out))
    return _py_form_vector(matches, team)


def h2h_stats(matches: list[dict], team_a: str, team_b: str) -> np.ndarray:
    lib = _load_lib()
    if lib:
        h2h = [m for m in matches
               if {m['home_team'], m['away_team']} == {team_a, team_b}][-15:]
        n = len(h2h)
        if n == 0:
            return np.zeros(6)
        hg = (ctypes.c_int * n)(*[m['home_goals'] for m in h2h])
        ag = (ctypes.c_int * n)(*[m['away_goals'] for m in h2h])
        wf = (ctypes.c_int * n)(*[1 if m['home_team'] == team_a else 0 for m in h2h])
        out = (ctypes.c_double * 6)()
        lib.compute_h2h_stats(hg, ag, wf, n, out)
        return np.array(list(out))
    return _py_h2h_stats(matches, team_a, team_b)


def goal_probability(attack_h, defense_a, attack_a, defense_h,
                     league_avg, home_adv) -> tuple[float, float]:
    lib = _load_lib()
    if lib:
        p25 = ctypes.c_double(); btts = ctypes.c_double()
        lib.compute_goal_probability(
            ctypes.c_double(attack_h), ctypes.c_double(defense_a),
            ctypes.c_double(attack_a), ctypes.c_double(defense_h),
            ctypes.c_double(league_avg), ctypes.c_double(home_adv),
            ctypes.byref(p25), ctypes.byref(btts)
        )
        return p25.value, btts.value
    return _py_goal_probability(attack_h, defense_a, attack_a, defense_h, league_avg, home_adv)
