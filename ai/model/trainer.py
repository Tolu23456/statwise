"""
FootballPredictor – 5-model deep stacking ensemble.

Architecture (Layer 1 → Layer 2):
  Base models (5), each probability-calibrated:
    1. XGBoost              – histogram trees, colsample/subsample noise
    2. HistGradientBoosting – sklearn native, NaN support, class-balanced
    3. ExtraTreesClassifier – high-variance random splits for diversity
    4. RandomForestClassifier – bagged decision trees, class-balanced
    5. NeuralNetClassifier  – PyTorch Residual MLP (256→128→64, BN+Dropout)

  Meta-learner (Layer 2):
    LogisticRegressionCV trained on out-of-fold predictions from all 5 base
    models + raw features (passthrough=True) → calibrated probability output.

  Separate stacks for: Outcome (Home/Draw/Away) and Goals (O/U 2.5).

Speed optimisations vs previous version:
  - XGB/HGB/ET/RF estimator counts reduced (~40% less compute)
  - Calibration: sigmoid (fast) instead of isotonic
  - Stacking cv=3 (was 5)
  - XGB: tree_method='hist' (explicit, avoids auto-detection overhead)
"""
from __future__ import annotations
import os, logging, joblib
import numpy as np
import pandas as pd
from typing import Optional

from xgboost import XGBClassifier
from sklearn.ensemble import (
    HistGradientBoostingClassifier,
    ExtraTreesClassifier,
    RandomForestClassifier,
    StackingClassifier,
)
from sklearn.linear_model import LogisticRegressionCV
from sklearn.calibration import CalibratedClassifierCV
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline

from .features import FeaturePipeline, N_FEATURES
from .neural_net import NeuralNetClassifier

logger = logging.getLogger(__name__)
MODEL_DIR     = os.path.join(os.path.dirname(__file__), '..', 'models')
os.makedirs(MODEL_DIR, exist_ok=True)
OUTCOME_LABELS = ['Home Win', 'Draw', 'Away Win']


# ─── base model factories ─────────────────────────────────────────────────────

def _xgb(n_classes: int, seed: int = 42) -> XGBClassifier:
    return XGBClassifier(
        n_estimators=200, max_depth=6, learning_rate=0.05,
        subsample=0.8, colsample_bytree=0.75, colsample_bylevel=0.75,
        min_child_weight=5, gamma=0.1, reg_alpha=0.2, reg_lambda=2.0,
        tree_method='hist',
        eval_metric='mlogloss' if n_classes > 2 else 'logloss',
        random_state=seed, n_jobs=2, verbosity=0,
    )


def _hgb(seed: int = 42) -> HistGradientBoostingClassifier:
    return HistGradientBoostingClassifier(
        max_iter=150, max_depth=6, max_leaf_nodes=47,
        learning_rate=0.05, min_samples_leaf=20,
        l2_regularization=2.0, random_state=seed,
        class_weight='balanced',
    )


def _et(n_classes: int, seed: int = 42) -> ExtraTreesClassifier:
    return ExtraTreesClassifier(
        n_estimators=150, max_depth=20, min_samples_leaf=5,
        max_features='sqrt', random_state=seed, n_jobs=2,
        class_weight='balanced',
    )


def _rf(seed: int = 42) -> RandomForestClassifier:
    return RandomForestClassifier(
        n_estimators=150, max_depth=16, min_samples_leaf=5,
        max_features='sqrt', random_state=seed, n_jobs=2,
        class_weight='balanced',
    )


def _nn(seed: int = 0) -> NeuralNetClassifier:
    return NeuralNetClassifier(
        epochs=50, batch_size=512,
        lr=3e-3, weight_decay=1e-4,
        random_state=seed,
    )


