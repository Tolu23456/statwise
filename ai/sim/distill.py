"""Distillation: the lab's worlds become SOFT LABELS on real rows.

Plan Step 1.5 hard rule 2: **synthetic mass never trains LightGBM as raw
rows.**  What crosses into training is only a *target* attached to a real,
already-computed feature row: a sampled fraction of the train rows is
duplicated with an (1X2, O/U) label SAMPLED from the teacher ensemble's
posterior scoreline distribution.  Features = real football; target = the
Monte Carlo lab's belief.  No synthetic fixture ever becomes a row.

Why sample a whole scoreline rather than drawing each market independently:
the 'joint' LightGBM mode builds its six-class label from the (1X2, O/U)
pair, and plan 1.6's auxiliary BTTS/O3.5 heads must agree with them too, so
the pair must be jointly consistent.  Draw (i, j) once from the
world-averaged grid; derive 1X2, O2.5, BTTS and O3.5 from that same draw.

Leak control — two independent guards:

  1. the teacher was fit on its own TRAIN rows only (sim/run.py splits
     out-of-time before fitting);
  2. retrain refuses a recipe whose ``meta.split_at`` is LATER than its own
     holdout split — a teacher that saw outcomes inside retrain's holdout
     window would smuggle holdout information into training labels and make
     the gate's eval log loss optimistic.

Holdout rows never receive duplicates: retrain calls this on the train
slice only, and the existing real-holdout gate still decides whether the
distillation-trained candidate ships.
"""
from __future__ import annotations

import json
import logging
import os

import numpy as np

from .model import StrengthModel
from .simulate import MAX_GOALS, fixture_grids

logger = logging.getLogger("statwise.sim.distill")

RECIPE_SCHEMA = "statwise.sim.worlds.v1"


def load_teacher(path: str) -> tuple[list[StrengthModel] | None, dict | None]:
    """Load (worlds, recipe_meta) from a stored recipe; (None, None) if absent."""
    if not path or not os.path.exists(path):
        return None, None
    try:
        with open(path, encoding="utf-8") as fh:
            recipe = json.load(fh)
    except Exception as e:                      # noqa: BLE001 — any bad file = no teacher
        logger.warning("distill: recipe %s unreadable (%s)", path, e)
        return None, None
    if not recipe.get("teams") or not recipe.get("models"):
        logger.warning("distill: recipe %s has no models", path)
        return None, None
    if recipe.get("schema") != RECIPE_SCHEMA:
        logger.warning("distill: recipe schema %r != %r (proceeding)",
                       recipe.get("schema"), RECIPE_SCHEMA)
    from .merge import recipe_models               # local import: merge pulls CLI deps
    worlds = recipe_models(recipe)
    logger.info("distill: loaded %d teacher worlds from %s (%.2f MB)",
                len(worlds), path, os.path.getsize(path) / 1e6)
    return worlds, dict(recipe.get("meta") or {})


def teacher_may_fit(recipe_meta: dict, split_at) -> tuple[bool, str]:
    """Guard 2: teacher's training data must sit inside retrain's TRAIN side.

    True iff recipe.split_at <= retrain split_at (both are max(date) - N days,
    so a recipe built on data ending no later than retrain's window start has
    only ever seen rows the gate will not score on).
    """
    if split_at is None:
        return False, "retrain split unknown"
    t_split = recipe_meta.get("split_at")
    if not t_split:
        return False, "recipe has no split_at"
    try:
        import pandas as pd
        t = pd.to_datetime(t_split, errors="coerce")
        s = pd.to_datetime(split_at, errors="coerce")
    except Exception:                             # noqa: BLE001
        return False, f"unparseable split ({t_split!r})"
    if t is None or s is None or pd.isna(t) or pd.isna(s):
        return False, f"unparseable split ({t_split!r})"
    if t > s:
        return False, (f"teacher split {t.date()} is AFTER retrain split "
                       f"{s.date()} — it saw holdout outcomes")
    return True, f"teacher split {t.date()} <= retrain split {s.date()}"


