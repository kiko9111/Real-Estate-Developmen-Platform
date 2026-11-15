"""Running full studies, scenarios and analyses, and keeping their results.

Results are stored as plain JSON so a page or a report can be rebuilt without
re-running the engine. Every number comes from `feasibility.run`; nothing is
recomputed here.
"""
from __future__ import annotations

import copy
import json
from importlib import resources
from typing import Optional

from pydantic import ValidationError

from feasibility import RiskSpec, Study, residual_land_value, risk_ranges, run, sensitivity, shocked
from feasibility.engine import CheckFailed, ConditionError

from .schemas import LandConditions

LINE_LABELS = {
    "collections": "Sales collections",
    "cancellation_refunds": "Cancellation refunds",
    "rent": "Rent",
    "lease_exit": "Sale of leased assets",
    "hard_cost": "Construction",
    "site_cost": "Infrastructure and site",
    "soft_cost": "Soft costs",
    "contingency": "Contingency",
    "marketing": "Marketing",
    "commission": "Commission",
    "transaction_tax": "Transaction tax",
    "overhead": "Overhead",
    "lease_opex": "Operating cost of leased assets",
    "land_cash": "Land price",
    "land_interest": "Land interest",
    "land_fees": "Land fees",
    "land_revenue_share": "Owner's revenue share",
    "land_partnership_share": "Owner's partnership share",
    "income_tax": "Income tax",
    "cash_flow_pre_tax": "Net cash flow before tax",
    "cash_flow_after_tax": "Net cash flow after tax",
    "debt_draw": "Debt drawn",
    "debt_repayment": "Debt repaid",
    "debt_interest": "Debt interest",
    "equity_cash_flow": "Equity cash flow",
}

PROJECT_LINES = [k for k in LINE_LABELS if k not in ("debt_draw", "debt_repayment", "debt_interest",
                                                      "equity_cash_flow", "income_tax", "cash_flow_pre_tax",
                                                      "cash_flow_after_tax")]

KPI_LABELS = {
    "revenue": "Revenue",
    "development_cost": "Development cost",
    "land_cost": "Land cost",
    "profit_pre_tax": "Profit before tax",
    "income_tax": "Income tax",
    "profit_after_tax": "Profit after tax",
    "margin_on_revenue": "Margin on revenue",
    "margin_on_cost": "Margin on cost",
    "npv_pre_tax": "NPV before tax",
    "npv_after_tax": "NPV after tax",
    "irr_pre_tax": "IRR before tax",
    "irr_after_tax": "IRR after tax",
    "peak_funding_need": "Peak funding need",
    "peak_funding_month": "Peak funding month",
    "payback_month": "Payback month",
    "equity_irr": "Equity IRR",
    "equity_multiple": "Equity multiple",
    "peak_equity": "Peak equity",
    "peak_debt": "Peak debt",
    "land_area_feddan": "Land area (feddan)",
    "units": "Units for sale",
}

SCENARIO_SHOCKS = [
    ("Prices -10%", "price", 0.9),
    ("Prices +10%", "price", 1.1),
    ("Costs +10%", "cost", 1.1),
    ("Sales 25% slower", "sales_pace", 0.8),
    ("6-month delay", "delay", 6),
    ("Cost inflation +3 pts", "cost_inflation", 0.03),
]


class StudyInputError(Exception):
    def __init__(self, messages: list[str]):
        super().__init__("; ".join(messages))
        self.messages = messages


def validate_study(data: dict) -> Study:
    try:
        return Study.model_validate(data)
    except ValidationError as e:
        msgs = []
        for err in e.errors():
            loc = " > ".join(str(x) for x in err["loc"])
            msgs.append(f"{loc}: {err['msg']}" if loc else err["msg"])
        raise StudyInputError(msgs) from None


