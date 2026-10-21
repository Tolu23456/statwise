"""
FootballPredictor – Pico ensemble.

Training modes (selected by retrain.py / MODEL_MODE env):
  'single' – two tuned LightGBM heads (1X2 + over/under 2.5).  Fast, accurate.
  'joint'  – ONE 6-class LightGBM over {H,D,A} x {under,over}; the 1X2 and
             over/under probabilities are marginals of the same joint fit.
"""
from __future__ import annotations
import os, gc, logging, joblib
import numpy as np
import pandas as pd
from typing import Optional

from lightgbm import LGBMClassifier
import lightgbm as lgb
from sklearn.isotonic import IsotonicRegression
from sklearn.pipeline import Pipeline

from .features import FeaturePipeline, N_FEATURES, FEATURE_NAMES

logger = logging.getLogger(__name__)
MODEL_DIR     = os.path.join(os.path.dirname(__file__), '..', 'models')
os.makedirs(MODEL_DIR, exist_ok=True)
OUTCOME_LABELS = ['Home Win', 'Draw', 'Away Win']


def _rss_mb() -> float:
    """Resident set size in MB (Linux runners; harmless elsewhere)."""
    try:
        with open('/proc/self/status') as fh:
            for line in fh:
                if line.startswith('VmRSS:'):
                    return int(line.split()[1]) / 1024.0
    except Exception:
        pass
    return 0.0


def _lgbm_single(n_classes: int, seed: int = 42) -> LGBMClassifier:
    """Tuned production learner: one model replaces the 6-member stack.

    Budgeted for a 4-vCPU runner: max_bin=127 + row-wise histogram roughly
    halve per-tree cost on 330k x 161 with no measurable accuracy loss.
    Early stopping trims the tree count automatically.
    """
    return LGBMClassifier(
        n_estimators=800,
        learning_rate=0.035,
        num_leaves=96,
        max_bin=127,
        min_child_samples=50,
        subsample=0.85,
        subsample_freq=1,
        colsample_bytree=0.7,
        reg_alpha=0.2,
        reg_lambda=2.0,
        force_row_wise=True,
        random_state=seed,
        n_jobs=4,
        verbosity=-1,
    )


class JointOutcomeGoals:
    """One 6-class model: 0-2 = under 2.5 (H,D,A), 3-5 = over 2.5.

    Exposes a 3-class ``predict_proba`` (1X2) or a 2-class one (over/under)
    depending on ``task``, so the rest of the predictor is unchanged while a
    single fit captures the result/total-goals correlation.
    """

    def __init__(self, estimator, task: str = 'outcome'):
        self.estimator = estimator
        self.task = task

    def fit(self, X, y6, sample_weight=None):
        self.estimator.fit(X, y6, sample_weight=sample_weight)
        self.classes_ = np.asarray(self.estimator.classes_)
        return self

    def _probs6(self, X) -> np.ndarray:
        p = np.asarray(self.estimator.predict_proba(X))
        if p.shape[1] == 6:
            return p
        full = np.zeros((len(p), 6), dtype=p.dtype)
        full[:, self.classes_] = p
        return full

    def predict_proba(self, X) -> np.ndarray:
        p = self._probs6(X)
        if self.task == 'outcome':
            return np.column_stack([p[:, 0] + p[:, 3],
                                    p[:, 1] + p[:, 4],
                                    p[:, 2] + p[:, 5]])
        over = p[:, 3] + p[:, 4] + p[:, 5]
        return np.column_stack([1.0 - over, over])


