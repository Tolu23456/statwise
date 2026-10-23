"""Build match history sequences with no leakage."""
from __future__ import annotations
import numpy as np
import pandas as pd
from typing import Dict, List, Tuple
from .config import CFG

def build_team_sequences(df: pd.DataFrame) -> Dict[str, List[dict]]:
    """Build chronological match history per team."""
    df = df.copy()
    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values('date').reset_index(drop=True)
    seqs = {}
    for idx, row in df.iterrows():
        h, a = row['home_team'], row['away_team']
        for t, is_home in [(h, True), (a, False)]:
            if t not in seqs:
                seqs[t] = []
            hg, ag = row['home_goals'], row['away_goals']
            gf = hg if is_home else ag
            ga = ag if is_home else hg
            res = 1 if gf > ga else (0.5 if gf == ga else 0.0)
            opp = a if is_home else h
            seqs[t].append({
                'idx': idx,
                'date': row['date'],
                'opp': opp,
                'gf': gf, 'ga': ga,
                'res': res,
                'home': 1.0 if is_home else 0.0,
                'league': row.get('league_slug', 'all')
            })
    return seqs

def get_sequence_for_match(row, team_seqs: Dict, team: str, is_home: bool, opp: str, cutoff_date) -> np.ndarray:
    """Get last K matches before cutoff_date for team."""
    seq = team_seqs.get(team, [])
    hist = []
    for m in reversed(seq):
        if m['date'] < cutoff_date:
            hist.append(m)
            if len(hist) >= CFG.max_seq_len:
                break
    hist = list(reversed(hist))
    # Build features
    feats = []
    for m in hist:
        feats.append([
            m['res'],
            m['gf']/5.0, m['ga']/5.0,
            m['home'],
            1.0 if m['opp'] == opp else 0.0,  # played opponent before?
        ])
    # pad
    while len(feats) < CFG.max_seq_len:
        feats.insert(0, [0,0,0,0,0])
    # extend to seq_feature_dim if needed
    arr = np.array(feats, dtype=np.float32)
    if arr.shape[1] < CFG.seq_feature_dim:
        pad = np.zeros((arr.shape[0], CFG.seq_feature_dim - arr.shape[1]), dtype=np.float32)
        arr = np.concatenate([arr, pad], axis=1)
    else:
        arr = arr[:, :CFG.seq_feature_dim]
    return arr

def build_pair_sequences(df: pd.DataFrame) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Build home/away sequences for all matches."""
    df = df.copy()
    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values('date').reset_index(drop=True)
    team_seqs = build_team_sequences(df)
    
    home_seqs, away_seqs, targets = [], [], []
    for idx, row in df.iterrows():
        if idx < 20: continue  # need history
        h,a = row['home_team'], row['away_team']
        cutoff = row['date']
        hs = get_sequence_for_match(row, team_seqs, h, True, a, cutoff)
        aws = get_sequence_for_match(row, team_seqs, a, False, h, cutoff)
        home_seqs.append(hs)
        away_seqs.append(aws)
        # target
        if row['home_goals'] > row['away_goals']:
            targets.append(0)
        elif row['home_goals'] == row['away_goals']:
            targets.append(1)
        else:
            targets.append(2)
    return np.array(home_seqs), np.array(away_seqs), np.array(targets)
