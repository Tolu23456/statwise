"""Tabular transformer (multi-head attention over per-feature tokens).

Architecture — FT-Transformer style, deliberately small enough for a 4-vCPU
runner but a REAL multi-head attention model:

    tokens   = [CLS] + {x_j * w_j + b_j : one token per input feature}
    hidden   = N x (pre-LN encoder blocks: MHA + FFN/GELU)
    pooled   = LayerNorm(hidden[CLS])
    heads    = 1X2 (3 logits) | O2.5 | BTTS | O3.5 (1 logit each)
               | expected goals (2 softplus rates)

`NumpyTabTransformer` mirrors the torch module EXACTLY (pre-LN blocks,
erf-GELU, LayerNorm eps 1e-5) so the trained weights serve production
predictions with pure NumPy — no torch dependency in the predict path.
Parity is asserted in ai/experimental/test_attention.py and re-checked
after every CI training run.
"""
from __future__ import annotations

import math
import os

import numpy as np

try:  # torch is a TRAIN-time dependency only (CI); serving uses the numpy class
    import torch
    import torch.nn as nn
    HAS_TORCH = True
except Exception:                                   # noqa: BLE001
    HAS_TORCH = False


if HAS_TORCH:

    class EncoderBlock(nn.Module):
        """Pre-LN block: x = x + MHA(LN(x)); x = x + FFN(LN(x))."""

        def __init__(self, d_model: int, n_heads: int, ff_mult: int,
                     dropout: float):
            super().__init__()
            self.ln1 = nn.LayerNorm(d_model)
            self.attn = nn.MultiheadAttention(d_model, n_heads,
                                              dropout=dropout, batch_first=True)
            self.ln2 = nn.LayerNorm(d_model)
            self.ffn = nn.Sequential(
                nn.Linear(d_model, d_model * ff_mult),
                nn.GELU(),
                nn.Dropout(dropout),
                nn.Linear(d_model * ff_mult, d_model),
            )
            self.drop = nn.Dropout(dropout)

        def forward(self, x):
            h = self.ln1(x)
            a, _ = self.attn(h, h, h, need_weights=False)
            x = x + self.drop(a)
            x = x + self.drop(self.ffn(self.ln2(x)))
            return x

    class TabTransformer(nn.Module):
        def __init__(self, n_tokens: int, d_model: int = 64, n_heads: int = 4,
                     n_layers: int = 3, ff_mult: int = 2, dropout: float = 0.1):
            super().__init__()
            self.n_tokens = n_tokens
            self.d_model = d_model
            self.tok_w = nn.Parameter(torch.empty(n_tokens, d_model))
            self.tok_b = nn.Parameter(torch.zeros(n_tokens, d_model))
            self.cls = nn.Parameter(torch.zeros(1, 1, d_model))
            nn.init.normal_(self.tok_w, std=0.02)
            self.blocks = nn.ModuleList([
                EncoderBlock(d_model, n_heads, ff_mult, dropout)
                for _ in range(n_layers)])
            self.ln_f = nn.LayerNorm(d_model)
            self.head_1x2 = nn.Linear(d_model, 3)
            self.head_o25 = nn.Linear(d_model, 1)
            self.head_btts = nn.Linear(d_model, 1)
            self.head_o35 = nn.Linear(d_model, 1)
            self.head_lam = nn.Linear(d_model, 2)

        def forward(self, x):
            # x: (B, n_tokens) — one scalar per token
            tok = x.unsqueeze(-1) * self.tok_w + self.tok_b   # (B, T, d)
            h = torch.cat([self.cls.expand(x.size(0), -1, -1), tok], dim=1)
            for blk in self.blocks:
                h = blk(h)
            pooled = self.ln_f(h[:, 0])
            return {
                "logits_1x2": self.head_1x2(pooled),
                "logit_o25": self.head_o25(pooled),
                "logit_btts": self.head_btts(pooled),
                "logit_o35": self.head_o35(pooled),
                "lam": nn.functional.softplus(self.head_lam(pooled)) + 1e-4,
            }

        def export_numpy(self) -> "NumpyTabTransformer":
            p = {"tok_w": self.tok_w.detach().cpu().numpy(),
                 "tok_b": self.tok_b.detach().cpu().numpy(),
                 "cls": self.cls.detach().cpu().numpy().reshape(-1),
                 "ln_f_w": None, "ln_f_b": None,
                 "head_1x2_w": None, "head_1x2_b": None,
                 "head_o25_w": None, "head_o25_b": None,
                 "head_btts_w": None, "head_btts_b": None,
                 "head_o35_w": None, "head_o35_b": None,
                 "head_lam_w": None, "head_lam_b": None,
                 "blocks": []}
            p["ln_f_w"] = self.ln_f.weight.detach().cpu().numpy()
            p["ln_f_b"] = self.ln_f.bias.detach().cpu().numpy()
            for name, key in (("head_1x2", "head_1x2"), ("head_o25", "head_o25"),
                              ("head_btts", "head_btts"), ("head_o35", "head_o35"),
                              ("head_lam", "head_lam")):
                mod = getattr(self, name)
                p[f"{key}_w"] = mod.weight.detach().cpu().numpy()
                p[f"{key}_b"] = mod.bias.detach().cpu().numpy()
            for blk in self.blocks:
                p["blocks"].append({
                    "ln1_w": blk.ln1.weight.detach().cpu().numpy(),
                    "ln1_b": blk.ln1.bias.detach().cpu().numpy(),
                    "ln2_w": blk.ln2.weight.detach().cpu().numpy(),
                    "ln2_b": blk.ln2.bias.detach().cpu().numpy(),
                    "Wq": blk.attn.in_proj_weight.detach().cpu().numpy(),
                    "bq": blk.attn.in_proj_bias.detach().cpu().numpy(),
                    "Wo": blk.attn.out_proj.weight.detach().cpu().numpy(),
                    "bo": blk.attn.out_proj.bias.detach().cpu().numpy(),
                    # ffn = [Linear, GELU, Dropout, Linear]: the second
                    # weight matrix lives at index 3 (index 2 is Dropout)
                    "ffn0_w": blk.ffn[0].weight.detach().cpu().numpy(),
                    "ffn0_b": blk.ffn[0].bias.detach().cpu().numpy(),
                    "ffn2_w": blk.ffn[3].weight.detach().cpu().numpy(),
                    "ffn2_b": blk.ffn[3].bias.detach().cpu().numpy(),
                })
            return NumpyTabTransformer(p, n_heads=self.blocks[0].attn.num_heads
                                       if self.blocks else 1)