def _make_stack(n_classes: int, seed: int = 42) -> Pipeline:
    """
    Build a 5-model stacking classifier wrapped in a StandardScaler pipeline.

    Memory-safe design:
      - StackingClassifier n_jobs=1: folds run sequentially so we never have
        5 × 3 model copies alive at the same time; each model still uses its
        own limited thread pool.
      - passthrough=False: meta-learner sees only the 15 OOF probability
        columns, not 98 raw features concatenated (saves ~150 MB peak RAM).
      - cv=3: three-fold OOF (good balance of bias/variance).
    """
    _cal = lambda est: CalibratedClassifierCV(est, method='sigmoid', cv=2)

    base = [
        ('xgb', _cal(_xgb(n_classes, seed))),
        ('hgb', _cal(_hgb(seed))),
        ('et',  _cal(_et(n_classes, seed))),
        ('rf',  _cal(_rf(seed))),
        ('nn',  _nn(seed)),
    ]
    meta = LogisticRegressionCV(
        Cs=10, cv=5, max_iter=1000,
        solver='lbfgs', n_jobs=-1, random_state=seed,
        l1_ratios=(0,),           # silence FutureWarning in sklearn ≥1.10
        use_legacy_attributes=False,
    )
    stack = StackingClassifier(
        estimators=base,
        final_estimator=meta,
        cv=3,
        stack_method='predict_proba',
        passthrough=False,          # meta-learner sees OOF probs only → less RAM
        n_jobs=1,                   # sequential folds → peak memory stays bounded
    )
    return Pipeline([('scaler', StandardScaler()), ('stack', stack)])


# ─── main predictor class ─────────────────────────────────────────────────────

