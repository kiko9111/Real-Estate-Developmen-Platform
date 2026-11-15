"""Analyses built on full re-runs of the engine: sensitivities, residual land
value and risk ranges. Nothing here uses a shortcut formula."""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Optional

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from .engine import Result, run
from .inputs import FEDDAN_M2, Study

SHOCKS = ("price", "sales_pace", "cost", "cost_inflation", "delay", "cancellation_rate", "land_price")


def shocked(study: Study, shock: str, value: float) -> Study:
    """A copy of the study with one assumption changed.

    price, cost, land_price: multiply by `value` (1.10 = 10% higher).
    sales_pace: multiply the selling speed by `value` (0.8 = 20% slower).
    cost_inflation, cancellation_rate: add `value` (0.02 = two points more).
    delay: push construction and sales back by `value` months.
    """
    d = study.model_dump()
    _apply(d, shock, value)
    return Study.model_validate(d)


def _apply(d: dict, shock: str, value: float) -> None:
    if shock == "price":
        for p in d["products"]:
            if p["mode"] == "sell":
                p["launch_price_per_m2"] *= value
            else:
                p["lease"]["rent_per_m2_month"] *= value
    elif shock == "sales_pace":
        for p in d["products"]:
            if p["sales_months"] is not None:
                p["sales_months"] = max(1, int(round(p["sales_months"] / value)))
            if p["units_per_month"] is not None:
                p["units_per_month"] *= value
    elif shock == "cost":
        for p in d["products"]:
            p["hard_cost_per_m2"] *= value
        for s in d["costs"]["site_costs"]:
            s["rate_per_m2_land"] *= value
    elif shock == "cost_inflation":
        d["costs"]["cost_inflation_annual"] += value
    elif shock == "delay":
        m = int(round(value))
        for ph in d["phases"]:
            ph["start_month"] += m
        for s in d["costs"]["site_costs"]:
            s["start_month"] += m
        for p in d["products"]:
            if p["mode"] == "sell":
                p["launch_month"] += m
                for r in p["releases"]:
                    r["from_month"] += m
        if d["costs"]["overhead_end_month"] is not None:
            d["costs"]["overhead_end_month"] += m
        if d["conditions"]["build_deadline_months"] is not None:
            d["conditions"]["build_deadline_months"] += m   # the shock tests timing, not the deadline
    elif shock == "cancellation_rate":
        d["sales_risk"]["cancellation_rate"] = min(1.0, max(0.0, d["sales_risk"]["cancellation_rate"] + value))
    elif shock == "land_price":
        if d["land"]["cash"] is not None:
            d["land"]["cash"]["price_per_m2"] *= value
    else:
        raise ValueError(f"unknown shock '{shock}'")


def sensitivity(study: Study, shock: str, values: list[float]) -> list[dict]:
    """KPIs for each value of one shock. Every row is a full re-run."""
    rows = []
    for v in values:
        k = run(shocked(study, shock, v)).kpis
        rows.append({"shock": shock, "value": v, "npv_pre_tax": k["npv_pre_tax"],
                     "irr_pre_tax": k["irr_pre_tax"], "npv_after_tax": k["npv_after_tax"],
                     "irr_after_tax": k["irr_after_tax"], "profit_pre_tax": k["profit_pre_tax"],
                     "peak_funding_need": k["peak_funding_need"]})
    return rows


# ---------------------------------------------------------------- residual land value

@dataclass
class ResidualLand:
    """The highest cash land price at which NPV at the target rate is still zero or better."""
    feasible: bool                 # False: even free land misses the target
    price_per_m2: float
    price_per_feddan: float
    total: float
    target_rate: float
    after_tax: bool


