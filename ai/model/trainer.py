"""
FootballPredictor – Pico ensemble.

Training modes (selected by retrain.py / MODEL_MODE env):
  'single' – two tuned LightGBM heads (1X2 + over/under 2.5).  Fast, accurate.
  'joint'  – ONE 6-class LightGBM over {H,D,A} x {under,over}; the 1X2 and
             over/under probabilities are marginals of the same joint fit.
"""
from __future__ import annotations
import os, gc, logging, joblib, difflib
import numpy as np
import pandas as pd
from typing import Optional

from lightgbm import LGBMClassifier
import lightgbm as lgb
from sklearn.isotonic import IsotonicRegression
from sklearn.pipeline import Pipeline

from .features import FeaturePipeline, N_FEATURES, FEATURE_NAMES
from .leagues import canon_league

logger = logging.getLogger(__name__)
MODEL_DIR     = os.path.join(os.path.dirname(__file__), '..', 'models')
os.makedirs(MODEL_DIR, exist_ok=True)
OUTCOME_LABELS = ['Home Win', 'Draw', 'Away Win']

# Feeds disagree on club names ("Barcelona" vs "FC Barcelona", "Stade Rennais"
# vs "Stade Rennais FC 1901"). Without reconciliation the form and head-to-head
# lookups silently return nothing, which is why those panels were blank.
_TEAM_ALIAS_CACHE: dict[str, str] = {}


def _load_insight_data() -> Optional[pd.DataFrame]:
    """Load the committed clean season files for the analysis snapshot.

    Only called when a cached pickle predates `recent_history`. The files are
    already part of the checkout, and reading them costs a few seconds once
    per process — far better than rendering empty form/H2H panels.
    """
    import glob
    clean_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             '..', 'data', 'clean')
    paths = sorted(glob.glob(os.path.join(clean_dir, '????_matches.csv')))
    if not paths:
        return None
    frames = []
    for p in paths:
        try:
            frames.append(pd.read_csv(p, low_memory=False))
        except Exception:
            continue
    return pd.concat(frames, ignore_index=True) if frames else None


