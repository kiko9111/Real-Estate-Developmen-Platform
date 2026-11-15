"""Small studies whose answers can be worked out on paper."""
from __future__ import annotations

import copy

from feasibility import Study


def simple(**over) -> dict:
    """1,000 m2 of land, floor-area cap 1.0, one product of 10 units of 100 m2
    at 10,000 per m2 (10,000,000 in all), sold for cash in month 1.
    No costs, free land, 10% discount rate."""
    d = {
        "name": "simple",
        "parcels": [{"name": "A", "area_m2": 1000.0}],
        "conditions": {"far_cap": 1.0},
        "products": [{
            "name": "apartments", "category": "residential", "bua_share": 1.0,
            "sellable_share": 1.0, "unit_size_m2": 100.0, "phase": "P1",
            "hard_cost_per_m2": 0.0, "launch_price_per_m2": 10_000.0,
            "sales_months": 1, "plan_mix": {"cash": 1.0},
        }],
        "plans": [{"name": "cash", "down_payment": 1.0}],
        "phases": [{"name": "P1", "start_month": 1, "duration_months": 12}],
        "finance": {"discount_rate_annual": 0.10},
    }
    d = copy.deepcopy(d)
    for path, value in over.items():
        set_path(d, path, value)
    return d


def set_path(d, path: str, value) -> None:
    """set_path(d, "products.0.sales_months", 10)"""
    keys = path.split(".")
    for k in keys[:-1]:
        d = d[int(k)] if isinstance(d, list) else d.setdefault(k, {})
    last = keys[-1]
    if isinstance(d, list):
        d[int(last)] = value
    else:
        d[last] = value


def study(d: dict) -> Study:
    return Study.model_validate(d)


def rich() -> dict:
    """A study that switches every feature on, used by the rule tests."""
    return {
        "name": "rich",
        "parcels": [{"name": "A", "area_m2": 60_000.0}, {"name": "B", "area_m2": 40_000.0}],
        "conditions": {
            "far_cap": 1.2, "footprint_pct": 0.25,
            "height_zones": [{"name": "low", "share_of_footprint": 0.6, "floors": 3},
                             {"name": "high", "share_of_footprint": 0.4, "floors": 6}],
            "built_area_m2": 90_000.0,
            "min_share_by_category": {"hotel": 0.05},
            "max_share_by_category": {"residential": 0.8},
            "build_deadline_months": 60,
        },
        "products": [
            {"name": "apartments", "category": "residential", "bua_share": 0.55,
             "sellable_share": 0.85, "unit_size_m2": 120.0, "phase": "P1",
             "hard_cost_per_m2": 18_000.0, "launch_price_per_m2": 70_000.0,
             "price_escalation_annual": 0.12, "launch_month": 4, "sales_months": 36,
             "sales_curve": {"kind": "front_loaded"},
             "premiums": [{"name": "sea view", "uplift": 0.15, "share_of_units": 0.3},
                          {"name": "corner", "uplift": 0.05, "share_of_units": 0.2}],
             "releases": [{"from_month": 16, "step": 0.04}],
             "plan_mix": {"cash": 0.1, "8y": 0.9}, "transaction_tax_pct": 0.005},
            {"name": "villas", "category": "residential", "bua_share": 0.20,
             "sellable_share": 1.0, "unit_size_m2": 300.0, "phase": "P2",
             "hard_cost_per_m2": 24_000.0, "launch_price_per_m2": 110_000.0,
             "price_escalation_annual": 0.10, "launch_month": 10, "units_per_month": 2.0,
             "premiums": [{"name": "first row", "uplift": 0.25}],
             "units": [{"id": "V1", "premiums": ["first row"]}, {"id": "V2"}, {"id": "V3"}, {"id": "V4"}],
             "plan_mix": {"8y": 1.0}},
            {"name": "retail", "category": "commercial", "mode": "lease", "bua_share": 0.15,
             "sellable_share": 0.8, "unit_size_m2": 80.0, "phase": "P2",
             "hard_cost_per_m2": 20_000.0,
             "lease": {"rent_per_m2_month": 900.0, "stable_occupancy": 0.9, "ramp_months": 18,
                       "rent_growth_annual": 0.08, "opex_pct_of_rent": 0.15, "hold_months": 84,
                       "exit_cap_rate": 0.10, "exit_cost_pct": 0.02}},
            {"name": "hotel apartments", "category": "hotel", "bua_share": 0.10,
             "sellable_share": 0.9, "unit_size_m2": 60.0, "phase": "P2",
             "hard_cost_per_m2": 30_000.0, "launch_price_per_m2": 95_000.0,
             "price_escalation_annual": 0.10, "launch_month": 12, "sales_months": 30,
             "sales_curve": {"kind": "manual", "weights": [3, 2, 2, 1, 1, 1]},
             "plan_mix": {"cash": 0.3, "8y": 0.7}},
        ],
        "plans": [
            {"name": "cash", "down_payment": 1.0, "price_adjustment": -0.25},
            {"name": "8y", "down_payment": 0.05, "delivery_payment": 0.10,
             "instalment_count": 32, "instalment_every_months": 3},
        ],
        "phases": [
            {"name": "P1", "start_month": 7, "duration_months": 36, "spend_curve": {"kind": "s_curve"}},
            {"name": "P2", "start_month": 13, "duration_months": 42},
        ],
        "sales_risk": {"cancellation_rate": 0.08, "cancel_after_months": 14,
                       "cancellation_penalty_pct": 0.10, "resale_delay_months": 4,
                       "resale_discount": 0.03, "unsold_share": 0.05, "clear_unsold": True,
                       "clearance_months": 9, "clearance_discount": 0.12},
        "costs": {
            "cost_inflation_annual": 0.14,
            "site_costs": [{"name": "infrastructure", "rate_per_m2_land": 2_500.0,
                            "start_month": 3, "duration_months": 24},
                           {"name": "landscape", "rate_per_m2_land": 600.0,
                            "start_month": 30, "duration_months": 18,
                            "spend_curve": {"kind": "s_curve"}}],
            "soft_costs": [{"name": "design", "pct": 0.03, "base": "building"},
                           {"name": "supervision", "pct": 0.02}],
            "contingency_pct": 0.10, "marketing_pct_of_sales": 0.02,
            "commission_pct_of_sales": 0.03, "overhead_monthly": 1_500_000.0,
            "overhead_start_month": 2, "overhead_end_month": 70,
        },
        "land": {
            "cash": {"price_per_m2": 9_000.0, "area_share": 0.5, "down_payment_pct": 0.2,
                     "instalment_count": 6, "instalment_every_months": 6,
                     "first_instalment_after_months": 12, "interest_annual": 0.10,
                     "fx_share": 0.25, "fx_depreciation_annual": 0.08},
            "fees": [{"name": "betterment", "amount_per_m2_land": 1_200.0,
                      "schedule": [{"month": 1, "share": 0.5}, {"month": 13, "share": 0.5}]}],
            "in_kind_share": 0.03, "revenue_share": 0.02,
            "partnership": {"area_share": 0.5, "owner_share": 0.4, "basis": "profit"},
        },
        "finance": {"discount_rate_annual": 0.18, "debt_limit": 400_000_000.0,
                    "debt_interest_annual": 0.22},
        "tax": {"income_tax_rate": 0.225, "payment_lag_months": 4},
    }
