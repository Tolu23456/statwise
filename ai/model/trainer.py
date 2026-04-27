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
from .neural_net import GrandmasterNeuralNet

logger = logging.getLogger(__name__)
MODEL_DIR     = os.path.join(os.path.dirname(__file__), '..', 'models')
os.makedirs(MODEL_DIR, exist_ok=True)
OUTCOME_LABELS = ['Home Win', 'Draw', 'Away Win']

def _xgb(n_classes: int, seed: int = 42) -> XGBClassifier:
    return XGBClassifier(n_estimators=400, max_depth=6, learning_rate=0.02, subsample=0.85, colsample_bytree=0.8, min_child_weight=15, random_state=seed, n_jobs=2, tree_method='hist')

def _lgbm(seed: int = 42) -> LGBMClassifier:
    return LGBMClassifier(n_estimators=400, max_depth=6, learning_rate=0.02, num_leaves=63, random_state=seed, n_jobs=2, verbosity=-1)

def _hgb(seed: int = 42) -> HistGradientBoostingClassifier:
    return HistGradientBoostingClassifier(max_iter=300, max_depth=8, learning_rate=0.03, random_state=seed)

def _et(n_classes: int, seed: int = 42) -> ExtraTreesClassifier:
    return ExtraTreesClassifier(n_estimators=200, max_depth=25, min_samples_leaf=4, random_state=seed, n_jobs=2)

def _rf(seed: int = 42) -> RandomForestClassifier:
    return RandomForestClassifier(n_estimators=200, max_depth=18, min_samples_leaf=4, random_state=seed, n_jobs=2)

def _make_stack(n_classes: int, seed: int = 42) -> Pipeline:
    _cal = lambda est: CalibratedClassifierCV(est, method='isotonic', cv=3)
    base = [
        ('xgb',  _cal(_xgb(n_classes, seed))),
        ('lgbm', _cal(_lgbm(seed))),
        ('hgb',  _cal(_hgb(seed))),
        ('et',   _cal(_et(n_classes, seed))),
        ('rf',   _cal(_rf(seed))),
        ('nn',   GrandmasterNeuralNet(epochs=160, random_state=seed)),
    ]
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
        logger.info(f"Grandmaster Training starting on {len(df):,} records…")
        self.feature_pipe.fit(df)
        X, y_1x2, y_goals, dates = self.feature_pipe.build_training_set(df)
        X = np.nan_to_num(X, nan=0.0, posinf=1e6, neginf=-1e6)

        logger.info("Step 3/4  Fitting Grandmaster Outcome stack…")
        self._outcome_pipe = _make_stack(3, 42)
        self._outcome_pipe.fit(X, y_1x2)

        logger.info("Step 4/4  Fitting Grandmaster Goals stack…")
        self._goals_pipe = _make_stack(2, 99)
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
        if history is None or history.empty:
            history = pd.DataFrame(columns=['date', 'home_team', 'away_team', 'home_goals', 'away_goals'])

        X = self.feature_pipe.build_features([{'home': home, 'away': away, 'league': league, 'odds_home': odds_home, 'odds_draw': odds_draw, 'odds_away': odds_away}], history)

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
            "avg_goals": round(float(avg_g), 2)
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
