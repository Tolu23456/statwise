#!/usr/bin/env python3
"""
Background retraining script.

Downloads data from all 5 open-source football data sources, merges them,
trains the ensemble model, and atomically replaces the .pkl file.

Usage:
    python3 ai/retrain.py [--force]

Can also be imported and called programmatically by the scheduler.
"""
from __future__ import annotations
import os, sys, logging, time, json, datetime, shutil, tempfile, glob, gc

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-5s] %(name)s: %(message)s",
)
logger = logging.getLogger("statwise.retrain")


def _rss_mb() -> float:
    try:
        with open('/proc/self/status') as fh:
            for line in fh:
                if line.startswith('VmRSS:'):
                    return int(line.split()[1]) / 1024.0
    except Exception:
        pass
    return 0.0

MODEL_PATH = os.path.join(BASE_DIR, "models", "football_predictor.pkl")
INFO_PATH  = os.path.join(BASE_DIR, "data",   "model_info.json")
os.makedirs(os.path.join(BASE_DIR, "models"), exist_ok=True)
os.makedirs(os.path.join(BASE_DIR, "data"),   exist_ok=True)


def _devig(o) -> "np.ndarray":
    """De-vigged market probabilities from a 3-column odds matrix."""
    import numpy as np
    imp = 1.0 / np.maximum(np.asarray(o, dtype=float), 1.0001)
    return imp / np.maximum(imp.sum(axis=1, keepdims=True), 1e-9)


def _valid_odds(o) -> "np.ndarray":
    """Rows that actually carry odds on all three outcomes."""
    import numpy as np
    a = np.asarray(o, dtype=float)
    return (a[:, 0] > 1.01) & (a[:, 1] > 1.01) & (a[:, 2] > 1.01)


def damp_alpha(fit_alpha, n_valid, prior: float = 0.35, prior_k: float = 1000.0) -> float:
    """Shrink a holdout-fitted blend weight toward the measured prior (plan 1.4).

    weight = n / (n + prior_k).  The eval-blend ablations measured a fixed
    alpha of 0.35 on the closing market as the best deployable blend
    (ll 0.99961 rolling vs 1.00033 for an alpha re-refit every week), so the
    fit only gets to move the weight in proportion to how much evidence backs
    it: ~1,280 valid rows with prior_k=1000 -> fit 56% / prior 44%.
    fit_alpha=None (too few odds rows to fit at all) deploys the prior.
    """
    if fit_alpha is None:
        return round(float(prior), 4)
    if prior_k <= 0:
        return round(float(fit_alpha), 4)
    w = float(n_valid) / (float(n_valid) + float(prior_k))
    return round(float(w) * float(fit_alpha) + (1.0 - float(w)) * float(prior), 4)


def _load_clean_data() -> "pd.DataFrame":
    """Load all matched from ai/data/clean/YYYY_matches.csv (unified 48-col schema)."""
    import pandas as pd
    clean_dir = os.path.join(BASE_DIR, "data", "clean")
    paths = sorted(glob.glob(os.path.join(clean_dir, "????_matches.csv")))
    if not paths:
        return pd.DataFrame()
    frames = []
    for p in paths:
        try:
            frames.append(pd.read_csv(p, low_memory=False))
        except Exception as e:
            logger.warning(f"Could not read {p}: {e}")
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    for col in ["home_team", "away_team", "home_goals", "away_goals"]:
        if col not in df.columns:
            return pd.DataFrame()
    df = df.dropna(subset=["home_team", "away_team", "home_goals", "away_goals"])
    df["home_goals"] = pd.to_numeric(df["home_goals"], errors="coerce").fillna(0).astype(int)
    df["away_goals"] = pd.to_numeric(df["away_goals"], errors="coerce").fillna(0).astype(int)
    for col in ["odds_home", "odds_draw", "odds_away"]:
        if col not in df.columns:
            df[col] = float("nan")
        else:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.sort_values("date").reset_index(drop=True)
    # Weather enrichment sidecar (ai/enrich_weather_history.py -> data/weather_by_match.csv.gz)
    wx_path = os.path.join(BASE_DIR, "data", "weather_by_match.csv.gz")
    if not os.path.exists(wx_path):  # older uncompressed sidecar
        wx_path = os.path.join(BASE_DIR, "data", "weather_by_match.csv")
    if os.path.exists(wx_path) and "date" in df.columns:
        try:
            wx = pd.read_csv(wx_path)
            wx["date"] = pd.to_datetime(wx["date"], errors="coerce")
            wx = wx.dropna(subset=["date"]).drop_duplicates(subset=["date", "home_team"])
            n0 = len(df.columns)
            df = df.merge(wx, on=["date", "home_team"], how="left")
            cov = pd.to_numeric(df.get("temp_c"), errors="coerce").notna().mean() if "temp_c" in df else 0
            logger.info(f"Weather sidecar joined: +{len(df.columns) - n0} cols, {cov:.0%} coverage")
        except Exception as e:
            logger.warning(f"Weather sidecar join failed: {e}")
    # Expanded temporal window to utilize historical depth (back to 1993)
    if "date" in df.columns:
        df = df[df["date"] >= pd.Timestamp("1993-08-01")]
    # Drop international matches (model is tuned for club football)
    if "is_international" in df.columns:
        df = df[pd.to_numeric(df["is_international"], errors="coerce").fillna(0) == 0]
    # Quality filter — require at least goals + source confirmed (score ≥ 40)
    if "quality_score" in df.columns:
        df = df[pd.to_numeric(df["quality_score"], errors="coerce").fillna(0) >= 40]
    logger.info(f"Clean data: {len(df):,} rows from {len(paths)} year files")
    return df.reset_index(drop=True)


