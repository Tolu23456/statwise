"""Attention student for the Monte Carlo Match Lab (plan Step 1.7).

A small tabular transformer (FT-Transformer style: one token per feature,
multi-head attention over the token set) that consumes the SAME 161-feature
matrix Pico's LightGBM sees, plus the lab teacher's 80-world posterior
features, and predicts 1X2 / O2.5 / BTTS / O3.5 / expected goals jointly.

Training lives in GitHub Actions (ai/attn/train.py, torch CPU); serving is
PURE NUMPy (ai/attn/serve.py) so the production predict path needs no new
dependencies — the trained weights ARE the recipe.
"""
from .config import AttnConfig  # noqa: F401
