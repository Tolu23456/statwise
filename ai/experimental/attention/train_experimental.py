#!/usr/bin/env python3
"""
Train attention-augmented model experimentally.
This will run on GitHub Actions as requested.
Compares against production model before replacement.
"""
import os, sys, json, pickle
from pathlib import Path
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset
from sklearn.metrics import log_loss, accuracy_score, brier_score_loss
import joblib

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from experimental.attention.config import CFG
from experimental.attention.data_utils import build_pair_sequences
from experimental.attention.encoder import MatchPairEncoder

def load_data():
    # Try to load all clean data
    clean_dir = Path("ai/data/clean")
    frames = []
    for p in sorted(clean_dir.glob("*.csv")):
        try:
            frames.append(pd.read_csv(p, low_memory=False))
        except Exception as e:
            print(f"Skip {p}: {e}")
    if not frames:
        raise SystemExit("No data found")
    df = pd.concat(frames, ignore_index=True)
    print(f"Loaded {len(df)} matches")
    return df

def train():
    os.makedirs("ai/experimental/attention/models", exist_ok=True)
    os.makedirs("ai/experimental/attention/reports", exist_ok=True)
    os.makedirs("ai/experimental/attention/cache", exist_ok=True)
    
    df = load_data()
    print("Building sequences...")
    home_seqs, away_seqs, targets = build_pair_sequences(df)
    print(f"Sequences: {home_seqs.shape}, targets: {targets.shape}")
    
    # Split by date
    df['date'] = pd.to_datetime(df['date'])
    df_sorted = df.sort_values('date').reset_index(drop=True)
    # Use same indices as sequences (after skip)
    split_date = pd.to_datetime(CFG.val_split_date)
    # Find split point
    val_mask = df_sorted['date'] >= split_date
    val_idx = np.where(val_mask)[0]
    # Adjust for skip of 20
    val_idx_adj = val_idx[val_idx >= 20] - 20
    train_idx_adj = np.array([i for i in range(len(targets)) if i not in set(val_idx_adj)])
    
    Xh_train, Xa_train, y_train = home_seqs[train_idx_adj], away_seqs[train_idx_adj], targets[train_idx_adj]
    Xh_val, Xa_val, y_val = home_seqs[val_idx_adj], away_seqs[val_idx_adj], targets[val_idx_adj]
    
    print(f"Train: {len(y_train)}, Val: {len(y_val)}")
    
    device = torch.device(CFG.device)
    model = MatchPairEncoder(seq_feature_dim=CFG.seq_feature_dim, d_model=CFG.d_model,
                             n_heads=CFG.n_heads, n_layers=CFG.n_layers,
                             dropout=CFG.dropout, embed_dim=CFG.embed_dim).to(device)
    
    optimizer = torch.optim.AdamW(model.parameters(), lr=CFG.lr, weight_decay=CFG.weight_decay)
    criterion = nn.CrossEntropyLoss()
    
    train_ds = TensorDataset(torch.from_numpy(Xh_train), torch.from_numpy(Xa_train), torch.from_numpy(y_train))
    train_dl = DataLoader(train_ds, batch_size=CFG.batch_size, shuffle=True)
    
    best_loss = float('inf')
    for epoch in range(CFG.epochs):
        model.train()
        total_loss = 0
        for xh, xa, y in train_dl:
            xh, xa, y = xh.to(device), xa.to(device), y.to(device)
            optimizer.zero_grad()
            out = model(xh, xa)
            loss = criterion(out, y)
            loss.backward()
            optimizer.step()
            total_loss += loss.item()
        
        # val
        model.eval()
        with torch.no_grad():
            xh_v = torch.from_numpy(Xh_val).to(device)
            xa_v = torch.from_numpy(Xa_val).to(device)
            y_v = torch.from_numpy(y_val).to(device)
            out_v = model(xh_v, xa_v)
            val_loss = criterion(out_v, y_v).item()
            preds = out_v.argmax(1).cpu().numpy()
            acc = accuracy_score(y_val, preds)
            probs = torch.softmax(out_v, 1).cpu().numpy()
            ll = log_loss(y_val, probs)
        print(f"Epoch {epoch+1}: train_loss={total_loss/len(train_dl):.4f} val_loss={val_loss:.4f} acc={acc:.4f} logloss={ll:.4f}")
        if val_loss < best_loss:
            best_loss = val_loss
            torch.save({'model': model.state_dict(), 'config': CFG.__dict__}, CFG.model_out)
    
    print("Training complete. Model saved to", CFG.model_out)
    
    # Generate embeddings for comparison
    model.eval()
    with torch.no_grad():
        all_emb_h = []
        all_emb_a = []
        bs = 512
        for i in range(0, len(home_seqs), bs):
            xh = torch.from_numpy(home_seqs[i:i+bs]).to(device)
            xa = torch.from_numpy(away_seqs[i:i+bs]).to(device)
            eh = model.team_enc(xh).cpu().numpy()
            ea = model.team_enc(xa).cpu().numpy()
            all_emb_h.append(eh)
            all_emb_a.append(ea)
        all_emb_h = np.concatenate(all_emb_h)
        all_emb_a = np.concatenate(all_emb_a)
    
    # Save embeddings aligned to df (after skip)
    emb_df = df_sorted.iloc[20:20+len(all_emb_h)].copy()
    emb_df['emb_h'] = [all_emb_h[i].tolist() for i in range(len(all_emb_h))]
    emb_df['emb_a'] = [all_emb_a[i].tolist() for i in range(len(all_emb_h))]
    emb_df.to_pickle(CFG.embed_cache)
    print("Embeddings cached to", CFG.embed_cache)
    
    # Save metrics
    report = {
        'val_logloss': ll,
        'val_acc': acc,
        'val_brier': brier_score_loss(pd.get_dummies(y_val).values.ravel(), probs.ravel()),
        'n_train': int(len(y_train)),
        'n_val': int(len(y_val)),
        'config': {k:v for k,v in CFG.__dict__.items() if not k.startswith('_')}
    }
    with open(CFG.comparison_out.replace('.json', '_attn.json'), 'w') as f:
        json.dump(report, f, indent=2)
    print("Report saved")

if __name__ == '__main__':
    train()
