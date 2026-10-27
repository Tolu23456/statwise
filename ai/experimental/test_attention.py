"""Attention-student regression tests — tiny, synthetic, seconds, no CI cost.

Guards the pieces the training workflow (train-attention-student.yml) and
the production NumPy serving path depend on:

  * the torch module and its NumPy mirror agree (parity < 1e-4) — serving
    is pure NumPy, so a drift here would silently change production odds;
  * `fast_posterior` (the O(W*F*11) teacher-feature path) reproduces
    `sim.simulate.posterior_markets` (O(W*F*121) reference) to 1e-9 on the
    REAL committed recipe;
  * unknown teams get the neutral (all-zero) teacher row, known teams don't;
  * the recipe guard rejects a teacher fit after our holdout split;
  * a student bundle round-trips through joblib into AttnHead pipes with
    the right output shapes;
  * train_model runs a couple of epochs on synthetic data, early-stops on
    the train-side inner tail and returns a finite best LL.

Data: synthetic arrays + the committed recipe only (skipped cleanly when
absent).  Nothing loads the 377k-row clean set, nothing trains LightGBM —
heavy work belongs in GitHub Actions.

Run:  .venv/bin/python ai/experimental/test_attention.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))          # .../statwise
sys.path.insert(0, os.path.join(ROOT, "ai"))

from attn.config import AttnConfig                              # noqa: E402
from attn.model import HAS_TORCH, NumpyTabTransformer           # noqa: E402
from attn.teacher import N_TEACHER, fast_posterior              # noqa: E402
from sim.distill import load_teacher                            # noqa: E402
from sim.simulate import posterior_markets                      # noqa: E402

RECIPE = os.path.join(ROOT, "ai", "data", "sim", "worlds.json")


def test_forward_shapes():
    if not HAS_TORCH:
        print("  [skip] torch not installed")
        return
    import torch
    from attn.model import TabTransformer
    torch.manual_seed(0)
    net = TabTransformer(n_tokens=37, d_model=32, n_heads=4, n_layers=2)
    x = torch.randn(8, 37)
    out = net(x)
    assert out["logits_1x2"].shape == (8, 3), out["logits_1x2"].shape
    for k in ("logit_o25", "logit_btts", "logit_o35"):
        # torch keeps (B, 1); the numpy mirror slices to (B,) — both are
        # squeezed before any loss/parity arithmetic (see train_model)
        assert out[k].shape == (8, 1), (k, out[k].shape)
    assert out["lam"].shape == (8, 2)
    assert (out["lam"] > 0).all(), "softplus expected-goals must be positive"
    print("  forward shapes ok")


def test_torch_numpy_parity():
    if not HAS_TORCH:
        print("  [skip] torch not installed")
        return
    import torch
    from attn.model import TabTransformer
    torch.manual_seed(1)
    np.random.seed(1)
    net = TabTransformer(n_tokens=37, d_model=32, n_heads=4, n_layers=2,
                         dropout=0.3)
    net.eval()                                   # dropout off = numpy mirror
    z = np.random.randn(64, 37).astype(np.float32)
    with torch.no_grad():
        ref = net(torch.from_numpy(z))
    mir = NumpyTabTransformer(net.export_numpy().p, n_heads=4).forward(z)
    worst = max(float(np.max(np.abs(ref[k].numpy().ravel()
                                     - np.asarray(mir[k]).ravel())))
                for k in ref)
    assert worst < 1e-4, f"torch<->numpy parity gap {worst:.2e}"
    # and probabilities from predict() are a proper distribution
    p = NumpyTabTransformer(net.export_numpy().p, n_heads=4).predict(z)["p_1x2"]
    assert p.shape == (64, 3) and np.all(p > 0)
    assert np.allclose(p.sum(1), 1.0, atol=1e-9)
    print(f"  torch<->numpy parity {worst:.2e} ok")


def _load_real_worlds():
    if not os.path.exists(RECIPE):
        return None
    worlds, meta = load_teacher(RECIPE)
    return worlds


def test_fast_posterior_parity():
    worlds = _load_real_worlds()
    if not worlds:
        print("  [skip] no committed recipe")
        return
    rng = np.random.default_rng(7)
    nt = len(worlds[0].t2i)
    nl = max(len(w.league_offset) for w in worlds)
    h = rng.integers(0, nt, 300)
    a = rng.integers(0, nt, 300)
    lg = rng.integers(0, nl, 300)
    ref = posterior_markets(worlds, h, a, lg)
    fast = fast_posterior(worlds, h, a, lg)
    worst_k, worst = None, 0.0
    for k in ref:
        diff = float(np.max(np.abs(ref[k] - fast[k])))
        if diff > worst:
            worst, worst_k = diff, k
    assert worst < 1e-9, f"fast_posterior diverges on {worst_k}: {worst:.2e}"
    print(f"  fast_posterior == posterior_markets to {worst:.2e} ok")


def test_teacher_unknown_teams_neutral():
    worlds = _load_real_worlds()
    if not worlds:
        print("  [skip] no committed recipe")
        return
    from attn.teacher import teacher_matrix
    known = list(worlds[0].t2i.keys())[:2]
    home = np.array([known[0], "FC Nowhere United", known[1]])
    away = np.array([known[1], known[0], "FC Nowhere United"])
    leagues = np.array(["all", "all", "all"])
    T = teacher_matrix(worlds, home, away, leagues)
    assert T.shape == (3, N_TEACHER)
    assert np.abs(T[0]).sum() > 0, "known pair must carry signal"
    assert np.abs(T[1]).sum() == 0 and np.abs(T[2]).sum() == 0, \
        "unknown team must map to the neutral (zero) row"
    print("  teacher unknown-team fallback ok")


def test_recipe_guard():
    worlds = _load_real_worlds()
    if not worlds:
        print("  [skip] no committed recipe")
        return
    from attn.teacher import load_recipe
    ok_w, _ = load_recipe(RECIPE, "2027-01-01")     # split after recipe fit
    assert ok_w, "guard must accept a recipe fit before our split"
    bad_w, _ = load_recipe(RECIPE, "2000-01-01")     # split before recipe fit
    assert not bad_w, "guard must reject a teacher that saw holdout outcomes"
    print("  recipe guard ok")


def test_student_roundtrip():
    if not HAS_TORCH:
        print("  [skip] torch not installed")
        return
    import joblib
    import torch
    from attn.model import TabTransformer
    from attn.serve import AttnHead, load_student

    torch.manual_seed(3)
    np.random.seed(3)
    n_feat = 12
    net = TabTransformer(n_tokens=n_feat + N_TEACHER, d_model=16,
                         n_heads=4, n_layers=1)
    net.eval()
    payload = {
        "cfg": {"n_features": n_feat},
        "params": net.export_numpy().p,
        "n_heads": 4,
        "x_mean": np.zeros(n_feat), "x_std": np.ones(n_feat),
        "t_mean": np.zeros(N_TEACHER), "t_std": np.ones(N_TEACHER),
        "recipe": None, "recipe_meta": {},          # neutral teacher path
    }
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "student.pkl")
        joblib.dump(payload, path)
        student = load_student(path)
        assert student is not None, "bundle must load"
        X = np.random.randn(5, n_feat)
        Z = student.extend(X)                       # no home/away -> neutral
        assert Z.shape == (5, n_feat + N_TEACHER)
        head = AttnHead(student, "outcome")
        p = head.predict_proba(Z)                  # extended input (contract)
        assert p.shape == (5, 3) and np.allclose(p.sum(1), 1.0, atol=1e-9)
        for task in ("goals", "btts", "o35"):
            pb = AttnHead(student, task).predict_proba(Z)
            assert pb.shape == (5, 2) and np.allclose(pb.sum(1), 1.0, atol=1e-9)
        try:
            head.predict_proba(X)                  # raw width must be refused
            raise AssertionError("raw input should be rejected")
        except ValueError:
            pass
        # extend() output must equal the raw scaled input when teacher is off
        assert np.allclose(Z[:, :n_feat], X, atol=1e-9)
    print("  student bundle round-trip + head shapes ok")


def test_tiny_training():
    if not HAS_TORCH:
        print("  [skip] torch not installed")
        return
    from attn.train import train_model
    rng = np.random.default_rng(4)
    n, nf = 2048, 24
    Z = rng.standard_normal((n, nf + N_TEACHER)).astype(np.float32)
    y = rng.integers(0, 3, n)
    tgt = np.eye(3, dtype=np.float32)[y]
    tgt = 0.98 * tgt + 0.02 / 3.0
    w = rng.uniform(0.05, 1.0, n).astype(np.float32)
    aux = {"g": (rng.random(n) > 0.5).astype(np.float32),
           "btts": (rng.random(n) > 0.5).astype(np.float32),
           "o35": None}                             # absent head must be fine
    cfg = AttnConfig()
    cfg.epochs, cfg.batch_size, cfg.patience = 2, 512, 2
    cfg.d_model, cfg.n_layers = 16, 1
    aux_fit = {k: (v[:1600] if v is not None else None)
               for k, v in aux.items()}
    model, info = train_model(Z[:1600], tgt[:1600], w[:1600], aux_fit,
                              Z[1600:], y[1600:], cfg)
    assert 1 <= len(info["history"]) <= 2
    assert np.isfinite(info["best_inner_val_ll"])
    assert all("inner_val_ll" in h for h in info["history"])
    print(f"  tiny training ok (best inner-val LL "
          f"{info['best_inner_val_ll']:.4f})")


def main():
    tests = [test_forward_shapes, test_torch_numpy_parity,
             test_fast_posterior_parity, test_teacher_unknown_teams_neutral,
             test_recipe_guard, test_student_roundtrip, test_tiny_training]
    for t in tests:
        print(f"[{t.__name__}]")
        t()
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
