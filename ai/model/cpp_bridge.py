"""
Bridge to the C++ feature extraction library (libstatwise.so).
Falls back gracefully to pure-Python implementations if the library
hasn't been compiled yet.

v2 additions:
  - compute_attack_defense_elo_bulk  (separate attack / defence Elo)
  - poisson_score_matrix             (Dixon-Coles full score matrix, 10 outputs)
  - consecutive_runs                 (unbeaten / winless run lengths)
  - venue_split_form                 (home-only / away-only form vector)
  - goals_variance                   (weighted variance in goals scored/conceded)
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
        # Original functions
        _lib.compute_elo_ratings.restype          = None
        _lib.compute_form_vector.restype          = None
        _lib.compute_h2h_stats.restype            = None
        _lib.compute_goal_probability.restype     = None
        _lib.compute_elo_probabilities.restype    = None
        _lib.batch_compute_features.restype       = None
        # New v2 functions
        _lib.compute_attack_defense_elo.restype   = None
        _lib.compute_poisson_score_matrix.restype = None
        _lib.compute_consecutive_runs.restype     = None
        _lib.compute_venue_split_form.restype     = None
        _lib.compute_goals_variance.restype       = None
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
    results = []
    for m in reversed(matches[-15:]):
        if m.get('home_team') == team:
            results.append((int(m.get('home_goals', 0)), int(m.get('away_goals', 0))))
        elif m.get('away_team') == team:
            results.append((int(m.get('away_goals', 0)), int(m.get('home_goals', 0))))
        else:
            continue
    if not results:
        return np.zeros(10)
    decay = 0.85
    n = len(results)
    wins = draws = losses = 0.0
    gs = gc = mom = cs = sg = ws = 0.0
    for i, (s, c) in enumerate(results):
        w = decay ** (n - 1 - i)
        ws += w; gs += w * s; gc += w * c
        if s > c:   wins += w; mom += w * 3
        elif s == c: draws += w; mom += w
        else:        losses += w
        if c == 0: cs += w
        if s > 0:  sg += w
    if ws < 1e-9:
        return np.zeros(10)
    return np.array([
        wins/ws, draws/ws, losses/ws, gs/ws, gc/ws,
        (gs-gc)/ws, mom/ws, (wins*3+draws)/(ws*3), cs/ws, sg/ws,
    ])


def _py_h2h_stats(matches: list[dict], team_a: str, team_b: str) -> np.ndarray:
    wins = draws = losses = ga = gb = n = 0.0
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
    return np.array([wins/n, draws/n, losses/n, ga/n, gb/n, n])


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


def _py_poisson_pmf(lam: float, k: int) -> float:
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    log_p = k * math.log(lam) - lam - sum(math.log(i) for i in range(1, k + 1))
    return math.exp(log_p)


def _py_dc_correction(h: int, a: int, lh: float, la: float, rho: float) -> float:
    if h == 0 and a == 0: return 1.0 - lh * la * rho
    if h == 0 and a == 1: return 1.0 + lh * rho
    if h == 1 and a == 0: return 1.0 + la * rho
    if h == 1 and a == 1: return 1.0 - rho
    return 1.0


def _py_poisson_score_matrix(lh: float, la: float, rho: float = -0.13) -> np.ndarray:
    """Pure-Python Dixon-Coles score matrix (10 outputs)."""
    lh = max(min(lh, 6.0), 0.01)
    la = max(min(la, 6.0), 0.01)
    N = 7
    mat = np.zeros((N, N))
    for h in range(N):
        for a in range(N):
            p = _py_poisson_pmf(lh, h) * _py_poisson_pmf(la, a)
            p *= _py_dc_correction(h, a, lh, la, rho)
            mat[h, a] = max(p, 0.0)
    mat /= max(mat.sum(), 1e-12)

    p_over15 = mat[mat.sum(axis=1) >= 0].sum()   # placeholder; compute below
    p_over15 = sum(mat[h, a] for h in range(N) for a in range(N) if h+a >= 2)
    p_over25 = sum(mat[h, a] for h in range(N) for a in range(N) if h+a >= 3)
    p_over35 = sum(mat[h, a] for h in range(N) for a in range(N) if h+a >= 4)
    p_btts   = sum(mat[h, a] for h in range(N) for a in range(N) if h>=1 and a>=1)
    p_hcs    = sum(mat[h, 0] for h in range(N))
    p_acs    = sum(mat[0, a] for a in range(N))
    return np.array([
        p_over15, p_over25, p_over35, p_btts, p_hcs, p_acs,
        mat[0, 0], mat[1, 0], mat[0, 1], mat[1, 1],
    ])


def _py_consecutive_runs(matches: list[dict], team: str) -> np.ndarray:
    team_m = [m for m in matches if m.get('home_team') == team or m.get('away_team') == team]
    if not team_m:
        return np.zeros(2)
    unbeaten = winless = 0
    for m in reversed(team_m[-15:]):
        h = m.get('home_team') == team
        s = int(m.get('home_goals', 0)) if h else int(m.get('away_goals', 0))
        c = int(m.get('away_goals', 0)) if h else int(m.get('home_goals', 0))
        if s >= c: unbeaten += 1
        else: break
    for m in reversed(team_m[-15:]):
        h = m.get('home_team') == team
        s = int(m.get('home_goals', 0)) if h else int(m.get('away_goals', 0))
        c = int(m.get('away_goals', 0)) if h else int(m.get('home_goals', 0))
        if s <= c: winless += 1
        else: break
    return np.array([min(unbeaten, 15) / 15.0, min(winless, 15) / 15.0])


def _py_venue_split_form(matches: list[dict], team: str, as_home: bool,
                          lookback: int = 20) -> np.ndarray:
    results = []
    for m in matches[-lookback:]:
        if as_home and m.get('home_team') == team:
            results.append((int(m.get('home_goals', 0)), int(m.get('away_goals', 0))))
        elif not as_home and m.get('away_team') == team:
            results.append((int(m.get('away_goals', 0)), int(m.get('home_goals', 0))))
    if not results:
        return np.zeros(4)
    n = len(results)
    wins = pts = gs = gc = 0.0
    for s, c in results:
        if s > c:   wins += 1; pts += 3
        elif s == c: pts += 1
        gs += s; gc += c
    return np.array([wins/n, pts/n, gs/n, gc/n])


def _py_goals_variance(matches: list[dict], team: str, lookback: int = 30) -> np.ndarray:
    team_m = [m for m in matches if m.get('home_team') == team or m.get('away_team') == team][-lookback:]
    if len(team_m) < 3:
        return np.array([0.5, 0.5])
    scored, conceded = [], []
    for m in team_m:
        h = m.get('home_team') == team
        scored.append(int(m.get('home_goals', 0)) if h else int(m.get('away_goals', 0)))
        conceded.append(int(m.get('away_goals', 0)) if h else int(m.get('home_goals', 0)))
    return np.array([float(np.var(scored)), float(np.var(conceded))])


# ─────────────────────────── Public API ─────────────────────────── #

def elo_probabilities(elo_home: float, elo_away: float,
                      home_advantage: float = 100.0) -> tuple[float, float, float]:
    lib = _load_lib()
    if lib:
        ph = ctypes.c_double(); pd = ctypes.c_double(); pa = ctypes.c_double()
        lib.compute_elo_probabilities(
            ctypes.c_double(elo_home), ctypes.c_double(elo_away),
            ctypes.c_double(home_advantage),
            ctypes.byref(ph), ctypes.byref(pd), ctypes.byref(pa),
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
        out_h = (ctypes.c_double * n)(); out_a = (ctypes.c_double * n)()
        lib.compute_elo_ratings(
            c_home, c_away, c_hg, c_ag, n,
            ctypes.c_double(k_factor), ctypes.c_double(home_advantage),
            out_h, out_a,
        )
        return np.array(list(out_h)), np.array(list(out_a))
    # Python fallback
    ratings: dict[str, float] = {}
    elo_h_out, elo_a_out = [], []
    DEFAULT = 1500.0
    for i in range(n):
        h, a = home_teams[i], away_teams[i]
        if h not in ratings: ratings[h] = DEFAULT
        if a not in ratings: ratings[a] = DEFAULT
        rh = ratings[h] + home_advantage; ra = ratings[a]
        eh = 1.0 / (1.0 + 10 ** ((ra - rh) / 400.0))
        sh = 1.0 if home_goals[i] > away_goals[i] else (0.5 if home_goals[i] == away_goals[i] else 0.0)
        gd = abs(home_goals[i] - away_goals[i])
        gm = min(1.0 + 0.5 * max(gd - 1, 0), 3.0)
        ek = k_factor * gm
        ratings[h] += ek * (sh - eh); ratings[a] += ek * ((1-sh) - (1-eh))
        elo_h_out.append(ratings[h]); elo_a_out.append(ratings[a])
    return np.array(elo_h_out), np.array(elo_a_out)


def compute_attack_defense_elo_bulk(home_teams, away_teams, home_goals, away_goals,
                                     k_factor: float = 24.0,
                                     home_advantage: float = 100.0
                                     ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Returns (home_att, home_def, away_att, away_def) arrays of length n."""
    n = len(home_teams)
    lib = _load_lib()
    if lib:
        enc_home = [s.encode() for s in home_teams]
        enc_away = [s.encode() for s in away_teams]
        c_home = (ctypes.c_char_p * n)(*enc_home)
        c_away = (ctypes.c_char_p * n)(*enc_away)
        c_hg = (ctypes.c_int * n)(*home_goals)
        c_ag = (ctypes.c_int * n)(*away_goals)
        o_ha = (ctypes.c_double * n)(); o_hd = (ctypes.c_double * n)()
        o_aa = (ctypes.c_double * n)(); o_ad = (ctypes.c_double * n)()
        lib.compute_attack_defense_elo(
            c_home, c_away, c_hg, c_ag, n,
            ctypes.c_double(k_factor), ctypes.c_double(home_advantage),
            o_ha, o_hd, o_aa, o_ad,
        )
        return (np.array(list(o_ha)), np.array(list(o_hd)),
                np.array(list(o_aa)), np.array(list(o_ad)))
    # Python fallback — simplified symmetric version
    att: dict[str, float] = {}; defs: dict[str, float] = {}
    ha_out, hd_out, aa_out, ad_out = [], [], [], []
    for i in range(n):
        h, a = home_teams[i], away_teams[i]
        if h not in att: att[h] = 1500.0; defs[h] = 1500.0
        if a not in att: att[a] = 1500.0; defs[a] = 1500.0
        SCALE = 300.0; NORM = 2.5
        e_ha = 1.0 / (1.0 + 10 ** ((defs[a] - att[h] - 30) / SCALE))
        e_aa = 1.0 / (1.0 + 10 ** ((defs[h] - att[a] + 30) / SCALE))
        a_ha = min(home_goals[i] / NORM, 1.0); a_aa = min(away_goals[i] / NORM, 1.0)
        a_hd = max(0.0, 1 - away_goals[i] / NORM); a_ad = max(0.0, 1 - home_goals[i] / NORM)
        att[h]  += k_factor * (a_ha - e_ha); att[a]  += k_factor * (a_aa - e_aa)
        defs[h] += k_factor * (a_hd - (1-e_aa)); defs[a] += k_factor * (a_ad - (1-e_ha))
        ha_out.append(att[h]); hd_out.append(defs[h])
        aa_out.append(att[a]); ad_out.append(defs[a])
    return (np.array(ha_out), np.array(hd_out),
            np.array(aa_out), np.array(ad_out))