def _layernorm(x: np.ndarray, w: np.ndarray, b: np.ndarray,
               eps: float = 1e-5) -> np.ndarray:
    mean = x.mean(axis=-1, keepdims=True)
    var = ((x - mean) ** 2).mean(axis=-1, keepdims=True)
    return (x - mean) / np.sqrt(var + eps) * w + b


def _gelu(x: np.ndarray) -> np.ndarray:
    # torch nn.GELU() default: exact erf formulation
    from scipy.special import erf
    return 0.5 * x * (1.0 + erf(x / math.sqrt(2.0)))


def _softmax(x: np.ndarray, axis: int = -1) -> np.ndarray:
    m = x.max(axis=axis, keepdims=True)
    e = np.exp(x - m)
    return e / e.sum(axis=axis, keepdims=True)


class NumpyTabTransformer:
    """Pure-NumPy mirror of TabTransformer (eval mode: dropout inactive)."""

    def __init__(self, params: dict, n_heads: int = 4):
        self.p = params
        self.n_heads = n_heads

    @classmethod
    def from_torch(cls, module) -> "NumpyTabTransformer":
        return module.export_numpy()

    def forward(self, X: np.ndarray) -> dict:
        """Forward pass, chunked over rows.

        Rows are fully independent (per-row layernorm + per-row attention),
        so chunking is EXACT — same numbers as one full-batch call.  The
        chunk bound exists because the float64 attention map is
        (chunk, heads, T, T) ≈ 0.5 GB at chunk=512 (T=175); a full holdout
        batch (~6.6k rows) unchunked would need ~6 GB.
        """
        X = np.asarray(X, dtype=np.float64)
        chunk = max(1, int(os.environ.get("ATTN_NP_CHUNK", "512")))
        if X.shape[0] > chunk:
            outs = [self._forward_chunk(X[i:i + chunk])
                    for i in range(0, X.shape[0], chunk)]
            return {k: np.concatenate([o[k] for o in outs], axis=0)
                    for k in outs[0]}
        return self._forward_chunk(X)

    def _forward_chunk(self, X: np.ndarray) -> dict:
        p = self.p
        tok = X[..., :, None] * p["tok_w"] + p["tok_b"]        # (B, T, d)
        cls = np.broadcast_to(p["cls"], (X.shape[0], 1, p["cls"].size))
        h = np.concatenate([cls, tok], axis=1)
        d = p["cls"].size
        dh = d // self.n_heads
        for blk in p["blocks"]:
            a = _layernorm(h, blk["ln1_w"], blk["ln1_b"])
            qkv = a @ blk["Wq"].T + blk["bq"]                  # (B, T, 3d)
            q, k, v = np.split(qkv, 3, axis=-1)
            # (B, T, H, dh) -> (B, H, T, dh)
            qh = q.reshape(*q.shape[:2], self.n_heads, dh).transpose(0, 2, 1, 3)
            kh = k.reshape(*k.shape[:2], self.n_heads, dh).transpose(0, 2, 1, 3)
            vh = v.reshape(*v.shape[:2], self.n_heads, dh).transpose(0, 2, 1, 3)
            att = _softmax(qh @ kh.transpose(0, 1, 3, 2) / math.sqrt(dh), axis=-1)
            out = (att @ vh).transpose(0, 2, 1, 3).reshape(*h.shape[:2], d)
            h = h + out @ blk["Wo"].T + blk["bo"]
            f = _layernorm(h, blk["ln2_w"], blk["ln2_b"])
            f = f @ blk["ffn0_w"].T + blk["ffn0_b"]
            f = _gelu(f)
            f = f @ blk["ffn2_w"].T + blk["ffn2_b"]
            h = h + f
        pooled = _layernorm(h[:, 0], p["ln_f_w"], p["ln_f_b"])
        lam = np.log1p(np.exp(pooled @ p["head_lam_w"].T + p["head_lam_b"])) + 1e-4
        return {
            "logits_1x2": pooled @ p["head_1x2_w"].T + p["head_1x2_b"],
            "logit_o25": (pooled @ p["head_o25_w"].T + p["head_o25_b"])[:, 0],
            "logit_btts": (pooled @ p["head_btts_w"].T + p["head_btts_b"])[:, 0],
            "logit_o35": (pooled @ p["head_o35_w"].T + p["head_o35_b"])[:, 0],
            "lam": lam,
        }

    # convenience: probabilities for every published market
    def predict(self, X: np.ndarray) -> dict:
        o = self.forward(X)
        p1 = _softmax(o["logits_1x2"], axis=1)
        sig = lambda z: 1.0 / (1.0 + np.exp(-z))               # noqa: E731
        return {
            "p_1x2": p1,
            "p_o25": sig(o["logit_o25"]),
            "p_btts": sig(o["logit_btts"]),
            "p_o35": sig(o["logit_o35"]),
            "lam": o["lam"],
        }
