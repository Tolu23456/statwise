"""
FootballPredictor – Grandmaster Stacking Ensemble v5.
"""
from __future__ import annotations
import os, logging, joblib
import numpy as np
import pandas as pd
from typing import Optional

from xgboost import XGBClassifier
from lightgbm import LGBMClassifier
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
from .neural_net import GrandmasterNeuralNet, _TitanFootballNet

logger = logging.getLogger(__name__)
MODEL_DIR     = os.path.join(os.path.dirname(__file__), '..', 'models')
os.makedirs(MODEL_DIR, exist_ok=True)
OUTCOME_LABELS = ['Home Win', 'Draw', 'Away Win']

def _xgb(n_classes: int, seed: int = 42) -> XGBClassifier:
    # Increased estimators and depth for larger dataset
    return XGBClassifier(n_estimators=800, max_depth=8, learning_rate=0.015, subsample=0.85, colsample_bytree=0.8, min_child_weight=20, random_state=seed, n_jobs=4, tree_method='hist')

def _lgbm(seed: int = 42) -> LGBMClassifier:
    # Increased estimators and leaves for larger dataset
    return LGBMClassifier(n_estimators=800, max_depth=8, learning_rate=0.015, num_leaves=127, random_state=seed, n_jobs=4, verbosity=-1)

def _hgb(seed: int = 42) -> HistGradientBoostingClassifier:
    return HistGradientBoostingClassifier(max_iter=600, max_depth=12, learning_rate=0.02, random_state=seed)

def _et(n_classes: int, seed: int = 42) -> ExtraTreesClassifier:
    return ExtraTreesClassifier(n_estimators=300, max_depth=30, min_samples_leaf=2, random_state=seed, n_jobs=4)

def _rf(seed: int = 42) -> RandomForestClassifier:
    return RandomForestClassifier(n_estimators=300, max_depth=22, min_samples_leaf=2, random_state=seed, n_jobs=4)

def _make_stack(n_classes: int, seed: int = 42) -> Pipeline:
    # Strategy: High-capacity base learners + Global Isotonic Calibration
    base = [
        ('xgb',  _xgb(n_classes, seed)),
        ('lgbm', _lgbm(seed)),
        ('hgb',  _hgb(seed)),
        ('et',   _et(n_classes, seed)),
        ('rf',   _rf(seed)),
        ('nn',   GrandmasterNeuralNet(epochs=200, batch_size=2048, random_state=seed)),
    ]
    # Meta-learner: Ridge classifier for robust blending
    meta = LogisticRegressionCV(Cs=20, cv=5, max_iter=2000, solver='lbfgs', n_jobs=-1, random_state=seed)

    stack = StackingClassifier(
        estimators=base,
        final_estimator=meta,
        cv=5, # Increased CV for better meta-training
        stack_method='predict_proba',
        passthrough=True,
        n_jobs=-1,
    )

    # Advanced Calibration Layer: Ensures confidence scores are mathematically accurate probabilities
    calibrated_stack = CalibratedClassifierCV(stack, method='isotonic', cv='prefit')

    return Pipeline([
        ('scaler', StandardScaler()),
        ('stack', stack),
        # Note: We wrap the pipeline or call calibration after fit in practice
    ])

class CalibratedStack(Pipeline):
    """Pipeline extension that automatically handles post-fit calibration."""
    def fit(self, X, y, **fit_params):
        super().fit(X, y, **fit_params)
        # Apply final calibration layer on the stack's output
        self.calibrator = CalibratedClassifierCV(self.named_steps['stack'], method='isotonic', cv=3)
        self.calibrator.fit(self.named_steps['scaler'].transform(X), y)
        return self

    def predict_proba(self, X):
        Xt = self.named_steps['scaler'].transform(X)
        return self.calibrator.predict_proba(Xt)