def form_vector(matches: list[dict], team: str) -> np.ndarray:
    lib = _load_lib()
    if lib:
        team_m = [m for m in matches
                  if m['home_team'] == team or m['away_team'] == team][-15:]
        n = len(team_m)
        if n == 0:
            return np.zeros(10)
        hg = (ctypes.c_int * n)(*[m['home_goals'] for m in team_m])
        ag = (ctypes.c_int * n)(*[m['away_goals'] for m in team_m])
        wh = (ctypes.c_int * n)(*[1 if m['home_team'] == team else 0 for m in team_m])
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
            ctypes.byref(p25), ctypes.byref(btts),
        )
        return p25.value, btts.value
    return _py_goal_probability(attack_h, defense_a, attack_a, defense_h, league_avg, home_adv)


def poisson_score_matrix(lambda_home: float, lambda_away: float,
                          rho: float = -0.13) -> np.ndarray:
    """
    Returns 10 probabilities from the Dixon-Coles score matrix:
      [p_over15, p_over25, p_over35, p_btts, p_home_cs, p_away_cs,
       p_0_0, p_1_0, p_0_1, p_1_1]
    """
    lib = _load_lib()
    if lib:
        out = (ctypes.c_double * 10)()
        lib.compute_poisson_score_matrix(
            ctypes.c_double(lambda_home), ctypes.c_double(lambda_away),
            ctypes.c_double(rho), out,
        )
        return np.array(list(out))
    return _py_poisson_score_matrix(lambda_home, lambda_away, rho)


