"""NPV and IRR on monthly cash flows.

Month 1 is time zero: its cash flow is not discounted. Month t is discounted
by (t - 1) / 12 years at the annual rate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np


def npv(cash_flow, annual_rate: float) -> float:
    cf = np.asarray(cash_flow, dtype=float)
    t = np.arange(len(cf)) / 12.0
    return float(np.sum(cf / (1.0 + annual_rate) ** t))


@dataclass
class IRR:
    """Result of an IRR search.

    status is "ok" (the IRR is in `annual`), "none" (no rate makes NPV zero)
    or "multiple" (several do and none can be singled out; they are listed in
    `candidates` and no figure is reported). `candidates` always lists every
    rate found, so a second, discarded rate stays visible.
    """
    status: str
    annual: Optional[float] = None
    candidates: list[float] = field(default_factory=list)


_LO, _HI = -0.9999, 1.0e6   # annual rate search range


def _f(cf: np.ndarray, t: np.ndarray, log1p_rate: float) -> float:
    return float(np.sum(cf * np.exp(-log1p_rate * t)))


def _bisect(cf, t, a, b, fa, fb) -> float:
    for _ in range(200):
        m = 0.5 * (a + b)
        fm = _f(cf, t, m)
        if fm == 0 or (b - a) < 1e-14:
            return m
        if (fm > 0) == (fa > 0):
            a, fa = m, fm
        else:
            b, fb = m, fm
    return 0.5 * (a + b)


def irr(cash_flow) -> IRR:
    cf = np.asarray(cash_flow, dtype=float)
    nz = cf[np.abs(cf) > 0]
    if len(nz) == 0 or np.all(nz > 0) or np.all(nz < 0):
        return IRR("none")
    t = np.arange(len(cf)) / 12.0
    scale = np.max(np.abs(cf))
    cfs = cf / scale
    # Search in x = ln(1 + annual rate), where the function is smooth.
    lo, hi = np.log1p(_LO), np.log1p(_HI)
    signs = np.sign(nz)
    if np.count_nonzero(signs[1:] != signs[:-1]) == 1:
        # One change of sign: at most one IRR exists (Descartes' rule).
        fa, fb = _f(cfs, t, lo), _f(cfs, t, hi)
        if fa == 0 or fb == 0 or (fa > 0) == (fb > 0):
            return IRR("none")
        r = float(np.expm1(_bisect(cfs, t, lo, hi, fa, fb)))
        return IRR("ok", annual=r, candidates=[r])
    grid = np.linspace(lo, hi, 4001)
    vals = np.exp(-np.outer(grid, t)) @ cfs
    roots = []
    for i in range(len(grid) - 1):
        a, b, fa, fb = grid[i], grid[i + 1], vals[i], vals[i + 1]
        if fa == 0:
            roots.append(a)
        elif (fa > 0) != (fb > 0) and fb != 0:
            roots.append(_bisect(cfs, t, a, b, fa, fb))
    if vals[-1] == 0:
        roots.append(grid[-1])
    rates = [float(np.expm1(x)) for x in roots]
    if not rates:
        return IRR("none")
    if len(rates) == 1:
        return IRR("ok", annual=rates[0], candidates=rates)
    # Several rates make NPV zero. One of them is still a sound IRR when it is
    # the only one on the side that matches the project's result: the only
    # rate of zero or more for a project that makes money, or the only
    # negative rate for one that loses it. For a profitable project this means
    # NPV is positive at every discount rate from zero up to that rate and
    # negative above it, which is exactly how an IRR is used.
    total = float(cf.sum())
    side = [r for r in rates if r >= 0] if total > 0 else [r for r in rates if r < 0] if total < 0 else []
    if len(side) == 1:
        return IRR("ok", annual=side[0], candidates=rates)
    return IRR("multiple", candidates=rates)
