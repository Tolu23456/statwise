#!/usr/bin/env python3
"""Feature enhancement utilities."""
import numpy as np
import pandas as pd

def add_rest_days(df):
    df = df.copy()
    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values('date')
    rest_h = []
    rest_a = []
    last_match = {}
    for idx, row in df.iterrows():
        h, a = row['home_team'], row['away_team']
        d = row['date']
        if h in last_match:
            rest_h.append((d - last_match[h]).days)
        else:
            rest_h.append(7)  # default
        if a in last_match:
            rest_a.append((d - last_match[a]).days)
        else:
            rest_a.append(7)
        last_match[h] = d
        last_match[a] = d
    df['rest_days_h'] = rest_h
    df['rest_days_a'] = rest_a
    df['rest_days_diff'] = df['rest_days_h'] - df['rest_days_a']
    return df

def add_xg_features(df):
    df = df.copy()
    # If HxG/AxG exist use them
    if 'HxG' in df.columns and 'AxG' in df.columns:
        df['hxg'] = pd.to_numeric(df['HxG'], errors='coerce').fillna(0)
        df['axg'] = pd.to_numeric(df['AxG'], errors='coerce').fillna(0)
        df['xg_diff'] = df['hxg'] - df['axg']
        df['xg_total'] = df['hxg'] + df['axg']
    # shots
    for c in ['HS','AS','HST','AST']:
        if c in df.columns:
            df[c.lower()] = pd.to_numeric(df[c], errors='coerce').fillna(0)
    if 'hs' in df.columns and 'as' in df.columns:
        df['shots_diff'] = df['hs'] - df['as']
    if 'hst' in df.columns and 'ast' in df.columns:
        df['sot_diff'] = df['hst'] - df['ast']
    return df
