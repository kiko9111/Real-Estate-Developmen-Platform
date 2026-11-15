"""Turning layout options into feasibility studies and ranking them.

Each option becomes a full engine `Study`: the land deal, timing, payment
plans, sales risk, costs, finance and tax come from a base study the team
already trusts; the products and the built area come from the option. The
engine itself is unchanged.

Phasing: blocks are taken frontage first and cut into the base study's
construction phases by equal built area, so the first phase builds what lies
nearest the sea (or the frontage).

Ranking: options that pass every condition come first, then by NPV before
tax, then IRR, then margin on revenue.
"""
from __future__ import annotations

import copy
from typing import Optional

from feasibility import Study, run
from feasibility.engine import CheckFailed, ConditionError

from .brief import LayoutBrief


def option_study(option: dict, brief: LayoutBrief, base: dict, name: Optional[str] = None) -> dict:
    """An engine study (as JSON) for one layout option."""
    base = copy.deepcopy(base)
    phases = sorted(base["phases"], key=lambda p: (p["start_month"], p["name"]))
    plans = base.get("plans") or []
    if not plans:
        plans = [{"name": "cash", "down_payment": 1.0}]
    plan_names = {p["name"] for p in plans}
    base_sell = [p for p in base.get("products", []) if p.get("mode", "sell") == "sell"]

    # built area by (type, phase), frontage first
    rows = option["phasing"]
    total = sum(r["bua"] for r in rows)
    if total <= 0:
        raise ValueError("the option has no built area")
    by: dict[tuple[str, int], float] = {}
    acc = 0.0
    for r in rows:
        mid = acc + r["bua"] / 2
        ph = min(len(phases) - 1, int(mid / total * len(phases)))
        by[(r["key"], ph)] = by.get((r["key"], ph), 0.0) + r["bua"]
        acc += r["bua"]

    info = {p["key"]: p for p in option["products"]}
    prem = option.get("premiums", {})
    products = []
    for (key, ph), bua in sorted(by.items(), key=lambda kv: (kv[0][1], -kv[1])):
        t = brief.typology(key)
        phase = phases[ph]
        pname = t.name if len(phases) == 1 else f"{t.name} / {phase['name']}"
        p = {"name": pname, "category": t.category, "mode": t.mode, "bua_share": bua / total,
             "sellable_share": t.sellable_share, "unit_size_m2": info[key]["unit_size_m2"],
             "phase": phase["name"], "hard_cost_per_m2": t.hard_cost_per_m2}
        if t.mode == "lease":
            p["lease"] = t.lease.model_dump()
        else:
            ref = next((b for b in base_sell if b.get("category") == t.category), base_sell[0] if base_sell else {})
            esc = t.price_escalation_annual if t.price_escalation_annual is not None else ref.get("price_escalation_annual", 0.0)
            launch = max(1, phase["start_month"] - t.presale_months)
            mix = t.plan_mix if t.plan_mix and set(t.plan_mix) <= plan_names else \
                {k: v for k, v in ref.get("plan_mix", {}).items() if k in plan_names}
            if not mix or abs(sum(mix.values()) - 1) > 1e-6:
                mix = {plans[0]["name"]: 1.0}
            curve = (ref.get("sales_curve") or {}).get("kind", "even")
            p.update({
                "launch_price_per_m2": t.price_per_m2 * (1 + esc) ** ((launch - 1) / 12),
                "price_escalation_annual": esc, "launch_month": launch, "sales_months": t.sales_months,
                "sales_curve": {"kind": curve if curve != "manual" else "even"}, "plan_mix": mix,
                "transaction_tax_pct": ref.get("transaction_tax_pct", 0.0),
                "premiums": [{"name": n, "uplift": _uplift(brief, n), "share_of_units": round(s, 6)}
                             for n, s in prem.get(key, {}).items() if s > 0],
            })
        products.append(p)
    # shares must total exactly 1 for the engine
    s = sum(p["bua_share"] for p in products)
    products[-1]["bua_share"] += 1 - s

    cond = {"built_area_m2": total}
    if base.get("conditions", {}).get("build_deadline_months"):
        cond["build_deadline_months"] = base["conditions"]["build_deadline_months"]
    costs = base.get("costs", {})
    if brief.site_costs == "from_layout":
        land = sum(p["area_m2"] for p in base["parcels"])
        k = option["kpis"]
        site = k["site_area_m2"]
        roads = k["roads_share"] * site
        green = k["open_space_share"] * site
        first = (costs.get("site_costs") or [None])[0]
        start = first["start_month"] if first else phases[0]["start_month"]
        dur = first["duration_months"] if first else phases[0]["duration_months"]
        costs = dict(costs)
        costs["site_costs"] = [
            {"name": "roads and utilities", "rate_per_m2_land": brief.road_cost_per_m2 * roads / land,
             "start_month": start, "duration_months": dur},
            {"name": "landscape", "rate_per_m2_land": brief.landscape_cost_per_m2 * green / land,
             "start_month": start + dur // 2, "duration_months": max(1, dur // 2 + 6)},
        ]
    study = {
        "name": name or f"Layout: {option['strategy']}",
        "parcels": base["parcels"], "conditions": cond, "products": products, "plans": plans,
        "phases": base["phases"], "sales_risk": base.get("sales_risk", {}), "costs": costs,
        "land": base.get("land", {}), "finance": base["finance"], "tax": base.get("tax", {}),
    }
    return Study.model_validate(study).model_dump(mode="json", exclude_defaults=False)


def _uplift(brief: LayoutBrief, name: str) -> float:
    return next(p.uplift for p in list(brief.depth_premiums) + list(brief.view_premiums) if p.name == name)


KEEP = ("revenue", "development_cost", "land_cost", "profit_pre_tax", "margin_on_revenue", "npv_pre_tax",
        "irr_pre_tax", "npv_after_tax", "irr_after_tax", "peak_funding_need", "payback_month", "units")


def evaluate(options: list[dict], brief: LayoutBrief, base: dict) -> list[dict]:
    """Run each option through the engine and rank them. Adds 'study', 'feasibility' and 'rank'."""
    for o in options:
        if "error" in o:
            continue
        o["passes"] = all(c["ok"] for c in o["compliance"])
        try:
            data = option_study(o, brief, base)
            res = run(Study.model_validate(data))
            o["study"] = data
            o["feasibility"] = {k: res.kpis.get(k) for k in KEEP}
            o["feasibility"]["months"] = res.months
            o["feasibility"]["warnings"] = res.warnings
        except (ConditionError, CheckFailed, ValueError) as e:
            o["feasibility_error"] = str(e)
    ok = [o for o in options if "feasibility" in o]

    def key(o):
        f = o["feasibility"]
        return (not o["passes"], -(f["npv_pre_tax"] or 0), -(f["irr_pre_tax"] or -9), -(f["margin_on_revenue"] or -9))

    for i, o in enumerate(sorted(ok, key=key)):
        o["rank"] = i + 1
    best = next((o for o in sorted(ok, key=key) if o["passes"]), None)
    for o in options:
        o["best"] = o is best
    return options
