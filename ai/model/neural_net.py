"""
PyTorch Residual MLP — football match outcome neural network.

Architecture:
  Input(N) → Stem(256) → ResBlock(256) → Down(128) → ResBlock(128) → Down(64) → Head(n_classes)

Each ResBlock: Linear → BN → ReLU → Dropout → Linear → BN → residual add → ReLU → Dropout
Training: AdamW + OneCycleLR scheduler + gradient clipping.
sklearn API: fit / predict_proba / predict (compatible with StackingClassifier).

v3 improvements:
  - Epochs: 50 → 120 (better convergence on large dataset)
  - LR: 3e-3 → 1e-3 (more stable training)
  - Input dropout: 0.3 → 0.2 (less aggressive regularization on input)
  - Label smoothing: 0.05 (improves probability calibration)
  - Validation split with early stopping: patience=10 epochs (avoids overfitting)

Falls back to LogisticRegression when PyTorch is unavailable.
"""
from __future__ import annotations
import logging, math
import numpy as np
from sklearn.base import BaseEstimator, ClassifierMixin

logger = logging.getLogger(__name__)

try:
    import torch
    import torch.nn as nn
    import torch.utils.data as tud
    _TORCH = True
except ImportError:
    _TORCH = False
    logger.warning("PyTorch not available — NeuralNetClassifier uses LR fallback.")


# ── Network Modules (only defined when PyTorch is available) ──────────────────

if _TORCH:
    class _ResBlock(nn.Module):
        """Pre-activation residual block: same input / output dimension."""
        def __init__(self, dim: int, dropout: float = 0.3):
            super().__init__()
            self.block = nn.Sequential(
                nn.Linear(dim, dim),
                nn.BatchNorm1d(dim),
                nn.ReLU(inplace=True),
                nn.Dropout(dropout),
                nn.Linear(dim, dim),
                nn.BatchNorm1d(dim),
            )
            self.act  = nn.ReLU(inplace=True)
            self.drop = nn.Dropout(dropout)

        def forward(self, x: "torch.Tensor") -> "torch.Tensor":
            return self.drop(self.act(x + self.block(x)))

    class _FootballResNet(nn.Module):
        """
        Deep residual MLP for football match prediction.
        Stem + 2 residual stages + 2 downsampling transitions + linear head.
        v3: input dropout reduced to 0.2.
        """
        def __init__(self, n_in: int, n_out: int):
            super().__init__()
            self.stem = nn.Sequential(
                nn.Linear(n_in, 256),
                nn.BatchNorm1d(256),
                nn.ReLU(inplace=True),
                nn.Dropout(0.2),   # reduced from 0.3
            )
            self.res1  = _ResBlock(256, dropout=0.3)
            self.down1 = nn.Sequential(
                nn.Linear(256, 128),
                nn.BatchNorm1d(128),
                nn.ReLU(inplace=True),
                nn.Dropout(0.25),
            )
            self.res2  = _ResBlock(128, dropout=0.25)
            self.down2 = nn.Sequential(
                nn.Linear(128, 64),
                nn.BatchNorm1d(64),
                nn.ReLU(inplace=True),
                nn.Dropout(0.2),
            )
            self.head = nn.Linear(64, n_out)

            for m in self.modules():
                if isinstance(m, nn.Linear):
                    nn.init.kaiming_normal_(m.weight, nonlinearity='relu')
                    if m.bias is not None:
                        nn.init.zeros_(m.bias)

        def forward(self, x: "torch.Tensor") -> "torch.Tensor":
            x = self.stem(x)
            x = self.res1(x)
            x = self.down1(x)
            x = self.res2(x)
            x = self.down2(x)
            return self.head(x)


# ── sklearn Wrapper ───────────────────────────────────────────────────────────

