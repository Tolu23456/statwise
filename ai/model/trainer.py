"""
FootballPredictor – trains an XGBoost + HistGradientBoosting ensemble on historical
match data and exposes predict() for individual match predictions.

Model architecture:
  - Outcome (1X2): CalibratedXGBClassifier + CalibratedHistGBClassifier blended 50/50
  - Goals (O/U 2.5): same ensemble
  - Features scaled with StandardScaler (60 features)

HistGradientBoostingClassifier is scikit-learn's native histogram-based gradient
boosting (same algorithm as LightGBM) — no extra system libraries required.
"""
from __future__ import annotations
import os, logging, joblib
import numpy as np
import pandas as pd
from typing import Optional

from xgboost import XGBClassifier
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.calibration import CalibratedClassifierCV
from sklearn.preprocessing import StandardScaler

from .features import FeaturePipeline, N_FEATURES

logger = logging.getLogger(__name__)

MODEL_DIR = os.path.join(os.path.dirname(__file__), '..', 'models')
os.makedirs(MODEL_DIR, exist_ok=True)

OUTCOME_LABELS = ['Home Win', 'Draw', 'Away Win']


class FootballPredictor:
    """
    End-to-end football match prediction model.
    Wraps a C++-accelerated feature pipeline + XGBoost / HistGradientBoosting ensemble.
    """

    def __init__(self, home_advantage: float = 100.0):
        self.home_advantage     = home_advantage
        self.feature_pipe       = FeaturePipeline(home_advantage)
        self._xgb_outcome:  Optional[CalibratedClassifierCV] = None
        self._hgb_outcome:  Optional[CalibratedClassifierCV] = None
        self._xgb_goals:    Optional[CalibratedClassifierCV] = None
        self._hgb_goals:    Optional[CalibratedClassifierCV] = None
        self._scaler        = StandardScaler()
        self._trained       = False

    # ─────────────────────── model factories ─────────────────────── #

    @staticmethod
    def _make_xgb_outcome() -> CalibratedClassifierCV:
        xgb = XGBClassifier(
            n_estimators=200,
            max_depth=6,
            learning_rate=0.08,
            subsample=0.8,
            colsample_bytree=0.8,
            min_child_weight=3,
            gamma=0.1,
            reg_alpha=0.1,
            reg_lambda=1.5,
            eval_metric='mlogloss',
            random_state=42,
            n_jobs=-1,
        )
        return CalibratedClassifierCV(xgb, method='sigmoid', cv=3)

    @staticmethod
    def _make_hgb_outcome() -> CalibratedClassifierCV:
        hgb = HistGradientBoostingClassifier(
            max_iter=200,
            max_depth=6,
            learning_rate=0.08,
            min_samples_leaf=20,
            l2_regularization=1.5,
            random_state=42,
        )
        return CalibratedClassifierCV(hgb, method='sigmoid', cv=3)

    @staticmethod
    def _make_xgb_goals() -> CalibratedClassifierCV:
        xgb = XGBClassifier(
            n_estimators=150,
            max_depth=5,
            learning_rate=0.08,
            subsample=0.8,
            colsample_bytree=0.75,
            min_child_weight=3,
            gamma=0.05,
            reg_alpha=0.05,
            eval_metric='logloss',
            random_state=99,
            n_jobs=-1,
        )
        return CalibratedClassifierCV(xgb, method='sigmoid', cv=3)

    @staticmethod
    def _make_hgb_goals() -> CalibratedClassifierCV:
        hgb = HistGradientBoostingClassifier(
            max_iter=150,
            max_depth=5,
            learning_rate=0.08,
            min_samples_leaf=20,
            l2_regularization=0.5,
            random_state=99,
        )
        return CalibratedClassifierCV(hgb, method='sigmoid', cv=3)

    # ─────────────────────── training ─────────────────────────────── #

    def train(self, df: pd.DataFrame, min_samples: int = 500) -> "FootballPredictor":
        logger.info(f"Starting training on {len(df)} raw match records…")

        # ── Step 1: Fit Elo ratings & league stats ──────────────────────
        logger.info("Step 1/4  Computing Elo ratings and league stats…")
        self.feature_pipe.fit(df)

        # ── Step 2: Build feature matrix ────────────────────────────────
        logger.info("Step 2/4  Building feature matrix (60 features, 2x denser sampling)…")
        X, y_1x2, y_goals = self.feature_pipe.build_training_set(df)

        if len(X) < min_samples:
            raise ValueError(
                f"Not enough training samples: {len(X)} < {min_samples}."
            )
        logger.info(f"Step 2/4  Done — {len(X)} samples, {N_FEATURES} features each.")

        # ── Step 3: Scale features ──────────────────────────────────────
        logger.info("Step 3/4  Scaling features…")
        X_scaled = self._scaler.fit_transform(X)

        # ── Step 4: Fit ensemble (XGBoost + HistGradientBoosting) ──────────────────
        logger.info("Step 4/4  Fitting XGBoost outcome model…")
        self._xgb_outcome = self._make_xgb_outcome()
        self._xgb_outcome.fit(X_scaled, y_1x2)

        logger.info("Step 4/4  Fitting HistGradientBoosting outcome model…")
        self._hgb_outcome = self._make_hgb_outcome()
        self._hgb_outcome.fit(X_scaled, y_1x2)

        logger.info("Step 4/4  Fitting XGBoost goals model…")
        self._xgb_goals = self._make_xgb_goals()
        self._xgb_goals.fit(X_scaled, y_goals)

        logger.info("Step 4/4  Fitting HistGradientBoosting goals model…")
        self._hgb_goals = self._make_hgb_goals()
        self._hgb_goals.fit(X_scaled, y_goals)

        self._trained = True
        logger.info(f"Training complete ✓  ({N_FEATURES} features, XGBoost+HGB ensemble)")
        return self

    def save(self, path: Optional[str] = None) -> str:
        path = path or os.path.join(MODEL_DIR, 'football_predictor.pkl')
        joblib.dump({
            'feature_pipe':   self.feature_pipe,
            'xgb_outcome':    self._xgb_outcome,
            'hgb_outcome':    self._hgb_outcome,
            'xgb_goals':      self._xgb_goals,
            'hgb_goals':      self._hgb_goals,
            'scaler':         self._scaler,
            'home_advantage': self.home_advantage,
            'n_features':     N_FEATURES,
        }, path, compress=3)
        logger.info(f"Model saved to {path}")
        return path

    @classmethod
    def load(cls, path: Optional[str] = None) -> "FootballPredictor":
        path = path or os.path.join(MODEL_DIR, 'football_predictor.pkl')
        if not os.path.exists(path):
            raise FileNotFoundError(f"No saved model at {path}")
        data = joblib.load(path)
        # Version check – require the current feature count
        saved_n = data.get('n_features', 0)
        if saved_n != N_FEATURES:
            raise ValueError(
                f"Model feature mismatch: saved {saved_n}, current {N_FEATURES}. "
                "Retraining required."
            )
        obj = cls.__new__(cls)
        obj.feature_pipe   = data['feature_pipe']
        obj._xgb_outcome   = data.get('xgb_outcome')
        obj._hgb_outcome   = data.get('hgb_outcome')
        obj._xgb_goals     = data.get('xgb_goals')
        obj._hgb_goals     = data.get('hgb_goals')
        obj._scaler        = data['scaler']
        obj.home_advantage = data.get('home_advantage', 100.0)
        obj._trained       = True
        logger.info(f"Model loaded from {path}")
        return obj

    # ─────────────────────── prediction ──────────────────────────── #

    def _blend_proba(self, X_scaled: np.ndarray,
                     model_a: CalibratedClassifierCV,
                     model_b: Optional[CalibratedClassifierCV],
                     weight_a: float = 0.5) -> np.ndarray:
        """Blend predictions from two calibrated classifiers."""
        pa = model_a.predict_proba(X_scaled)[0]
        if model_b is not None:
            pb = model_b.predict_proba(X_scaled)[0]
            # Align class order (should be same but be safe)
            return weight_a * pa + (1 - weight_a) * pb
        return pa

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
            'home_team':   home_team,
            'away_team':   away_team,
            'league_slug': league_slug,
            'odds_home':   odds_home,
            'odds_draw':   odds_draw,
            'odds_away':   odds_away,
        }

        X = self.feature_pipe.build_features([match], history)
        X_scaled = self._scaler.transform(X)

        # Blended ensemble predictions (XGBoost 50% + HistGradientBoosting 50%)
        outcome_probs = self._blend_proba(X_scaled, self._xgb_outcome, self._hgb_outcome)
        goals_probs   = self._blend_proba(X_scaled, self._xgb_goals,   self._hgb_goals)

        p_home   = float(outcome_probs[0])
        p_draw   = float(outcome_probs[1])
        p_away   = float(outcome_probs[2])
        p_over25 = float(goals_probs[1]) if len(goals_probs) > 1 else 0.5

        # Best prediction + confidence
        idx = int(np.argmax([p_home, p_draw, p_away]))
        prediction_label = OUTCOME_LABELS[idx]
        confidence = int(round(max(p_home, p_draw, p_away) * 100))
        confidence = max(52, min(95, confidence))

        # Value (edge over market odds)
        def edge(prob: float, odds: Optional[float]) -> float:
            return (prob * odds) - 1.0 if odds and odds > 1 else 0.0

        value_home = edge(p_home, odds_home)
        value_away = edge(p_away, odds_away)
        value_draw = edge(p_draw, odds_draw)

        # Suggested bet
        suggested_odds = None
        if   idx == 0 and odds_home: suggested_odds = odds_home
        elif idx == 2 and odds_away: suggested_odds = odds_away
        elif idx == 1 and odds_draw: suggested_odds = odds_draw

        # Pull key feature values for reasoning (60-feature vector)
        feat_vec      = X[0]
        elo_diff      = feat_vec[2]
        home_form_ppg = feat_vec[13]
        away_form_ppg = feat_vec[23]
        home_elo      = feat_vec[0]
        away_elo      = feat_vec[1]
        home_streak   = feat_vec[46]   # new feature idx
        away_streak   = feat_vec[47]
        home_trend    = feat_vec[48]
        away_trend    = feat_vec[49]
        h2h_n         = feat_vec[37]   # h2h_n_matches
        h2h_avg_g     = feat_vec[56]   # h2h_avg_goals

        reasoning = _build_reasoning(
            home_team, away_team, prediction_label,
            p_home, p_draw, p_away,
            home_elo, away_elo, elo_diff,
            home_form_ppg, away_form_ppg,
            p_over25, confidence,
            home_streak, away_streak,
            home_trend, away_trend,
            h2h_n, h2h_avg_g,
        )

        return {
            'prediction':      prediction_label,
            'confidence':      confidence,
            'prob_home':       round(p_home  * 100, 1),
            'prob_draw':       round(p_draw  * 100, 1),
            'prob_away':       round(p_away  * 100, 1),
            'prob_over25':     round(p_over25 * 100, 1),
            'odds_implied_home': round(1 / max(p_home, 0.01), 2),
            'odds_implied_away': round(1 / max(p_away, 0.01), 2),
            'odds_implied_draw': round(1 / max(p_draw, 0.01), 2),
            'suggested_odds':  suggested_odds,
            'value_home':      round(value_home, 3),
            'value_away':      round(value_away, 3),
            'value_draw':      round(value_draw, 3),
            'reasoning':       reasoning,
        }