def consecutive_runs(matches: list[dict], team: str) -> np.ndarray:
    """Returns [unbeaten_run_norm, winless_run_norm]."""
    lib = _load_lib()
    if lib:
        team_m = [m for m in matches
                  if m.get('home_team') == team or m.get('away_team') == team][-15:]
        n = len(team_m)
        if n == 0:
            return np.zeros(2)
        hg = (ctypes.c_int * n)(*[m['home_goals'] for m in team_m])
        ag = (ctypes.c_int * n)(*[m['away_goals'] for m in team_m])
        wh = (ctypes.c_int * n)(*[1 if m.get('home_team') == team else 0 for m in team_m])
        out = (ctypes.c_double * 2)()
        lib.compute_consecutive_runs(hg, ag, wh, n, out)
        return np.array(list(out))
    return _py_consecutive_runs(matches, team)


def venue_split_form(matches: list[dict], team: str, as_home: bool,
                     lookback: int = 20) -> np.ndarray:
    """Returns [win_rate, ppg, avg_goals_scored, avg_goals_conceded] for home or away games."""
    lib = _load_lib()
    if lib:
        team_m = [m for m in matches
                  if m.get('home_team') == team or m.get('away_team') == team][-lookback:]
        n = len(team_m)
        if n == 0:
            return np.zeros(4)
        hg = (ctypes.c_int * n)(*[m['home_goals'] for m in team_m])
        ag = (ctypes.c_int * n)(*[m['away_goals'] for m in team_m])
        wh = (ctypes.c_int * n)(*[1 if m.get('home_team') == team else 0 for m in team_m])
        out = (ctypes.c_double * 4)()
        lib.compute_venue_split_form(hg, ag, wh, n, 1 if as_home else 0, out)
        return np.array(list(out))
    return _py_venue_split_form(matches, team, as_home, lookback)


def goals_variance(matches: list[dict], team: str, lookback: int = 30) -> np.ndarray:
    """Returns [scored_variance, conceded_variance]."""
    lib = _load_lib()
    if lib:
        team_m = [m for m in matches
                  if m.get('home_team') == team or m.get('away_team') == team][-lookback:]
        n = len(team_m)
        if n == 0:
            return np.array([0.5, 0.5])
        hg = (ctypes.c_int * n)(*[m['home_goals'] for m in team_m])
        ag = (ctypes.c_int * n)(*[m['away_goals'] for m in team_m])
        wh = (ctypes.c_int * n)(*[1 if m.get('home_team') == team else 0 for m in team_m])
        out = (ctypes.c_double * 2)()
        lib.compute_goals_variance(hg, ag, wh, n, out)
        return np.array(list(out))
    return _py_goals_variance(matches, team, lookback)