class NeuralNetClassifier(BaseEstimator, ClassifierMixin):
    """
    sklearn-compatible wrapper around _FootballResNet.
    Falls back to LogisticRegression when PyTorch is unavailable.

    Parameters
    ----------
    epochs       : training epochs (default 120)
    batch_size   : mini-batch size (default 512)
    lr           : peak learning rate for OneCycleLR (default 1e-3)
    weight_decay : AdamW weight decay (default 1e-4)
    label_smooth : label smoothing for CrossEntropyLoss (default 0.05)
    patience     : early stopping patience in epochs (default 10, 0=disabled)
    val_frac     : fraction of data to hold out for early stopping (default 0.1)
    random_state : seed for reproducibility
    """
    _estimator_type = "classifier"

    def __init__(
        self,
        epochs:       int   = 120,
        batch_size:   int   = 512,
        lr:           float = 1e-3,
        weight_decay: float = 1e-4,
        label_smooth: float = 0.05,
        patience:     int   = 10,
        val_frac:     float = 0.10,
        random_state: int   = 0,
    ):
        self.epochs       = epochs
        self.batch_size   = batch_size
        self.lr           = lr
        self.weight_decay = weight_decay
        self.label_smooth = label_smooth
        self.patience     = patience
        self.val_frac     = val_frac
        self.random_state = random_state

    def fit(self, X: np.ndarray, y: np.ndarray,
            sample_weight: np.ndarray | None = None) -> "NeuralNetClassifier":
        if not _TORCH:
            return self._fit_fallback(X, y, sample_weight)

        torch.manual_seed(self.random_state)
        np.random.seed(self.random_state)

        self.classes_   = np.unique(y)
        n_classes       = len(self.classes_)
        n_features      = X.shape[1]
        self._label_map = {c: i for i, c in enumerate(self.classes_)}

        y_idx = np.array([self._label_map[c] for c in y], dtype=np.int64)
        n     = len(X)

        # ── Validation split for early stopping ───────────────────────────────
        use_early = self.patience > 0 and n > 500 and self.val_frac > 0
        if use_early:
            val_size = max(100, int(self.val_frac * n))
            X_tr, y_tr = X[:-val_size], y_idx[:-val_size]
            X_val_np, y_val_np = X[-val_size:], y_idx[-val_size:]
            X_val_t = torch.FloatTensor(np.ascontiguousarray(X_val_np, dtype=np.float32))
            y_val_t = torch.LongTensor(np.ascontiguousarray(y_val_np, dtype=np.int64))
        else:
            X_tr, y_tr = X, y_idx
            X_val_t = None; y_val_t = None

        if sample_weight is not None:
            sw_tr = sample_weight[:-int(self.val_frac * n)] if use_early else sample_weight
        else:
            sw_tr = None

        device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
        if device.type == 'cuda':
            logger.info(f"  [NeuralNet] Training on GPU: {torch.cuda.get_device_name(0)}")

        self._model = _FootballResNet(n_features, n_classes).to(device)

        if use_early and X_val_t is not None:
            X_val_t = X_val_t.to(device)
            y_val_t = y_val_t.to(device)

        # Class weights from training fold only
        counts = np.bincount(y_tr, minlength=n_classes).astype(np.float32)
        cls_w  = torch.FloatTensor((counts.sum() / (n_classes * counts.clip(1)))).to(device)
        if sw_tr is not None:
            sw = np.asarray(sw_tr, dtype=np.float32)
            for c in range(n_classes):
                mask = (y_tr == c)
                if mask.any():
                    cls_w[c] *= float(sw[mask].mean())

        criterion = nn.CrossEntropyLoss(
            weight=cls_w,
            label_smoothing=self.label_smooth,
        )

        opt = torch.optim.AdamW(
            self._model.parameters(),
            lr=self.lr, weight_decay=self.weight_decay,
        )
        steps_per_epoch = max(1, math.ceil(len(X_tr) / self.batch_size))
        sched = torch.optim.lr_scheduler.OneCycleLR(
            opt, max_lr=self.lr,
            epochs=self.epochs, steps_per_epoch=steps_per_epoch,
            pct_start=0.3, anneal_strategy='cos', div_factor=10,
        )

        dataset = tud.TensorDataset(
            torch.FloatTensor(np.ascontiguousarray(X_tr, dtype=np.float32)),
            torch.LongTensor(np.ascontiguousarray(y_tr, dtype=np.int64)),
        )
        loader = tud.DataLoader(
            dataset, batch_size=self.batch_size,
            shuffle=True, drop_last=False,
        )

        best_val_loss = float('inf')
        best_state    = None
        no_improve    = 0

        self._model.train()
        for epoch in range(self.epochs):
            for xb, yb in loader:
                xb, yb = xb.to(device), yb.to(device)
                opt.zero_grad(set_to_none=True)
                loss = criterion(self._model(xb), yb)
                loss.backward()
                nn.utils.clip_grad_norm_(self._model.parameters(), 1.0)
                opt.step()
                sched.step()

            if (epoch + 1) % 20 == 0:
                logger.debug(f"  [NeuralNet] epoch {epoch+1}/{self.epochs}")

            # Early stopping check
            if use_early and X_val_t is not None:
                self._model.eval()
                with torch.no_grad():
                    val_logits = self._model(X_val_t)
                    val_loss   = nn.functional.cross_entropy(val_logits, y_val_t).item()
                self._model.train()

                if val_loss < best_val_loss - 1e-4:
                    best_val_loss = val_loss
                    best_state    = {k: v.clone() for k, v in self._model.state_dict().items()}
                    no_improve    = 0
                else:
                    no_improve += 1
                    if no_improve >= self.patience:
                        logger.debug(
                            f"  [NeuralNet] early stop at epoch {epoch+1} "
                            f"(val_loss={best_val_loss:.4f})"
                        )
                        break

        # Restore best weights if early stopping triggered
        if best_state is not None:
            self._model.load_state_dict(best_state)

        # Move back to CPU so pickle/joblib works on any machine
        self._model.cpu().eval()
        return self

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        if not _TORCH or not hasattr(self, '_model'):
            if hasattr(self, '_fallback'):
                return self._fallback.predict_proba(X)
            raise RuntimeError("NeuralNetClassifier not fitted.")

        self._model.eval()
        with torch.no_grad():
            t = torch.FloatTensor(np.ascontiguousarray(X, dtype=np.float32))
            logits = self._model(t)
            return torch.softmax(logits, dim=1).numpy()

    def predict(self, X: np.ndarray) -> np.ndarray:
        return self.classes_[np.argmax(self.predict_proba(X), axis=1)]

    def _fit_fallback(self, X: np.ndarray, y: np.ndarray,
                      sample_weight=None) -> "NeuralNetClassifier":
        from sklearn.linear_model import LogisticRegression
        self._fallback = LogisticRegression(
            max_iter=500, class_weight='balanced',
            random_state=self.random_state,
        )
        self._fallback.fit(X, y, sample_weight=sample_weight)
        self.classes_ = self._fallback.classes_
        return self