def _build_reasoning(
    home: str, away: str, pred: str,
    ph: float, pd_: float, pa: float,
    elo_h: float, elo_a: float, elo_diff: float,
    form_h: float, form_a: float,
    p_over25: float, confidence: int,
    home_streak: float, away_streak: float,
    home_trend: float, away_trend: float,
    h2h_n: float, h2h_avg_goals: float,
) -> str:
    parts = []

    # Elo edge
    if abs(elo_diff) > 150:
        stronger = home if elo_diff > 0 else away
        parts.append(f"{stronger} holds a commanding Elo advantage ({abs(elo_diff):.0f} pts).")
    elif abs(elo_diff) > 60:
        stronger = home if elo_diff > 0 else away
        parts.append(f"{stronger} has an edge in Elo rating ({abs(elo_diff):.0f} pts ahead).")
    else:
        parts.append(f"Teams are closely matched on Elo ({elo_h:.0f} vs {elo_a:.0f}).")

    # Form
    if form_h > form_a + 0.4:
        parts.append(f"{home} is in superior recent form ({form_h:.2f} PPG vs {form_a:.2f}).")
    elif form_a > form_h + 0.4:
        parts.append(f"{away} is in superior recent form ({form_a:.2f} PPG vs {form_h:.2f}).")
    else:
        parts.append("Both teams are in comparable form.")

    # Streaks
    streak_msgs = []
    if home_streak > 0.4:
        streak_msgs.append(f"{home} is on a strong winning run")
    elif home_streak < -0.4:
        streak_msgs.append(f"{home} has been struggling recently")
    if away_streak > 0.4:
        streak_msgs.append(f"{away} is on a winning streak")
    elif away_streak < -0.4:
        streak_msgs.append(f"{away} has lost momentum")
    if streak_msgs:
        parts.append("; ".join(streak_msgs) + ".")

    # Form trend
    if home_trend > 0.5 and away_trend < -0.2:
        parts.append(f"{home} is improving while {away} is declining in form.")
    elif away_trend > 0.5 and home_trend < -0.2:
        parts.append(f"{away} has been on a strong upward trend in recent fixtures.")

    # H2H
    if h2h_n >= 3:
        avg = h2h_avg_goals
        if avg > 3.0:
            parts.append(f"H2H history ({int(h2h_n)} matches) tends to produce goals ({avg:.1f} avg).")
        elif avg < 2.0:
            parts.append(f"H2H history ({int(h2h_n)} matches) has been low-scoring ({avg:.1f} avg).")

    # Goals
    if p_over25 > 65:
        parts.append(f"Goals expected — {p_over25:.0f}% probability of Over 2.5.")
    elif p_over25 < 38:
        parts.append(f"Tight match anticipated — only {p_over25:.0f}% chance of Over 2.5.")

    # Model summary
    parts.append(
        f"Ensemble (XGBoost + HistGradientBoosting) probabilities: "
        f"{home} Win {ph*100:.0f}%, Draw {pd_*100:.0f}%, {away} Win {pa*100:.0f}%. "
        f"Confidence: {confidence}%."
    )
    return " ".join(parts)
