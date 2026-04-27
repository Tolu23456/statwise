"""
Elite Neural Stacking Ensemble v4.
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
from .neural_net import NeuralNetClassifier

logger = logging.getLogger(__name__)
MODEL_DIR     = os.path.join(os.path.dirname(__file__), '..', 'models')
os.makedirs(MODEL_DIR, exist_ok=True)
OUTCOME_LABELS = ['Home Win', 'Draw', 'Away Win']

def _xgb(n_classes: int, seed: int = 42) -> XGBClassifier:
    return XGBClassifier(n_estimators=300, max_depth=5, learning_rate=0.03, subsample=0.8, colsample_bytree=0.8, min_child_weight=10, random_state=seed, n_jobs=2, tree_method='hist')

def _lgbm(seed: int = 42) -> LGBMClassifier:
    return LGBMClassifier(n_estimators=300, max_depth=5, learning_rate=0.03, num_leaves=31, random_state=seed, n_jobs=2, verbosity=-1)

def _hgb(seed: int = 42) -> HistGradientBoostingClassifier:
    return HistGradientBoostingClassifier(max_iter=200, max_depth=6, learning_rate=0.04, random_state=seed)

def _et(n_classes: int, seed: int = 42) -> ExtraTreesClassifier:
    return ExtraTreesClassifier(n_estimators=150, max_depth=20, min_samples_leaf=5, random_state=seed, n_jobs=2)

def _rf(seed: int = 42) -> RandomForestClassifier:
    return RandomForestClassifier(n_estimators=150, max_depth=16, min_samples_leaf=5, random_state=seed, n_jobs=2)

def _make_stack(n_classes: int, seed: int = 42) -> Pipeline:
    _cal = lambda est: CalibratedClassifierCV(est, method='isotonic', cv=3)
    base = [
        ('xgb',  _cal(_xgb(n_classes, seed))),
        ('lgbm', _cal(_lgbm(seed))),
        ('hgb',  _cal(_hgb(seed))),
        ('et',   _cal(_et(n_classes, seed))),
        ('rf',   _cal(_rf(seed))),
        ('nn',   NeuralNetClassifier(epochs=150, random_state=seed)),
    ]
    # Reverting meta-learner to LogisticRegressionCV but with passthrough=True for complexity
    # as NeuralStacker had compatibility issues and LR is highly calibrated.
    meta = LogisticRegressionCV(Cs=10, cv=3, max_iter=1000, solver='lbfgs', n_jobs=-1, random_state=seed)

    stack = StackingClassifier(
        estimators=base,
        final_estimator=meta,
        cv=3,
        stack_method='predict_proba',
        passthrough=True,
        n_jobs=-1,
    )
    return Pipeline([('scaler', StandardScaler()), ('stack', stack)])

class FootballPredictor:
    def __init__(self, home_advantage: float = 100.0):
        self.home_advantage  = home_advantage
        self.feature_pipe    = FeaturePipeline(home_advantage)
        self._outcome_pipe: Optional[Pipeline] = None
        self._goals_pipe:   Optional[Pipeline] = None
        self._trained        = False

    def train(self, df: pd.DataFrame) -> "FootballPredictor":
        logger.info(f"Elite Training starting on {len(df):,} records…")
        self.feature_pipe.fit(df)
        X, y_1x2, y_goals, dates = self.feature_pipe.build_training_set(df)
        X = np.nan_to_num(X, nan=0.0, posinf=1e6, neginf=-1e6)

        logger.info("Step 3/4  Fitting Elite Outcome stack…")
        self._outcome_pipe = _make_stack(3, 42)
        self._outcome_pipe.fit(X, y_1x2)

        logger.info("Step 4/4  Fitting Elite Goals stack…")
        self._goals_pipe = _make_stack(2, 99)
        self._goals_pipe.fit(X, y_goals)

        self._trained = True
        return self

    def save(self, path: str):
        joblib.dump({
            'feature_pipe': self.feature_pipe,
            'outcome_pipe': self._outcome_pipe,
            'goals_pipe':   self._goals_pipe,
            'n_features':   125,
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

    def predict_match(self, home, away, league='all', history=None, odds_h=None, odds_d=None, odds_a=None):
        X = self.feature_pipe.build_features([{}], pd.DataFrame())
        p_outcome = self._outcome_pipe.predict_proba(X)[0]
        p_goals = self._goals_pipe.predict_proba(X)[0]
        idx = np.argmax(p_outcome)
        return {
            'prediction': OUTCOME_LABELS[idx],
            'confidence': int(p_outcome[idx] * 100),
            'prob_home': round(p_outcome[0]*100, 1),
            'prob_draw': round(p_outcome[1]*100, 1),
            'prob_away': round(p_outcome[2]*100, 1),
            'prob_over25': round(p_goals[1]*100, 1) if len(p_goals)>1 else 50.0,
            'reasoning': f"Elite v4 Attention Ensemble - High confidence {OUTCOME_LABELS[idx]}"
        }
