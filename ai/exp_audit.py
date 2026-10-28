#!/usr/bin/env python3
"""
Plan 5.16 experiment driver — paired arms on the 100k out-of-time audit split.

READ-ONLY by construction (same guarantee as ai/audit_100k.py): never saves a
model, never touches ai/models/** or model_info.json, never reads
FORCE_REPLACE.  Arms are trained in ONE process on the identical split and
scored on the SAME 49,956 eval rows, so every delta gets a paired test.

Pre-registration (primary metrics + decision rules) lives in ai/exp_plan.md
and must not be edited after a run has started.  Heavy training runs in
GitHub Actions via .github/workflows/exp-audit.yml.

Usage (Actions):  python -u ai/exp_audit.py --suite ensemble
Env:
    EXP_TEST_ROWS              holdout rows from the end (default 100000)
    EXP_OUT                    report JSON (default ai/data/exp/<suite>_report.json)
    EXP_ARMS                   comma list of arm names (override the suite)
    EXP_RECENCY_HALF_LIFE      baseline recency half-life days (default 1460)
    EXP_MAX_ROWS               smoke-test truncate: keep last N corpus rows (0 = all)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from exp_lib import (  # noqa: E402
    AUDIT_BASELINE_ACC, AUDIT_BASELINE_LL, PROD_HALF_LIFE,
    all_idx_for, apply_iso, best_alpha, blend_apply, build_features,
    build_estimator, ci95, damp_alpha, devig, fit_dirichlet, fit_iso3,
    fit_league_isotonic, fit_outcome_head, fit_platt3, fit_vector_scaling,
    league_alphas, load_frame, ll, metrics_block, paired_test, per_league,
    per_row_ll, recency_weights, split_masks, valid_odds,
)

TEST_ROWS = int(os.environ.get("EXP_TEST_ROWS", "100000"))
MAX_ROWS = int(os.environ.get("EXP_MAX_ROWS", "0") or 0)
HALF_LIFE = float(os.environ.get("EXP_RECENCY_HALF_LIFE", str(PROD_HALF_LIFE))
                  or PROD_HALF_LIFE)

# ── Arm registry ─────────────────────────────────────────────────────────────
# hp overrides on top of the production learner; hl overrides the recency
# half-life; x selects the feature matrix ("base" | "repair" | "repair+books").
FITS = {
    "baseline":     dict(seed=42,  hp={}, hl=None, x="base"),
    "ens_m2":       dict(seed=101, hp=dict(num_leaves=63, learning_rate=0.05),
                         hl=None, x="base"),
    "ens_m3":       dict(seed=202, hp=dict(num_leaves=155, learning_rate=0.02),
                         hl=None, x="base"),
    "ens_m4":       dict(seed=303, hp=dict(colsample_bytree=0.5),
                         hl=None, x="base"),
    "ens_m5":       dict(seed=404, hp=dict(num_leaves=127, learning_rate=0.03,
                                           colsample_bytree=0.85,
                                           min_child_samples=100),
                         hl=None, x="base"),
    "hl_365":       dict(seed=42, hp={}, hl=365.0, x="base"),
    "hl_730":       dict(seed=42, hp={}, hl=730.0, x="base"),
    "mktfix":       dict(seed=42, hp={}, hl=None, x="repair"),
    "mktfix_books": dict(seed=42, hp={}, hl=None, x="repair+books"),
}

SUITES = {
    "ensemble": {
        "fits": ["baseline", "ens_m2", "ens_m3", "ens_m4", "ens_m5"],
        "arms": {"baseline": ["baseline"],
                 "ens2": ["baseline", "ens_m2"],
                 "ens3": ["baseline", "ens_m2", "ens_m3"],
                 "ens5": ["baseline", "ens_m2", "ens_m3", "ens_m4", "ens_m5"]},
    },
    "recency_market": {
        "fits": ["baseline", "hl_365", "hl_730", "mktfix", "mktfix_books"],
        "arms": {"baseline": ["baseline"], "hl_365": ["hl_365"],
                 "hl_730": ["hl_730"], "mktfix": ["mktfix"],
                 "mktfix_books": ["mktfix_books"]},
    },
}


def _ece_block(p, y):
    from exp_lib import ece_of
    return round(ece_of(p, y), 4)


def calibration_suite(raw_cal, y_cal, raw_ev, y_ev, lg_cal, lg_ev) -> dict:
    """E5: every calibration variant fitted on the CALIBRATION window only."""
    out = {}
    cals = fit_iso3(raw_cal, y_cal)
    p_iso = apply_iso(cals, raw_ev)
    out["isotonic"] = {**metrics_block(p_iso, y_ev)}
    out["raw"] = {**metrics_block(raw_ev, y_ev)}
    try:
        out["platt"] = {**metrics_block(fit_platt3(raw_cal, y_cal, raw_ev), y_ev)}
    except Exception as e:  # noqa: BLE001
        out["platt"] = {"error": str(e)}
    try:
        p_vec, vinfo = fit_vector_scaling(raw_cal, y_cal, raw_ev)
        out["vector"] = {**metrics_block(p_vec, y_ev), "params": vinfo}
    except Exception as e:  # noqa: BLE001
        out["vector"] = {"error": str(e)}
    try:
        out["dirichlet"] = {**metrics_block(
            fit_dirichlet(raw_cal, y_cal, raw_ev), y_ev)}
    except Exception as e:  # noqa: BLE001
        out["dirichlet"] = {"error": str(e)}
    try:
        p_lg, info = fit_league_isotonic(raw_cal, y_cal, lg_cal, raw_ev, lg_ev)
        out["league_isotonic"] = {**metrics_block(p_lg, y_ev),
                                  "leagues": info["leagues"]}
    except Exception as e:  # noqa: BLE001
        out["league_isotonic"] = {"error": str(e)}
    return out, p_iso


def blend_suite(p_iso_cal, p_iso_ev, y_cal, y_ev, odds_cal, odds_ev,
                lg_cal, lg_ev, mkt_cal, mkt_ev, valid_cal, valid_ev):
    """Market blends: audit-protocol (alpha fit on eval) + honest (fit on cal)."""
    out = {}
    # 1) audit protocol — alpha grid on the EVAL window (comparability with
    #    the published blended 0.9952; kept as a secondary number only).
    #    The grid is evaluated on odds-valid rows only, exactly like
    #    ai/audit_100k.py (`mask & valid_c`).
    ga_ev = best_alpha(p_iso_ev, mkt_ev, y_ev, valid_ev, 0.05)
    n_ev = int(valid_ev.sum())
    a_ev = damp_alpha(ga_ev, n_ev, 0.35, 1000.0)
    b_ev = blend_apply(p_iso_ev, mkt_ev, None, lg_ev, a_ev)
    out["audit_protocol"] = {"alpha_fitted": ga_ev, "alpha_damped": a_ev,
                             **metrics_block(b_ev[valid_ev], y_ev[valid_ev])}
    # 2) honest global — alpha fitted on the CALIBRATION window only.
    ga_cal = best_alpha(p_iso_cal, mkt_cal, y_cal, valid_cal, 0.01)
    n_cal = int(valid_cal.sum())
    a_glob = damp_alpha(ga_cal, n_cal, 0.35, 1000.0)
    b_glob = blend_apply(p_iso_ev, mkt_ev, None, lg_ev, a_glob)
    out["honest_global"] = {"alpha_fitted": ga_cal, "alpha_damped": a_glob,
                            **metrics_block(b_glob[valid_ev], y_ev[valid_ev])}
    # 3) E2: per-league alpha, shrunk toward the honest global, CAL window only.
    la = league_alphas(p_iso_cal, mkt_cal, y_cal, lg_cal, valid_cal,
                       a_glob, shrink_k=300.0, min_n=60)
    b_lg = blend_apply(p_iso_ev, mkt_ev, la, lg_ev, a_glob)
    out["honest_league"] = {"alpha_leagues": len(la), "alpha_map": la,
                            **metrics_block(b_lg[valid_ev], y_ev[valid_ev])}
    blends = {"audit_protocol": b_ev, "honest_global": b_glob,
              "honest_league": b_lg}
    return out, blends


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--suite", default="ensemble")
    ap.add_argument("--arms", default="")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    suite = SUITES.get(args.suite)
    if suite is None:
        print(f"::error::unknown suite '{args.suite}' "
              f"(known: {', '.join(SUITES)})")
        return 2
    fit_names = list(suite["fits"])
    arm_map = dict(suite["arms"])
    if args.arms:
        want = [a.strip() for a in args.arms.split(",") if a.strip()]
        arm_map = {k: v for k, v in arm_map.items() if k in want}
        fit_names = sorted({f for v in arm_map.values() for f in v})
    out_path = args.out or os.path.join(BASE_DIR, "data", "exp",
                                        f"{args.suite}_report.json")

    t0 = time.time()
    df = load_frame(MAX_ROWS)
    print(f"[exp:{args.suite}] corpus {len(df):,} rows")

    print(f"[exp] building features (one pass)…")
    tf = time.time()
    X_all, y_all, g_all, d_all, meta, _ = build_features(df)
    idx = all_idx_for(len(X_all))
    print(f"[exp] features {X_all.shape} in {time.time() - tf:.1f}s")

    split_at, m_tr, m_ho = split_masks(df["date"].values, d_all, TEST_ROWS)
    print(f"[exp] train <= {split_at.date()} ({int(m_tr.sum()):,}) | "
          f"holdout {int(m_ho.sum()):,}")

    odds_all = np.asarray(meta["odds"])[idx]
    odds_close_all = np.asarray(meta["odds_closing"])[idx]
    leagues_all = np.asarray(meta["leagues"])[idx]

    # Feature-matrix variants (E4).  The repair is applied to the whole frame
    # BEFORE the split so train and holdout share one consistent definition.
    import exp_market
    X_variants = {"base": X_all}
    if any(FITS[f]["x"] != "base" for f in fit_names):
        X_variants["repair"] = exp_market.repair_matrix(X_all, odds_all)
        book_stats = {}
        if any(FITS[f]["x"] == "repair+books" for f in fit_names):
            extra, book_stats = exp_market.build_extra(df)
            extra = np.asarray(extra, dtype=np.float32)[idx]
            X_variants["repair+books"] = np.hstack(
                [X_variants["repair"], extra]).astype(np.float32)
        print(f"[exp] E4 matrices built (book join: {book_stats})")

    Xt = {k: v[m_tr] for k, v in X_variants.items()}
    Xh = {k: v[m_ho] for k, v in X_variants.items()}
    yt = y_all[m_tr]
    yh = y_all[m_ho]
    lg_ho = leagues_all[m_ho]
    odds_ho = odds_close_all[m_ho]
    d_ho = pd.to_datetime(d_all)[m_ho]

    mid = max(len(yh) // 2, 100)
    cal_sl = slice(0, mid)
    ev_sl = slice(mid, None)
    y_cal, y_ev = yh[:mid], yh[mid:]
    lg_cal, lg_ev = lg_ho[:mid], lg_ho[mid:]
    odds_cal, odds_ev = odds_ho[:mid], odds_ho[mid:]
    mkt_cal, mkt_ev = devig(odds_cal), devig(odds_ev)
    valid_cal, valid_ev = valid_odds(odds_cal), valid_odds(odds_ev)
    print(f"[exp] cal window {len(y_cal):,} | eval window {len(y_ev):,} "
          f"({int(valid_ev.sum()):,} eval rows with closing odds)")

    # ── Train every fit in this suite ────────────────────────────────────
    raw = {}
    fit_info = {}
    for name in fit_names:
        spec = FITS[name]
        hl = spec["hl"] if spec["hl"] is not None else HALF_LIFE
        w_full = recency_weights(d_all, m_tr, hl)
        w_tr = w_full[m_tr]          # slice to train rows
        X_train = Xt[spec["x"]]
        t1 = time.time()
        print(f"[exp] fit '{name}' on {X_train.shape[0]:,} rows "
              f"(x={spec['x']}, hl={hl:.0f}, seed={spec['seed']}, hp={spec['hp']})")
        est = fit_outcome_head(X_train, yt, sample_weight=w_tr,
                               seed=spec["seed"], **spec["hp"])
        raw[name] = est.predict_proba(Xh[spec["x"]])
        best_iter = getattr(est, "best_iteration_", None)
        fit_info[name] = {"x": spec["x"], "half_life_days": hl,
                          "seed": spec["seed"], "hp": spec["hp"],
                          "best_iteration": best_iter,
                          "train_seconds": round(time.time() - t1, 1)}
        print(f"[exp] fit '{name}' done in {fit_info[name]['train_seconds']}s "
              f"({best_iter or 'full'} trees)")

    # ── Derived arms (ensembles = mean of member raw probs) ──────────────
    report = {
        "experiment": f"plan5.16 {args.suite}",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "git_sha": os.environ.get("GITHUB_SHA", "")[:12] or None,
        "split": {
            "corpus_rows": int(len(df)),
            "train_rows": int(m_tr.sum()),
            "holdout_rows": int(m_ho.sum()),
            "cal_rows": int(len(y_cal)),
            "eval_rows": int(len(y_ev)),
            "eval_rows_with_odds": int(valid_ev.sum()),
            "split_at": str(split_at.date()),
            "recency_half_life_days": HALF_LIFE,
            "smoke_truncate_rows": MAX_ROWS or None,
        },
        "fits": fit_info,
        "arms": {},
        "paired_vs_baseline_fit": {},
        "arm_blends_vs_baseline": {},
        "e5_vector_vs_isotonic": {},
    }

    base_arm_probs = None       # isotonic probs of the baseline arm
    base_blends = None
    baseline_ll = None
    # baseline ALWAYS first: every other arm is paired against it.
    ordered_arms = ((["baseline"] if "baseline" in arm_map else [])
                    + [a for a in arm_map if a != "baseline"])
    for arm_name in ordered_arms:
        members = arm_map[arm_name]
        p_raw_h = np.mean([raw[m] for m in members], axis=0)
        p_raw_cal, p_raw_ev = p_raw_h[:mid], p_raw_h[mid:]
        cals = fit_iso3(p_raw_cal, y_cal)
        p_iso_ev = apply_iso(cals, p_raw_ev)
        block = {
            "members": members,
            "raw": metrics_block(p_raw_ev, y_ev),
            "isotonic": metrics_block(p_iso_ev, y_ev),
        }
        cal_suite, p_iso_used = calibration_suite(p_raw_cal, y_cal, p_raw_ev,
                                                  y_ev, lg_cal, lg_ev)
        block["calibration"] = cal_suite
        # E5 primary: vector scaling vs isotonic, per arm (baseline = the
        # pre-registered primary; others = consistency checks).
        try:
            p_vec, _ = fit_vector_scaling(p_raw_cal, y_cal, p_raw_ev)
            block["e5_vector_vs_isotonic"] = paired_test(p_vec, p_iso_used, y_ev)
        except Exception as e:  # noqa: BLE001
            block["e5_vector_vs_isotonic"] = {"error": str(e)}

        blends, blend_probs = blend_suite(
            apply_iso(cals, p_raw_cal), p_iso_ev, y_cal, y_ev,
            odds_cal, odds_ev, lg_cal, lg_ev, mkt_cal, mkt_ev,
            valid_cal, valid_ev)
        block["blend"] = blends

        pl = per_league(p_iso_ev, y_ev, lg_ev)
        if pl:
            block["per_league"] = pl

        if arm_name == "baseline":
            base_arm_probs = p_iso_ev
            base_blends = blend_probs
            baseline_ll = block["isotonic"]["eval_log_loss"]
            block["audit_guard"] = {
                "audit_baseline_ll": AUDIT_BASELINE_LL,
                "audit_baseline_acc": AUDIT_BASELINE_ACC,
                "this_run_ll": baseline_ll,
                "ll_match_within_0.002": bool(
                    abs(baseline_ll - AUDIT_BASELINE_LL) <= 0.002),
            }
        else:
            report["paired_vs_baseline_fit"][arm_name] = paired_test(
                p_iso_ev, base_arm_probs, y_ev)
            # blended paired comparisons (same protocol, same rows)
            bpair = {}
            for proto in ("audit_protocol", "honest_global", "honest_league"):
                m = valid_ev
                bpair[proto] = paired_test(
                    blend_probs[proto][m], base_blends[proto][m], y_ev[m])
            report["arm_blends_vs_baseline"][arm_name] = bpair
        report["arms"][arm_name] = block
        print(f"[exp] arm '{arm_name}': ll={block['isotonic']['eval_log_loss']:.5f} "
              f"acc={block['isotonic']['accuracy']} "
              f"ece={block['isotonic']['ece']} | blended(honest_league)="
              f"{blends['honest_league']['eval_log_loss']}")

    # E2 headline (per-arm, honest_league vs honest_global) for the summary.
    report["e2_per_league_alpha"] = {
        arm: {"dLL": report["arms"][arm]["blend"]["honest_league"]["eval_log_loss"]
                    - report["arms"][arm]["blend"]["honest_global"]["eval_log_loss"],
              "global": report["arms"][arm]["blend"]["honest_global"]["eval_log_loss"],
              "league": report["arms"][arm]["blend"]["honest_league"]["eval_log_loss"],
              "n_league_alphas": report["arms"][arm]["blend"]["honest_league"]["alpha_leagues"]}
        for arm in arm_map
    }

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2)

    # ── Console / job summary ────────────────────────────────────────────
    lines = ["", "=" * 68,
             f" plan 5.16 experiment — suite '{args.suite}' "
             f"({report['split']['eval_rows']:,} eval rows)",
             "=" * 68,
             f" split_at {report['split']['split_at']} | train "
             f"{report['split']['train_rows']:,} | eval "
             f"{report['split']['eval_rows']:,} "
             f"(odds on {report['split']['eval_rows_with_odds']:,})"]
    for arm in arm_map:
        b = report["arms"][arm]
        lines.append(f" {arm:<14} ll {b['isotonic']['eval_log_loss']:.5f} "
                     f"acc {b['isotonic']['accuracy']:.4f} ece "
                     f"{b['isotonic']['ece']:.4f} | raw ll "
                     f"{b['raw']['eval_log_loss']:.5f} | blend "
                     f"audit {b['blend']['audit_protocol']['eval_log_loss']} "
                     f"honest {b['blend']['honest_global']['eval_log_loss']} "
                     f"league {b['blend']['honest_league']['eval_log_loss']}")
        if arm != "baseline" and arm in report["paired_vs_baseline_fit"]:
            p = report["paired_vs_baseline_fit"][arm]
            lines.append(f"   vs baseline: dLL {p['ll_delta']:+.5f} "
                         f"CI {p['ll_delta_ci95']} p={p['ll_delta_p']} | "
                         f"dAcc {p['accuracy_delta']:+.4f} "
                         f"McNemar p={p['mcnemar_p']} | dECE {p['ece_delta']:+.4f}")
    if "baseline" in report["arms"]:
        g = report["arms"]["baseline"].get("audit_guard", {})
        lines.append(f" baseline guard: run ll {g.get('this_run_ll')} vs audit "
                     f"{g.get('audit_baseline_ll')} match={g.get('ll_match_within_0.002')}")
    lines += [f" report -> {out_path}", "=" * 68]
    summary = "\n".join(lines)
    print(summary)

    sm = os.environ.get("GITHUB_STEP_SUMMARY")
    if sm:
        try:
            with open(sm, "a") as fh:
                fh.write(f"\n## Plan 5.16 — suite `{args.suite}`\n\n```\n"
                         + summary + "\n```\n")
        except Exception:  # noqa: BLE001
            pass

    print(f"[exp] read-only: no model saved, model_info.json untouched "
          f"({time.time() - t0:.0f}s total)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
