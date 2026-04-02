"""
Neural network models for football match prediction.

Architecture: TabTransformer
  - Each of the N_FEATURES input features gets its own learned embedding
  - Multi-head self-attention layers let features "talk to each other"
  - An MLP head produces the final class probabilities
  - Dropout + LayerNorm for regularisation

This module exposes a scikit-learn compatible wrapper (TabTransformerClassifier)
so it can be dropped into the existing ensemble with no API changes.
"""
from __future__ import annotations
import logging
import numpy as np
import torch
import torch.nn as nn
from sklearn.base import BaseEstimator, ClassifierMixin
from sklearn.preprocessing import LabelEncoder

logger = logging.getLogger(__name__)

# ─── Core transformer model ───────────────────────────────────────────────────

class _TabTransformer(nn.Module):
    """
    Transformer encoder over continuous features.

    Each scalar feature → linear projection → sequence of tokens
    → N transformer layers with multi-head attention
    → flatten → MLP head → logits
    """
    def __init__(
        self,
        n_features: int,
        n_classes: int,
        d_model: int = 64,
        n_heads: int = 4,
        n_layers: int = 3,
        mlp_hidden: int = 128,
        dropout: float = 0.2,
    ):
        super().__init__()
        self.n_features = n_features
        self.d_model = d_model

        # Project each feature scalar into a d_model-dim token
        self.feature_embed = nn.Linear(1, d_model)

        # Learnable positional embedding (one per feature position)
        self.pos_embed = nn.Embedding(n_features, d_model)

        # Transformer encoder
        encoder_layer = nn.TransformerEncoderLayer(
            d_model=d_model,
            nhead=n_heads,
            dim_feedforward=d_model * 4,
            dropout=dropout,
            batch_first=True,
            norm_first=True,       # Pre-LayerNorm (more stable)
        )
        self.transformer = nn.TransformerEncoder(encoder_layer, num_layers=n_layers)

        # MLP head
        flat_dim = n_features * d_model
        self.head = nn.Sequential(
            nn.LayerNorm(flat_dim),
            nn.Linear(flat_dim, mlp_hidden),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(mlp_hidden, mlp_hidden // 2),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(mlp_hidden // 2, n_classes),
        )

        self._init_weights()

    def _init_weights(self):
        for m in self.modules():
            if isinstance(m, nn.Linear):
                nn.init.xavier_uniform_(m.weight)
                if m.bias is not None:
                    nn.init.zeros_(m.bias)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x: (batch, n_features)
        b, f = x.shape
        # Embed each feature: (batch, features, d_model)
        tokens = self.feature_embed(x.unsqueeze(-1))
        pos = self.pos_embed(torch.arange(f, device=x.device)).unsqueeze(0)
        tokens = tokens + pos
        # Self-attention
        tokens = self.transformer(tokens)          # (batch, features, d_model)
        flat = tokens.reshape(b, -1)              # (batch, features * d_model)
        return self.head(flat)                    # (batch, n_classes)


# ─── sklearn-compatible wrapper ───────────────────────────────────────────────

class TabTransformerClassifier(BaseEstimator, ClassifierMixin):
    """
    Wraps _TabTransformer with a scikit-learn interface.

    Supports predict_proba() so it can be calibrated with
    CalibratedClassifierCV and blended into the existing ensemble.
    """
    def __init__(
        self,
        n_classes: int = 3,
        d_model: int = 64,
        n_heads: int = 4,
        n_layers: int = 3,
        mlp_hidden: int = 128,
        dropout: float = 0.2,
        epochs: int = 60,
        batch_size: int = 256,
        lr: float = 3e-4,
        weight_decay: float = 1e-3,
        patience: int = 10,
        device: str = "cpu",
    ):
        self.n_classes    = n_classes
        self.d_model      = d_model
        self.n_heads      = n_heads
        self.n_layers     = n_layers
        self.mlp_hidden   = mlp_hidden
        self.dropout      = dropout
        self.epochs       = epochs
        self.batch_size   = batch_size
        self.lr           = lr
        self.weight_decay = weight_decay
        self.patience     = patience
        self.device       = device

        self.model_: _TabTransformer | None = None
        self.classes_: np.ndarray | None = None
        self._le = LabelEncoder()

    # sklearn: fit(X, y) ──────────────────────────────────────────────────────
    def fit(self, X: np.ndarray, y: np.ndarray) -> "TabTransformerClassifier":
        y_enc = self._le.fit_transform(y)
        self.classes_ = self._le.classes_
        n_feat = X.shape[1]

        dev = torch.device(self.device)
        self.model_ = _TabTransformer(
            n_features=n_feat,
            n_classes=self.n_classes,
            d_model=self.d_model,
            n_heads=self.n_heads,
            n_layers=self.n_layers,
            mlp_hidden=self.mlp_hidden,
            dropout=self.dropout,
        ).to(dev)

        optimizer = torch.optim.AdamW(
            self.model_.parameters(),
            lr=self.lr,
            weight_decay=self.weight_decay,
        )
        scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
            optimizer, T_max=self.epochs
        )

        # Class-balanced loss
        counts = np.bincount(y_enc, minlength=self.n_classes).astype(float)
        weights = torch.tensor(1.0 / (counts + 1e-6), dtype=torch.float32, device=dev)
        weights /= weights.sum()
        criterion = nn.CrossEntropyLoss(weight=weights)

        X_t = torch.tensor(X, dtype=torch.float32, device=dev)
        y_t = torch.tensor(y_enc, dtype=torch.long, device=dev)

        n = len(X_t)
        best_loss, patience_left = float("inf"), self.patience
        best_state = None

        self.model_.train()
        for epoch in range(self.epochs):
            idx = torch.randperm(n, device=dev)
            epoch_loss = 0.0
            steps = 0
            for start in range(0, n, self.batch_size):
                batch_idx = idx[start: start + self.batch_size]
                xb, yb = X_t[batch_idx], y_t[batch_idx]
                optimizer.zero_grad()
                loss = criterion(self.model_(xb), yb)
                loss.backward()
                nn.utils.clip_grad_norm_(self.model_.parameters(), 1.0)
                optimizer.step()
                epoch_loss += loss.item()
                steps += 1
            scheduler.step()
            avg_loss = epoch_loss / max(steps, 1)
            if avg_loss < best_loss - 1e-4:
                best_loss = avg_loss
                best_state = {k: v.cpu().clone() for k, v in self.model_.state_dict().items()}
                patience_left = self.patience
            else:
                patience_left -= 1
                if patience_left == 0:
                    logger.info(f"TabTransformer early stopping at epoch {epoch + 1}")
                    break
            if (epoch + 1) % 10 == 0:
                logger.info(f"  TabTransformer epoch {epoch + 1}/{self.epochs}  loss={avg_loss:.4f}")

        if best_state:
            self.model_.load_state_dict({k: v.to(dev) for k, v in best_state.items()})

        self.model_.eval()
        return self

    # sklearn: predict_proba(X) ───────────────────────────────────────────────
    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        if self.model_ is None:
            raise RuntimeError("Call fit() first.")
        dev = torch.device(self.device)
        self.model_.eval()
        with torch.no_grad():
            X_t = torch.tensor(X, dtype=torch.float32, device=dev)
            logits = self.model_(X_t)
            proba = torch.softmax(logits, dim=-1).cpu().numpy()
        return proba

    # sklearn: predict(X) ─────────────────────────────────────────────────────
    def predict(self, X: np.ndarray) -> np.ndarray:
        proba = self.predict_proba(X)
        return self.classes_[np.argmax(proba, axis=1)]

    def __getstate__(self):
        state = self.__dict__.copy()
        if self.model_ is not None:
            state["_model_state_dict"] = {
                k: v.cpu() for k, v in self.model_.state_dict().items()
            }
            state["_model_config"] = dict(
                n_features=self.model_.n_features,
                n_classes=self.n_classes,
                d_model=self.d_model,
                n_heads=self.n_heads,
                n_layers=self.n_layers,
                mlp_hidden=self.mlp_hidden,
                dropout=self.dropout,
            )
        state["model_"] = None   # don't pickle the live module
        return state

    def __setstate__(self, state):
        self.__dict__.update(state)
        if "_model_state_dict" in state:
            cfg = state["_model_config"]
            self.model_ = _TabTransformer(**cfg)
            self.model_.load_state_dict(state["_model_state_dict"])
            self.model_.eval()
