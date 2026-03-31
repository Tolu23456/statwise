"""
FootballPredictor – trains an XGBoost ensemble on historical match data
and exposes predict() for individual match predictions.
"""
from __future__ import annotations
import os, logging, joblib
import numpy as np
import pandas as pd
from typing import Optional

from xgboost import XGBClassifier
from sklearn.ensemble import (
    GradientBoostingClassifier,
    RandomForestClassifier,
    VotingClassifier,
)
from sklearn.calibration import CalibratedClassifierCV
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import cross_val_score
from sklearn.pipeline import Pipeline

from .features import FeaturePipeline, N_FEATURES

logger = logging.getLogger(__name__)

MODEL_DIR = os.path.join(os.path.dirname(__file__), '..', 'models')
os.makedirs(MODEL_DIR, exist_ok=True)

OUTCOME_LABELS = ['Home Win', 'Draw', 'Away Win']


class FootballPredictor:
    """
    End-to-end football match prediction model.
    Wraps a C++-accelerated feature pipeline + XGBoost ensemble.
    """

    def __init__(self, home_advantage: float = 100.0):
        self.home_advantage = home_advantage
        self.feature_pipe   = FeaturePipeline(home_advantage)
        self._outcome_model: Optional[CalibratedClassifierCV] = None
        self._goals_model:   Optional[CalibratedClassifierCV] = None
        self._scaler = StandardScaler()
        self._trained = False

    # ─────────────────────── building models ─────────────────────── #

    @staticmethod
    def _make_outcome_model() -> CalibratedClassifierCV:
        xgb = XGBClassifier(
            n_estimators=400,
            max_depth=6,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            use_label_encoder=False,
            eval_metric='mlogloss',
            random_state=42,
            n_jobs=-1,
        )
        rf = RandomForestClassifier(
            n_estimators=300,
            max_depth=8,
            random_state=42,
            n_jobs=-1,
        )
        gb = GradientBoostingClassifier(
            n_estimators=200,
            max_depth=4,
            learning_rate=0.08,
            subsample=0.8,
            random_state=42,
        )
        ensemble = VotingClassifier(
            estimators=[('xgb', xgb), ('rf', rf), ('gb', gb)],
            voting='soft',
            weights=[3, 2, 2],
        )
        return CalibratedClassifierCV(ensemble, method='isotonic', cv=3)

    @staticmethod
    def _make_goals_model() -> CalibratedClassifierCV:
        xgb = XGBClassifier(
            n_estimators=300,
            max_depth=5,
            learning_rate=0.07,
            subsample=0.8,
            colsample_bytree=0.7,
            use_label_encoder=False,
            eval_metric='logloss',
            random_state=99,
            n_jobs=-1,
        )
        return CalibratedClassifierCV(xgb, method='isotonic', cv=3)

    # ─────────────────────── training ─────────────────────────────── #

    def train(self, df: pd.DataFrame, min_samples: int = 500) -> "FootballPredictor":
        logger.info(f"Starting training on {len(df)} raw match records…")
        self.feature_pipe.fit(df)
        X, y_1x2, y_goals = self.feature_pipe.build_training_set(df)

        if len(X) < min_samples:
            raise ValueError(
                f"Not enough training samples: {len(X)} < {min_samples}. "
                "Download more historical data first."
            )

        logger.info(f"Training on {len(X)} samples, {N_FEATURES} features")

        X_scaled = self._scaler.fit_transform(X)

        # Cross-validated accuracy estimate
        xgb_quick = XGBClassifier(
            n_estimators=100, max_depth=5,
            use_label_encoder=False, eval_metric='mlogloss',
            random_state=42, n_jobs=-1,
        )
        cv_scores = cross_val_score(xgb_quick, X_scaled, y_1x2, cv=5, scoring='accuracy')
        logger.info(f"CV accuracy (quick XGBoost, 5-fold): {cv_scores.mean():.3f} ± {cv_scores.std():.3f}")

        # Train full outcome model
        logger.info("Fitting outcome model (ensemble)…")
        self._outcome_model = self._make_outcome_model()
        self._outcome_model.fit(X_scaled, y_1x2)

        # Train goals model
        logger.info("Fitting goals model…")
        self._goals_model = self._make_goals_model()
        self._goals_model.fit(X_scaled, y_goals)

        self._trained = True
        logger.info("Training complete.")
        return self

    def save(self, path: Optional[str] = None) -> str:
        path = path or os.path.join(MODEL_DIR, 'football_predictor.pkl')
        joblib.dump({
            'feature_pipe':    self.feature_pipe,
            'outcome_model':   self._outcome_model,
            'goals_model':     self._goals_model,
            'scaler':          self._scaler,
            'home_advantage':  self.home_advantage,
        }, path, compress=3)
        logger.info(f"Model saved to {path}")
        return path

    @classmethod
    def load(cls, path: Optional[str] = None) -> "FootballPredictor":
        path = path or os.path.join(MODEL_DIR, 'football_predictor.pkl')
        if not os.path.exists(path):
            raise FileNotFoundError(f"No saved model at {path}")
        data = joblib.load(path)
        obj = cls.__new__(cls)
        obj.feature_pipe   = data['feature_pipe']
        obj._outcome_model = data['outcome_model']
        obj._goals_model   = data['goals_model']
        obj._scaler        = data['scaler']
        obj.home_advantage = data.get('home_advantage', 100.0)
        obj._trained       = True
        logger.info(f"Model loaded from {path}")
        return obj

    # ─────────────────────── prediction ──────────────────────────── #

    def predict_match(
        self, home_team: str, away_team: str,
        league_slug: str = 'all',
        history: Optional[pd.DataFrame] = None,
        odds_home: Optional[float] = None,
        odds_draw: Optional[float] = None,
        odds_away: Optional[float] = None,
    ) -> dict:
        if not self._trained:
            raise RuntimeError("Model not trained yet. Call train() or load() first.")

        if history is None:
            history = pd.DataFrame()

        match = {
            'home_team':  home_team,
            'away_team':  away_team,
            'league_slug': league_slug,
            'odds_home':  odds_home,
            'odds_draw':  odds_draw,
            'odds_away':  odds_away,
        }

        X = self.feature_pipe.build_features([match], history)
        X_scaled = self._scaler.transform(X)

        # Outcome probabilities
        outcome_probs = self._outcome_model.predict_proba(X_scaled)[0]
        goals_probs   = self._goals_model.predict_proba(X_scaled)[0]

        p_home = float(outcome_probs[0])
        p_draw = float(outcome_probs[1])
        p_away = float(outcome_probs[2])
        p_over25 = float(goals_probs[1]) if len(goals_probs) > 1 else 0.5

        # Best prediction + confidence
        idx = int(np.argmax([p_home, p_draw, p_away]))
        prediction_label = OUTCOME_LABELS[idx]
        confidence = int(round(max(p_home, p_draw, p_away) * 100))
        confidence = max(52, min(95, confidence))

        # Compute value (edge over market odds)
        def edge(prob, odds):
            if odds and odds > 1:
                return (prob * odds) - 1.0
            return 0.0

        value_home = edge(p_home, odds_home)
        value_away = edge(p_away, odds_away)

        # Suggested bet
        suggested_odds = None
        if idx == 0 and odds_home:   suggested_odds = odds_home
        elif idx == 2 and odds_away: suggested_odds = odds_away
        elif idx == 1 and odds_draw: suggested_odds = odds_draw

        # Reasoning
        feat_vec = X[0]
        elo_diff = feat_vec[2]
        home_form_ppg = feat_vec[13]
        away_form_ppg = feat_vec[23]
        home_elo = feat_vec[0]
        away_elo = feat_vec[1]

        reasoning = _build_reasoning(
            home_team, away_team, prediction_label,
            p_home, p_draw, p_away,
            home_elo, away_elo, elo_diff,
            home_form_ppg, away_form_ppg,
            p_over25, confidence,
        )

        return {
            'prediction': prediction_label,
            'confidence': confidence,
            'prob_home':  round(p_home * 100, 1),
            'prob_draw':  round(p_draw * 100, 1),
            'prob_away':  round(p_away * 100, 1),
            'prob_over25': round(p_over25 * 100, 1),
            'odds_implied_home': round(1 / max(p_home, 0.01), 2),
            'odds_implied_away': round(1 / max(p_away, 0.01), 2),
            'odds_implied_draw': round(1 / max(p_draw, 0.01), 2),
            'suggested_odds': suggested_odds,
            'value_home':  round(value_home, 3),
            'value_away':  round(value_away, 3),
            'reasoning':   reasoning,
        }


