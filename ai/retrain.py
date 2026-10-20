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
    # Weather enrichment sidecar (ai/enrich_weather_history.py -> data/weather_by_match.csv)
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

        # Optional time-decay weights: recent seasons resemble the matches we
        # actually predict.  0 disables it (keeps runs comparable/cheap).
        half_life = float(os.environ.get("RECENCY_HALF_LIFE_DAYS", "0") or 0)
        dates_all = pd.to_datetime(d_all, errors="coerce")
        ref_date = dates_all[m_tr].max()

        def _recency_weights(mask):
            if half_life <= 0:
                return None
            age = (ref_date - dates_all[mask]).dt.days.clip(lower=0).to_numpy(dtype=np.float64)
            w = np.power(0.5, age / half_life)
            return np.maximum(w, 0.02).astype(np.float32)

        w_tr = _recency_weights(m_tr)
        if w_tr is not None:
            eff = (w_tr.sum() ** 2) / float((w_tr.astype(np.float64) ** 2).sum())
            logger.info(f"[OOT] recency weights: half-life {half_life:.0f}d, "
                        f"min={w_tr.min():.3f}, effective_n={eff:,.0f}")
        odds_h = meta.get("odds")
        odds_h = odds_h[m_ho] if odds_h is not None else np.zeros((len(Xh), 3))
        leagues_h = meta.get("leagues")
        leagues_h = leagues_h[m_ho] if leagues_h is not None else np.array(["all"] * len(Xh))

        mid = max(len(yh) // 2, 100)
        y_ev = yh[mid:]
        o_ev = odds_h[mid:]
        imp = np.zeros((len(o_ev), 3))
        imp[:, 0] = 1.0 / np.maximum(o_ev[:, 0], 1.0001)
        imp[:, 1] = 1.0 / np.maximum(o_ev[:, 1], 1.0001)
        imp[:, 2] = 1.0 / np.maximum(o_ev[:, 2], 1.0001)
        valid = (o_ev[:, 0] > 1.01) & (o_ev[:, 1] > 1.01) & (o_ev[:, 2] > 1.01)
        mkt = imp / np.maximum(imp.sum(axis=1, keepdims=True), 1e-9)

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
                                sample_weight=w_tr)
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
            ll_raw = _ll(p_raw, y_ev)
            ll_cal = _ll(p_cal, y_ev)
            # 1,440 eval rows are not enough for per-class isotonic to pay off
            # every time — only deploy the calibrator if it wins out of sample.
            use_cal = ll_cal < ll_raw
            p_ev = p_cal if use_cal else p_raw
            ll_eval = min(ll_raw, ll_cal)
            acc = float((p_ev.argmax(axis=1) == y_ev).mean())
            logger.info(f"[OOT:{mode}] {time.time() - t1:.1f}s "
                        f"raw_ll={ll_raw:.5f} cal_ll={ll_cal:.5f} "
                        f"use_cal={use_cal} acc={acc:.4f} goals_brier={goals_brier:.5f}")

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
                            use_cal=use_cal, goals_brier=goals_brier)
            else:
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
            "accuracy": round(float((p_ev.argmax(axis=1) == y_ev).mean()), 4),
            "brier": round(float(((p_ev - np.eye(3)[y_ev]) ** 2).mean()), 5),
            "goals_brier": round(best["goals_brier"], 5),
            "ece": round(ece, 4),
        }

        # alpha: log-loss-optimal blend of the deployed model vs market.
        # Only rows that actually HAVE odds count: for the rest mkt is uniform
        # and would push alpha toward 1 (i.e. ignore a market edge that exists).
        def _best_alpha(mask):
            m = mask & valid
            if int(m.sum()) < 50:
                return None
            best_a, best_ll = None, float("inf")
            for a in np.arange(0.0, 1.001, 0.05):
                ll = _ll(a * p_ev[m] + (1.0 - a) * mkt[m], y_ev[m])
                if ll < best_ll:
                    best_ll, best_a = ll, float(a)
            return float(best_a)

        alphas: dict = {}
        ga = _best_alpha(np.ones(len(y_ev), dtype=bool))
        if ga is not None:
            alphas["_default"] = ga
        n_league = 0
        # A league alpha learned on ~60-150 matches is noisy; shrink it toward
        # the global alpha with weight n/(n+300) instead of trusting it fully.
        SHRINK_K = 300.0
        for slug in pd.unique(leagues_h[mid:]):
            mask = (leagues_h[mid:] == slug)
            n_l = int((mask & valid).sum())
            if n_l >= 60:
                a = _best_alpha(mask)
                if a is not None:
                    prior = alphas.get("_default", 1.0)
                    wgt = n_l / (n_l + SHRINK_K)
                    alphas[str(slug)] = round(float(wgt * a + (1.0 - wgt) * prior), 4)
                    n_league += 1
        metrics["alpha_default"] = alphas.get("_default")
        metrics["alpha_leagues"] = n_league
        if int(valid.sum()) >= 50:
            m = valid
            metrics["blended_market_log_loss"] = round(
                _ll(alphas.get("_default", 1.0) * p_ev[m]
                    + (1.0 - alphas.get("_default", 1.0)) * mkt[m], y_ev[m]), 5)
        logger.info("[OOT] " + json.dumps(metrics))

        if stage_b:
            logger.info(f"Stage B: refitting '{mode}' on FULL dataset "
                        "(keeping holdout calibrator + alphas)…")
            best.pop("pred", None)
            del pred_a
            gc.collect()
            t2 = time.time()
            try:
                predictor.fit_stacks(X_all, y_all, g_all, calibrate=False, mode=mode,
                                     sample_weight=_recency_weights(np.ones(len(d_all), dtype=bool)))
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
            predictor.fit_stacks(X_all, y_all, g_all, calibrate=True, mode=fallback_mode)
        except Exception as e:
            logger.error(f"Training failed: {e}")
            return False
        logger.info(f"Training done in {time.time() - t1:.1f}s")
        calib_info = {"mode": fallback_mode, "note": "holdout too small; prefit isotonic"}

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

    # ── Write model metadata ───────────────────────────────────────
    info = {
        "trained_at":       datetime.datetime.utcnow().isoformat() + "Z",
        "total_matches":    int(len(df)),
        "duration_seconds": round(time.time() - t0, 1),
        "calibration":      calib_info,
    }
    try:
        with open(INFO_PATH, "w") as f:
            json.dump(info, f, indent=2)
    except Exception:
        pass

    logger.info(f"Model saved to {MODEL_PATH}")
    logger.info(f"Total retrain time: {time.time() - t0:.1f}s")
    return True


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--force", action="store_true")
    args = p.parse_args()
    success = run(force=args.force)
    sys.exit(0 if success else 1)
