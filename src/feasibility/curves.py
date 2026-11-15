"""Spreading a quantity over months."""
from __future__ import annotations

import numpy as np

from .inputs import Curve


def weights(curve: Curve, n: int) -> np.ndarray:
    """n monthly weights that sum to exactly 1."""
    if n < 1:
        raise ValueError("a curve needs at least one month")
    if curve.kind == "even":
        w = np.ones(n)
    elif curve.kind == "front_loaded":
        w = np.arange(n, 0, -1, dtype=float)          # n, n-1, ..., 1
    elif curve.kind == "s_curve":
        x = np.linspace(0.0, 1.0, n + 1)
        w = np.diff(3 * x**2 - 2 * x**3)              # slow, fast, slow
    else:
        w = resample(np.asarray(curve.weights, dtype=float), n)
    return w / w.sum()


def resample(w: np.ndarray, n: int) -> np.ndarray:
    """Stretch or squeeze a weight profile to n months, keeping its shape."""
    if len(w) == n:
        return w.astype(float)
    cum = np.concatenate([[0.0], np.cumsum(w)])
    x_old = np.linspace(0.0, 1.0, len(w) + 1)
    x_new = np.linspace(0.0, 1.0, n + 1)
    return np.diff(np.interp(x_new, x_old, cum))
