"""StatWise model package.

``FootballPredictor`` and ``PredictionEngine`` pull in LightGBM, scikit-learn
and joblib just to be *imported*. Lightweight entry points (settlement, the
fixture fetchers, the odds sync) only need helpers from ``model.live_fetcher``,
so those heavy modules are exposed lazily via PEP 562 instead of at package
import time. Behaviour is unchanged for ``from model import FootballPredictor``.
"""

from __future__ import annotations

__all__ = ["FootballPredictor", "PredictionEngine"]

_LAZY = {
    "FootballPredictor": ".trainer",
    "PredictionEngine": ".predictor",
}


def __getattr__(name: str):
    if name in _LAZY:
        import importlib

        module = importlib.import_module(_LAZY[name], __name__)
        value = getattr(module, name)
        globals()[name] = value  # cache: only resolved once
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(set(globals()) | set(__all__))
