# Production comparison — LightGBM vs lab vs market

> generated 2026-10-28T06:18:11.858710+00:00 · schema statwise.sim.compare.v1 · 2,250 eval rows (second half of the 180d holdout, split 2026-04-21) · runtime 16.4s

| system | 1X2 log loss (proper) | rows | notes |
|---|---|---|---|
| deployed LightGBM | **1.01129** | 2,250 | raw probs |
| lab ensemble | **1.07091** | 2,250 | 80 worlds, known-only 1.07161 (3.6% unknown-team rows) |
| uniform | 1.09861 | 2,250 | ln 3 |
| closing market | **0.99796** | 1,282 | de-vigged consensus |

## Blends

| blend | alpha | LL (scoring rows) | endpoints | protocol |
|---|---|---|---|---|
| lgbm_market | 0.350 | **0.99491** (1,282 rows) | 1.00467 / 0.99796 | production protocol: alpha grid + damp(0.35) on full eval (reproduces the stored 0.99491 when faithful) |
| lab_market | 0.210 | **1.00119** (616 rows) | 1.06171 / 0.99614 | honest: alpha fit on first eval half, scored on second |
| lgbm_lab | 1.000 | **1.00608** (1,125 rows) | 1.00608 / 1.06857 | honest: alpha fit on first eval half (prior 1.0 = lab earns its weight), scored on second |

## Paired differences (A − B, bootstrap CI90)

| difference | mean | CI90 | P(A better) |
|---|---|---|---|
| lab_minus_lgbm | +0.05962 | [+0.04820, +0.07151] | 0.0 |
| lgbm_lab_blend_minus_lgbm | +0.00000 | [+0.00000, +0.00000] | 0.0 |
| lgbm_minus_market | +0.00671 | [-0.00295, +0.01713] | 0.139 |
| lab_market_blend_minus_market | +0.00505 | [+0.00063, +0.00980] | 0.033 |

## Information overlap (H7)

- corr(home prob): 0.5823 · corr(draw): 0.3645 · corr(margin): 0.6242 · corr(per-row LL): 0.6383

## Reproduction vs the stored v7 gate

- eval LL: stored 1.01129 vs recomputed 1.01129 (delta -0.00000) — faithful
- market LL: stored 0.99796 vs recomputed 0.99796
- blend LL: stored 0.99491 vs recomputed 0.99491

## Verdicts

- **H1** (the DC ensemble is a weaker predictor than the deployed LightGBM on identical rows): **CONFIRMED** (gap +0.05962, CI90 [+0.04820, +0.07151])
- **H7** (the lab carries no signal beyond the production feature matrix (stacking cannot improve it)): **CONFIRMED** (stacking gap +0.00000, CI90 [+0.00000, +0.00000], lgbm weight alpha 1.00000)
- gaps are log-loss differences: positive = first system worse.  H1 'confirmed' = lab significantly weaker.  H7 'confirmed' = stacking gains nothing; 'refuted' would mean the lab deserves the effort.