def _fit_with_early_stopping(est, X, y, seed_cb=100, sample_weight=None):
    """Fit a LightGBM estimator, holding out the most recent 5% of rows.

    X is chronological (retrain sorts by date), so the tail slice is a valid
    time-based validation set for early stopping.  It sits strictly before the
    out-of-time holdout used for calibration, so no evaluation leakage.
    """
    n = len(y)
    n_val = min(20000, max(2000, n // 20)) if n >= 40000 else 0
    w = None if sample_weight is None else np.asarray(sample_weight)
    if n_val:
        fit_kw = {}
        if w is not None:
            fit_kw["sample_weight"] = w[:-n_val]
            fit_kw["eval_sample_weight"] = [w[-n_val:]]
        est.fit(
            X[:-n_val], y[:-n_val],
            eval_set=[(X[-n_val:], y[-n_val:])],
            callbacks=[lgb.early_stopping(seed_cb, verbose=False)],
            **fit_kw,
        )
        # sklearn's predict_proba does NOT default to best_iteration_, so trim
        # the booster to the selected tree count explicitly.
        best = getattr(est, 'best_iteration_', None)
        if best and best < est.booster_.num_trees():
            est._Booster = lgb.Booster(
                model_str=est.booster_.model_to_string(num_iteration=best))
    else:
        est.fit(X, y, sample_weight=w)
    return est


class IsotonicCalibrator:
    """Per-class isotonic regression over a fitted estimator's probabilities.

    Used for the ``calibrate=True`` fallback (tiny/absent out-of-time holdout).
    Implemented locally because sklearn 1.8 removed ``cv='prefit'`` from
    CalibratedClassifierCV.
    """

    def __init__(self, estimator):
        self.estimator = estimator

    def fit(self, X, y):
        p = np.asarray(self.estimator.predict_proba(X))
        classes = np.asarray(getattr(self.estimator, 'classes_', np.arange(p.shape[1])))
        y = np.asarray(y)
        self.models_ = []
        for k in range(p.shape[1]):
            iso = IsotonicRegression(out_of_bounds='clip')
            iso.fit(p[:, k], (y == classes[k]).astype(float))
            self.models_.append(iso)
        return self

    def predict_proba(self, X):
        p = np.asarray(self.estimator.predict_proba(X))
        q = np.column_stack([iso.predict(p[:, k]) for k, iso in enumerate(self.models_)])
        q = np.clip(q, 1e-6, None)
        return q / q.sum(axis=1, keepdims=True)


class CalibratedStack(Pipeline):
    """Single-step pipeline with an optional isotonic calibration layer.

    calibrate=True  -> isotonic fitted on the estimator's own predictions
                       (prefit: NO refits of the estimator).
    calibrate=False -> raw probabilities; retrain.py fits an honest calibrator
                       on an out-of-time holdout instead.

    LightGBM needs no feature scaling, so there is deliberately no scaler step
    (it would only add a full float32 copy of the training matrix).
    """
    def fit(self, X, y, calibrate=True, **fit_params):
        super().fit(X, y, **fit_params)
        if calibrate:
            self._fit_prefit_calibrator(X, y)
        else:
            self.calibrator = None
        return self

    def _fit_prefit_calibrator(self, X, y):
        self.calibrator = IsotonicCalibrator(self[-1]).fit(X, y)

    def predict_proba(self, X):
        if getattr(self, 'calibrator', None) is None:
            return self[-1].predict_proba(X)
        return self.calibrator.predict_proba(X)

    @classmethod
    def from_fitted(cls, estimator, X, y, calibrate: bool = False) -> "CalibratedStack":
        """Wrap an ALREADY-FITTED estimator without refitting it."""
        pipe = cls([('model', estimator)])
        if calibrate:
            pipe._fit_prefit_calibrator(X, y)
        else:
            pipe.calibrator = None
        return pipe


def _fit_single(n_classes: int, seed: int, X, y, calibrate: bool,
                sample_weight=None) -> CalibratedStack:
    """One tuned LightGBM head (no scaler, early-stopped on the tail slice)."""
    est = _lgbm_single(n_classes, seed)
    _fit_with_early_stopping(est, X, y, sample_weight=sample_weight)
    best_iter = getattr(est, 'best_iteration_', None)
    logger.info(f"  LightGBM ({n_classes} classes, seed {seed}): "
                f"{best_iter or est.n_estimators} trees used")
    return CalibratedStack.from_fitted(est, X, y, calibrate=calibrate)


def _fit_joint(X, y_1x2, y_goals, calibrate: bool, sample_weight=None):
    """One 6-class LightGBM; returns (outcome_pipe, goals_pipe) sharing the fit."""
    y6 = (np.asarray(y_1x2, dtype=np.int64) + 3 * np.asarray(y_goals, dtype=np.int64))
    est = _lgbm_single(6, 42)
    _fit_with_early_stopping(est, X, y6, sample_weight=sample_weight)
    logger.info(f"  Joint 6-class LightGBM: "
                f"{getattr(est, 'best_iteration_', None) or est.n_estimators} trees used")
    outcome = JointOutcomeGoals(est, 'outcome')
    goals = JointOutcomeGoals(est, 'goals')
    outcome.classes_ = np.asarray(est.classes_)
    goals.classes_ = np.asarray(est.classes_)
    return (CalibratedStack.from_fitted(outcome, X, y_1x2, calibrate=calibrate),
            CalibratedStack.from_fitted(goals, X, y_goals, calibrate=calibrate))


class FootballPredictor:
    def __init__(self, home_advantage: float = 100.0):
        self.home_advantage  = home_advantage
        self.feature_pipe    = FeaturePipeline(home_advantage)
        self._outcome_pipe: Optional[Pipeline] = None
        self._goals_pipe:   Optional[Pipeline] = None
        self._trained        = False
        # honest calibration + market-blend alpha (set by retrain.py / load)
        self._calibrators: Optional[list] = None
        self._alphas: dict = {}
        self._calibration_info: dict = {}

    def train(self, df: pd.DataFrame, calibrate: bool = True,
              mode: str = 'single') -> "FootballPredictor":
        logger.info(f"Grandmaster Training starting on {len(df):,} records…")
        self.feature_pipe.fit(df)
        X, y_1x2, y_goals, dates = self.feature_pipe.build_training_set(df)
        return self.fit_stacks(X, y_1x2, y_goals, calibrate=calibrate, mode=mode)

    def fit_stacks(self, X: np.ndarray, y_1x2: np.ndarray, y_goals: np.ndarray,
                   calibrate: bool = True, mode: str = 'single',
                   sample_weight=None) -> "FootballPredictor":
        """Fit the outcome + goals heads on a prebuilt feature matrix.

        Split out from train() so retrain.py can build features ONCE for the
        full frame, slice an out-of-time train/holdout split, and fit only on
        the train slice without re-deriving feature state.

        mode: 'single' (default, 2x LightGBM) | 'joint' (1x 6-class LightGBM).
        """
        mode = (mode or 'single').lower()
        if mode not in ('single', 'joint'):
            logger.warning(f"Unknown MODEL_MODE '{mode}' — falling back to 'single'")
            mode = 'single'
        X = np.nan_to_num(X, nan=0.0, posinf=1e6, neginf=-1e6)
        # float32 halves every in-memory copy (330k x 161 is ~213 MB instead of
        # ~426 MB); tree learners only use float32 splits anyway.
        X = np.ascontiguousarray(X, dtype=np.float32)
        logger.info(f"Training matrix: {X.shape} float32 = {X.nbytes / 1e6:.0f} MB, "
                    f"{_rss_mb():.0f} MB RSS | mode={mode}")

        if mode == 'joint':
            logger.info("Fitting joint 6-class outcome+goals model…")
            self._outcome_pipe, self._goals_pipe = _fit_joint(
                X, y_1x2, y_goals, calibrate, sample_weight)
        else:
            logger.info("Step 1/2  Fitting LightGBM 1X2 head…")
            self._outcome_pipe = _fit_single(3, 42, X, y_1x2, calibrate,
                                             sample_weight)
            gc.collect()
            logger.info("Step 2/2  Fitting LightGBM over/under 2.5 head…")
            self._goals_pipe = _fit_single(2, 99, X, y_goals, calibrate,
                                           sample_weight)
        logger.info(f"Training done, {_rss_mb():.0f} MB RSS")

        self._trained = True
        return self

    def attach_calibration(self, calibrators: list, alphas: dict, info: dict) -> None:
        """Attach honest holdout-fitted isotonic calibrators + market alphas."""
        self._calibrators = calibrators
        self._alphas = alphas or {}
        self._calibration_info = info or {}

    def save(self, path: str):
        joblib.dump({
            'feature_pipe': self.feature_pipe,
            'outcome_pipe': self._outcome_pipe,
            'goals_pipe':   self._goals_pipe,
            'n_features':   N_FEATURES,
            'calibrators':  self._calibrators,
            'alphas':       self._alphas,
            'calibration_info': self._calibration_info,
        }, path, compress=3)

    @classmethod
    def load(cls, path: str = None) -> "FootballPredictor":
        path = path or os.path.join(MODEL_DIR, 'football_predictor.pkl')
        data = joblib.load(path)
        obj = cls.__new__(cls)
        obj.feature_pipe = data['feature_pipe']
        obj._outcome_pipe = data['outcome_pipe']
        obj._goals_pipe = data['goals_pipe']
        obj._trained = True
        obj._calibrators = data.get('calibrators')
        obj._alphas = data.get('alphas') or {}
        obj._calibration_info = data.get('calibration_info') or {}
        return obj

    def predict_match(self, home: str, away: str, league: str = 'all', history: pd.DataFrame = None, odds_home: float = 0, odds_draw: float = 0, odds_away: float = 0, weather: dict | None = None):
        home_squad_value = history[history["home_team"] == home]["home_squad_value"].tail(1).values[0] if "home_squad_value" in history.columns and not history[history["home_team"] == home].empty else 0
        away_squad_value = history[history["away_team"] == away]["away_squad_value"].tail(1).values[0] if "away_squad_value" in history.columns and not history[history["away_team"] == away].empty else 0
        home_avg_age = history[history["home_team"] == home]["home_avg_age"].tail(1).values[0] if "home_avg_age" in history.columns and not history[history["home_team"] == home].empty else 25
        away_avg_age = history[history["away_team"] == away]["away_avg_age"].tail(1).values[0] if "away_avg_age" in history.columns and not history[history["away_team"] == away].empty else 25
        home_lineup_rating = history[history["home_team"] == home]["home_lineup_rating"].tail(1).values[0] if "home_lineup_rating" in history.columns and not history[history["home_team"] == home].empty else 70
        away_lineup_rating = history[history["away_team"] == away]["away_lineup_rating"].tail(1).values[0] if "away_lineup_rating" in history.columns and not history[history["away_team"] == away].empty else 70
        if history is None or history.empty:
            history = pd.DataFrame(columns=['date', 'home_team', 'away_team', 'home_goals', 'away_goals'])

        if weather and "temperature_c" in weather:  # normalize enricher-style keys
            weather = {"temp_c": weather.get("temperature_c"), "precip_mm": weather.get("precipitation_mm"),
                       "wind_kmh": weather.get("wind_speed_kmh", weather.get("wind_kmh")),
                       "weather_code": weather.get("weather_code")}
        X = self.feature_pipe.build_features([{"home": home, "away": away, "league": league, "odds_home": odds_home, "odds_draw": odds_draw, "odds_away": odds_away, "home_squad_value": home_squad_value, "away_squad_value": away_squad_value, "home_avg_age": home_avg_age, "away_avg_age": away_avg_age, "home_lineup_rating": home_lineup_rating, "away_lineup_rating": away_lineup_rating, "weather": weather}], history)
        # single-row features can carry NaN (missing squad value / weather /
        # unknown team elo).  Training sanitizes the matrix the same way — keep
        # inference consistent so NaN never reaches the scaler or the net.
        X = np.nan_to_num(np.asarray(X, dtype=np.float64), nan=0.0, posinf=1e6, neginf=-1e6)

        p_outcome_raw = self._outcome_pipe.predict_proba(X)[0]
        p_goals = self._goals_pipe.predict_proba(X)[0]
        p_outcome_cal = p_outcome_raw.copy()

        # 1) honest isotonic calibration (fitted on out-of-time holdout by retrain.py)
        calibs = getattr(self, '_calibrators', None)
        if calibs:
            p_outcome_cal = np.array(
                [float(iso.predict([float(p_outcome_raw[i])])[0]) for i, iso in enumerate(calibs)])
            p_outcome_cal = np.clip(p_outcome_cal, 1e-6, None)
            p_outcome_cal = p_outcome_cal / p_outcome_cal.sum()

        p_outcome = p_outcome_cal.copy()

        # 2) market blend: alpha*model + (1-alpha)*de-vigged market odds.
        #    alpha learned per-league on the holdout (log-loss minimizing).
        alphas = getattr(self, '_alphas', None) or {}
        if alphas and odds_home and odds_draw and odds_away \
                and odds_home > 1.01 and odds_draw > 1.01 and odds_away > 1.01:
            imp = np.array([1.0 / odds_home, 1.0 / odds_draw, 1.0 / odds_away])
            mkt = imp / imp.sum()
            alpha = alphas.get(str(league), alphas.get('_default'))
            if alpha is not None:
                p_outcome = float(alpha) * p_outcome + (1.0 - float(alpha)) * mkt
                p_outcome = np.clip(p_outcome, 1e-12, None)
                p_outcome = p_outcome / p_outcome.sum()

        # Uncertainty estimation: entropy + ensemble/head disagreement
        eps_ent = 1e-12
        p_for_ent = np.clip(p_outcome, eps_ent, 1.0)
        entropy = float(-np.sum(p_for_ent * np.log(p_for_ent)))
        max_entropy = float(-np.log(1.0 / 3.0))  # ~1.098612
        entropy_norm = float(entropy / max_entropy) if max_entropy > 0 else 0.0

        # Head disagreement: compare raw outcome head vs calibrated outcome head
        head_disagreement = float(np.mean(np.abs(p_outcome_raw - p_outcome_cal)))

        # If market blend pushed it far from model, also count as disagreement
        if alphas and odds_home and odds_draw and odds_away:
            head_disagreement = float(max(head_disagreement, np.mean(np.abs(p_outcome_cal - p_outcome)) * 0.5))

        # High uncertainty if entropy near max or heads disagree a lot
        is_uncertain = bool(entropy_norm >= 0.92 or head_disagreement >= 0.08 or (float(np.max(p_outcome)) * 100.0 < 52.0))

        idx = np.argmax(p_outcome)

        # Compute real statistics from history
        def get_stats(team):
            th = history[(history['home_team'] == team) | (history['away_team'] == team)].sort_values('date').tail(5)
            form = []
            for _, r in th.iterrows():
                if r['home_team'] == team:
                    form.append(1.0 if r['home_goals'] > r['away_goals'] else (0.5 if r['home_goals'] == r['away_goals'] else 0.0))
                else:
                    form.append(1.0 if r['away_goals'] > r['home_goals'] else (0.5 if r['home_goals'] == r['away_goals'] else 0.0))
            return form

        h2h = history[((history['home_team'] == home) & (history['away_team'] == away)) | ((history['home_team'] == away) & (history['away_team'] == home))]
        hw = len(h2h[((h2h['home_team'] == home) & (h2h['home_goals'] > h2h['away_goals'])) | ((h2h['away_team'] == home) & (h2h['away_goals'] > h2h['home_goals']))])
        aw = len(h2h[((h2h['home_team'] == away) & (h2h['home_goals'] > h2h['away_goals'])) | ((h2h['away_team'] == away) & (h2h['away_goals'] > h2h['home_goals']))])
        dr = len(h2h) - hw - aw

        total_h2h = max(len(h2h), 1)
        avg_g = (h2h['home_goals'] + h2h['away_goals']).mean() if not h2h.empty else 2.5

        stats = {
            "h2h": {"home_wins": int(hw/total_h2h*100), "draws": int(dr/total_h2h*100), "away_wins": int(aw/total_h2h*100)},
            "home_form": get_stats(home),
            "away_form": get_stats(away),
            "avg_goals": round(float(avg_g), 2),
            "home_value": float(X[0, 125]),
            "away_value": float(X[0, 126]),
            "home_rating": float(X[0, 130]),
            "away_rating": float(X[0, 131]),
            # pre-match expected goals — consumed by ai/odds_sync.py in-play mode
            "lambda_h": float(X[0, FEATURE_NAMES.index("lambda_h")]),
            "lambda_a": float(X[0, FEATURE_NAMES.index("lambda_a")]),
        }

        # Dynamic reasoning based on model insights
        conf = int(p_outcome[idx] * 100)
        reasoning = f"Pico Analysis: {OUTCOME_LABELS[idx]} predicted with {conf}% confidence. "
        if stats['h2h']['home_wins'] > 50:
            reasoning += f"{home} dominates the historical H2H matchup. "
        elif stats['h2h']['away_wins'] > 50:
            reasoning += f"{away} has a strong historical advantage in this fixture. "

        avg_f_h = sum(stats['home_form'])/len(stats['home_form']) if stats['home_form'] else 0.5
        avg_f_a = sum(stats['away_form'])/len(stats['away_form']) if stats['away_form'] else 0.5
        if avg_f_h > 0.7: reasoning += f"{home} is in elite form. "
        if avg_f_a > 0.7: reasoning += f"{away} is in elite form. "

        return {
            'prediction': OUTCOME_LABELS[idx],
            'confidence': conf,
            'prob_home': round(float(p_outcome[0])*100, 1),
            'prob_draw': round(float(p_outcome[1])*100, 1),
            'prob_away': round(float(p_outcome[2])*100, 1),
            'prob_over25': round(float(p_goals[1])*100, 1) if len(p_goals)>1 else 50.0,
            'stats': stats,
            'reasoning': reasoning.strip(),
            'uncertainty': {
                'entropy_norm': round(float(entropy_norm), 3),
                'head_disagreement': round(float(head_disagreement), 3),
                'is_uncertain': bool(is_uncertain),
            },
        }