def _canonical_team(name: str, candidates: set[str], freq: dict | None = None) -> str:
    """Map a feed's team name onto the spelling used in the historical data."""
    if not name or not candidates:
        return name
    if name in candidates:
        return name
    cached = _TEAM_ALIAS_CACHE.get(name)
    if cached in candidates:
        return cached
    needle = name.strip().lower()
    max_freq = max(freq.values()) if freq else 1
    best, best_score = name, 0.0
    for cand in candidates:
        hay = cand.strip().lower()
        if hay == needle:
            best, best_score = cand, 1.0
            break
        # Cheap containment pre-check keeps this fast across ~2k club names.
        if needle in hay or hay in needle:
            score = difflib.SequenceMatcher(None, needle, hay).ratio()
            # A name that *ends* with the feed's name is nearly always the
            # senior club ("FC Barcelona" vs the reserve side "Barcelona B").
            if hay.endswith(needle):
                score += 0.12
            # Frequency breaks remaining ties between lookalikes (FC Barcelona
            # vs Barcelona SC); the club that actually appears in the data most
            # is the one a fixture is almost always referring to.
            if freq:
                score += 0.03 * (freq.get(cand, 0) / max_freq)
            if score > best_score:
                best, best_score = cand, score
    resolved = best if best_score >= 0.72 else name
    _TEAM_ALIAS_CACHE[name] = resolved
    return resolved


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
        # Plan 1.6: auxiliary binary heads for BTTS and over/under 3.5.
        # None until a retrain with the labels supplies them — predict_match
        # then simply omits prob_btts/prob_over35 (older pickles predate them).
        self._btts_pipe: Optional[Pipeline] = None
        self._o35_pipe:  Optional[Pipeline] = None
        self._trained        = False
        # honest calibration + market-blend alpha (set by retrain.py / load)
        self._calibrators: Optional[list] = None
        self._alphas: dict = {}
        self._calibration_info: dict = {}
        # How many historical matches each league contributed to training.
        # Persisted so we can tell a fixture backed by real league-specific
        # form from one the model is guessing at from pooled priors (cup ties
        # like the Europa/Conference League have no season CSV of their own).
        self._league_counts: dict = {}
        # Recent matches per team, used only for the analysis panel (form, H2H).
        self._recent_history = None

    @property
    def league_counts(self) -> dict:
        """Per-league match counts seen during training ({} = unknown)."""
        return self._league_counts or {}

    @property
    def recent_history(self):
        """Recent matches per team, for the human-readable analysis panel.

        The fitted pipeline already encodes the bulk history, but the app needs
        *recent* results to show form and head-to-head. Without this the panel
        was always empty at inference time because the engine's history frame is
        not part of the pickle. Kept deliberately small: the last few matches
        per team, unioned.
        """
        return self._recent_history if self._recent_history is not None else None

    def record_recent_history(self, df: pd.DataFrame, per_team: int = 40) -> None:
        """Store the most recent matches per team for display-time statistics."""
        try:
            cols = [c for c in ("date", "home_team", "away_team", "home_goals",
                                "away_goals", "home_squad_value", "away_squad_value",
                                "home_avg_age", "away_avg_age", "home_lineup_rating",
                                "away_lineup_rating") if c in getattr(df, "columns", [])]
            if "date" not in cols or "home_team" not in cols:
                return
            d = df[cols].sort_values("date")
            parts = []
            for _, g in d.groupby("home_team"):
                parts.append(g.tail(per_team))
            for _, g in d.groupby("away_team"):
                parts.append(g.tail(per_team))
            if not parts:
                return
            recent = pd.concat(parts).drop_duplicates()
            self._recent_history = recent.reset_index(drop=True)
            logger.info(f"Recorded recent history for the analysis panel: "
                        f"{len(self._recent_history):,} rows")
        except Exception as e:  # noqa: BLE001
            logger.debug(f"Could not record recent history: {e}")

    def record_league_support(self, df: pd.DataFrame) -> None:
        """Remember per-league sample sizes from the training frame."""
        try:
            cols = getattr(df, "columns", [])
            # The live history frame calls it 'league'; the retrain frame calls
            # it 'league_slug'. Accept either rather than silently recording {}.
            for col in ("league", "league_slug"):
                if col in cols:
                    self._league_counts = {
                        str(k): int(v) for k, v in df[col].value_counts().items()
                    }
                    return
        except Exception as e:  # noqa: BLE001
            logger.debug(f"Could not record league support: {e}")

    def train(self, df: pd.DataFrame, calibrate: bool = True,
              mode: str = 'single') -> "FootballPredictor":
        logger.info(f"Pico Training starting on {len(df):,} records…")
        self.record_league_support(df)
        self.record_recent_history(df)
        self.feature_pipe.fit(df)
        X, y_1x2, y_goals, dates = self.feature_pipe.build_training_set(df)
        # plan 1.6: fit the BTTS/O3.5 aux heads whenever the feature build
        # supplied the labels (it always does — see last_meta)
        _meta = getattr(self.feature_pipe, "last_meta", {}) or {}
        return self.fit_stacks(X, y_1x2, y_goals, calibrate=calibrate, mode=mode,
                               y_btts=_meta.get("y_btts"),
                               y_o35=_meta.get("y_o35"))

    def fit_stacks(self, X: np.ndarray, y_1x2: np.ndarray, y_goals: np.ndarray,
                   calibrate: bool = True, mode: str = 'single',
                   sample_weight=None,
                   league_counts: dict | None = None,
                   y_btts: np.ndarray | None = None,
                   y_o35: np.ndarray | None = None) -> "FootballPredictor":
        """Fit the outcome + goals heads on a prebuilt feature matrix.

        Split out from train() so retrain.py can build features ONCE for the
        full frame, slice an out-of-time train/holdout split, and fit only on
        the train slice without re-deriving feature state.

        mode: 'single' (default, 2x LightGBM) | 'joint' (1x 6-class LightGBM).

        Plan 1.6: `y_btts` / `y_o35` optionally fit AUXILIARY binary heads for
        the BTTS and over/under 3.5 markets.  They are independent binary
        tasks, so they get their own 2-class heads regardless of the main
        mode; absent labels leave the heads None and inference omits them.
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
            logger.info("Fitting LightGBM 1X2 head…")
            self._outcome_pipe = _fit_single(3, 42, X, y_1x2, calibrate,
                                             sample_weight)
            gc.collect()
            logger.info("Fitting LightGBM over/under 2.5 head…")
            self._goals_pipe = _fit_single(2, 99, X, y_goals, calibrate,
                                           sample_weight)
            gc.collect()

        # Plan 1.6 — auxiliary market heads (fit for BOTH main modes: they are
        # standalone binary tasks sharing only the feature matrix).  Labels
        # come from last_meta["y_btts"/"y_o35"], extended by distillation the
        # same way y_1x2/y_goals are, so lengths always match X here.
        self._btts_pipe = None
        self._o35_pipe = None
        for label, y_aux, seed, attr in (("BTTS", y_btts, 7, "_btts_pipe"),
                                         ("over/under 3.5", y_o35, 14, "_o35_pipe")):
            if y_aux is None:
                continue
            y_aux = np.asarray(y_aux, dtype=np.int64)
            if len(y_aux) != len(X):
                logger.warning(f"  Skipping {label} head: {len(y_aux)} labels "
                               f"for {len(X)} rows")
                continue
            if len(np.unique(y_aux)) < 2:
                logger.warning(f"  Skipping {label} head: single-class labels")
                continue
            logger.info(f"Fitting LightGBM {label} head…")
            setattr(self, attr, _fit_single(2, seed, X, y_aux, calibrate,
                                            sample_weight))
            gc.collect()
        logger.info(f"Training done, {_rss_mb():.0f} MB RSS")

        # retrain.py builds the frame itself and calls us directly, so league
        # support has to be capturable here as well as via train().
        if league_counts:
            self._league_counts = {str(k): int(v) for k, v in league_counts.items()}
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
            # Plan 1.6 auxiliary heads — None for pickles trained without the
            # BTTS/O35 labels, which is exactly what inference should expect.
            'btts_pipe':    self._btts_pipe,
            'o35_pipe':     self._o35_pipe,
            'n_features':   N_FEATURES,
            'calibrators':  self._calibrators,
            'alphas':       self._alphas,
            'calibration_info': self._calibration_info,
            # Written last, and read with .get() so a pickle produced before
            # this key existed still loads.
            'league_counts': self._league_counts,
            # Compact recent-results snapshot for the analysis panel.
            'recent_history': self._recent_history,
        }, path, compress=3)

    @classmethod
    def load(cls, path: str = None) -> "FootballPredictor":
        path = path or os.path.join(MODEL_DIR, 'football_predictor.pkl')
        data = joblib.load(path)
        obj = cls.__new__(cls)
        obj.feature_pipe = data['feature_pipe']
        obj._outcome_pipe = data['outcome_pipe']
        obj._goals_pipe = data['goals_pipe']
        # .get(): pickles from before plan 1.6 simply have no aux heads.
        obj._btts_pipe = data.get('btts_pipe')
        obj._o35_pipe = data.get('o35_pipe')
        obj._trained = True
        obj._calibrators = data.get('calibrators')
        obj._alphas = data.get('alphas') or {}
        obj._calibration_info = data.get('calibration_info') or {}
        # Older pickles predate this key; {} means "support unknown", which the
        # predictor must treat as *not* thin rather than as thin — otherwise a
        # stale model file would silently suppress every AI Pick.
        obj._league_counts = data.get('league_counts') or {}
        obj._recent_history = data.get('recent_history')
        if obj._recent_history is None or len(obj._recent_history) == 0:
            # The cached model may predate the snapshot (the training workflow
            # writes it, and may not have run since). Rebuild it from the
            # committed clean season files so the analysis panel still shows
            # real form and head-to-head instead of falling back to blanks.
            try:
                fallback = _load_insight_data()
                if fallback is not None and not fallback.empty:
                    obj.record_recent_history(fallback)
            except Exception as e:  # noqa: BLE001
                logger.debug(f"Could not rebuild insight history: {e}")
        return obj

    def predict_match(self, home: str, away: str, league: str = 'all', history: pd.DataFrame = None, odds_home: float = 0, odds_draw: float = 0, odds_away: float = 0, weather: dict | None = None, insight_history: pd.DataFrame = None, match_date: str | None = None, feature_blocks=None):
        cols = ['date', 'home_team', 'away_team', 'home_goals', 'away_goals']
        # Fixture feeds spell six competitions differently from the season
        # CSVs (efl-championship vs championship); canonicalising once here
        # fixes league priors, league_id, per-league alphas and league support
        # in a single place.  See model/leagues.py.
        league = canon_league(league)
        # `history` drives the feature pipeline, whose fitted state already
        # encodes the bulk training set. `insight_history` is the compact
        # recent-results snapshot used only for the human-readable analysis
        # panel — so a small frame there can never alter the prediction itself.
        if history is None or history.empty:
            history = pd.DataFrame(columns=cols)
        stats_hist = insight_history if (insight_history is not None
                                         and not insight_history.empty) else history
        # Reconcile feed names with the historical spellings once per fixture.
        _freq = None
        if not stats_hist.empty and 'home_team' in stats_hist.columns:
            _cands = set(stats_hist['home_team'].dropna().unique()) | \
                     set(stats_hist['away_team'].dropna().unique())
            _freq = pd.concat([stats_hist['home_team'], stats_hist['away_team']]) \
                .dropna().value_counts().to_dict()
        else:
            _cands = set()
        home_key = _canonical_team(home, _cands, _freq)
        away_key = _canonical_team(away, _cands, _freq)

        def _latest(frame, side_col, team, col, default):
            try:
                if col in frame.columns:
                    sel = frame[frame[side_col] == team]
                    if not sel.empty:
                        val = sel[col].tail(1).values[0]
                        if pd.notna(val):
                            return val
            except Exception:
                pass
            return default

        home_squad_value = _latest(stats_hist, "home_team", home_key, "home_squad_value", 0)
        away_squad_value = _latest(stats_hist, "away_team", away_key, "away_squad_value", 0)
        home_avg_age = _latest(stats_hist, "home_team", home_key, "home_avg_age", 25)
        away_avg_age = _latest(stats_hist, "away_team", away_key, "away_avg_age", 25)
        home_lineup_rating = _latest(stats_hist, "home_team", home_key, "home_lineup_rating", 70)
        away_lineup_rating = _latest(stats_hist, "away_team", away_key, "away_lineup_rating", 70)

        if weather and "temperature_c" in weather:  # normalize enricher-style keys
            weather = {"temp_c": weather.get("temperature_c"), "precip_mm": weather.get("precipitation_mm"),
                       "wind_kmh": weather.get("wind_speed_kmh", weather.get("wind_kmh")),
                       "weather_code": weather.get("weather_code")}
        X = self.feature_pipe.build_features(
            [{"home": home, "away": away, "league": league,
              # the fixture's own date: without it the travel/rest slots read
              # NaT (they are the only features keyed on kickoff, not "now")
              "date": match_date,
              "odds_home": odds_home, "odds_draw": odds_draw, "odds_away": odds_away,
              "home_squad_value": home_squad_value, "away_squad_value": away_squad_value,
              "home_avg_age": home_avg_age, "away_avg_age": away_avg_age,
              "home_lineup_rating": home_lineup_rating, "away_lineup_rating": away_lineup_rating,
              "weather": weather}],
            history, blocks=feature_blocks)
        # single-row features can carry NaN (missing squad value / weather /
        # unknown team elo).  Training sanitizes the matrix the same way — keep
        # inference consistent so NaN never reaches the scaler or the net.
        X = np.nan_to_num(np.asarray(X, dtype=np.float64), nan=0.0, posinf=1e6, neginf=-1e6)

        # Plan 2.4: the attention student needs the lab-teacher posterior
        # tokens appended — advertised via needs_teacher on the pipe.  Extend
        # ONCE here and hand every head the same extended row; LightGBM pipes
        # never set the flag, so their serving path is byte-identical.
        if getattr(self._outcome_pipe, "needs_teacher", False):
            X = self._outcome_pipe.extend(X, [home], [away], league)

        p_outcome_raw = self._outcome_pipe.predict_proba(X)[0]
        p_goals = self._goals_pipe.predict_proba(X)[0]
        # Plan 1.6 auxiliary heads — absent on older pickles, in which case
        # the goal-market rows below simply don't render.
        _btts = getattr(self, '_btts_pipe', None)
        _o35 = getattr(self, '_o35_pipe', None)
        p_btts = _btts.predict_proba(X)[0] if _btts is not None else None
        p_o35 = _o35.predict_proba(X)[0] if _o35 is not None else None
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

        # Certainty score (0-100): how sure the model really is.
        # Primary signal = margin over the 2nd-most-likely outcome (a 69/17/14
        # split is far more decisive than a 45/30/25 split even though both have
        # similar "confidence"). Blended with the top probability and damped
        # by how much the model heads disagree.
        sorted_p = np.sort(p_outcome)[::-1]
        p1 = float(sorted_p[0])
        p2 = float(sorted_p[1])
        margin = (p1 - p2) * 100.0                       # gap in percentage points
        margin_ratio = float((p1 - p2) / p1) if p1 > 0 else 0.0
        agreement = 1.0 - min(1.0, float(head_disagreement) * 4.0)
        certainty = float(
            100.0 * (0.55 * p1 + 0.45 * margin_ratio) * (0.85 + 0.15 * agreement)
        )
        certainty = float(max(0.0, min(100.0, certainty)))

        idx = np.argmax(p_outcome)

        # Compute real statistics from history
        def get_stats(team):
            th = stats_hist[(stats_hist['home_team'] == team) | (stats_hist['away_team'] == team)].sort_values('date').tail(5)
            form = []
            for _, r in th.iterrows():
                if r['home_team'] == team:
                    form.append(1.0 if r['home_goals'] > r['away_goals'] else (0.5 if r['home_goals'] == r['away_goals'] else 0.0))
                else:
                    form.append(1.0 if r['away_goals'] > r['home_goals'] else (0.5 if r['home_goals'] == r['away_goals'] else 0.0))
            return form

        h2h = stats_hist[((stats_hist['home_team'] == home_key) & (stats_hist['away_team'] == away_key)) | ((stats_hist['home_team'] == away_key) & (stats_hist['away_team'] == home_key))]
        hw = len(h2h[((h2h['home_team'] == home_key) & (h2h['home_goals'] > h2h['away_goals'])) | ((h2h['away_team'] == home_key) & (h2h['away_goals'] > h2h['home_goals']))])
        aw = len(h2h[((h2h['home_team'] == away_key) & (h2h['home_goals'] > h2h['away_goals'])) | ((h2h['away_team'] == away_key) & (h2h['away_goals'] > h2h['home_goals']))])
        dr = len(h2h) - hw - aw

        total_h2h = max(len(h2h), 1)
        # None (not 2.5) when there is no head-to-head data, so the UI can omit
        # the row instead of inventing a league-average number.
        avg_g = (h2h['home_goals'] + h2h['away_goals']).mean() if not h2h.empty else None

        stats = {
            "h2h": {"home_wins": int(hw/total_h2h*100), "draws": int(dr/total_h2h*100), "away_wins": int(aw/total_h2h*100)},
            "h2h_matches": int(len(h2h)),
            "home_form": get_stats(home_key),
            "away_form": get_stats(away_key),
            "avg_goals": round(float(avg_g), 2) if avg_g is not None else None,
            "home_value": float(home_squad_value or 0),
            "away_value": float(away_squad_value or 0),
            "home_rating": float(home_lineup_rating or 0),
            "away_rating": float(away_lineup_rating or 0),
            # pre-match expected goals — consumed by ai/odds_sync.py in-play mode
            "lambda_h": float(X[0, FEATURE_NAMES.index("lambda_h")]),
            "lambda_a": float(X[0, FEATURE_NAMES.index("lambda_a")]),
            # over/under 2.5 from the goals head — previously computed and then
            # thrown away, so the app had no goal-market view at all
            "prob_over25": round(float(p_goals[1]) * 100, 1) if len(p_goals) > 1 else None,
            # plan 1.6: BTTS + over/under 3.5 from the auxiliary heads
            "prob_btts": round(float(p_btts[1]) * 100, 1)
                         if p_btts is not None and len(p_btts) > 1 else None,
            "prob_over35": round(float(p_o35[1]) * 100, 1)
                           if p_o35 is not None and len(p_o35) > 1 else None,
        }

        # ── Analysis narrative ────────────────────────────────────────────
        # Written from the numbers the model actually used, so no two fixtures
        # read alike. Ordered by what matters most: the probability split, then
        # recent form, head-to-head, goal expectancy, and finally how the market
        # compares. Every clause is conditional on the data being present —
        # nothing is asserted that the model did not look at.
        pick = OUTCOME_LABELS[idx]
        conf = int(p_outcome[idx] * 100)
        ph, pd_pct, pa = (float(p_outcome[0]) * 100, float(p_outcome[1]) * 100,
                          float(p_outcome[2]) * 100)
        po25 = round(float(p_goals[1]) * 100, 1) if len(p_goals) > 1 else None

        parts: list[str] = []

        # 1. The verdict, expressed through the actual probability split.
        ranked = sorted([ph, pd_pct, pa], reverse=True)
        gap = ranked[0] - ranked[1]
        if gap >= 25:
            lean = "a clear lean"
        elif gap >= 12:
            lean = "a definite edge"
        elif gap >= 6:
            lean = "a narrow edge"
        else:
            lean = "a near coin-flip"
        parts.append(f"Pico splits this {ph:.0f}% {home} / {pd_pct:.0f}% draw / "
                     f"{pa:.0f}% {away} — {lean} towards {pick}")

        # 2. Recent form, with the points actually taken.
        def _form_line(team, form):
            if not form:
                return None
            w = sum(1 for f in form if f == 1.0)
            d = sum(1 for f in form if f == 0.5)
            l = len(form) - w - d
            return (f"{team} {w}W-{d}D-{l}L in their last {len(form)} "
                    f"({w * 3 + d}/{len(form) * 3} pts)")

        hf = _form_line(home, stats['home_form'])
        af = _form_line(away, stats['away_form'])
        if hf and af:
            parts.append(f"{hf}, against {af}")
        elif hf or af:
            parts.append(hf or af)

        # 3. Head-to-head, using real counts rather than only percentages.
        if len(h2h) > 0:
            h2h_line = (f"Head-to-head {home} {hw}W-{dr}D-{aw}L in {len(h2h)} "
                        f"meeting(s)")
            if avg_g is not None:
                h2h_line += f", averaging {avg_g:.1f} goals"
            parts.append(h2h_line)
        else:
            parts.append("No previous meetings between these two in the training data")

        # 4. Goal expectancy — only when the value is plausible. The feature is
        # clamped to 0.1 when a team has no usable history, and reporting
        # "0.2 goals" would be worse than saying nothing.
        lh, la = stats.get('lambda_h'), stats.get('lambda_a')
        if lh and la and 0.5 <= (lh + la) <= 8.0:
            gline = f"Pico expects {lh + la:.1f} goals ({lh:.1f} v {la:.1f})"
            if po25 is not None:
                gline += f", with over 2.5 rated at {po25:.0f}%"
            parts.append(gline)

        # 5. Market comparison — only when real prices exist.
        if odds_home and odds_draw and odds_away:
            imp = np.array([1.0 / odds_home, 1.0 / odds_draw, 1.0 / odds_away])
            mkt = imp / imp.sum() * 100
            model_pick = (ph, pd_pct, pa)[idx]
            delta = model_pick - mkt[idx]
            if delta >= 2:
                verdict = "above the market, which is where the value sits"
            elif delta <= -2:
                verdict = "below the market, so the price offers no edge"
            else:
                verdict = "in line with the market"
            parts.append(f"The market implies {mkt[idx]:.0f}% for {pick} — Pico is "
                         f"{abs(delta):.0f} points {verdict}")

        # 6. How much to trust it.
        if certainty >= 70:
            parts.append(f"Certainty is high at {certainty:.0f}%")
        elif certainty >= 55:
            parts.append(f"Certainty is moderate at {certainty:.0f}%")
        else:
            parts.append(f"Certainty is low at {certainty:.0f}%, so treat this as one "
                         f"scenario rather than a forecast")

        # 7. Honest caveats, stated rather than hidden.
        if is_uncertain:
            parts.append("The outcomes are tightly bunched, which is why this is not "
                         "promoted to an AI Pick")
        n_league = 0
        try:
            if history is not None and 'league' in getattr(history, 'columns', []):
                n_league = int((history['league'] == league).sum())
        except Exception:
            n_league = 0
        if 0 < n_league < 200:
            parts.append(f"Only {n_league} matches of league-specific history back this "
                         f"competition, so the model leans on pooled priors")

        reasoning = ". ".join(p.rstrip(".") for p in parts) + "."

        return {
            'prediction': OUTCOME_LABELS[idx],
            'confidence': conf,
            'prob_home': round(float(p_outcome[0])*100, 1),
            'prob_draw': round(float(p_outcome[1])*100, 1),
            'prob_away': round(float(p_outcome[2])*100, 1),
            'prob_over25': round(float(p_goals[1])*100, 1) if len(p_goals)>1 else 50.0,
            # plan 1.6 goal markets (None when the pickle has no aux heads —
            # the UI renders those rows only when a number exists)
            'prob_btts': round(float(p_btts[1])*100, 1)
                         if p_btts is not None and len(p_btts) > 1 else None,
            'prob_over35': round(float(p_o35[1])*100, 1)
                           if p_o35 is not None and len(p_o35) > 1 else None,
            'stats': stats,
            'reasoning': reasoning.strip(),
            'uncertainty': {
                'entropy_norm': round(float(entropy_norm), 3),
                'head_disagreement': round(float(head_disagreement), 3),
                'is_uncertain': bool(is_uncertain),
                'certainty': round(float(certainty), 1),
                'margin': round(float(margin), 1),
            },
        }