class FootballPredictor:
    def __init__(self, home_advantage: float = 100.0):
        self.home_advantage  = home_advantage
        self.feature_pipe    = FeaturePipeline(home_advantage)
        self._outcome_pipe: Optional[Pipeline] = None
        self._goals_pipe:   Optional[Pipeline] = None
        self._trained        = False

    def train(self, df: pd.DataFrame) -> "FootballPredictor":
        logger.info(f"Grandmaster Training starting on {len(df):,} records…")
        self.feature_pipe.fit(df)
        X, y_1x2, y_goals, dates = self.feature_pipe.build_training_set(df)
        X = np.nan_to_num(X, nan=0.0, posinf=1e6, neginf=-1e6)

        logger.info("Step 3/4  Fitting Grandmaster Outcome stack…")
        # Use CalibratedStack for enhanced confidence mapping
        self._outcome_pipe = CalibratedStack([
            ('scaler', StandardScaler()),
            ('stack', StackingClassifier(
                estimators=[
                    ('xgb',  _xgb(3, 42)),
                    ('lgbm', _lgbm(42)),
                    ('hgb',  _hgb(42)),
                    ('et',   _et(3, 42)),
                    ('rf',   _rf(42)),
                    ('nn',   GrandmasterNeuralNet(epochs=200, batch_size=2048, random_state=42)),
                ],
                final_estimator=LogisticRegressionCV(Cs=20, cv=5, max_iter=2000, n_jobs=-1),
                cv=5, stack_method='predict_proba', passthrough=True, n_jobs=-1
            ))
        ])
        self._outcome_pipe.fit(X, y_1x2)

        logger.info("Step 4/4  Fitting Grandmaster Goals stack…")
        self._goals_pipe = CalibratedStack([
            ('scaler', StandardScaler()),
            ('stack', StackingClassifier(
                estimators=[
                    ('xgb',  _xgb(2, 99)),
                    ('lgbm', _lgbm(99)),
                    ('hgb',  _hgb(99)),
                    ('et',   _et(2, 99)),
                    ('rf',   _rf(99)),
                    ('nn',   GrandmasterNeuralNet(epochs=200, batch_size=2048, random_state=99)),
                ],
                final_estimator=LogisticRegressionCV(Cs=20, cv=5, max_iter=2000, n_jobs=-1),
                cv=5, stack_method='predict_proba', passthrough=True, n_jobs=-1
            ))
        ])
        self._goals_pipe.fit(X, y_goals)

        self._trained = True
        return self

    def save(self, path: str):
        joblib.dump({
            'feature_pipe': self.feature_pipe,
            'outcome_pipe': self._outcome_pipe,
            'goals_pipe':   self._goals_pipe,
            'n_features':   N_FEATURES,
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
        return obj

    def predict_match(self, home: str, away: str, league: str = 'all', history: pd.DataFrame = None, odds_home: float = 0, odds_draw: float = 0, odds_away: float = 0):
        home_squad_value = history[history["home_team"] == home]["home_squad_value"].tail(1).values[0] if "home_squad_value" in history.columns and not history[history["home_team"] == home].empty else 0
        away_squad_value = history[history["away_team"] == away]["away_squad_value"].tail(1).values[0] if "away_squad_value" in history.columns and not history[history["away_team"] == away].empty else 0
        home_avg_age = history[history["home_team"] == home]["home_avg_age"].tail(1).values[0] if "home_avg_age" in history.columns and not history[history["home_team"] == home].empty else 25
        away_avg_age = history[history["away_team"] == away]["away_avg_age"].tail(1).values[0] if "away_avg_age" in history.columns and not history[history["away_team"] == away].empty else 25
        home_lineup_rating = history[history["home_team"] == home]["home_lineup_rating"].tail(1).values[0] if "home_lineup_rating" in history.columns and not history[history["home_team"] == home].empty else 70
        away_lineup_rating = history[history["away_team"] == away]["away_lineup_rating"].tail(1).values[0] if "away_lineup_rating" in history.columns and not history[history["away_team"] == away].empty else 70
        if history is None or history.empty:
            history = pd.DataFrame(columns=['date', 'home_team', 'away_team', 'home_goals', 'away_goals'])

        X = self.feature_pipe.build_features([{"home": home, "away": away, "league": league, "odds_home": odds_home, "odds_draw": odds_draw, "odds_away": odds_away, "home_squad_value": home_squad_value, "away_squad_value": away_squad_value, "home_avg_age": home_avg_age, "away_avg_age": away_avg_age, "home_lineup_rating": home_lineup_rating, "away_lineup_rating": away_lineup_rating}], history)

        p_outcome = self._outcome_pipe.predict_proba(X)[0]
        p_goals = self._goals_pipe.predict_proba(X)[0]
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
            "away_rating": float(X[0, 131])
        }

        # Dynamic reasoning based on model insights
        conf = int(p_outcome[idx] * 100)
        reasoning = f"Grandmaster v5 Analysis: {OUTCOME_LABELS[idx]} predicted with {conf}% confidence. "
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
            'prob_home': round(p_outcome[0]*100, 1),
            'prob_draw': round(p_outcome[1]*100, 1),
            'prob_away': round(p_outcome[2]*100, 1),
            'prob_over25': round(p_goals[1]*100, 1) if len(p_goals)>1 else 50.0,
            'stats': stats,
            'reasoning': reasoning.strip()
        }
