"""
Grandmaster Neural Architecture — League-Aware Attention Network.

Architecture (v5):
  1. League Embedding: Learns tactical 'fingerprints' for each league.
  2. Numerical Projection: Continuous features → High-dimensional space.
  3. Feature-Context Fusion: Merges league context with team stats.
  4. Multi-Head Cross-Attention: Dynamically weighs features based on league context.
  5. Deep Residual Stages: v4 ResBlocks for deep pattern recognition.

v5 Improvements:
  - nn.Embedding for League IDs (handles 100+ professional leagues).
  - Cross-Attention mechanism to modulate feature importance by league.
  - Dropout optimization for large-scale data.
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
    logger.warning("PyTorch not available — GrandmasterNet uses LR fallback.")


# ── Network Modules ───────────────────────────────────────────────────────────

if _TORCH:
    class _SEBlock(nn.Module):
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
            return self.drop(self.act(self.se(self.block(x)) + x))

    class _TitanFootballNet(nn.Module):
        """
        Titan v6: Multi-Modal Transformer with Player-Team Cross-Attention.
        Architected for 10M+ match datasets.
        """
        def __init__(self, n_cont: int, n_leagues: int, n_out: int,
                     d_model: int = 512, d_embed: int = 128, n_heads: int = 8):
            super().__init__()
            self.n_leagues = n_leagues
            self.d_model = d_model

            # 1. League & Tactical Embeddings
            self.league_embed = nn.Embedding(n_leagues, d_embed)

            # 2. Dual-Path Input Processing
            # Path A: Team Tactical Features (indices 0-124)
            self.team_proj = nn.Sequential(
                nn.Linear(125, 256),
                nn.BatchNorm1d(256),
                nn.GELU(),
                nn.Linear(256, d_model // 2),
                nn.LayerNorm(d_model // 2)
            )

            # Path B: Player/Squad Multi-Modal Features (indices 125-159)
            self.player_proj = nn.Sequential(
                nn.Linear(35, 128),
                nn.BatchNorm1d(128),
                nn.GELU(),
                nn.Linear(128, d_model // 2 - d_embed),
                nn.LayerNorm(d_model // 2 - d_embed)
            )

            # 3. Cross-Attention Bottleneck
            self.n_tokens = 16
            self.token_dim = d_model // self.n_tokens
            self.mha_blocks = nn.ModuleList([
                nn.MultiheadAttention(self.token_dim, n_heads, batch_first=True, dropout=0.1)
                for _ in range(4)
            ])
            self.ln_blocks = nn.ModuleList([nn.LayerNorm(self.token_dim) for _ in range(4)])

            # 4. Deep Residual Pipeline (Increased depth and width)
            self.res1 = _ResBlock(d_model, dropout=0.25)
            self.res1b = _ResBlock(d_model, dropout=0.2)
            self.down1 = nn.Sequential(nn.Linear(d_model, d_model // 2), nn.BatchNorm1d(d_model // 2), nn.GELU())

            curr_dim = d_model // 2
            self.res2 = _ResBlock(curr_dim, dropout=0.15)
            self.res2b = _ResBlock(curr_dim, dropout=0.15)
            self.down2 = nn.Sequential(nn.Linear(curr_dim, curr_dim // 2), nn.BatchNorm1d(curr_dim // 2), nn.GELU())

            curr_dim //= 2
            self.res3 = _ResBlock(curr_dim, dropout=0.1)
            self.res3b = _ResBlock(curr_dim, dropout=0.1)

            self.head = nn.Linear(curr_dim, n_out)

            for m in self.modules():
                if isinstance(m, nn.Linear):
                    nn.init.kaiming_normal_(m.weight, nonlinearity='linear')
                    if m.bias is not None: nn.init.zeros_(m.bias)

        def forward(self, x_num: torch.Tensor, x_cat: torch.Tensor) -> torch.Tensor:
            # x_num: (B, 160)
            team_feats = x_num[:, :125]
            player_feats = x_num[:, 125:]

            t_proj = self.team_proj(team_feats)
            p_proj = self.player_proj(player_feats)
            l_emb = self.league_embed(x_cat)

            # Multi-Modal Fusion
            fused = torch.cat([t_proj, p_proj, l_emb], dim=1) # (B, d_model)

            b = fused.shape[0]
            tokens = fused.view(b, self.n_tokens, self.token_dim)

            # Deep Transformer Pipeline
            for mha, ln in zip(self.mha_blocks, self.ln_blocks):
                attn_out, _ = mha(tokens, tokens, tokens)
                tokens = ln(tokens + attn_out)

            x = tokens.reshape(b, -1)

            x = self.res1(x)
            x = self.res1b(x)
            x = self.down1(x)
            x = self.res2(x)
            x = self.res2b(x)
            x = self.down2(x)
            x = self.res3(x)
            x = self.res3b(x)

            return self.head(x)


# ── sklearn Wrapper ───────────────────────────────────────────────────────────

class GrandmasterNeuralNet(BaseEstimator, ClassifierMixin):
    """
    Grandmaster Classifier with League-Aware Embeddings.
    Expects input X to have league_id in the last column.
    """
    _estimator_type = "classifier"

    def __init__(
        self,
        n_leagues:    int   = 120,   # Max anticipated professional leagues
        epochs:       int   = 160,
        batch_size:   int   = 1024,
        lr:           float = 1e-3,
        weight_decay: float = 3e-4,
        label_smooth: float = 0.1,
        patience:     int   = 20,
        val_frac:     float = 0.15,
        random_state: int   = 42,
    ):
        self.n_leagues    = n_leagues
        self.epochs       = epochs
        self.batch_size   = batch_size
        self.lr           = lr
        self.weight_decay = weight_decay
        self.label_smooth = label_smooth
        self.patience     = patience
        self.val_frac     = val_frac
        self.random_state = random_state

    def fit(self, X: np.ndarray, y: np.ndarray,
            sample_weight: np.ndarray | None = None) -> "GrandmasterNeuralNet":
        if not _TORCH: return self._fit_fallback(X, y, sample_weight)

        torch.manual_seed(self.random_state)
        np.random.seed(self.random_state)

        self.classes_   = np.unique(y)
        n_classes       = len(self.classes_)
        # X has continuous features in cols [0:-1] and league_id in [-1]
        n_cont          = X.shape[1] - 1
        self._label_map = {c: i for i, c in enumerate(self.classes_)}

        y_idx = np.array([self._label_map[c] for c in y], dtype=np.int64)
        n     = len(X)

        val_size = int(self.val_frac * n) if self.val_frac > 0 else 0
        if val_size > 0:
            X_tr, y_tr = X[:-val_size], y_idx[:-val_size]
            X_vl, y_vl = X[-val_size:], y_idx[-val_size:]
            sw_tr = sample_weight[:-val_size] if sample_weight is not None else None
        else:
            X_tr, y_tr = X, y_idx
            X_vl, y_vl = None, None
            sw_tr = sample_weight

        device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
        self._model = _TitanFootballNet(n_cont, self.n_leagues, n_classes).to(device)
        self._scaler = torch.cuda.amp.GradScaler(enabled=(device.type == 'cuda'))

        counts = np.bincount(y_tr, minlength=n_classes).astype(np.float32)
        cls_w  = torch.FloatTensor((counts.sum() / (n_classes * counts.clip(1)))).to(device)

        criterion = nn.CrossEntropyLoss(weight=cls_w, label_smoothing=self.label_smooth)
        optimizer = torch.optim.AdamW(self._model.parameters(), lr=self.lr, weight_decay=self.weight_decay)

        steps = max(1, math.ceil(len(X_tr) / self.batch_size))
        scheduler = torch.optim.lr_scheduler.OneCycleLR(
            optimizer, max_lr=self.lr, epochs=self.epochs, steps_per_epoch=steps,
            pct_start=0.2, div_factor=20
        )

        # Dataset with league ID extraction
        def _prep_tensor(arr):
            num = torch.FloatTensor(arr[:, :-1].astype(np.float32))
            cat = torch.LongTensor(arr[:, -1].astype(np.int64)).clamp(0, self.n_leagues - 1)
            return num, cat

        tr_num, tr_cat = _prep_tensor(X_tr)
        tr_y = torch.LongTensor(y_tr)
        tr_sw = torch.FloatTensor(sw_tr if sw_tr is not None else np.ones(len(y_tr)))

        dataset = tud.TensorDataset(tr_num, tr_cat, tr_y, tr_sw)
        loader = tud.DataLoader(dataset, batch_size=self.batch_size, shuffle=True)

        if X_vl is not None:
            vl_num, vl_cat = _prep_tensor(X_vl)
            vl_num, vl_cat = vl_num.to(device), vl_cat.to(device)
            vl_y = torch.LongTensor(y_vl).to(device)

        best_loss, best_state, no_imp = float('inf'), None, 0

        self._model.train()
        for epoch in range(self.epochs):
            for bn, bc, by, bw in loader:
                bn, bc, by, bw = bn.to(device), bc.to(device), by.to(device), bw.to(device)
                optimizer.zero_grad(set_to_none=True)

                with torch.cuda.amp.autocast(enabled=(device.type == 'cuda')):
                    logits = self._model(bn, bc)
                    loss = (nn.functional.cross_entropy(logits, by, weight=cls_w, reduction='none') * bw).mean()

                self._scaler.scale(loss).backward()
                self._scaler.unscale_(optimizer)
                nn.utils.clip_grad_norm_(self._model.parameters(), 1.0)
                self._scaler.step(optimizer)
                self._scaler.update()
                scheduler.step()

            if X_vl is not None:
                self._model.eval()
                with torch.no_grad():
                    v_logits = self._model(vl_num, vl_cat)
                    v_loss = nn.functional.cross_entropy(v_logits, vl_y).item()
                self._model.train()
                if v_loss < best_loss - 1e-4:
                    best_loss, best_state, no_imp = v_loss, {k: v.clone() for k,v in self._model.state_dict().items()}, 0
                else:
                    no_imp += 1
                    if no_imp >= self.patience: break

        if best_state: self._model.load_state_dict(best_state)
        self._model.cpu().eval()
        return self

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        self._model.eval()
        with torch.no_grad():
            num = torch.FloatTensor(X[:, :-1].astype(np.float32))
            cat = torch.LongTensor(X[:, -1].astype(np.int64)).clamp(0, self.n_leagues - 1)
            logits = self._model(num, cat)
            return torch.softmax(logits, dim=1).numpy()

    def predict(self, X: np.ndarray) -> np.ndarray:
        return self.classes_[np.argmax(self.predict_proba(X), axis=1)]

    def _fit_fallback(self, X, y, sw):
        from sklearn.linear_model import LogisticRegression
        self._fb = LogisticRegression(max_iter=1000, class_weight='balanced')
        # Drop league ID for fallback
        self._fb.fit(X[:, :-1], y, sample_weight=sw)
        self.classes_ = self._fb.classes_
        return self

# Maintain compatibility for existing imports
NeuralNetClassifier = GrandmasterNeuralNet