def _build_reasoning(
    home, away, pred, ph, pd_, pa,
    elo_h, elo_a, elo_diff, form_h, form_a,
    p_over25, confidence,
) -> str:
    parts = []

    if abs(elo_diff) > 150:
        stronger = home if elo_diff > 0 else away
        parts.append(f"{stronger} has a significant Elo advantage ({abs(elo_diff):.0f} points).")
    elif abs(elo_diff) > 60:
        stronger = home if elo_diff > 0 else away
        parts.append(f"{stronger} edges the Elo rating ({abs(elo_diff):.0f} points ahead).")
    else:
        parts.append(f"Teams are closely matched in Elo ({elo_h:.0f} vs {elo_a:.0f}).")

    if form_h > form_a + 0.4:
        parts.append(f"{home} is in stronger recent form ({form_h:.2f} PPG vs {form_a:.2f}).")
    elif form_a > form_h + 0.4:
        parts.append(f"{away} is in stronger recent form ({form_a:.2f} PPG vs {form_h:.2f}).")
    else:
        parts.append("Both teams are in comparable form.")

    if p_over25 > 65:
        parts.append(f"Goals are expected ({p_over25:.0f}% probability of Over 2.5).")
    elif p_over25 < 40:
        parts.append(f"This looks like a low-scoring affair ({p_over25:.0f}% for Over 2.5).")

    parts.append(
        f"Model probabilities: {home} Win {ph*100:.0f}%, "
        f"Draw {pd_*100:.0f}%, {away} Win {pa*100:.0f}%. "
        f"Prediction confidence: {confidence}%."
    )
    return " ".join(parts)
