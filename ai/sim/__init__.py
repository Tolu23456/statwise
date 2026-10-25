"""Monte Carlo Match Lab — the "fool's plan" simulator.

Recipe, not the rows: this package fits a Dixon-Coles goal process, samples a
posterior spread of team strengths with the "cap down" forward/backward noisy
sampler, breeds the sampled worlds, and scores every number it produces on
REAL held-out outcomes.  Simulated scorelines are folded into small mergeable
counters and discarded; the worlds JSON (a few MB) is the whole recipe.

Note the repo's `ai/` directory is not an installed package — tests and CI
insert `ai/` on sys.path (see ai/experimental/test_sim.py and
.github/workflows/simulate.yml), so imports inside this package are relative
and the runner ai/sim/run.py bootstraps sys.path itself.
"""
