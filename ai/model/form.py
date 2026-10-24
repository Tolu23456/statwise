"""
Recent-form differentials (feature slots 146-154).

Per side we look at the team's LAST FIVE matches before this one (never the
match itself, so there is no leakage):

    146  form5_ppg_home      points per game over last 5        [0..1]  (/3)
    147  form5_ppg_away      points per game over last 5        [0..1]
    148  form5_ppg_diff      home - away
    149  form5_gd_home       mean goal difference over last 5   [-1..1] (/4)
    150  form5_gd_away       mean goal difference over last 5   [-1..1]
    151  form5_gd_diff       home - away
    152  gd_trend_home       slope of goal difference over last 5
    153  gd_trend_away       slope of goal difference over last 5
    154  form_present        fraction of sides with >=5 prior matches

Sides with fewer than five prior matches fall back to whatever history they
do have (min_periods=1); teams seen for the first time score 0 with
form_present 0, so the model can tell "bad form" from "no data".

`compute_form_block` is shared by training (ai/model/features.py
build_training_set) and inference (build_features) so both paths stay
identical.
"""
from __future__ import annotations

import os

import numpy as np
import pandas as pd

SLOT_PPG_H = 146
SLOT_PPG_A = 147
SLOT_PPG_DIFF = 148
SLOT_GD_H = 149
SLOT_GD_A = 150
SLOT_GD_DIFF = 151
SLOT_TREND_H = 152
SLOT_TREND_A = 153
SLOT_FORM_PRESENT = 154
FORM_SLICE = slice(SLOT_PPG_H, SLOT_FORM_PRESENT + 1)  # 146:155
N_FORM = 9

_WINDOW = 5          # matches of form history
_MIN_PPG = 3.0       # 3 points per win -> normalise to [0, 1]
_GD_SCALE = 4.0      # +-4 goals per match saturates the normalisation
_TREND_SCALE = 2.0   # goals/match swing per match of the trend


def _slope_last5(shifted: pd.Series, group: pd.Series) -> pd.Series:
    """Least-squares slope of a 5-point window over prior values only.

    `shifted` must already be shifted once (current match excluded); x = 0..4
    with x=0 the oldest value, so with x-bar = 2 the intercept cancels:

        slope = (-2*y0 - y1 + y3 + 2*y4) / 10

    Rows with fewer than 5 prior matches return 0 (no trend yet).
    """
    y4 = shifted                       # most recent prior match
    y3 = shifted.groupby(group).shift(1)
    y1 = shifted.groupby(group).shift(3)
    y0 = shifted.groupby(group).shift(4)
    complete = y0.notna() & y3.notna() & y4.notna()
    slope = (-2.0 * y0 - y1 + y3 + 2.0 * y4) / 10.0
    return slope.where(complete, 0.0).fillna(0.0)


def compute_form_block(df: pd.DataFrame) -> np.ndarray:
    """(n, 9) recent-form features aligned with df's row order.

    Expects columns home_team, away_team, home_goals, away_goals, date.
    """
    n = len(df)
    out = np.zeros((n, N_FORM), dtype=np.float64)
    if n == 0:
        return out

    home = df["home_team"].astype(str).to_numpy()
    away = df["away_team"].astype(str).to_numpy()
    dates = pd.to_datetime(df["date"], errors="coerce")
    hg = pd.to_numeric(df["home_goals"], errors="coerce").fillna(0).to_numpy()
    ag = pd.to_numeric(df["away_goals"], errors="coerce").fillna(0).to_numpy()

    # ONE frame with both sides' records per match, so a team's form includes
    # its home AND away matches.  Current match is excluded by shift(1).
    def _pts(gf, ga):
        return np.where(gf > ga, 3.0, np.where(gf == ga, 1.0, 0.0))

    long = pd.DataFrame({
        "team": np.concatenate([home, away]),
        "date": np.concatenate([dates, dates]),
        "pts": np.concatenate([_pts(hg, ag), _pts(ag, hg)]),
        "gd": np.concatenate([hg - ag, ag - hg]),
        "row": np.concatenate([np.arange(n), np.arange(n)]),
        "is_away_side": np.concatenate([np.zeros(n, dtype=np.int8),
                                        np.ones(n, dtype=np.int8)]),
    })
    long = long.sort_values(["team", "date"], kind="stable")
    grp = long["team"]

    pts_prev = long.groupby("team", sort=False)["pts"].shift(1)
    gd_prev = long.groupby("team", sort=False)["gd"].shift(1)
    n_prior = long.groupby("team", sort=False).cumcount()

    ppg = pts_prev.groupby(grp, sort=False).transform(
        lambda s: s.rolling(_WINDOW, min_periods=1).mean()).fillna(0.0)
    gd = gd_prev.groupby(grp, sort=False).transform(
        lambda s: s.rolling(_WINDOW, min_periods=1).mean()).fillna(0.0)
    trend = _slope_last5(gd_prev, grp)

    ppg_v = (ppg / _MIN_PPG).clip(0.0, 1.0).to_numpy()
    gd_v = (gd / _GD_SCALE).clip(-1.0, 1.0).to_numpy()
    tr_v = (trend / _TREND_SCALE).clip(-1.0, 1.0).to_numpy()
    ready = (n_prior.to_numpy() >= _WINDOW).astype(np.float64)

    rows = long["row"].to_numpy()
    is_away = long["is_away_side"].to_numpy().astype(bool)
    home_side, away_side = ~is_away, is_away

    # each input row appears exactly once per side -> no double writes
    out[rows[home_side], 0] = ppg_v[home_side]
    out[rows[away_side], 1] = ppg_v[away_side]
    out[:, 2] = out[:, 0] - out[:, 1]
    out[rows[home_side], 3] = gd_v[home_side]
    out[rows[away_side], 4] = gd_v[away_side]
    out[:, 5] = out[:, 3] - out[:, 4]
    out[rows[home_side], 6] = tr_v[home_side]
    out[rows[away_side], 7] = tr_v[away_side]
    pres = np.zeros(n, dtype=np.float64)
    pres[rows[home_side]] = ready[home_side]
    pres_a = np.zeros(n, dtype=np.float64)
    pres_a[rows[away_side]] = ready[away_side]
    out[:, 8] = (pres + pres_a) / 2.0
    return out
