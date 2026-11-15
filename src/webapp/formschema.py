"""JSON schemas for the in-browser forms, with short help texts.

The study editor and the land-conditions form are generated from the same
pydantic models the engine and the database use, so a new engine input shows
up in the form without template work.
"""
from __future__ import annotations

from typing import Any

HELP = {
    # study
    "parcels": "The plots that make up the land. Their areas add up to the land area.",
    "conditions": "Allotment conditions. Leave blank what does not apply.",
    "far_cap": "Floor area ratio: most built area per m2 of land.",
    "footprint_pct": "Coverage: share of the land that buildings may cover.",
    "height_zones": "Parts of the footprint with their own floor limit. Shares must add up to 1.",
    "built_area_m2": "Built area you choose to develop. Blank builds up to the cap.",
    "min_share_by_category": "Minimum share of built area per category, e.g. hotel: 0.05.",
    "max_share_by_category": "Maximum share of built area per category.",
    "build_deadline_months": "All construction must finish by this month.",
    "products": "What is built. Their shares of built area must add up to 1.",
    "bua_share": "Share of the total built area.",
    "sellable_share": "Sellable m2 per built m2.",
    "unit_size_m2": "Average sellable m2 per unit.",
    "phase": "Name of the construction phase that builds it.",
    "hard_cost_per_m2": "Construction cost per built m2, in month-1 money.",
    "launch_price_per_m2": "Price per sellable m2 at launch, in month-1 money.",
    "price_escalation_annual": "Yearly price growth from the launch month (0.10 = 10%).",
    "launch_month": "Month sales open. Month 1 is the land deal.",
    "sales_months": "Months to sell everything. Give this or units per month.",
    "units_per_month": "Fixed selling pace. Give this or sales months.",
    "premiums": "Price premiums such as sea view. Each applies to its share of units, or to listed units.",
    "units": "Optional unit list; each unit names the premiums it carries.",
    "releases": "Price steps from a month onward (0.04 = +4%).",
    "plan_mix": "Share of sales on each payment plan, by plan name. Must total 1.",
    "transaction_tax_pct": "Tax on each sale.",
    "lease": "Lease terms, for products kept and rented.",
    "plans": "Payment plans offered to buyers.",
    "down_payment": "Paid at signing (0.10 = 10%).",
    "delivery_payment": "Paid at delivery.",
    "instalment_count": "Number of equal instalments.",
    "instalment_every_months": "Months between instalments.",
    "first_instalment_after_months": "Months from sale to the first instalment. Blank = one interval.",
    "price_adjustment": "Price change for this plan, e.g. -0.25 for a cash discount.",
    "manual_schedule": "Replaces the instalments with your own schedule.",
    "phases": "Construction phases.",
    "spend_curve": "How spending is spread over the phase.",
    "sales_risk": "Cancellations and unsold stock.",
    "costs": "Costs other than land.",
    "cost_inflation_annual": "Yearly cost inflation from month 1.",
    "site_costs": "Costs per m2 of land, such as infrastructure and landscape.",
    "soft_costs": "Percent of construction (and optionally site) cost.",
    "contingency_pct": "One contingency, as a share of its base.",
    "overhead_monthly": "Monthly overhead in month-1 money.",
    "land": "How the land is paid for.",
    "cash": "Cash purchase of all or part of the land.",
    "price_per_m2": "Land price per m2. One feddan is 4,200.83 m2.",
    "area_share": "Share of the land this applies to.",
    "down_payment_pct": "Share paid at the start.",
    "interest_annual": "Interest on the unpaid balance.",
    "fx_share": "Share of the price owed in foreign currency.",
    "fees": "Extra land charges with their own schedule, such as betterment.",
    "in_kind_share": "Sellable area handed to the owner.",
    "revenue_share": "Owner's share of revenue as collected.",
    "partnership": "Owner keeps part of the land and shares profit or revenue on it.",
    "owner_share": "Owner's share in the partnership.",
    "finance": "Target return and debt.",
    "discount_rate_annual": "Target return used for NPV (nominal, yearly).",
    "debt_limit": "Most debt available. Equity covers the rest.",
    "tax": "Income tax.",
    # land conditions
    "primary_use": "Main permitted use of the land.",
    "permitted_uses": "All uses allowed on the land.",
    "authority": "Body that sets the terms (for example a city authority).",
    "far": "Floor area ratio: most built area per m2 of land.",
    "max_built_area_m2": "Ceiling on total built area, if one is given.",
    "max_floors": "Floors allowed, if the same everywhere.",
    "max_units_per_feddan": "Density limit in units.",
    "max_persons_per_feddan": "Density limit in persons.",
    "services_share_of_land": "Land kept for services.",
    "open_space_share_of_land": "Land kept as open space.",
    "roads_share_of_land": "Land taken by roads.",
    "share_rules": "Minimum or maximum share for a use. Built-area rules for residential, commercial, hotel and serviced feed the feasibility study.",
    "setbacks": "Distances buildings keep from boundaries.",
    "parking": "Parking spaces required per use.",
    "payment_terms": "Land payment terms set by the seller or authority.",
    "custom": "Anything else that applies to this land.",
}


def form_schema(model) -> dict:
    s = model.model_json_schema()
    _add_help(s)
    for d in s.get("$defs", {}).values():
        _add_help(d)
    return s


def _add_help(node: Any) -> None:
    if isinstance(node, dict):
        props = node.get("properties")
        if isinstance(props, dict):
            for name, p in props.items():
                if isinstance(p, dict) and "description" not in p and name in HELP:
                    p["description"] = HELP[name]
                _add_help(p)
        for key in ("items", "additionalProperties"):
            if isinstance(node.get(key), dict):
                _add_help(node[key])
        for key in ("anyOf", "allOf", "oneOf"):
            for sub in node.get(key, []) or []:
                _add_help(sub)
