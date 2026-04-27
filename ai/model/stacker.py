"""
Neural Meta-Learner for Stacking (Layer 3).
Learns non-linear combinations of base model probabilities and raw features.
"""
from __future__ import annotations
import torch
import torch.nn as nn
import numpy as np
from sklearn.base import BaseEstimator, ClassifierMixin
from sklearn.utils.validation import check_is_fitted
from sklearn.preprocessing import LabelEncoder

class NeuralStacker(BaseEstimator, ClassifierMixin):
    def __init__(self, epochs: int = 50, lr: float = 1e-3):
        self.epochs = epochs
        self.lr = lr
        self._model = None
        self._le = LabelEncoder()

    def fit(self, X: np.ndarray, y: np.ndarray):
        self.classes_ = np.unique(y)
        y_enc = self._le.fit_transform(y)

        device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
        in_dim = X.shape[1]
        self._model = nn.Sequential(
            nn.Linear(in_dim, 64),
            nn.BatchNorm1d(64),
            nn.GELU(),
            nn.Dropout(0.1),
            nn.Linear(64, 32),
            nn.BatchNorm1d(32),
            nn.GELU(),
            nn.Linear(32, len(self.classes_))
        ).to(device)

        X_t = torch.FloatTensor(X.astype(np.float32)).to(device)
        y_t = torch.LongTensor(y_enc.astype(np.int64)).to(device)

        optimizer = torch.optim.AdamW(self._model.parameters(), lr=self.lr)
        criterion = nn.CrossEntropyLoss()

        self._model.train()
        for _ in range(self.epochs):
            optimizer.zero_grad()
            loss = criterion(self._model(X_t), y_t)
            loss.backward()
            optimizer.step()

        self._model.cpu().eval()
        return self

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        check_is_fitted(self)
        with torch.no_grad():
            t = torch.FloatTensor(X.astype(np.float32))
            logits = self._model(t)
            return torch.softmax(logits, dim=1).numpy()

    def predict(self, X: np.ndarray) -> np.ndarray:
        return self._le.inverse_transform(np.argmax(self.predict_proba(X), axis=1))

    # Crucial for sklearn's is_classifier check
    @property
    def _estimator_type(self):
        return "classifier"