def sample_soft_labels(models: list[StrengthModel], home, away, leagues, *,
                       fraction: float = 0.5, seed: int = 0,
                       row_batch: int = 20000):
    """Sample teacher labels for a fraction of the given rows.

    Returns ``(idx, y_1x2, y_goals, y_btts, y_o35)``: positions within the
    input arrays that should receive a duplicated row with a teacher-sampled
    label.  ALL four labels come from ONE sampled scoreline, so a duplicate
    is jointly consistent across 1X2 / O2.5 / BTTS / O3.5 — the same reason
    the main labels are sampled as a pair for the joint 6-class head (plan
    1.6: the auxiliary BTTS and O3.5 heads inherit the identical draw).
    Rows whose team the recipe does not know are excluded from the candidate
    pool (their teacher distribution would be meaningless).

    Cost: one world-averaged scoreline grid per row-batch, computed in world
    chunks so 160 worlds x 20k rows never materialises the full cube.
    """
    z = np.zeros(0, dtype=np.int64)
    empty = (z, np.zeros(0, dtype=np.int32), np.zeros(0, dtype=np.int32),
             np.zeros(0, dtype=np.int32), np.zeros(0, dtype=np.int32))
    if not models or fraction <= 0 or len(home) == 0:
        return empty
    m0 = models[0]
    h = np.fromiter((m0.t2i.get(str(x), -1) for x in home),
                    dtype=np.int64, count=len(home))
    a = np.fromiter((m0.t2i.get(str(x), -1) for x in away),
                    dtype=np.int64, count=len(away))
    # leagues: retrain hands canonical slugs, the recipe stores raw CSV slugs
    # (a superset — verified), unknown ones fall back to the most neutral
    # league offset (offsets are zero-mean, so that is the average league).
    neutral = int(np.argmin(np.abs(m0.league_offset))) if len(m0.league_offset) else 0
    lg = np.fromiter((m0.l2i.get(str(x), neutral) for x in leagues),
                     dtype=np.int64, count=len(leagues))

    cand = np.flatnonzero((h >= 0) & (a >= 0))
    if not len(cand):
        logger.warning("distill: no row whose teams the recipe knows")
        return empty
    n_take = int(round(float(fraction) * len(cand)))
    if n_take <= 0:
        return empty

    rng = np.random.default_rng(seed)
    sel = np.sort(rng.choice(cand, size=min(n_take, len(cand)), replace=False))
    g_max = MAX_GOALS + 1
    idx_out: list[np.ndarray] = []
    y_out: list[np.ndarray] = []
    g_out: list[np.ndarray] = []
    b_out: list[np.ndarray] = []
    o_out: list[np.ndarray] = []
    n_worlds = len(models)

    for start in range(0, len(sel), row_batch):
        sl = sel[start:start + row_batch]
        n = len(sl)
        grid = np.zeros((n, g_max, g_max), dtype=np.float64)
        chunk = max(1, min(n_worlds, int(260_000 / max(n, 1))))
        for w0 in range(0, n_worlds, chunk):
            grid += fixture_grids(models[w0:w0 + chunk], h[sl], a[sl],
                                  lg[sl]).sum(axis=0)
        grid /= n_worlds
        tot = grid.sum(axis=(1, 2), keepdims=True)
        grid /= np.where(tot > 0, tot, 1.0)

        # inverse-CDF sample one scoreline per row -> joint (1X2, O/U) label
        cdf = np.cumsum(grid.reshape(n, -1), axis=1)
        u = rng.random(n) * cdf[:, -1]
        cell = np.minimum((cdf < u[:, None]).sum(axis=1), g_max * g_max - 1)
        ii, jj = np.divmod(cell, g_max)
        idx_out.append(sl)
        y_out.append(np.where(ii > jj, 0, np.where(ii == jj, 1, 2)).astype(np.int32))
        g_out.append(((ii + jj) > 2.5).astype(np.int32))
        # plan 1.6: same scoreline -> BTTS and O3.5 labels (jointly
        # consistent with 1X2/O2.5 by construction)
        b_out.append(((ii > 0) & (jj > 0)).astype(np.int32))
        o_out.append(((ii + jj) > 3.5).astype(np.int32))

    idx = np.concatenate(idx_out)
    y = np.concatenate(y_out)
    g = np.concatenate(g_out)
    b = np.concatenate(b_out)
    o = np.concatenate(o_out)
    logger.info("distill: sampled teacher labels for %s/%s candidate rows "
                "(fraction %.2f, seed %d)",
                f"{len(idx):,}", f"{len(cand):,}", fraction, seed)
    return idx, y, g, b, o