def residual_land_value(study: Study, target_rate: Optional[float] = None,
                        after_tax: bool = False) -> ResidualLand:
    """Solve for the cash land price by re-running the engine.

    The rest of the deal (fees, shares, partnership, payment schedule) stays as
    entered, so each deal structure gets its own answer.
    """
    if study.land.cash is None:
        raise ValueError("residual land value needs a cash purchase in the land deal")
    rate = study.finance.discount_rate_annual if target_rate is None else target_rate
    base = study.model_dump()
    base["finance"]["discount_rate_annual"] = rate
    key = "npv_after_tax" if after_tax else "npv_pre_tax"
    area = study.land_area_m2 * study.land.cash.area_share

    def f(price: float) -> float:
        base["land"]["cash"]["price_per_m2"] = price
        return run(Study.model_validate(base)).kpis[key]

    if f(0.0) < 0:
        return ResidualLand(False, 0.0, 0.0, 0.0, rate, after_tax)
    hi = max(1.0, study.land.cash.price_per_m2)
    for _ in range(80):
        if f(hi) < 0:
            break
        hi *= 2
    else:
        raise RuntimeError("no land price makes NPV negative; check the inputs")
    lo = 0.0
    for _ in range(100):
        mid = 0.5 * (lo + hi)
        if f(mid) >= 0:
            lo = mid
        else:
            hi = mid
        if hi - lo <= 1e-9 * max(1.0, hi):
            break
    return ResidualLand(True, lo, lo * FEDDAN_M2, lo * area, rate, after_tax)


# ---------------------------------------------------------------- risk ranges

class Triangular(BaseModel):
    model_config = ConfigDict(extra="forbid")
    low: float
    mode: float
    high: float


class RiskSpec(BaseModel):
    """Ranges for the Monte Carlo run. Each draw varies all four together."""
    model_config = ConfigDict(extra="forbid")
    price: Triangular = Triangular(low=0.9, mode=1.0, high=1.1)               # multiplier
    sales_pace: Triangular = Triangular(low=0.7, mode=1.0, high=1.2)          # multiplier
    cost_inflation: Triangular = Triangular(low=-0.02, mode=0.0, high=0.05)   # points added
    delay_months: Triangular = Triangular(low=0, mode=0, high=12)
    runs: int = Field(default=1000, ge=1)
    seed: int = 1


def _draw(rng, t: Triangular, n: int) -> np.ndarray:
    if t.high == t.low:
        return np.full(n, t.mode)
    return rng.triangular(t.low, t.mode, t.high, n)


def risk_ranges(study: Study, spec: RiskSpec = RiskSpec(), after_tax: bool = False) -> dict:
    rng = np.random.default_rng(spec.seed)
    price = _draw(rng, spec.price, spec.runs)
    pace = _draw(rng, spec.sales_pace, spec.runs)
    infl = _draw(rng, spec.cost_inflation, spec.runs)
    delay = np.rint(_draw(rng, spec.delay_months, spec.runs))
    k_npv = "npv_after_tax" if after_tax else "npv_pre_tax"
    k_irr = "irr_after_tax" if after_tax else "irr_pre_tax"
    npvs, irrs = [], []
    base = study.model_dump()
    for i in range(spec.runs):
        d = copy.deepcopy(base)
        _apply(d, "price", float(price[i]))
        _apply(d, "sales_pace", float(pace[i]))
        _apply(d, "cost_inflation", float(infl[i]))
        _apply(d, "delay", float(delay[i]))
        k = run(Study.model_validate(d)).kpis
        npvs.append(k[k_npv])
        if k[k_irr] is not None:
            irrs.append(k[k_irr])
    npvs = np.array(npvs)
    pct = lambda a: {f"p{q}": float(np.percentile(a, q)) for q in (10, 50, 90)} if len(a) else None
    return {
        "runs": spec.runs,
        "target_rate": study.finance.discount_rate_annual,
        "npv": pct(npvs),
        "irr": pct(np.array(irrs)),
        "runs_with_irr": len(irrs),
        # NPV at the target rate is zero or better exactly when the target return is met.
        "probability_target_met": float(np.mean(npvs >= 0)),
    }
