"""Serve the attention student with PURE NumPy (no torch at predict time).

The student pickle stores: the exported weight arrays (the recipe), the
train-side standardisers, and the lab teacher recipe needed to recompute the
13 posterior features for any fixture.  `AttnHead` duck-types the pipeline
interface the rest of FootballPredictor already uses (`predict_proba(X)`,
plus an `extend(...)` hook retrain.py / predict_match call when
`needs_teacher` is set), so the gate, calibration, market blending, saving
and loading paths are all unchanged.
"""
from __future__ import annotations

import json
import logging
import os

import joblib
import numpy as np

from sim.model import StrengthModel
from attn.model import NumpyTabTransformer
from attn.teacher import N_TEACHER, teacher_matrix

logger = logging.getLogger("statwise.attn.serve")


def _recipe_models(recipe: dict) -> list[StrengthModel]:
    """Rehydrate worlds from the stored recipe (store the recipe, not rows)."""
    out = []
    for m in recipe.get("models", []):
        out.append(StrengthModel.from_dict({
            "teams": recipe["teams"], "leagues": recipe["leagues"],
            "att": m["att"], "def": m["def"], "mu": m["mu"],
            "league_offset": m.get("league_offset", []),
            "home_adv": m["home_adv"], "xi": m["xi"],
            "n_matches": m.get("n_matches", 0),
        }))
    return out


class AttnStudent:
    """Loaded student: standardisers + numpy net + teacher worlds."""

    def __init__(self, payload: dict):
        self.cfg = payload.get("cfg") or {}
        self.n_features = int(self.cfg.get("n_features", 161))
        self.net = NumpyTabTransformer(payload["params"],
                                       n_heads=int(payload.get("n_heads", 4)))
        self.x_mean = np.asarray(payload["x_mean"], dtype=np.float64)
        self.x_std = np.asarray(payload["x_std"], dtype=np.float64)
        self.t_mean = np.asarray(payload["t_mean"], dtype=np.float64)
        self.t_std = np.asarray(payload["t_std"], dtype=np.float64)
        self.recipe_meta = payload.get("recipe_meta") or {}
        worlds = payload.get("recipe")
        self.worlds = _recipe_models(worlds) if worlds and worlds.get("models") \
            else None
        if self.worlds is None:
            logger.warning("attn student has no teacher recipe — teacher "
                           "features fall back to neutral (training-time mean)")

    def extend(self, X: np.ndarray, home=None, away=None,
               league="all") -> np.ndarray:
        """(n, 161) raw Pico features -> (n, 174) standardised token input.

        The teacher features are recomputed on the fly from the recipe for
        the given fixture(s); unknown teams get the neutral (mean) row.
        `league` may be a single slug (broadcast) or a per-row array — the
        holdout race passes per-row slugs.
        """
        X = np.asarray(X, dtype=np.float64).reshape(len(X), -1)[:, :self.n_features]
        Xs = (X - self.x_mean[None, :self.n_features]) / \
             self.x_std[None, :self.n_features]
        n = Xs.shape[0]
        if self.worlds is not None and home is not None:
            lg = league if not isinstance(league, str) \
                else np.array([league] * n, dtype=object)
            T = teacher_matrix(self.worlds,
                               np.asarray(home).reshape(-1)[:n],
                               np.asarray(away).reshape(-1)[:n],
                               np.asarray(lg).reshape(-1)[:n])
        else:
            T = np.zeros((n, N_TEACHER), dtype=np.float32)
        Ts = (T.astype(np.float64) - self.t_mean[None, :]) / self.t_std[None, :]
        return np.hstack([Xs, Ts]).astype(np.float32)

    def predict(self, X_ext: np.ndarray) -> dict:
        return self.net.predict(np.asarray(X_ext, dtype=np.float64))


class AttnHead:
    """One market head over the shared student (duck-types a fitted pipe)."""

    needs_teacher = True

    def __init__(self, student: AttnStudent, task: str):
        self.student = student
        self.task = task                      # 'outcome' | 'goals' | 'btts' | 'o35'

    def extend(self, X, home=None, away=None, league: str = "all") -> np.ndarray:
        return self.student.extend(X, home, away, league)

    def predict_proba(self, X) -> np.ndarray:
        X = np.asarray(X, dtype=np.float64)
        need = self.student.n_features + N_TEACHER
        if X.ndim != 2 or X.shape[1] != need:
            raise ValueError(
                f"AttnHead received {X.shape[1] if X.ndim == 2 else X} "
                f"columns, expected {need} (teacher-extended).  Call "
                f"extend(X, home, away, league) first — needs_teacher=True "
                f"on this pipe advertises exactly that.")
        r = self.student.predict(X)
        if self.task == "outcome":
            return r["p_1x2"]
        p = {"goals": r["p_o25"], "btts": r["p_btts"], "o35": r["p_o35"]}[self.task]
        return np.column_stack([1.0 - p, p])


def load_student(path: str) -> AttnStudent | None:
    if not path or not os.path.exists(path):
        return None
    try:
        payload = joblib.load(path)
        return AttnStudent(payload)
    except Exception as e:                       # noqa: BLE001
        logger.warning("attn student %s unreadable: %s", path, e)
        return None


def attach(predictor, path: str) -> AttnStudent | None:
    """Attach the student as all four market heads of a FootballPredictor.

    Returns the student (None when the file is absent/unreadable, in which
    case the predictor keeps its LightGBM heads untouched).
    """
    student = load_student(path)
    if student is None:
        return None
    predictor._outcome_pipe = AttnHead(student, "outcome")
    predictor._goals_pipe = AttnHead(student, "goals")
    predictor._btts_pipe = AttnHead(student, "btts")
    predictor._o35_pipe = AttnHead(student, "o35")
    return student
