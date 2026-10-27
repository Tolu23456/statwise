"""Configuration for the attention student (all overridable via env)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _f(name: str, default: float) -> float:
    v = os.environ.get(name, "")
    try:
        return float(v) if v else default
    except ValueError:
        return default


def _i(name: str, default: int) -> int:
    v = os.environ.get(name, "")
    try:
        return int(float(v)) if v else default
    except ValueError:
        return default


@dataclass
class AttnConfig:
    # token set: one token per raw feature + one per teacher feature + CLS
    n_features: int = 161          # Pico feature matrix width
    n_teacher: int = 13            # lab-teacher posterior features
    # transformer
    d_model: int = _i("ATTN_D_MODEL", 64)
    n_heads: int = _i("ATTN_HEADS", 4)
    n_layers: int = _i("ATTN_LAYERS", 3)
    ff_mult: int = _i("ATTN_FF", 2)
    dropout: float = _f("ATTN_DROPOUT", 0.1)
    label_smoothing: float = _f("ATTN_LABEL_SMOOTH", 0.01)
    # optimisation
    batch_size: int = _i("ATTN_BATCH", 2048)
    epochs: int = _i("ATTN_EPOCHS", 12)
    patience: int = _i("ATTN_PATIENCE", 4)
    lr: float = _f("ATTN_LR", 3e-4)
    weight_decay: float = _f("ATTN_WD", 1e-4)
    warmup_frac: float = _f("ATTN_WARMUP", 0.05)
    # data
    holdout_days: int = _i("HOLDOUT_DAYS", 180)
    inner_val_rows: int = _i("ATTN_INNER_VAL", 40000)
    recency_half_life: float = _f("RECENCY_HALF_LIFE_DAYS", 1460)
    # lab teacher distillation (soft labels on real rows — tuned on inner val)
    distill_weight: float = _f("ATTN_DISTILL_WEIGHT", 0.0)
    distill_frac: float = _f("ATTN_DISTILL_FRAC", 0.3)
    # loss weights
    w_1x2: float = _f("ATTN_W_1X2", 1.0)
    w_goals: float = _f("ATTN_W_GOALS", 0.5)
    w_btts: float = _f("ATTN_W_BTTS", 0.35)
    w_o35: float = _f("ATTN_W_O35", 0.35)
    w_lam: float = _f("ATTN_W_LAM", 0.15)
    # paths / bookkeeping
    seed: int = _i("ATTN_SEED", 42)
    recipe_path: str = os.environ.get("ATTN_RECIPE", "ai/data/sim/worlds.json")
    model_out: str = os.environ.get("ATTN_OUT", "ai/models/attn_student.pkl")
    report_out: str = os.environ.get("ATTN_REPORT",
                                     "ai/attn/reports/attn_report.json")

    @property
    def n_tokens(self) -> int:
        return self.n_features + self.n_teacher

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if not k.startswith("_")}


CFG = AttnConfig()
