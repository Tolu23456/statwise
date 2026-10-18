"""
In-play win probability from pre-match expected goals.

Given pre-match expected goals (lambda_h, lambda_a), the current score and the
minute played, compute P(home win / draw / away win) for the FINAL score using
conditional Poisson: remaining goals are Poisson(lambda * remaining_time_fraction).

This is the standard score-state model (Dixon-Coles style) — it needs no
historical in-play training data, only the pre-match lambdas. The result is
blended with live bookmaker odds by the caller.
"""
from __future__ import annotations
import math

_MAX_REM = 12  # remaining-goals sum cap (Poisson tail beyond this is negligible)


def _pois(k: int, lam: float) -> float:
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-lam) * lam ** k / math.factorial(k)


def conditional_win_probs(lam_h: float, lam_a: float, score_h: int, score_a: int,
                          minute: float, stoppage: float = 5.0) -> dict | None:
    """Final-outcome probabilities given the current score state.

    minute: minutes elapsed (0-90+). Returns {home, draw, away} or None if the
    state is unusable (missing minute/scores, negative lambdas).
    """
    try:
        lam_h, lam_a = float(lam_h), float(lam_a)
        score_h, score_a = int(score_h), int(score_a)
        minute = float(minute)
    except (TypeError, ValueError):
        return None
    if minute < 0 or minute > 120 or score_h < 0 or score_a < 0:
        return None

    elapsed = min(minute, 90.0)
    remaining = max(90.0 - elapsed, 0.0) + min(max(stoppage, 0.0), 10.0)
    frac = min(remaining / 90.0, 1.0)
    rh, ra = lam_h * frac, lam_a * frac

    ph = pd = pa = 0.0
    ph_term = [_pois(k, rh) for k in range(_MAX_REM + 1)]
    pa_term = [_pois(j, ra) for j in range(_MAX_REM + 1)]
    for k, pk in enumerate(ph_term):
        if pk < 1e-12:
            continue
        for j, pj in enumerate(pa_term):
            if pj < 1e-12:
                continue
            fh, fa = score_h + k, score_a + j
            w = pk * pj
            if fh > fa:
                ph += w
            elif fh == fa:
                pd += w
            else:
                pa += w
    total = ph + pd + pa
    if total <= 0:
        return None
    return {"home": ph / total, "draw": pd / total, "away": pa / total}


def blend(model_probs: dict, market_probs: dict, alpha: float) -> dict:
    """final = alpha * model + (1 - alpha) * market (both de-vigged, same keys)."""
    keys = ("home", "draw", "away")
    out = {}
    for k in keys:
        m = model_probs.get(k)
        mk = market_probs.get(k)
        if m is None or mk is None:
            out[k] = m if mk is None else mk
        else:
            out[k] = alpha * float(m) + (1.0 - alpha) * float(mk)
    s = sum(out.values())
    return {k: v / s for k, v in out.items()} if s > 0 else out


def edge(final_probs: dict, market_probs: dict) -> dict:
    """final minus market per outcome — positive = model sees value."""
    return {k: float(final_probs.get(k, 0)) - float(market_probs.get(k, 0))
            for k in ("home", "draw", "away")}


def fit_lambdas_to_probs(target: dict, lam_max: float = 3.5, step: float = 0.05) -> tuple[float, float]:
    """Grid-search pre-match (lam_h, lam_a) whose 0-0 minute-0 probs best match a
    target distribution (e.g., de-vigged market probs).

    Gives a market-consistent score-state model for rows that don't carry
    model-exported lambdas. Returns (lam_h, lam_a).
    """
    keys = ("home", "draw", "away")
    t = [max(float(target.get(k, 0) or 0), 1e-6) for k in keys]
    ts = sum(t)
    t = [x / ts for x in t]

    n = int(lam_max / step)
    best, best_err = (1.35, 1.15), float("inf")
    # coarse-then-fine would be faster, but this is already <1s
    for i in range(1, n + 1):
        lh = i * step
        for j in range(1, n + 1):
            la = j * step
            p = conditional_win_probs(lh, la, 0, 0, 0, stoppage=0)
            if not p:
                continue
            err = sum((p[k] - t[m]) ** 2 for m, k in enumerate(keys))
            if err < best_err:
                best_err, best = err, (lh, la)
    return best