def _distill_soft_labels(X, y, g, w, home, away, leagues, split_at, tag,
                         aux: dict | None = None):
    """Step 1.5 distillation: duplicate a fraction of rows with lab labels.

    Appends teacher-sampled duplicates of REAL feature rows (see
    ai/sim/distill.py).  Returns the possibly-extended (X, y, g, w) plus a
    JSON-serialisable info dict for the calibration record, or the inputs
    unchanged with info=None whenever distillation must not happen:

      * SYNTH_LABELS not set                       -> off (default);
      * no recipe at ai/data/sim/worlds.json       -> the lab hasn't published;
      * teacher split AFTER our holdout split      -> it saw holdout outcomes;
      * meta without aligned home/away/leagues     -> can't price the rows.

    Nothing here ever touches the holdout arrays — callers pass train-side
    slices — and the gate below still decides what ships.

    `aux` (plan 1.6): optional dict of label arrays ALIGNED TO X (e.g.
    {"y_btts": …, "y_o35": …}); its entries are extended IN PLACE with the
    TEACHER's label for each duplicated row (same scoreline draw), keeping
    the auxiliary heads row-aligned with X.  A misaligned or teacherless
    entry becomes None (with a warning) instead of crashing — fit_stacks
    then skips that head.
    """
    import numpy as np

    flag = os.environ.get("SYNTH_LABELS", "").strip().lower()
    if flag not in ("1", "true", "yes"):
        return X, y, g, w, None
    try:
        from sim.distill import load_teacher, sample_soft_labels, teacher_may_fit
    except Exception as e:
        logger.warning(f"[SYNTH] distill module unavailable: {e}")
        return X, y, g, w, None
    recipe_path = (os.environ.get("SYNTH_RECIPE")
                   or os.path.join(BASE_DIR, "data", "sim", "worlds.json"))
    worlds, rmeta = load_teacher(recipe_path)
    if not worlds:
        logger.info(f"[SYNTH] no teacher recipe at {recipe_path} — "
                    "training without soft labels")
        return X, y, g, w, None
    ok, why = teacher_may_fit(rmeta or {}, split_at)
    if not ok:
        logger.warning(f"[SYNTH] distillation skipped: {why}")
        return X, y, g, w, None
    if home is None or away is None or leagues is None or len(home) != len(y):
        logger.warning("[SYNTH] meta missing aligned home/away/leagues — "
                        "cannot sample teacher labels, training without them")
        return X, y, g, w, None

    frac = float(os.environ.get("SYNTH_FRACTION", "0.5") or 0.5)
    seed = int(os.environ.get("SYNTH_SEED", "0") or 0)
    idx, y_s, g_s, b_s, o_s = sample_soft_labels(worlds, home, away, leagues,
                                                 fraction=frac, seed=seed)
    if not len(idx):
        return X, y, g, w, None
    X2 = np.concatenate([X, X[idx]])
    y2 = np.concatenate([np.asarray(y), y_s])
    g2 = np.concatenate([np.asarray(g), g_s])
    w2 = np.concatenate([w, w[idx]]) if w is not None else None
    # plan 1.6: aux labels get the TEACHER's label from the SAME scoreline
    # draw (not the source row's real label — the teacher's belief is the
    # point of distillation, and all four labels stay jointly consistent).
    if aux:
        teacher = {"y_btts": b_s, "y_o35": o_s}
        for k, arr in list(aux.items()):
            if arr is None:
                continue          # upstream already reported labels unavailable
            if len(arr) != len(X):
                logger.warning(f"[SYNTH] aux label {k} misaligned "
                               f"({len(arr)} vs {len(X)}) — head dropped")
                aux[k] = None
            elif k not in teacher:
                logger.warning(f"[SYNTH] no teacher label for aux {k} — head dropped")
                aux[k] = None
            else:
                aux[k] = np.concatenate([np.asarray(arr), teacher[k]])
    info = {"enabled": True, "rows_added": int(len(idx)),
            "rows_before": int(len(X)), "fraction": frac, "seed": seed,
            "teacher_worlds": len(worlds),
            "teacher_split": (rmeta or {}).get("split_at")}
    logger.info(f"[SYNTH] {tag}: +{len(idx):,} teacher-labeled duplicates "
                f"({len(X):,} -> {len(X2):,} rows, {len(worlds)} worlds, "
                f"teacher split {info['teacher_split']})")
    return X2, y2, g2, w2, info


