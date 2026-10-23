"""Transformer encoder for team match sequences."""
import torch
import torch.nn as nn
import math

class PositionalEncoding(nn.Module):
    def __init__(self, d_model, max_len=20):
        super().__init__()
        pe = torch.zeros(max_len, d_model)
        position = torch.arange(0, max_len, dtype=torch.float).unsqueeze(1)
        div_term = torch.exp(torch.arange(0, d_model, 2).float() * (-math.log(10000.0) / d_model))
        pe[:, 0::2] = torch.sin(position * div_term)
        pe[:, 1::2] = torch.cos(position * div_term)
        pe = pe.unsqueeze(0)
        self.register_buffer('pe', pe)
    
    def forward(self, x):
        return x + self.pe[:, :x.size(1), :]

class TeamSequenceEncoder(nn.Module):
    def __init__(self, seq_feature_dim=16, d_model=64, n_heads=4, n_layers=2, dropout=0.1, embed_dim=32):
        super().__init__()
        self.input_proj = nn.Linear(seq_feature_dim, d_model)
        self.pos_enc = PositionalEncoding(d_model)
        encoder_layer = nn.TransformerEncoderLayer(
            d_model=d_model, nhead=n_heads, dim_feedforward=d_model*4,
            dropout=dropout, batch_first=True, activation='gelu'
        )
        self.transformer = nn.TransformerEncoder(encoder_layer, num_layers=n_layers)
        self.pool = nn.Sequential(
            nn.Linear(d_model, d_model),
            nn.ReLU(),
            nn.Dropout(dropout)
        )
        self.out_proj = nn.Linear(d_model, embed_dim)
    
    def forward(self, x, mask=None):
        # x: [B, T, F]
        x = self.input_proj(x)
        x = self.pos_enc(x)
        if mask is not None:
            x = self.transformer(x, src_key_padding_mask=mask)
        else:
            x = self.transformer(x)
        # attention-weighted mean pooling
        x = self.pool(x)
        # mean pooling
        emb = x.mean(dim=1)
        emb = self.out_proj(emb)
        return emb

class MatchPairEncoder(nn.Module):
    def __init__(self, **kwargs):
        super().__init__()
        self.team_enc = TeamSequenceEncoder(**kwargs)
        embed_dim = kwargs.get('embed_dim', 32)
        self.classifier = nn.Sequential(
            nn.Linear(embed_dim * 4, 128),
            nn.ReLU(),
            nn.Dropout(0.2),
            nn.Linear(128, 64),
            nn.ReLU(),
            nn.Dropout(0.2),
            nn.Linear(64, 3)
        )
    
    def forward(self, home_seq, away_seq):
        eh = self.team_enc(home_seq)
        ea = self.team_enc(away_seq)
        feat = torch.cat([eh, ea, eh-ea, eh*ea], dim=1)
        return self.classifier(feat)