def summarize(res) -> dict:
    """A JSON-safe copy of an engine result: KPIs, products and the yearly cash flow."""
    years = res.table("year")
    quarters = res.table("quarter")
    keep = [k for k in LINE_LABELS if k in years]
    return {
        "study_name": res.study_name,
        "months": res.months,
        "land_area_m2": res.land_area_m2,
        "allowed_built_area_m2": res.allowed_built_area_m2,
        "built_area_m2": res.built_area_m2,
        "kpis": res.kpis,
        "irr_status": {"pre_tax": res.irr_pre_tax.status, "after_tax": res.irr_after_tax.status,
                       "equity": res.irr_equity.status},
        "products": [{
            "name": p.name, "category": p.category, "mode": p.mode, "built_area_m2": p.built_area_m2,
            "sellable_area_m2": p.sellable_area_m2, "units": p.units, "delivery_month": p.delivery_month,
            "contracted_value": p.contracted_value, "hard_cost": p.hard_cost,
            "average_price_per_m2": p.average_price_per_m2,
        } for p in res.products],
        "yearly": {k: [float(x) for x in years[k]] for k in keep},
        "quarterly_net": [float(x) for x in quarters["cash_flow_pre_tax"]],
        "cumulative_yearly": _cum([float(x) for x in years["cash_flow_pre_tax"]]),
        "checks": res.checks,
        "warnings": res.warnings,
    }


def _cum(v):
    out, s = [], 0.0
    for x in v:
        s += x
        out.append(s)
    return out


def run_study(data: dict) -> dict:
    """Validate and run. Raises StudyInputError with readable messages."""
    study = validate_study(data)
    try:
        return summarize(run(study))
    except (ConditionError, CheckFailed, ValueError) as e:
        raise StudyInputError([str(e)]) from None


def quick_scenarios(data: dict) -> list[dict]:
    """The base case and each standard shock, each a full engine re-run."""
    study = validate_study(data)
    rows = [{"label": "Base case", **_kpi_row(run(study).kpis)}]
    for label, shock, value in SCENARIO_SHOCKS:
        try:
            rows.append({"label": label, **_kpi_row(run(shocked(study, shock, value)).kpis)})
        except (ConditionError, CheckFailed, ValueError) as e:
            rows.append({"label": label, "error": str(e)})
    return rows


def _kpi_row(k: dict) -> dict:
    return {key: k.get(key) for key in ("revenue", "profit_pre_tax", "margin_on_revenue", "npv_pre_tax",
                                         "irr_pre_tax", "npv_after_tax", "irr_after_tax", "peak_funding_need")}


def sensitivity_table(data: dict, shock: str, values: list[float]) -> list[dict]:
    return sensitivity(validate_study(data), shock, values)


def residual(data: dict, after_tax: bool = False) -> Optional[dict]:
    study = validate_study(data)
    if study.land.cash is None:
        return None
    r = residual_land_value(study, after_tax=after_tax)
    return r.__dict__


def risk(data: dict, runs: int = 200) -> dict:
    return risk_ranges(validate_study(data), RiskSpec(runs=runs))


# ---------------------------------------------------------------- starting a study

def sample_study() -> dict:
    """The invented sample study that ships with the engine, as a template."""
    for base in (resources.files("webapp") / "sample_study.json",):
        if base.is_file():
            return json.loads(base.read_text(encoding="utf-8"))
    raise FileNotFoundError("sample_study.json")


def starter_study(land, name: str) -> dict:
    """A study for this land: the sample's structure, the land's own area and conditions."""
    d = copy.deepcopy(sample_study())
    d["name"] = name
    parcels = [p for p in (land.parcels or []) if p.get("area_m2")]
    d["parcels"] = ([{"name": p.get("name") or f"P{i + 1}", "area_m2": float(p["area_m2"])} for i, p in enumerate(parcels)]
                    or [{"name": "land", "area_m2": land.area_m2}])
    cond = LandConditions.model_validate(land.conditions or {})
    area = sum(p["area_m2"] for p in d["parcels"])
    ec = cond.to_engine_conditions(area).model_dump(exclude_defaults=True)
    if ec.get("far_cap") or ec.get("height_zones") or ec.get("built_area_m2"):
        d["conditions"] = ec
    else:
        d["conditions"] = {"far_cap": 0.5}     # placeholder until the conditions form is filled in
    return d