def run(force: bool = False) -> bool:
    """
    Full retrain pipeline. Returns True on success.
    Safe to call from the scheduler — writes to a temp file first,
    then atomically moves it into place so predictions keep working.
    """
    import pandas as pd
    from model.trainer import FootballPredictor

    t0 = time.time()
    logger.info("=" * 60)
    logger.info(" StatWise multi-source retrain starting")
    logger.info("=" * 60)

    # ── Primary: clean CSV pipeline (ai/data/clean/*.csv) ─────────
    logger.info("[1/2] Loading clean match data (ai/data/clean/)…")
    df = _load_clean_data()

    if df.empty:
        # Fallback: old per-source download approach
        logger.warning("Clean data not found — falling back to live download…")
        from model.downloader   import load_training_data, SEASONS, LEAGUE_CODES
        from model.open_sources import load_all_open_sources

        df1 = load_training_data(seasons=SEASONS, leagues=list(LEAGUE_CODES.keys()))
        logger.info(f"      football-data.co.uk: {len(df1):,} rows")
        df2 = load_all_open_sources()
        logger.info(f"      supplementary open sources: {len(df2):,} rows")

        frames = [f for f in [df1, df2] if not f.empty]
        if not frames:
            logger.error("No training data available — aborting retrain.")
            return False
        df = pd.concat(frames, ignore_index=True)

        # Strip ClubElo synthetic rows (fake 1-0 home wins that bias the model)
        before = len(df)
        if "league_slug" in df.columns:
            df = df[df["league_slug"] != "clubelo-reference"]
        if "away_team" in df.columns:
            df = df[df["away_team"] != "Reference"]
        logger.info(f"Dropped {before - len(df):,} synthetic ClubElo rows")

        # Robust deduplication by normalizing team names
        def normalize_name(name):
            if not isinstance(name, str): return ""
            import unicodedata
            name = name.lower().strip()
            name = "".join(c for c in unicodedata.normalize('NFD', name)
                           if unicodedata.category(c) != 'Mn')
            # Remove punctuation
            import string
            name = "".join(c for c in name if c not in string.punctuation)
            return name

        df["h_norm"] = df["home_team"].apply(normalize_name)
        df["a_norm"] = df["away_team"].apply(normalize_name)

        df = df.drop_duplicates(
            subset=["h_norm", "a_norm", "date", "home_goals", "away_goals"])
        df = df.drop(columns=["h_norm", "a_norm"])
    else:
        logger.info("[2/2] Supplementary live sources skipped (clean data available)")

    logger.info(f"Combined dataset: {len(df):,} unique real matches ({_rss_mb():.0f} MB RSS)")

    # ── Honest out-of-time split: calibrate + evaluate + market alpha ──
    # Features are built ONCE through a chronological feature state; the
    # stacking ensemble trains only on rows BEFORE the holdout window, the
    # calibrator/alpha/metrics come from the holdout (first half calibrates,
    # second half evaluates), then Stage B refits the raw stacks on ALL rows
    # keeping that honest calibrator.  STAGE_B=0 skips the refit (~half time).
    # KNOWN SMALL LEAK: league-level aggregates are fit on the full frame
    # (<1% of their signal sits inside a 90d holdout); per-row ratings are
    # strictly chronological.
    import numpy as np
    from sklearn.isotonic import IsotonicRegression
    from sklearn.metrics import log_loss

    holdout_days = int(os.environ.get("HOLDOUT_DAYS", "90"))
    stage_b = os.environ.get("STAGE_B", "0") != "0"
    df = df.sort_values("date", kind="stable").reset_index(drop=True)
    dates = pd.to_datetime(df["date"], errors="coerce")
    split_at = dates.max() - pd.Timedelta(days=holdout_days)

    logger.info("Building feature state (single pass over full frame)…")
    t0f = time.time()
    predictor = FootballPredictor()
    predictor.feature_pipe.fit(df)
    X_all, y_all, g_all, d_all = predictor.feature_pipe.build_training_set(df)
    X_all = np.nan_to_num(X_all, nan=0.0, posinf=1e6, neginf=-1e6).astype(np.float32, copy=False)
    meta = getattr(predictor.feature_pipe, "last_meta", {}) or {}
    logger.info(f"Features built in {time.time() - t0f:.1f}s "
                f"({X_all.shape[0]:,} x {X_all.shape[1]} float32 = {X_all.nbytes / 1e6:.0f} MB)")

    m_tr = pd.to_datetime(d_all) <= split_at
    m_ho = ~m_tr
    calib_info: dict = {}

    def _apply_cal(p, calibs):
        q = np.column_stack([iso.predict(p[:, k]) for k, iso in enumerate(calibs)])
        q = np.clip(q, 1e-6, None)
        return q / q.sum(axis=1, keepdims=True)

    def _ll(p, y):
        return float(log_loss(y, p, labels=[0, 1, 2]))

    if int(m_tr.sum()) >= 2000 and int(m_ho.sum()) >= 600:
        logger.info(f"[OOT] train {int(m_tr.sum()):,} rows (<= {split_at.date()}) | "
                    f"holdout {int(m_ho.sum()):,} rows (last {holdout_days}d)")

        Xt, yt, gt = X_all[m_tr], y_all[m_tr], g_all[m_tr]
        Xh, yh, gh = X_all[m_ho], y_all[m_ho], g_all[m_ho]

        # Plan 1.6: auxiliary-head labels (BTTS / over-under 3.5) — sliced
        # with the SAME out-of-time mask.  The train copy may be extended by
        # distillation below; the holdout copy never is (holdout rows never
        # receive duplicates).
        _bt = meta.get("y_btts"); _ot = meta.get("y_o35")
        _aux_ok = (_bt is not None and len(_bt) == len(m_tr)
                   and _ot is not None and len(_ot) == len(m_tr))
        if not _aux_ok:
            logger.warning("[1.6] y_btts/y_o35 missing or misaligned in "
                           "last_meta — training without auxiliary heads")
        aux_tr = {"y_btts": _bt[m_tr] if _aux_ok else None,
                  "y_o35": _ot[m_tr] if _aux_ok else None}
        bh = _bt[m_ho] if _aux_ok else None   # holdout BTTS labels
        oh = _ot[m_ho] if _aux_ok else None   # holdout O3.5 labels

        # Time-decay weights: recent seasons resemble the matches we actually
        # predict.  Tuned on the OOT holdout (joint log loss): 4y half-life
        # 1.00999 < 6y 1.01154 < 2y 1.01194 < none 1.01275.  0 disables.
        half_life = float(os.environ.get("RECENCY_HALF_LIFE_DAYS", "1460") or 0)
        dates_all = pd.to_datetime(d_all, errors="coerce")
        ref_date = dates_all[m_tr].max()

        def _recency_weights(mask):
            if half_life <= 0:
                return None
            # works whether dates_all is a Series or a DatetimeIndex
            age_days = np.asarray((ref_date - dates_all) / pd.Timedelta(days=1),
                                  dtype=np.float64)
            age = np.clip(age_days[mask], 0.0, None)
            w = np.power(0.5, age / half_life)
            return np.maximum(w, 0.02).astype(np.float32)

        w_tr = _recency_weights(m_tr)
        if w_tr is not None:
            eff = (w_tr.sum() ** 2) / float((w_tr.astype(np.float64) ** 2).sum())
            logger.info(f"[OOT] recency weights: half-life {half_life:.0f}d, "
                        f"min={w_tr.min():.3f}, effective_n={eff:,.0f}")

        # Step 1.5 distillation: append teacher-labeled duplicates of TRAIN
        # rows only (holdout arrays below never see them).  Off unless
        # SYNTH_LABELS=1; skipped automatically when the lab's recipe is
        # missing or newer than this split (would leak holdout outcomes).
        _h = meta.get("home"); _a = meta.get("away"); _l = meta.get("leagues")
        if _h is not None and _a is not None and _l is not None and len(_h) == len(m_tr):
            _h, _a, _l = _h[m_tr], _a[m_tr], _l[m_tr]
        else:
            _h = _a = _l = None
        Xt, yt, gt, w_tr, synth_a = _distill_soft_labels(
            Xt, yt, gt, w_tr, _h, _a, _l, split_at, "train", aux=aux_tr)

        odds_h = meta.get("odds")
        odds_h = odds_h[m_ho] if odds_h is not None else np.zeros((len(Xh), 3))
        # Closing consensus (plan 1.4 "prefer closing odds when available"):
        # avg closing across books, falling back to the single-book price per
        # cell (features.py exposes it as odds_closing).  This is the market
        # side that won EVERY comparison in the eval-blend ablations —
        # -0.00112 on the eval half, -0.00109 on the cal half, -0.00136
        # rolling — and it is the same kind of consensus price odds_sync feeds
        # at inference, so train and serve now blend against one source.
        odds_close_h = meta.get("odds_closing")
        odds_close_h = odds_close_h[m_ho] if odds_close_h is not None else odds_h
        leagues_h = meta.get("leagues")
        leagues_h = leagues_h[m_ho] if leagues_h is not None else np.array(["all"] * len(Xh))

        mid = max(len(yh) // 2, 100)
        y_ev = yh[mid:]
        o_ev = odds_h[mid:]
        o_ev_c = odds_close_h[mid:]

        mkt = _devig(o_ev)        # single book -> market_log_loss (baseline continuity)
        mkt_c = _devig(o_ev_c)    # closing consensus -> alpha fit + blend metric
        valid = _valid_odds(o_ev)
        valid_c = _valid_odds(o_ev_c)

        # ── Candidate architectures ────────────────────────────────
        # 'single' = 2 LightGBM heads, 'joint' = 1 six-class LightGBM.
        # 'both' trains each on the SAME train slice and keeps whichever wins
        # on the untouched later-holdout log loss (goals Brier breaks ties).
        mode_env = os.environ.get("MODEL_MODE", "both").strip().lower()
        modes = ["single", "joint"] if mode_env in ("both", "auto") else [mode_env]
        logger.info(f"[OOT] candidate modes: {modes}")

        best = None
        for mode in modes:
            t1 = time.time()
            cand = FootballPredictor()
            cand.feature_pipe = predictor.feature_pipe  # share the fitted state
            try:
                cand.fit_stacks(Xt, yt, gt, calibrate=False, mode=mode,
                                sample_weight=w_tr,
                                y_btts=aux_tr["y_btts"], y_o35=aux_tr["y_o35"])
                raw_h = cand._outcome_pipe.predict_proba(Xh)
            except Exception as e:
                logger.exception(f"[{mode}] training failed: {e}")
                del cand
                gc.collect()
                continue

            # calibrate on the EARLIER holdout half, evaluate on the LATER half
            cals = []
            for k in range(3):
                iso = IsotonicRegression(out_of_bounds="clip")
                iso.fit(raw_h[:mid, k], (yh[:mid] == k).astype(float))
                cals.append(iso)
            p_cal = _apply_cal(raw_h[mid:], cals)
            p_raw = raw_h[mid:]
            gp = cand._goals_pipe.predict_proba(Xh[mid:])[:, 1]
            goals_brier = float(((gp - gh[mid:]) ** 2).mean())
            # Plan 1.6: honest holdout Brier for the auxiliary heads on the
            # SAME eval rows as goals_brier (None when the labels or heads
            # are absent, e.g. an older feature build).
            btts_brier = o35_brier = None
            if getattr(cand, "_btts_pipe", None) is not None and bh is not None:
                bp = cand._btts_pipe.predict_proba(Xh[mid:])[:, 1]
                btts_brier = float(((bp - bh[mid:]) ** 2).mean())
            if getattr(cand, "_o35_pipe", None) is not None and oh is not None:
                op = cand._o35_pipe.predict_proba(Xh[mid:])[:, 1]
                o35_brier = float(((op - oh[mid:]) ** 2).mean())
            ll_raw = _ll(p_raw, y_ev)
            ll_cal = _ll(p_cal, y_ev)
            # 1,440 eval rows are not enough for per-class isotonic to pay off
            # every time — only deploy the calibrator if it wins out of sample.
            use_cal = ll_cal < ll_raw
            p_ev = p_cal if use_cal else p_raw
            ll_eval = min(ll_raw, ll_cal)
            acc = float((p_ev.argmax(axis=1) == y_ev).mean())
            _btxt = (f"btts_brier={btts_brier:.5f}" if btts_brier is not None
                     else "btts_brier=n/a")
            _otxt = (f"o35_brier={o35_brier:.5f}" if o35_brier is not None
                     else "o35_brier=n/a")
            logger.info(f"[OOT:{mode}] {time.time() - t1:.1f}s "
                        f"raw_ll={ll_raw:.5f} cal_ll={ll_cal:.5f} "
                        f"use_cal={use_cal} acc={acc:.4f} goals_brier={goals_brier:.5f} "
                        f"{_btxt} {_otxt}")

            # lower deployed log loss wins; within 0.0005 prefer better goals Brier
            better = (best is None
                      or ll_eval < best["ll"] - 0.0005
                      or (abs(ll_eval - best["ll"]) <= 0.0005
                          and goals_brier < best["goals_brier"]))
            if better:
                if best is not None:
                    del best["pred"]
                best = dict(mode=mode, pred=cand, raw=raw_h,
                            calibs=cals if use_cal else None, p_ev=p_ev,
                            ll=ll_eval, ll_raw=ll_raw, ll_cal=ll_cal,
                            use_cal=use_cal, goals_brier=goals_brier,
                            btts_brier=btts_brier, o35_brier=o35_brier)
            else:
                del cand
            gc.collect()

        # ── Plan 2.4: attention student race ─────────────────────────
        # The trained bundle (ai/attn/train.py, ATTN_STUDENT env) enters the
        # SAME competition as the LightGBM modes: identical train/holdout
        # slices, identical per-candidate isotonic protocol, identical
        # 0.0005 gate arithmetic below.  Unset env or missing bundle -> the
        # race silently doesn't happen; FORCE_REPLACE is never read here.
        attn_path = os.environ.get("ATTN_STUDENT", "").strip()
        if attn_path and not os.path.exists(attn_path) and \
                attn_path.startswith("ai" + os.sep):
            # workflows set repo-root-relative paths but run retrain from ai/
            _alt = os.path.join(BASE_DIR, attn_path[len("ai" + os.sep):])
            if os.path.exists(_alt):
                attn_path = _alt
        if attn_path:
            cand = None
            try:
                from attn.serve import attach
                t1 = time.time()
                cand = FootballPredictor()
                cand.feature_pipe = predictor.feature_pipe  # share fitted state
                if attach(cand, attn_path) is None:
                    logger.warning(f"[attn] bundle {attn_path} missing/unreadable "
                                   "— attention candidate not raced")
                    del cand
                    cand = None
                else:
                    _hh = meta.get("home")
                    _aa = meta.get("away")
                    _hh = _hh[m_ho] if _hh is not None and len(_hh) == len(X_all) else None
                    _aa = _aa[m_ho] if _aa is not None and len(_aa) == len(X_all) else None
                    Xh_e = cand._outcome_pipe.extend(Xh, _hh, _aa, leagues_h)
                    raw_a = cand._outcome_pipe.predict_proba(Xh_e)
                    cals_a = []
                    for k in range(3):
                        iso = IsotonicRegression(out_of_bounds="clip")
                        iso.fit(raw_a[:mid, k], (yh[:mid] == k).astype(float))
                        cals_a.append(iso)
                    p_cal_a = _apply_cal(raw_a[mid:], cals_a)
                    p_raw_a = raw_a[mid:]
                    ll_raw_a, ll_cal_a = _ll(p_raw_a, y_ev), _ll(p_cal_a, y_ev)
                    use_cal_a = ll_cal_a < ll_raw_a
                    p_ev_a = p_cal_a if use_cal_a else p_raw_a
                    ll_a = min(ll_raw_a, ll_cal_a)
                    goals_a = float(
                        ((cand._goals_pipe.predict_proba(Xh_e[mid:])[:, 1]
                          - gh[mid:]) ** 2).mean())
                    btts_a = o35_a = None
                    if bh is not None and getattr(cand, "_btts_pipe", None) is not None:
                        btts_a = float(
                            ((cand._btts_pipe.predict_proba(Xh_e[mid:])[:, 1]
                              - bh[mid:]) ** 2).mean())
                    if oh is not None and getattr(cand, "_o35_pipe", None) is not None:
                        o35_a = float(
                            ((cand._o35_pipe.predict_proba(Xh_e[mid:])[:, 1]
                              - oh[mid:]) ** 2).mean())
                    logger.info(f"[OOT:attn] {time.time() - t1:.1f}s "
                                f"raw_ll={ll_raw_a:.5f} cal_ll={ll_cal_a:.5f} "
                                f"use_cal={use_cal_a} goals_brier={goals_a:.5f}")
                    better = (best is None
                              or ll_a < best["ll"] - 0.0005
                              or (abs(ll_a - best["ll"]) <= 0.0005
                                  and goals_a < best["goals_brier"]))
                    if better:
                        if best is not None:
                            del best["pred"]
                        best = dict(mode="attn", pred=cand, raw=raw_a,
                                    calibs=cals_a if use_cal_a else None,
                                    p_ev=p_ev_a, ll=ll_a, ll_raw=ll_raw_a,
                                    ll_cal=ll_cal_a, use_cal=use_cal_a,
                                    goals_brier=goals_a, btts_brier=btts_a,
                                    o35_brier=o35_a)
                        logger.info(f"[OOT] attention student takes the lead "
                                    f"(eval_ll={ll_a:.5f})")
                    else:
                        del cand
                        cand = None
                    gc.collect()
            except Exception:
                logger.exception("[attn] candidate race failed — "
                                 "LightGBM result stands")
                if cand is not None:
                    del cand
                    gc.collect()

        if best is None:
            logger.error("All candidate modes failed to train")
            return False

        mode = best["mode"]
        raw_h, calibs, p_ev = best["raw"], best["calibs"], best["p_ev"]
        raw_ev = raw_h[mid:]
        pred_a = best["pred"]
        logger.info(f"[OOT] selected mode='{mode}' "
                    f"(eval_ll={best['ll']:.5f}, calibration={'on' if best['use_cal'] else 'off'}, "
                    f"goals_brier={best['goals_brier']:.5f})")

        conf = p_ev.max(axis=1)
        correct = (p_ev.argmax(axis=1) == y_ev).astype(float)
        ece = 0.0
        edges = np.linspace(0, 1, 11)
        for bi in range(10):
            m = (conf >= edges[bi]) & (conf < edges[bi + 1] if bi < 9 else conf <= edges[bi + 1])
            if m.sum():
                ece += float(m.mean()) * abs(float(correct[m].mean()) - float(conf[m].mean()))

        metrics = {
            "mode": mode,
            "holdout_days": holdout_days,
            "holdout_matches": int(len(y_ev)),
            "split_at": str(split_at.date()),
            "raw_log_loss": round(best["ll_raw"], 5),
            "isotonic_log_loss": round(best["ll_cal"], 5),
            "calibration_used": bool(best["use_cal"]),
            "eval_log_loss": round(best["ll"], 5),
            "market_log_loss": round(_ll(mkt[valid], y_ev[valid]), 5) if int(valid.sum()) > 50 else None,
            "market_rows": int(valid.sum()),
            # closing consensus (the source alpha is actually fitted against)
            "market_closing_log_loss": round(_ll(mkt_c[valid_c], y_ev[valid_c]), 5) if int(valid_c.sum()) > 50 else None,
            "market_closing_rows": int(valid_c.sum()),
            "accuracy": round(float((p_ev.argmax(axis=1) == y_ev).mean()), 4),
            "brier": round(float(((p_ev - np.eye(3)[y_ev]) ** 2).mean()), 5),
            "goals_brier": round(best["goals_brier"], 5),
            # plan 1.6: auxiliary goal-market heads — honest holdout Brier
            # (null when the labels/heads weren't available this run)
            "btts_brier": (round(best["btts_brier"], 5)
                           if best.get("btts_brier") is not None else None),
            "o35_brier": (round(best["o35_brier"], 5)
                          if best.get("o35_brier") is not None else None),
            "ece": round(ece, 4),
            # step-1.5 distillation record: which teacher, how many duplicates
            "synth_labels": synth_a or {"enabled": False},
        }

        # alpha: log-loss-optimal blend of the deployed model vs the CLOSING
        # consensus market (plan 1.4 — the source that won every ablation).
        # Only rows that actually HAVE odds count: for the rest the market side
        # is uniform and would push alpha toward 1 (ignore a real market edge).
        #
        # The fitted weight is then DAMPED toward the prior that won the
        # rolling weekly validation (fixed alpha 0.35 + closing market,
        # ll 0.99961 vs 1.00033 for an alpha re-refit every week): one 180-day
        # window's optimum is noisy, so the fit gets weight n/(n+K) and the
        # measured prior keeps the rest.  ~1,280 valid rows -> fit 56% / prior
        # 44% with K=1000.  ALPHA_PRIOR=1.0 disables the damping entirely.
        ALPHA_PRIOR = float(os.environ.get("ALPHA_PRIOR", "0.35") or 0.35)
        ALPHA_PRIOR_K = float(os.environ.get("ALPHA_PRIOR_K", "1000") or 1000)

        def _best_alpha(mask):
            m = mask & valid_c
            if int(m.sum()) < 50:
                return None
            best_a, best_ll = None, float("inf")
            for a in np.arange(0.0, 1.001, 0.05):
                ll = _ll(a * p_ev[m] + (1.0 - a) * mkt_c[m], y_ev[m])
                if ll < best_ll:
                    best_ll, best_a = ll, float(a)
            return float(best_a)

        alphas: dict = {}
        n_a = int(valid_c.sum())
        damp_w = (n_a / (n_a + ALPHA_PRIOR_K)) if n_a and ALPHA_PRIOR_K > 0 else 1.0
        ga_raw = _best_alpha(np.ones(len(y_ev), dtype=bool))
        if ga_raw is not None:
            alphas["_default"] = damp_alpha(ga_raw, n_a, ALPHA_PRIOR, ALPHA_PRIOR_K)
        n_league = 0
        # A league alpha learned on ~60-150 matches is noisy; shrink it toward
        # the (already damped) global alpha with weight n/(n+300) instead of
        # trusting it fully — measured a tie against the global alpha, so this
        # stays conservative rather than being removed.
        SHRINK_K = 300.0
        for slug in pd.unique(leagues_h[mid:]):
            mask = (leagues_h[mid:] == slug)
            n_l = int((mask & valid_c).sum())
            if n_l >= 60:
                a = _best_alpha(mask)
                if a is not None:
                    prior = alphas.get("_default", ALPHA_PRIOR)
                    wgt = n_l / (n_l + SHRINK_K)
                    alphas[str(slug)] = round(float(wgt * a + (1.0 - wgt) * prior), 4)
                    n_league += 1
        metrics["alpha_default"] = alphas.get("_default")
        metrics["alpha_fitted_raw"] = ga_raw
        metrics["alpha_prior"] = ALPHA_PRIOR
        metrics["alpha_damp_weight"] = round(float(damp_w), 4)
        metrics["alpha_leagues"] = n_league
        if int(valid_c.sum()) >= 50:
            m = valid_c
            a_dep = alphas.get("_default", ALPHA_PRIOR)
            metrics["blended_market_log_loss"] = round(
                _ll(a_dep * p_ev[m] + (1.0 - a_dep) * mkt_c[m], y_ev[m]), 5)
        logger.info("[OOT] " + json.dumps(metrics))

        # The attention candidate never reaches Stage B: its heads ARE the
        # student (trained on its own train-side split), and fit_stacks only
        # knows the LightGBM modes.  The holdout calibrator/alphas stay fit.
        if stage_b and str(mode) == "attn":
            logger.info("Stage B skipped for the attention candidate")
            stage_b = False
        if stage_b:
            logger.info(f"Stage B: refitting '{mode}' on FULL dataset "
                        "(keeping holdout calibrator + alphas)…")
            best.pop("pred", None)
            del pred_a
            gc.collect()
            t2 = time.time()
            try:
                w_full = _recency_weights(np.ones(len(d_all), dtype=bool))
                aux_full = {"y_btts": meta.get("y_btts"),
                            "y_o35": meta.get("y_o35")}
                X_all_s, y_all_s, g_all_s, w_full, _ = _distill_soft_labels(
                    X_all, y_all, g_all, w_full, meta.get("home"),
                    meta.get("away"), meta.get("leagues"), split_at, "stage_b",
                    aux=aux_full)
                predictor.fit_stacks(X_all_s, y_all_s, g_all_s, calibrate=False,
                                     mode=mode, sample_weight=w_full,
                                     y_btts=aux_full["y_btts"],
                                     y_o35=aux_full["y_o35"])
            except Exception as e:
                logger.error(f"Stage B training failed: {e}")
                return False
            logger.info(f"Stage B train done in {time.time() - t2:.1f}s")
            predictor.attach_calibration(calibs, alphas, metrics)
        else:
            logger.info(f"STAGE_B=0 — deploying the Stage A '{mode}' model as-is")
            predictor = pred_a
            predictor.attach_calibration(calibs, alphas, metrics)
        calib_info = metrics
    else:
        logger.info("[OOT] holdout too small — full training with prefit calibration")
        t1 = time.time()
        fallback_mode = os.environ.get("MODEL_MODE", "single").strip().lower()
        if fallback_mode in ("both", "auto"):
            fallback_mode = "single"
        try:
            aux_fb = {"y_btts": meta.get("y_btts"), "y_o35": meta.get("y_o35")}
            X_all_s, y_all_s, g_all_s, _, _ = _distill_soft_labels(
                X_all, y_all, g_all, None, meta.get("home"), meta.get("away"),
                meta.get("leagues"), split_at, "fallback", aux=aux_fb)
            predictor.fit_stacks(X_all_s, y_all_s, g_all_s, calibrate=True,
                                 mode=fallback_mode,
                                 y_btts=aux_fb["y_btts"], y_o35=aux_fb["y_o35"])
        except Exception as e:
            logger.error(f"Training failed: {e}")
            return False
        logger.info(f"Training done in {time.time() - t1:.1f}s")
        calib_info = {"mode": fallback_mode, "note": "holdout too small; prefit isotonic"}

    # ── Holdout validation against the deployed baseline (plan 1.3) ──
    # Never replace a working model with one that scores worse on the SAME
    # out-of-time holdout.  Comparability: identical HOLDOUT_DAYS and a split
    # date that has drifted no further than SPLIT_AT_TOLERANCE_DAYS (the split
    # slides as new rows land), otherwise the numbers are not close enough to
    # apples-to-apples and the gate steps aside — loudly, never silently.
    prev_info: dict = {}
    try:
        with open(INFO_PATH) as f:
            prev_info = json.load(f)
    except Exception:
        prev_info = {}
    prev_cal = prev_info.get("calibration") or {}
    tol = float(os.environ.get("MAX_LL_REGRESSION", "0.0005") or 0.0005)
    force_replace = os.environ.get("FORCE_REPLACE", "").strip() in ("1", "true", "yes")

    gate: dict = {
        "baseline_version": prev_info.get("version"),
        "baseline_eval_log_loss": prev_cal.get("eval_log_loss"),
        "new_eval_log_loss": calib_info.get("eval_log_loss"),
        "tolerance": tol,
        "forced": force_replace,
    }

    # split_at = max(date) - HOLDOUT_DAYS, and fetch-football-data appends rows
    # every 3 hours, so an identical HOLDOUT_DAYS still slides the split a day
    # or two between runs.  Requiring byte-identical splits would make the gate
    # skip forever; allow a small drift (default 7d, ~4% of the 180d window)
    # and record how far it moved so the comparison stays inspectable.
    max_shift = int(os.environ.get("SPLIT_AT_TOLERANCE_DAYS", "7") or 7)
    split_shift = None
    try:
        prev_split = pd.to_datetime(prev_cal.get("split_at"), errors="coerce")
        new_split = pd.to_datetime(calib_info.get("split_at"), errors="coerce")
        if pd.notna(prev_split) and pd.notna(new_split):
            split_shift = int(abs((new_split - prev_split).days))
    except Exception:
        split_shift = None
    gate["baseline_split_at"] = prev_cal.get("split_at")
    gate["split_at_shift_days"] = split_shift
    gate["split_tolerance_days"] = max_shift

    comparable = (
        isinstance(prev_cal.get("eval_log_loss"), (int, float))
        and isinstance(calib_info.get("eval_log_loss"), (int, float))
        and prev_cal.get("holdout_days") == calib_info.get("holdout_days")
        and split_shift is not None
        and split_shift <= max_shift
    )
    gate["comparable"] = bool(comparable)

    if comparable:
        d_ll = float(calib_info["eval_log_loss"]) - float(prev_cal["eval_log_loss"])
        d_acc = (float(calib_info.get("accuracy") or 0.0)
                 - float(prev_cal.get("accuracy") or 0.0))
        gate["delta_log_loss"] = round(d_ll, 5)
        gate["delta_accuracy"] = round(d_acc, 4)
        if d_ll > tol and not force_replace:
            gate["decision"] = "rejected"
            logger.error(
                f"[GATE] new model is WORSE on the identical holdout: "
                f"log loss {prev_cal['eval_log_loss']} -> {calib_info['eval_log_loss']} "
                f"(+{d_ll:.5f} > tolerance {tol}), accuracy {d_acc:+.4f}. "
                f"Keeping the deployed model. Set FORCE_REPLACE=1 to override.")
            try:
                with open(INFO_PATH, "w") as f:
                    json.dump({**prev_info, "last_rejected": {**gate,
                                                              "rejected_at": datetime.datetime.utcnow().isoformat() + "Z"}},
                              f, indent=2)
            except Exception:
                pass
            return False
        gate["decision"] = "accepted" if d_ll <= 0 else "accepted_with_regression"
        logger.info(f"[GATE] {gate['decision']}: log loss {d_ll:+.5f}, accuracy {d_acc:+.4f} "
                    f"vs baseline (tolerance {tol}, split_at shift {split_shift}d)")
    else:
        gate["decision"] = "skipped_not_comparable"
        why = []
        if prev_cal.get("holdout_days") != calib_info.get("holdout_days"):
            why.append(f"holdout_days {prev_cal.get('holdout_days')} -> "
                       f"{calib_info.get('holdout_days')}")
        if split_shift is None:
            why.append("baseline has no split_at")
        elif split_shift > max_shift:
            why.append(f"split_at moved {split_shift}d "
                       f"({prev_cal.get('split_at')} -> {calib_info.get('split_at')}, "
                       f"tolerance {max_shift}d)")
        if not why:
            why.append("baseline metrics missing")
        logger.warning("[GATE] skipped — not apples-to-apples: " + "; ".join(why) +
                       ". The new model is deployed unverified; the NEXT run "
                       "will compare against it on an identical window.")
        gate["skip_reason"] = "; ".join(why)

    # Per-league sample sizes ride along inside the pickle so predictor.py can
    # flag fixtures the model has little league-specific history for (the Europa
    # and Conference League have no football-data.co.uk season CSV of their own).
    try:
        predictor.record_league_support(df)
        logger.info(f"League support recorded for {len(predictor.league_counts)} league(s)")
    except Exception as e:
        logger.debug(f"Could not record league support: {e}")

    # Recent results per team ride along too, so the analysis panel has real
    # form/head-to-head at inference time instead of always rendering blanks.
    try:
        predictor.record_recent_history(df)
        logger.info(f"Recent history snapshot recorded: "
                    f"{len(predictor.recent_history) if predictor.recent_history is not None else 0:,} rows")
    except Exception as e:
        logger.debug(f"Could not record recent history: {e}")

    # ── Save atomically ────────────────────────────────────────────
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".pkl", dir=os.path.dirname(MODEL_PATH))
    os.close(tmp_fd)
    try:
        predictor.save(tmp_path)
        shutil.move(tmp_path, MODEL_PATH)
    except Exception as e:
        logger.error(f"Save failed: {e}")
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        return False

    # ── Write model metadata (versioned — plan 1.3) ─────────────────
    version = int(prev_info.get("version") or 0) + 1
    entry = {
        "version": version,
        "trained_at": datetime.datetime.utcnow().isoformat() + "Z",
        "mode": calib_info.get("mode"),
        "eval_log_loss": calib_info.get("eval_log_loss"),
        "accuracy": calib_info.get("accuracy"),
        "holdout_days": calib_info.get("holdout_days"),
        "split_at": calib_info.get("split_at"),
        "git_sha": _git_sha(),
    }
    history = list(prev_info.get("history") or [])
    history.append(entry)
    info = {
        "version": version,
        "trained_at":       entry["trained_at"],
        "git_sha":          entry["git_sha"],
        "total_matches":    int(len(df)),
        "duration_seconds": round(time.time() - t0, 1),
        "previous":         {"version": prev_info.get("version"),
                             "trained_at": prev_info.get("trained_at"),
                             "git_sha": prev_info.get("git_sha")},
        "baseline_comparison": gate,
        "calibration":      calib_info,
        "history":          history[-25:],   # rolling window of deployed versions
    }
    try:
        with open(INFO_PATH, "w") as f:
            json.dump(info, f, indent=2)
    except Exception:
        pass

    logger.info(f"Model saved to {MODEL_PATH} (version {version})")
    logger.info(f"Total retrain time: {time.time() - t0:.1f}s")
    return True


def _git_sha() -> str | None:
    """Best-effort commit the model was trained from (None outside a checkout)."""
    sha = os.environ.get("GITHUB_SHA")
    if sha:
        return sha[:12]
    try:
        import subprocess
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=10,
                             cwd=os.path.dirname(os.path.abspath(__file__)))
        return out.stdout.strip() or None
    except Exception:
        return None


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--force", action="store_true")
    args = p.parse_args()
    success = run(force=args.force)
    sys.exit(0 if success else 1)
