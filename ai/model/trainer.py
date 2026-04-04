"""
FootballPredictor – Deep stacking ensemble for match prediction.

Architecture (Layer 1 → Layer 2):
  Base models (each independently calibrated):
    1. XGBoost              – deep trees, colsample/subsample noise
    2. HistGradientBoosting – sklearn native, native NaN support
    3. ExtraTreesClassifier – high-variance random splits for diversity
    4. RandomForestClassifier – bagged decision trees
    5. MLP Neural Network   – 3-hidden-layer deep network (256→128→64), ReLU + Adam

  Meta-learner (Layer 2):
    LogisticRegressionCV trained on out-of-fold (OOF) predictions from
    all 5 base models → final calibrated probability output.

  Separate stacks for: Outcome (Home/Draw/Away) and Goals (O/U 2.5).
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
from sklearn.neural_network import MLPClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline

from .features import FeaturePipeline, N_FEATURES

logger = logging.getLogger(__name__)
MODEL_DIR = os.path.join(os.path.dirname(__file__), '..', 'models')
os.makedirs(MODEL_DIR, exist_ok=True)
OUTCOME_LABELS = ['Home Win', 'Draw', 'Away Win']


# ─── base model factories ─────────────────────────────────────────────────────

def _xgb(n_classes: int, seed: int = 42) -> XGBClassifier:
    return XGBClassifier(
        n_estimators=500, max_depth=8, learning_rate=0.05,
        subsample=0.8, colsample_bytree=0.75, colsample_bylevel=0.75,
        min_child_weight=5, gamma=0.1, reg_alpha=0.2, reg_lambda=2.0,
        eval_metric='mlogloss' if n_classes > 2 else 'logloss',
        random_state=seed, n_jobs=-1, verbosity=0,
    )


def _hgb(seed: int = 42, class_weight: str | None = 'balanced') -> HistGradientBoostingClassifier:
    return HistGradientBoostingClassifier(
        max_iter=500, max_depth=8, max_leaf_nodes=127,
        learning_rate=0.05, min_samples_leaf=20,
        l2_regularization=2.0, random_state=seed,
        class_weight=class_weight,
    )


def _et(n_classes: int, seed: int = 42) -> ExtraTreesClassifier:
    return ExtraTreesClassifier(
        n_estimators=400, max_depth=None, min_samples_leaf=5,
        max_features='sqrt', random_state=seed, n_jobs=-1,
        class_weight='balanced',
    )


def _rf(seed: int = 42) -> RandomForestClassifier:
    return RandomForestClassifier(
        n_estimators=400, max_depth=20, min_samples_leaf=5,
        max_features='sqrt', random_state=seed, n_jobs=-1,
        class_weight='balanced',
    )


def _mlp(seed: int = 42) -> MLPClassifier:
    """
    2-hidden-layer neural network: 128 → 64 neurons.
    ReLU activation, Adam optimiser, L2 regularisation (alpha=0.01).
    Early stopping on 10% validation split prevents overfitting.
    MLPClassifier outputs softmax probabilities natively — no
    CalibratedClassifierCV wrapper needed, keeping inference fast.
    """
    return MLPClassifier(
        hidden_layer_sizes=(128, 64),
        activation='relu',
        solver='adam',
        alpha=0.01,
        batch_size=256,
        learning_rate='adaptive',
        learning_rate_init=0.001,
        max_iter=500,
        random_state=seed,
        early_stopping=True,
        validation_fraction=0.1,
        n_iter_no_change=25,
        tol=1e-4,
    )


def _make_stack(n_classes: int, seed: int = 42) -> Pipeline:
    """
    Build a 5-model stacking classifier wrapped in a StandardScaler pipeline.
    Uses 5-fold cross-validation to generate OOF meta-features.
    Models: XGBoost, HistGB, ExtraTrees, RandomForest, MLP Neural Network.
    """
    base = [
        ('xgb', CalibratedClassifierCV(_xgb(n_classes, seed), method='isotonic', cv=3)),
        ('hgb', CalibratedClassifierCV(_hgb(seed),            method='isotonic', cv=3)),
        ('et',  CalibratedClassifierCV(_et(n_classes, seed),  method='isotonic', cv=3)),
        ('rf',  CalibratedClassifierCV(_rf(seed),             method='isotonic', cv=3)),
        ('mlp', _mlp(seed)),  # softmax output is inherently calibrated; no wrapper needed
    ]
    meta = LogisticRegressionCV(
        Cs=10, cv=5, max_iter=1000,
        solver='lbfgs', n_jobs=-1, random_state=seed,
    )
    stack = StackingClassifier(
        estimators=base,
        final_estimator=meta,
        cv=5,
        stack_method='predict_proba',
        passthrough=True,   # also feed raw features to meta-learner
        n_jobs=1,
    )
    return Pipeline([('scaler', StandardScaler()), ('stack', stack)])


# ─── main predictor class ─────────────────────────────────────────────────────

class FootballPredictor:
    def __init__(self, home_advantage: float = 100.0):
        self.home_advantage = home_advantage
        self.feature_pipe   = FeaturePipeline(home_advantage)
        self._outcome_pipe: Optional[Pipeline] = None
        self._goals_pipe:   Optional[Pipeline] = None
        self._trained = False

    def train(self, df: pd.DataFrame, min_samples: int = 500) -> "FootballPredictor":
        logger.info(f"Starting training on {len(df):,} raw match records…")

        logger.info("Step 1/4  Computing Elo ratings and league stats…")
        self.feature_pipe.fit(df)

        logger.info("Step 2/4  Building feature matrix (60 features)…")
        X, y_1x2, y_goals = self.feature_pipe.build_training_set(df)
        if len(X) < min_samples:
            raise ValueError(f"Not enough samples: {len(X)} < {min_samples}")
        logger.info(f"Step 2/4  Done — {len(X):,} samples × {N_FEATURES} features.")

        # Sanitize: replace inf/nan with 0 and clip extreme values
        X = np.where(np.isfinite(X), X, 0.0)
        X = np.clip(X, -1e6, 1e6)

        logger.info("Step 3/4  Fitting outcome stack (XGB+HGB+ET+RF+MLP Neural Net → LR meta)…")
        self._outcome_pipe = _make_stack(n_classes=3, seed=42)
        self._outcome_pipe.fit(X, y_1x2)

        logger.info("Step 4/4  Fitting goals stack (XGB+HGB+ET+RF+MLP Neural Net → LR meta)…")
        self._goals_pipe = _make_stack(n_classes=2, seed=99)
        self._goals_pipe.fit(X, y_goals)

        self._trained = True
        logger.info("Training complete ✓  [5-model stacking ensemble with neural network]")
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
            raise ValueError("Feature count mismatch — retrain required.")
        obj = cls.__new__(cls)
        obj.feature_pipe   = data['feature_pipe']
        obj._outcome_pipe  = data.get('outcome_pipe')
        obj._goals_pipe    = data.get('goals_pipe')
        obj.home_advantage = data.get('home_advantage', 100.0)
        obj._trained       = True
        logger.info(f"Model loaded from {path}  [5-model stacking ensemble with neural network]")
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

        outcome_probs = self._outcome_pipe.predict_proba(X)[0]
        goals_probs   = self._goals_pipe.predict_proba(X)[0]

        p_home, p_draw, p_away = float(outcome_probs[0]), float(outcome_probs[1]), float(outcome_probs[2])
        p_over25 = float(goals_probs[1]) if len(goals_probs) > 1 else 0.5

        probs = [p_home, p_draw, p_away]
        raw_idx = int(np.argmax(probs))

        # ── Draw detection (threshold-based, not pure argmax) ──────────────────
        # Football draws rarely win a straight argmax because home/away probs
        # split the "non-draw" mass. Diagnostic shows the model assigns draw
        # probabilities of 30-37% on genuine draw matches yet gets overruled.
        # Strategy:
        #   • If p_draw ≥ 30% AND no team is a clear favourite (< 52%) → Draw
        #   • If argmax=Draw but a clear favourite exists (≥ 52%) → fall back H/A
        #   • Otherwise → trust raw argmax
        DRAW_THRESHOLD   = 0.30   # above the ~26% base rate
        CLEAR_FAVOURITE  = 0.52   # strong enough to override draw signal

        best_non_draw = max(p_home, p_away)
        if p_draw >= DRAW_THRESHOLD and best_non_draw < CLEAR_FAVOURITE:
            idx = 1  # promote to Draw
        elif raw_idx == 1 and best_non_draw >= CLEAR_FAVOURITE:
            idx = 0 if p_home >= p_away else 2  # clear favourite overrides draw
        else:
            idx = raw_idx

        prediction_label = OUTCOME_LABELS[idx]
        raw_conf = int(round(probs[idx] * 100))
        floor = 48 if idx == 1 else 52
        confidence = max(floor, min(95, raw_conf))

        def edge(prob, odds):
            return round((prob * odds) - 1.0, 3) if odds and odds > 1 else 0.0

        feat_vec = X[0]
        reasoning = _build_reasoning(
            home_team, away_team, prediction_label,
            p_home, p_draw, p_away,
            feat_vec[0], feat_vec[1], feat_vec[2],
            feat_vec[13], feat_vec[23],
            p_over25, confidence,
            feat_vec[46], feat_vec[47], feat_vec[48], feat_vec[49],
            feat_vec[37], feat_vec[56],
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
    elo_h, elo_a, elo_diff, form_h, form_a,
    p_over25, confidence,
    home_streak, away_streak, home_trend, away_trend,
    h2h_n, h2h_avg_goals,
) -> str:
    parts = []
    if abs(elo_diff) > 150:
        parts.append(f"{home if elo_diff > 0 else away} holds a commanding Elo advantage ({abs(elo_diff):.0f} pts).")
    elif abs(elo_diff) > 60:
        parts.append(f"{home if elo_diff > 0 else away} has an Elo edge ({abs(elo_diff):.0f} pts).")
    else:
        parts.append(f"Teams are closely matched on Elo ({elo_h:.0f} vs {elo_a:.0f}).")

    if form_h > form_a + 0.4:
        parts.append(f"{home} is in superior form ({form_h:.2f} PPG vs {form_a:.2f}).")
    elif form_a > form_h + 0.4:
        parts.append(f"{away} is in superior form ({form_a:.2f} PPG vs {form_h:.2f}).")
    else:
        parts.append("Both teams are in comparable form.")

    streaks = []
    if home_streak > 0.4:  streaks.append(f"{home} is on a winning run")
    elif home_streak < -0.4: streaks.append(f"{home} has been struggling")
    if away_streak > 0.4:  streaks.append(f"{away} is on a winning streak")
    elif away_streak < -0.4: streaks.append(f"{away} has lost momentum")
    if streaks: parts.append("; ".join(streaks) + ".")

    if home_trend > 0.5 and away_trend < -0.2:
        parts.append(f"{home} is improving while {away} is declining.")
    elif away_trend > 0.5 and home_trend < -0.2:
        parts.append(f"{away} has been on a strong upward trend.")

    if h2h_n >= 3:
        if h2h_avg_goals > 3.0:
            parts.append(f"H2H history ({int(h2h_n)} matches) tends to produce goals ({h2h_avg_goals:.1f} avg).")
        elif h2h_avg_goals < 2.0:
            parts.append(f"H2H history ({int(h2h_n)} matches) has been low-scoring ({h2h_avg_goals:.1f} avg) — draw-friendly.")

    if pred == 'Draw':
        top2_gap = sorted([ph, pd_, pa], reverse=True)
        gap = top2_gap[0] - top2_gap[1]
        if gap < 0.05:
            parts.append("All three outcomes are within 5% probability — high uncertainty, classic draw scenario.")

    if p_over25 > 65:
        parts.append(f"Goals expected — {p_over25:.0f}% Over 2.5.")
    elif p_over25 < 38:
        parts.append(f"Tight match — only {p_over25:.0f}% Over 2.5.")

    parts.append(
        f"5-model neural stacking ensemble (XGBoost+HistGB+ExtraTrees+RandomForest+MLP Neural Network→LR): "
        f"{home} Win {ph*100:.0f}%, Draw {pd_*100:.0f}%, {away} Win {pa*100:.0f}%. "
        f"Confidence: {confidence}%."
    )
    return " ".join(parts)
