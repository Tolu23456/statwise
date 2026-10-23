"""Configuration for attention-augmented experiment."""
from dataclasses import dataclass
import os

@dataclass
class AttentionConfig:
    # Sequence params
    max_seq_len: int = 10  # last K matches per team
    seq_feature_dim: int = 16  # features per historical match
    # Attention params
    d_model: int = 64
    n_heads: int = 4
    n_layers: int = 2
    dropout: float = 0.1
    # Embedding output
    embed_dim: int = 32
    # Training
    batch_size: int = 256
    epochs: int = 20
    lr: float = 1e-3
    weight_decay: float = 1e-4
    device: str = os.environ.get("ATTN_DEVICE", "cpu")
    # Data
    val_split_date: str = os.environ.get("VAL_SPLIT_DATE", "2025-01-01")
    min_seq_len: int = 3  # need at least this many matches for attention
    # Paths
    data_path: str = os.environ.get("TRAIN_DATA_PATH", "ai/data/clean/2024_matches.csv")
    model_out: str = "ai/experimental/attention/models/att_encoder.pt"
    embed_cache: str = "ai/experimental/attention/cache/team_embeddings.pkl"
    comparison_out: str = "ai/experimental/attention/reports/comparison.json"

CFG = AttentionConfig()