class FootballPredictor:
    def __init__(self, home_advantage: float = 100.0):
        self.home_advantage  = home_advantage
        self.feature_pipe    = FeaturePipeline(home_advantage)
        self._outcome_pipe: Optional[Pipeline] = None
        self._goals_pipe:   Optional[Pipeline] = None
        self._trained        = False

    def train(self, df: pd.DataFrame, min_samples: int = 500) -> "FootballPredictor":
        logger.info(f"Starting training on {len(df):,} raw match records…")

        logger.info("Step 1/4  Computing Elo ratings and league stats…")
        self.feature_pipe.fit(df)

        logger.info(f"Step 2/4  Building feature matrix ({N_FEATURES} features, ≤60 K samples)…")
        X, y_1x2, y_goals = self.feature_pipe.build_training_set(df)
        if len(X) < min_samples:
            raise ValueError(f"Not enough samples: {len(X)} < {min_samples}")
        logger.info(f"Step 2/4  Done — {len(X):,} samples × {N_FEATURES} features.")

        # Sanitize
        X = np.where(np.isfinite(X), X, 0.0)
        X = np.clip(X, -1e6, 1e6)

        # Compute balanced sample weights to counteract home-win class imbalance.
        # StackingClassifier passes these to every base estimator that supports it.
        def _balanced_weights(y: np.ndarray) -> np.ndarray:
            classes, counts = np.unique(y, return_counts=True)
            freq = dict(zip(classes, counts / len(y)))
            w = np.array([1.0 / freq[c] for c in y], dtype=np.float64)
            return w / w.mean()   # normalise so mean weight == 1

        sw_1x2   = _balanced_weights(y_1x2)
        sw_goals = _balanced_weights(y_goals)

        dist = {int(c): int(n) for c, n in zip(*np.unique(y_1x2, return_counts=True))}
        logger.info(f"Outcome class distribution (0=home,1=draw,2=away): {dist}")

        logger.info("Step 3/4  Fitting outcome stack (XGB+HGB+ET+RF+NeuralNet → LR)…")
        self._outcome_pipe = _make_stack(n_classes=3, seed=42)
        self._outcome_pipe.fit(X, y_1x2,
                               stack__sample_weight=sw_1x2)

        logger.info("Step 4/4  Fitting goals stack (XGB+HGB+ET+RF+NeuralNet → LR)…")
        self._goals_pipe = _make_stack(n_classes=2, seed=99)
        self._goals_pipe.fit(X, y_goals,
                             stack__sample_weight=sw_goals)

        self._trained = True
        logger.info("Training complete ✓  [5-model deep stacking ensemble]")
        return self

    def save(self, path: Optional[str] = None) -> str:
        path = path or os.path.join(MODEL_DIR, 'football_predictor.pkl')
        joblib.dump({
            'feature_pipe':   self.feature_pipe,
            'outcome_pipe':   self._outcome_pipe,
            'goals_pipe':     self._goals_pipe,
            'home_advantage': self.home_advantage,
            'n_features':     N_FEATURES,
        }, path, compress=3)
        logger.info(f"Model saved → {path}")
        return path

    @classmethod
    def load(cls, path: Optional[str] = None) -> "FootballPredictor":
        path = path or os.path.join(MODEL_DIR, 'football_predictor.pkl')
        if not os.path.exists(path):
            raise FileNotFoundError(f"No saved model at {path}")
        data = joblib.load(path)
        if data.get('n_features', 0) != N_FEATURES:
            raise ValueError(
                f"Feature count mismatch ({data.get('n_features')} ≠ {N_FEATURES}) "
                "— retrain required."
            )
        obj = cls.__new__(cls)
        obj.feature_pipe   = data['feature_pipe']
        obj._outcome_pipe  = data.get('outcome_pipe')
        obj._goals_pipe    = data.get('goals_pipe')
        obj.home_advantage = data.get('home_advantage', 100.0)
        obj._trained       = True
        logger.info(f"Model loaded from {path}  [5-model deep stacking ensemble]")
        return obj

    def predict_match(
        self, home_team: str, away_team: str,
        league_slug: str = 'all',
        history: Optional[pd.DataFrame] = None,
        odds_home: Optional[float] = None,
        odds_draw: Optional[float] = None,
        odds_away: Optional[float] = None,
    ) -> dict:
        if not self._trained:
            raise RuntimeError("Model not trained. Call train() or load() first.")

        match = {
            'home_team': home_team, 'away_team': away_team,
            'league_slug': league_slug,
            'odds_home': odds_home, 'odds_draw': odds_draw, 'odds_away': odds_away,
        }
        hist_df = history if isinstance(history, pd.DataFrame) else pd.DataFrame()
        X = self.feature_pipe.build_features([match], hist_df)

        outcome_probs = self._outcome_pipe.predict_proba(X)[0]  # ty:ignore[unresolved-attribute]
        goals_probs   = self._goals_pipe.predict_proba(X)[0]  # ty:ignore[unresolved-attribute]

        p_home, p_draw, p_away = float(outcome_probs[0]), float(outcome_probs[1]), float(outcome_probs[2])
        p_over25 = float(goals_probs[1]) if len(goals_probs) > 1 else 0.5

        probs = [p_home, p_draw, p_away]

        # ── Prediction decision logic ────────────────────────────────────
        #
        # The ensemble's draw probability rarely tops home/away, so a naive
        # argmax never predicts draws.  We use two separate corrections:
        #
        # 1. Draw detection: predict draw when
        #      p_draw ≥ 0.255  (just above the historical base-rate of ~26%)
        #      AND |p_home - p_away| ≤ 0.14  (genuinely open match)
        #
        # 2. Away-team bias correction: the model systematically under-rates
        #    away wins (home advantage bleeds into training).  Give away a
        #    3pp bonus — predict away whenever p_away ≥ p_home − 0.03.
        #
        DRAW_PROB_FLOOR  = 0.255   # raised slightly from 0.245 to reduce noise
        HA_GAP_CEIL      = 0.14    # max |p_home - p_away| to allow draw call
        AWAY_BOOST       = 0.03    # away corrects for model home-bias

        if p_draw >= DRAW_PROB_FLOOR and abs(p_home - p_away) <= HA_GAP_CEIL:
            idx = 1
        elif p_away >= p_home - AWAY_BOOST:   # away-team bias correction
            idx = 2
        else:
            idx = 0

        prediction_label = OUTCOME_LABELS[idx]
        raw_conf = int(round(probs[idx] * 100))
        # Draws are inherently uncertain — softer confidence ceiling
        if idx == 1:
            confidence = max(50, min(62, raw_conf))
        else:
            confidence = max(52, min(95, raw_conf))

        def edge(prob, odds):
            return round((prob * odds) - 1.0, 3) if odds and odds > 1 else 0.0

        feat_vec = X[0]
        reasoning = _build_reasoning(
            home_team, away_team, prediction_label,
            p_home, p_draw, p_away,
            feat_vec[0], feat_vec[1], feat_vec[2],   # elo_h, elo_a, elo_diff
            feat_vec[13], feat_vec[23],               # home_ppg, away_ppg
            p_over25, confidence,
            feat_vec[50], feat_vec[51],               # home_streak, away_streak (FIXED)
            feat_vec[52], feat_vec[53],               # home_trend, away_trend (FIXED)
            feat_vec[31], feat_vec[56],               # h2h_n_matches, h2h_avg_goals (FIXED)
            feat_vec[60], feat_vec[61],               # [NEW] recent-3 goals scored
        )

        return {
            'prediction':        prediction_label,
            'confidence':        confidence,
            'prob_home':         round(p_home  * 100, 1),
            'prob_draw':         round(p_draw  * 100, 1),
            'prob_away':         round(p_away  * 100, 1),
            'prob_over25':       round(p_over25 * 100, 1),
            'odds_implied_home': round(1 / max(p_home, 0.01), 2),
            'odds_implied_draw': round(1 / max(p_draw, 0.01), 2),
            'odds_implied_away': round(1 / max(p_away, 0.01), 2),
            'suggested_odds':    [odds_home, odds_draw, odds_away][idx],
            'value_home':        edge(p_home, odds_home),
            'value_draw':        edge(p_draw, odds_draw),
            'value_away':        edge(p_away, odds_away),
            'reasoning':         reasoning,
        }


