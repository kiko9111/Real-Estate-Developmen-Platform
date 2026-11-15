from __future__ import annotations

import statistics
from typing import Iterable, Optional


def _q(values: list[float], q: float) -> float:
    if len(values) == 1:
        return values[0]
    s = sorted(values)
    pos = (len(s) - 1) * q
    lo, hi = int(pos), min(int(pos) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def _summary(values: list[float]) -> Optional[dict]:
    values = [v for v in values if v is not None]
    if not values:
        return None
    return {"n": len(values), "min": min(values), "p25": _q(values, 0.25), "median": statistics.median(values),
            "p75": _q(values, 0.75), "max": max(values)}


def market_stats(comps: Iterable, verified_only: bool = False) -> dict:
    """Price and payment-plan statistics per product type.

    Excluded rows never count. With verified_only, unverified rows are left out too.
    """
    rows = [c for c in comps if not c.excluded and (c.verified or not verified_only)]
    by_type: dict[str, list] = {}
    for c in rows:
        by_type.setdefault((c.product_type or "unspecified").lower(), []).append(c)
    out = {}
    for t, cs in sorted(by_type.items()):
        out[t] = {
            "count": len(cs),
            "price_per_m2": _summary([c.price_per_m2 for c in cs]),
            "down_payment_pct": _summary([c.down_payment_pct for c in cs]),
            "instalment_years": _summary([c.instalment_years for c in cs]),
        }
    return {"rows": len(rows), "by_type": out,
            "all_price_per_m2": _summary([c.price_per_m2 for c in rows])}


def median_price(comps: Iterable, product_type: Optional[str] = None) -> Optional[float]:
    vals = [c.price_per_m2 for c in comps if not c.excluded and c.price_per_m2
            and (product_type is None or (c.product_type or "").lower() == product_type.lower())]
    return statistics.median(vals) if vals else None
