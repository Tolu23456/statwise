"""
Elite Neural Architecture — Attention-based Residual Network for Football Prediction.

Architecture (v4):
  1. Input Projection: Linear(N) → BN → GELU → (Batch, K, D)
  2. Multi-Head Self-Attention: Learns relationships between feature groups.
  3. Deep Residual Stages: Multiple levels of ResBlocks with downsampling.
  4. Squeeze-and-Excitation (SE): Channel-wise attention for feature calibration.
  5. Output Head: Final classification for 1X2 outcomes.

v4 Improvements:
  - Multi-Head Attention (MHA) over projected feature embeddings.
  - GELU activation instead of ReLU for smoother gradients.
  - Increased depth: 3 residual stages (512, 256, 128).
  - Label smoothing and adaptive learning rates.
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
    logger.warning("PyTorch not available — EliteNeuralNet uses LR fallback.")


# ── Network Modules (only defined when PyTorch is available) ──────────────────

if _TORCH:
    class _SEBlock(nn.Module):
        """Squeeze-and-Excitation block for channel-wise attention."""
        def __init__(self, dim: int, reduction: int = 4):
            super().__init__()
            self.attn = nn.Sequential(
                nn.Linear(dim, dim // reduction),
                nn.GELU(),
                nn.Linear(dim // reduction, dim),
                nn.Sigmoid()
            )
        def forward(self, x):
            return x * self.attn(x)

    class _ResBlock(nn.Module):
        """Enhanced Residual Block with GELU and SE-Attention."""
        def __init__(self, dim: int, dropout: float = 0.2):
            super().__init__()
            self.block = nn.Sequential(
                nn.Linear(dim, dim),
                nn.BatchNorm1d(dim),
                nn.GELU(),
                nn.Dropout(dropout),
                nn.Linear(dim, dim),
                nn.BatchNorm1d(dim),
            )
            self.se   = _SEBlock(dim)
            self.act  = nn.GELU()
            self.drop = nn.Dropout(dropout)

        def forward(self, x: torch.Tensor) -> torch.Tensor:
            identity = x
            out = self.block(x)
            out = self.se(out)
            return self.drop(self.act(out + identity))

    class _EliteFootballNet(nn.Module):
        """
        Elite Attention-based Residual Network.
        Projects flat features into a 2D 'embedding' space to apply attention.
        """
        def __init__(self, n_in: int, n_out: int, d_model: int = 128, n_heads: int = 4):
            super().__init__()
            # Projection: treat features as groups
            self.d_model = d_model
            self.n_groups = 8  # split projected space into 8 virtual 'tokens'
            self.proj = nn.Sequential(
                nn.Linear(n_in, d_model * self.n_groups),
                nn.BatchNorm1d(d_model * self.n_groups),
                nn.GELU(),
            )

            # Multi-Head Attention over virtual feature groups
            self.attn = nn.MultiheadAttention(d_model, n_heads, batch_first=True)
            self.attn_norm = nn.LayerNorm(d_model)

            # Global pooling + Flatten
            self.flatten = nn.Flatten()

            # Deep Residual Stages
            curr_dim = d_model * self.n_groups
            self.res1 = _ResBlock(curr_dim, dropout=0.2)

            self.down1 = nn.Sequential(
                nn.Linear(curr_dim, curr_dim // 2),
                nn.BatchNorm1d(curr_dim // 2),
                nn.GELU(),
            )
            curr_dim //= 2
            self.res2 = _ResBlock(curr_dim, dropout=0.2)

            self.down2 = nn.Sequential(
                nn.Linear(curr_dim, curr_dim // 2),
                nn.BatchNorm1d(curr_dim // 2),
                nn.GELU(),
            )
            curr_dim //= 2
            self.res3 = _ResBlock(curr_dim, dropout=0.1)

            self.head = nn.Linear(curr_dim, n_out)

            # Weight initialization
            for m in self.modules():
                if isinstance(m, nn.Linear):
                    nn.init.kaiming_normal_(m.weight, nonlinearity='linear')
                    if m.bias is not None:
                        nn.init.zeros_(m.bias)

        def forward(self, x: torch.Tensor) -> torch.Tensor:
            # (Batch, N) -> (Batch, Groups * D)
            x = self.proj(x)

            # Reshape for Attention: (Batch, Groups, D)
            b, total_d = x.shape
            x_attn = x.view(b, self.n_groups, self.d_model)

            # Apply Self-Attention
            attn_out, _ = self.attn(x_attn, x_attn, x_attn)
            x_attn = self.attn_norm(x_attn + attn_out)

            # Back to Flat: (Batch, curr_dim)
            x = x_attn.view(b, -1)

            # Residual Stages
            x = self.res1(x)
            x = self.down1(x)
            x = self.res2(x)
            x = self.down2(x)
            x = self.res3(x)

            return self.head(x)


# ── sklearn Wrapper ───────────────────────────────────────────────────────────

class NeuralNetClassifier(BaseEstimator, ClassifierMixin):
    """
    sklearn-compatible wrapper around _EliteFootballNet.
    v4: Attention-based ResNet with GELU.
    """
    _estimator_type = "classifier"

    def __init__(
        self,
        epochs:       int   = 150,    # Increased from 120
        batch_size:   int   = 1024,   # Larger batches for global dataset
        lr:           float = 1e-3,
        weight_decay: float = 2e-4,
        label_smooth: float = 0.1,    # Increased from 0.05
        patience:     int   = 15,
        val_frac:     float = 0.15,
        random_state: int   = 42,
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
            sw_tr = sample_weight[:-val_size] if sample_weight is not None else None
        else:
            X_tr, y_tr = X, y_idx
            X_val_t = None; y_val_t = None
            sw_tr = sample_weight

        device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
        if device.type == 'cuda':
            logger.info(f"  [EliteNet] Training on GPU: {torch.cuda.get_device_name(0)}")

        self._model = _EliteFootballNet(n_features, n_classes).to(device)

        if use_early and X_val_t is not None:
            X_val_t = X_val_t.to(device)
            y_val_t = y_val_t.to(device)

        # Class weights from training fold only
        counts = np.bincount(y_tr, minlength=n_classes).astype(np.float32)
        cls_w  = torch.FloatTensor((counts.sum() / (n_classes * counts.clip(1)))).to(device)

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
            pct_start=0.25, anneal_strategy='cos', div_factor=25,
        )

        dataset = tud.TensorDataset(
            torch.FloatTensor(np.ascontiguousarray(X_tr, dtype=np.float32)),
            torch.LongTensor(np.ascontiguousarray(y_tr, dtype=np.int64)),
            torch.FloatTensor(np.ascontiguousarray(sw_tr if sw_tr is not None else np.ones(len(y_tr)), dtype=np.float32))
        )
        loader = tud.DataLoader(
            dataset, batch_size=self.batch_size,
            shuffle=True, drop_last=False, num_workers=0
        )

        best_val_loss = float('inf')
        best_state    = None
        no_improve    = 0

        self._model.train()
        for epoch in range(self.epochs):
            total_loss = 0
            for xb, yb, wb in loader:
                xb, yb, wb = xb.to(device), yb.to(device), wb.to(device)
                opt.zero_grad(set_to_none=True)

                logits = self._model(xb)
                # Apply sample weights manually if they exist
                loss_vec = nn.functional.cross_entropy(logits, yb, weight=cls_w, reduction='none')
                loss = (loss_vec * wb).mean()

                loss.backward()
                nn.utils.clip_grad_norm_(self._model.parameters(), 1.0)
                opt.step()
                sched.step()
                total_loss += loss.item()

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
                        logger.info(f"  [EliteNet] early stop at epoch {epoch+1} (val_loss={best_val_loss:.4f})")
                        break

            if (epoch + 1) % 50 == 0:
                logger.debug(f"  [EliteNet] epoch {epoch+1}/{self.epochs} | loss={total_loss/len(loader):.4f}")

        # Restore best weights
        if best_state is not None:
            self._model.load_state_dict(best_state)

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
            max_iter=1000, class_weight='balanced',
            random_state=self.random_state,
        )
        self._fallback.fit(X, y, sample_weight=sample_weight)
        self.classes_ = self._fallback.classes_
        return self
