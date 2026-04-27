"""
Bayesian Meta-Learner for Stacking (Layer 3).
Calculates uncertainty and league-specific reliability.
"""
from __future__ import annotations
import numpy as np
from sklearn.base import BaseEstimator, ClassifierMixin
from sklearn.linear_model import LogisticRegressionCV
from sklearn.utils.validation import check_is_fitted

class BayesianMetaStacker(BaseEstimator, ClassifierMixin):
    def __init__(self, random_state: int = 42):
        self.random_state = random_state
        self._meta = LogisticRegressionCV(
            Cs=10, cv=3, max_iter=2000,
            solver='lbfgs', n_jobs=-1,
            random_state=self.random_state
        )

    def fit(self, X: np.ndarray, y: np.ndarray):
        self.classes_ = np.unique(y)
        self._meta.fit(X, y)
        return self

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        check_is_fitted(self)
        probs = self._meta.predict_proba(X)
        entropy = -np.sum(probs * np.log(probs + 1e-12), axis=1)
        self.uncertainty_ = entropy / np.log(len(self.classes_))
        return probs

    def predict(self, X: np.ndarray) -> np.ndarray:
        return self.classes_[np.argmax(self.predict_proba(X), axis=1)]

    # Explicit class-level attribute for is_classifier check
    _estimator_type = "classifier"

    def get_params(self, deep=True):
        return {"random_state": self.random_state}

    def set_params(self, **parameters):
        for parameter, value in parameters.items():
            setattr(self, parameter, value)
        return self
