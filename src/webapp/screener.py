"""Quick go/no-go screening of a new land.

The screener asks for about twenty numbers, builds a one-product study from
them and runs the full engine on it. There is no separate shortcut formula, so
a screening and a full study of the same inputs agree to the pound.

Verdict:
  go          NPV at the target rate is zero or better and the margin on
              revenue meets the minimum.
  borderline  Not a go, but the asking price is within the band of the
              residual land value, or the IRR is within the band of the target.
  no_go       Anything else.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from feasibility import FEDDAN_M2, Study, residual_land_value, run
from feasibility.engine import ConditionError


class ScreenInputs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # land and deal
    land_area_m2: float = Field(gt=0)
    land_price_per_feddan: float = Field(ge=0)
    purchase_share: float = Field(default=1.0, ge=0, le=1)          # rest is in partnership
    partnership_owner_share: float = Field(default=0.4, ge=0, le=1)
    partnership_basis: Literal["profit", "revenue"] = "profit"
    land_down_payment_pct: float = Field(default=1.0, gt=0, le=1)
    land_instalment_count: int = Field(default=0, ge=0)
    land_instalment_every_months: int = Field(default=12, ge=1)
    land_interest_annual: float = Field(default=0.0, ge=0)
    betterment_fee_per_m2: float = Field(default=0.0, ge=0)
    # what can be built
    far: float = Field(gt=0)
    product_category: Literal["residential", "commercial", "hotel", "serviced"] = "residential"
    sellable_share: float = Field(default=0.8, gt=0, le=1)
    unit_size_m2: float = Field(default=150.0, gt=0)
    # sales
    price_per_m2: float = Field(gt=0)                  # sellable m2, today's money
    price_escalation_annual: float = 0.10
    launch_month: int = Field(default=6, ge=1)
    sales_months: int = Field(default=60, ge=1)
    down_payment_pct: float = Field(default=0.10, ge=0, le=1)
    instalment_years: float = Field(default=8, ge=0, le=20)
    # costs
    hard_cost_per_m2: float = Field(gt=0)              # built m2, today's money
    construction_start_month: int = Field(default=12, ge=1)
    construction_months: int = Field(default=60, ge=1)
    infrastructure_per_m2_land: float = Field(default=0.0, ge=0)
    soft_cost_pct: float = Field(default=0.06, ge=0)
    contingency_pct: float = Field(default=0.10, ge=0)
    marketing_pct: float = Field(default=0.02, ge=0, le=1)
    commission_pct: float = Field(default=0.025, ge=0, le=1)
    cost_inflation_annual: float = 0.12
    # money
    discount_rate_annual: float = Field(default=0.25, gt=-1)
    income_tax_rate: float = Field(default=0.225, ge=0, le=1)
    # thresholds
    min_margin: float = Field(default=0.20, ge=-1, le=1)
    band: float = Field(default=0.10, ge=0, le=1)


LABELS = {
    "land_area_m2": ("Land area", "m2"),
    "land_price_per_feddan": ("Land price", "per feddan"),
    "purchase_share": ("Share of land bought", "fraction; the rest is in partnership"),
    "partnership_owner_share": ("Owner's share in the partnership", "fraction"),
    "partnership_basis": ("Partnership shares", ""),
    "land_down_payment_pct": ("Land down payment", "fraction"),
    "land_instalment_count": ("Land instalments", "count"),
    "land_instalment_every_months": ("Land instalment every", "months"),
    "land_interest_annual": ("Interest on unpaid land price", "per year"),
    "betterment_fee_per_m2": ("Betterment fee", "per m2 of land"),
    "far": ("Floor area ratio (built area / land)", ""),
    "product_category": ("Main product", ""),
    "sellable_share": ("Sellable share of built area", "fraction"),
    "unit_size_m2": ("Average unit size", "m2"),
    "price_per_m2": ("Selling price today", "per sellable m2"),
    "price_escalation_annual": ("Price growth", "per year"),
    "launch_month": ("Sales launch", "month"),
    "sales_months": ("Sales period", "months"),
    "down_payment_pct": ("Buyer down payment", "fraction"),
    "instalment_years": ("Buyer instalments", "years, quarterly"),
    "hard_cost_per_m2": ("Construction cost today", "per built m2"),
    "construction_start_month": ("Construction start", "month"),
    "construction_months": ("Construction period", "months"),
    "infrastructure_per_m2_land": ("Infrastructure and landscape", "per m2 of land"),
    "soft_cost_pct": ("Soft costs", "fraction of construction"),
    "contingency_pct": ("Contingency", "fraction of construction"),
    "marketing_pct": ("Marketing", "fraction of sales"),
    "commission_pct": ("Sales commission", "fraction of sales"),
    "cost_inflation_annual": ("Cost inflation", "per year"),
    "discount_rate_annual": ("Target return (discount rate)", "per year"),
    "income_tax_rate": ("Income tax", "fraction"),
    "min_margin": ("Minimum margin on revenue", "fraction"),
    "band": ("Borderline band", "fraction"),
}

GROUPS = [
    ("Land and deal", ["land_area_m2", "land_price_per_feddan", "purchase_share", "partnership_owner_share",
                       "partnership_basis", "land_down_payment_pct", "land_instalment_count",
                       "land_instalment_every_months", "land_interest_annual", "betterment_fee_per_m2"]),
    ("What can be built", ["far", "product_category", "sellable_share", "unit_size_m2"]),
    ("Sales", ["price_per_m2", "price_escalation_annual", "launch_month", "sales_months",
               "down_payment_pct", "instalment_years"]),
    ("Costs", ["hard_cost_per_m2", "construction_start_month", "construction_months",
               "infrastructure_per_m2_land", "soft_cost_pct", "contingency_pct", "marketing_pct",
               "commission_pct", "cost_inflation_annual"]),
    ("Target", ["discount_rate_annual", "income_tax_rate", "min_margin", "band"]),
]


def build_study(i: ScreenInputs, name: str = "Screening") -> Study:
    """A one-product engine study from the screening inputs."""
    if i.instalment_years > 0 and i.down_payment_pct < 1:
        plan = {"name": "plan", "down_payment": i.down_payment_pct,
                "instalment_count": max(1, int(round(i.instalment_years * 4))), "instalment_every_months": 3}
    else:
        plan = {"name": "plan", "down_payment": 1.0}
    land: dict = {}
    if i.purchase_share > 0:
        cash = {"price_per_m2": i.land_price_per_feddan / FEDDAN_M2, "area_share": i.purchase_share,
                "down_payment_pct": i.land_down_payment_pct, "interest_annual": i.land_interest_annual}
        if i.land_down_payment_pct < 1:
            cash["instalment_count"] = max(1, i.land_instalment_count)
            cash["instalment_every_months"] = i.land_instalment_every_months
        land["cash"] = cash
    if i.purchase_share < 1:
        land["partnership"] = {"area_share": 1 - i.purchase_share, "owner_share": i.partnership_owner_share,
                               "basis": i.partnership_basis}
    if i.betterment_fee_per_m2 > 0:
        land["fees"] = [{"name": "betterment", "amount_per_m2_land": i.betterment_fee_per_m2,
                         "schedule": [{"month": 1, "share": 1.0}]}]
    costs: dict = {
        "cost_inflation_annual": i.cost_inflation_annual,
        "soft_costs": [{"name": "soft costs", "pct": i.soft_cost_pct}] if i.soft_cost_pct else [],
        "contingency_pct": i.contingency_pct,
        "marketing_pct_of_sales": i.marketing_pct,
        "commission_pct_of_sales": i.commission_pct,
    }
    if i.infrastructure_per_m2_land > 0:
        costs["site_costs"] = [{"name": "infrastructure", "rate_per_m2_land": i.infrastructure_per_m2_land,
                                "start_month": i.construction_start_month,
                                "duration_months": max(1, min(36, i.construction_months)),
                                "spend_curve": {"kind": "even"}}]
    return Study.model_validate({
        "name": name,
        "parcels": [{"name": "land", "area_m2": i.land_area_m2}],
        "conditions": {"far_cap": i.far},
        "products": [{
            "name": i.product_category, "category": i.product_category, "bua_share": 1.0,
            "sellable_share": i.sellable_share, "unit_size_m2": i.unit_size_m2, "phase": "build",
            "hard_cost_per_m2": i.hard_cost_per_m2, "launch_price_per_m2": i.price_per_m2,
            "price_escalation_annual": i.price_escalation_annual, "launch_month": i.launch_month,
            "sales_months": i.sales_months, "sales_curve": {"kind": "s_curve"},
            "plan_mix": {"plan": 1.0},
        }],
        "plans": [plan],
        "phases": [{"name": "build", "start_month": i.construction_start_month,
                    "duration_months": i.construction_months, "spend_curve": {"kind": "s_curve"}}],
        "costs": costs,
        "land": land,
        "finance": {"discount_rate_annual": i.discount_rate_annual},
        "tax": {"income_tax_rate": i.income_tax_rate},
    })


def screen(i: ScreenInputs) -> dict:
    """Run the screening. Returns plain JSON-safe data."""
    study = build_study(i)
    try:
        res = run(study)
    except ConditionError as e:
        return {"verdict": "no_go", "reasons": [f"The inputs break a land condition: {e}"], "kpis": {}, "error": str(e)}
    k = res.kpis
    out: dict = {"kpis": k, "built_area_m2": res.built_area_m2, "months": res.months,
                 "sellable_area_m2": sum(p.sellable_area_m2 for p in res.products),
                 "yearly_net_cash": [float(x) for x in res.table("year")["cash_flow_pre_tax"]],
                 "warnings": res.warnings}
    residual = None
    if i.purchase_share > 0:
        r = residual_land_value(study)
        residual = r.price_per_feddan if r.feasible else 0.0
    out["residual_price_per_feddan"] = residual
    out["asking_price_per_feddan"] = i.land_price_per_feddan

    npv, margin, irr = k["npv_pre_tax"], k["margin_on_revenue"], k["irr_pre_tax"]
    target = i.discount_rate_annual
    reasons = []
    is_go = npv >= 0 and margin is not None and margin >= i.min_margin
    reasons.append(f"NPV at the {target:.0%} target is {_m(npv)}" + (" (meets the target)." if npv >= 0 else " (misses the target)."))
    if irr is None:
        reasons.append("No single IRR exists for this cash flow.")
    else:
        reasons.append(f"IRR is {irr:.1%} against a {target:.0%} target.")
    if margin is not None:
        reasons.append(f"Margin on revenue is {margin:.1%} against a {i.min_margin:.0%} minimum.")
    near = False
    if residual is not None:
        if residual <= 0:
            reasons.append("Even free land would miss the target return.")
        else:
            reasons.append(f"The most the land can cost and still meet the target is {_m(residual)} per feddan; "
                           f"the asking price is {_m(i.land_price_per_feddan)}.")
            near = i.land_price_per_feddan <= residual * (1 + i.band)
    if irr is not None and irr >= target - i.band * abs(target):
        near = True
    verdict = "go" if is_go else ("borderline" if near else "no_go")
    out["verdict"] = verdict
    out["reasons"] = reasons
    return out


def _m(x: float) -> str:
    a = abs(x)
    s = f"{a / 1e9:,.2f}B" if a >= 1e9 else f"{a / 1e6:,.1f}M" if a >= 1e6 else f"{a:,.0f}"
    return ("-" if x < 0 else "") + s


def defaults_for_land(land, market_price: Optional[float] = None) -> dict:
    """Starting inputs filled from what the land record already holds."""
    from .schemas import LandConditions, Legal, Offer
    offer = Offer.model_validate(land.offer or {})
    legal = Legal.model_validate(land.legal or {})
    cond = LandConditions.model_validate(land.conditions or {})
    d = {k: f.default for k, f in ScreenInputs.model_fields.items() if f.default is not None and not f.is_required()}
    d["land_area_m2"] = land.area_m2
    d["land_price_per_feddan"] = offer.asking_price_per_feddan or 0.0
    if offer.purchase_area_m2 and land.area_m2:
        d["purchase_share"] = round(min(1.0, offer.purchase_area_m2 / land.area_m2), 6)
    if offer.partnership_owner_share is not None:
        d["partnership_owner_share"] = offer.partnership_owner_share
    d["partnership_basis"] = offer.partnership_basis
    if legal.betterment_fee_per_m2:
        d["betterment_fee_per_m2"] = legal.betterment_fee_per_m2 * (1 - (legal.betterment_paid_share or 0))
    pt = cond.payment_terms
    if pt.down_payment_pct is not None:
        d["land_down_payment_pct"] = pt.down_payment_pct or 1.0
    if pt.instalment_count:
        d["land_instalment_count"] = pt.instalment_count
    if pt.instalment_every_months:
        d["land_instalment_every_months"] = pt.instalment_every_months
    if pt.interest_annual is not None:
        d["land_interest_annual"] = pt.interest_annual
    if cond.far:
        d["far"] = cond.far
    elif cond.footprint_pct and cond.max_floors:
        d["far"] = round(cond.footprint_pct * cond.max_floors, 4)
    if market_price:
        d["price_per_m2"] = round(market_price, -2)
    return d