def _build_reasoning(
    home, away, pred, ph, pd_, pa,
    elo_h, elo_a, elo_diff,
    form_h, form_a,
    p_over25, confidence,
    home_streak, away_streak,
    home_trend, away_trend,
    h2h_n, h2h_avg_goals,
    home_last3, away_last3,
) -> str:
    parts = []

    # Elo narrative
    if abs(elo_diff) > 150:
        parts.append(
            f"{home if elo_diff > 0 else away} holds a commanding Elo advantage "
            f"({abs(elo_diff):.0f} pts)."
        )
    elif abs(elo_diff) > 60:
        parts.append(f"{home if elo_diff > 0 else away} has an Elo edge ({abs(elo_diff):.0f} pts).")
    else:
        parts.append(f"Teams are closely matched on Elo ({elo_h:.0f} vs {elo_a:.0f}).")

    # Form narrative
    if form_h > form_a + 0.4:
        parts.append(f"{home} is in superior form ({form_h:.2f} PPG vs {form_a:.2f}).")
    elif form_a > form_h + 0.4:
        parts.append(f"{away} is in superior form ({form_a:.2f} PPG vs {form_h:.2f}).")
    else:
        parts.append("Both teams are in comparable form.")

    # Recent 3-match goals
    if home_last3 > 2.0:
        parts.append(f"{home} is in hot scoring form ({home_last3:.1f} goals/game last 3).")
    if away_last3 > 2.0:
        parts.append(f"{away} has been prolific lately ({away_last3:.1f} goals/game last 3).")

    # Streaks
    streaks = []
    if home_streak > 0.4:   streaks.append(f"{home} is on a winning run")
    elif home_streak < -0.4: streaks.append(f"{home} has been struggling")
    if away_streak > 0.4:   streaks.append(f"{away} is on a winning streak")
    elif away_streak < -0.4: streaks.append(f"{away} has lost momentum")
    if streaks:
        parts.append("; ".join(streaks) + ".")

    # Trends
    if home_trend > 0.5 and away_trend < -0.2:
        parts.append(f"{home} is improving while {away} is declining.")
    elif away_trend > 0.5 and home_trend < -0.2:
        parts.append(f"{away} has been on a strong upward trend.")

    # H2H
    if h2h_n >= 3:
        if h2h_avg_goals > 3.0:
            parts.append(
                f"H2H history ({int(h2h_n)} matches) tends to produce goals "
                f"({h2h_avg_goals:.1f} avg)."
            )
        elif h2h_avg_goals < 2.0:
            parts.append(
                f"H2H history ({int(h2h_n)} matches) has been low-scoring "
                f"({h2h_avg_goals:.1f} avg) — draw-friendly."
            )

    # Draw uncertainty
    if pred == 'Draw':
        gap = sorted([ph, pd_, pa], reverse=True)
        if gap[0] - gap[1] < 0.05:
            parts.append(
                "All three outcomes are within 5% probability — "
                "high uncertainty, classic draw scenario."
            )

    # Goals
    if p_over25 > 65:
        parts.append(f"Goals expected — {p_over25:.0f}% Over 2.5.")
    elif p_over25 < 38:
        parts.append(f"Tight match — only {p_over25:.0f}% Over 2.5.")

    # Model signature
    parts.append(
        f"5-model deep ensemble (XGBoost+HistGB+ExtraTrees+RandomForest+NeuralNet→LR): "
        f"{home} Win {ph*100:.0f}%, Draw {pd_*100:.0f}%, {away} Win {pa*100:.0f}%. "
        f"Confidence: {confidence}%."
    )
    return " ".join(parts)
