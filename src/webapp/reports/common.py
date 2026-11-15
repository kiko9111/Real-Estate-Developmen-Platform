"""The content of an investor report, independent of the file format."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from feasibility import FEDDAN_M2

from ..schemas import LandConditions, Offer

INFLOW = ("collections", "cancellation_refunds", "rent", "lease_exit")
DEV = ("hard_cost", "site_cost", "soft_cost", "contingency", "marketing", "commission", "transaction_tax",
       "overhead", "lease_opex")
LAND = ("land_cash", "land_interest", "land_fees", "land_revenue_share", "land_partnership_share")


@dataclass
class ReportData:
    title: str
    subtitle: str
    prepared: str
    currency: str
    headline: list[tuple[str, str]]
    land_facts: list[tuple[str, str]]
    program_header: list[str]
    program_rows: list[list[str]]
    years: list[str]
    cash_rows: list[tuple[str, list[float]]]
    net_by_year: list[float]
    cumulative: list[float]
    scenarios: list[dict] = field(default_factory=list)
    residual: Optional[dict] = None
    market: list[list[str]] = field(default_factory=list)
    assumptions: list[tuple[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def money(x: Optional[float], cur: str = "") -> str:
    if x is None:
        return "n/a"
    a = abs(x)
    s = f"{a / 1e9:,.2f} B" if a >= 1e9 else f"{a / 1e6:,.1f} M" if a >= 1e6 else f"{a:,.0f}"
    return ("-" if x < 0 else "") + s + (f" {cur}" if cur else "")


def pct(x: Optional[float]) -> str:
    return "n/a" if x is None else f"{x:.1%}"


def _sum_lines(yearly: dict, keys) -> list[float]:
    n = max((len(v) for v in yearly.values()), default=0)
    out = [0.0] * n
    for k in keys:
        for i, v in enumerate(yearly.get(k, [])):
            out[i] += v
    return out


def month_label(m: Optional[int]) -> str:
    if m is None:
        return "n/a"
    y, mm = divmod(m - 1, 12)
    return f"month {m} (year {y + 1})"


def build_report_data(land, study, result: dict, study_data: dict, scenarios: list[dict] | None = None,
                      residual: dict | None = None, market_stats: dict | None = None,
                      currency: str = "EGP", prepared_by: str = "") -> ReportData:
    k = result["kpis"]
    rate = study_data["finance"]["discount_rate_annual"]
    headline = [
        ("Revenue", money(k["revenue"], currency)),
        ("Profit before tax", money(k["profit_pre_tax"], currency)),
        ("Margin on revenue", pct(k["margin_on_revenue"])),
        (f"NPV at {rate:.0%}", money(k["npv_pre_tax"], currency)),
        ("IRR before tax", pct(k["irr_pre_tax"])),
        ("IRR after tax", pct(k["irr_after_tax"])),
        ("Peak funding need", money(k["peak_funding_need"], currency)),
        ("Payback", month_label(k["payback_month"])),
    ]
    offer = Offer.model_validate(land.offer or {})
    cond = LandConditions.model_validate(land.conditions or {})
    loc = ", ".join(x for x in (land.area_name, land.region, land.governorate) if x)
    facts = [("Location", loc or "n/a"),
             ("Land area", f"{land.area_m2:,.0f} m2 ({land.area_m2 / FEDDAN_M2:,.1f} feddan)")]
    if land.frontage_m:
        facts.append(("Frontage", f"{land.frontage_m:,.0f} m{(' on ' + land.frontage_type) if land.frontage_type else ''}"))
    if offer.deal_type:
        facts.append(("Deal offered", offer.deal_type.replace("_", " ")))
    if offer.asking_price_per_feddan:
        facts.append(("Asking price", f"{money(offer.asking_price_per_feddan, currency)} per feddan"))
    if cond.primary_use:
        facts.append(("Primary use", cond.primary_use.replace("_", " ")))
    if cond.far:
        facts.append(("Floor area ratio", f"{cond.far:g}"))
    if cond.footprint_pct:
        facts.append(("Coverage", pct(cond.footprint_pct)))
    if cond.max_floors:
        facts.append(("Maximum floors", f"{cond.max_floors:g}"))
    facts.append(("Built area in this study", f"{result['built_area_m2']:,.0f} m2 "
                                              f"(allowed {result['allowed_built_area_m2']:,.0f} m2)"))

    prog_head = ["Product", "Mode", "Built m2", "Sellable m2", "Units", "Avg price / m2", "Contract value", "Delivery"]
    prog = []
    for p in result["products"]:
        prog.append([p["name"], p["mode"], f"{p['built_area_m2']:,.0f}", f"{p['sellable_area_m2']:,.0f}",
                     f"{p['units']:,.0f}" if p["mode"] == "sell" else "-",
                     f"{p['average_price_per_m2']:,.0f}" if p["mode"] == "sell" else "-",
                     money(p["contracted_value"]) if p["mode"] == "sell" else "-",
                     f"month {p['delivery_month']}"])

    y = result["yearly"]
    inflow, dev, landc = _sum_lines(y, INFLOW), _sum_lines(y, DEV), _sum_lines(y, LAND)
    tax = y.get("income_tax", [0.0] * len(inflow))
    net_after = y.get("cash_flow_after_tax", [])
    n = max(len(inflow), len(net_after))
    pad = lambda v: list(v) + [0.0] * (n - len(v))
    cash_rows = [("Inflows", pad(inflow)), ("Development costs", pad(dev)), ("Land", pad(landc)),
                 ("Income tax", pad(tax)), ("Net cash flow after tax", pad(net_after))]
    net_pre = pad(y.get("cash_flow_pre_tax", []))

    market_rows = []
    if market_stats and market_stats.get("by_type"):
        for t, s in market_stats["by_type"].items():
            pp = s["price_per_m2"]
            dp, yrs = s["down_payment_pct"], s["instalment_years"]
            market_rows.append([t, str(s["count"]),
                                f"{pp['median']:,.0f}" if pp else "n/a",
                                f"{pp['min']:,.0f} - {pp['max']:,.0f}" if pp else "n/a",
                                pct(dp["median"]) if dp else "n/a",
                                f"{yrs['median']:g}" if yrs else "n/a"])

    costs = study_data.get("costs", {})
    tax_in = study_data.get("tax", {})
    assumptions = [
        ("Discount rate (target return)", pct(rate)),
        ("Cost inflation", pct(costs.get("cost_inflation_annual", 0.0))),
        ("Contingency", pct(costs.get("contingency_pct", 0.0))),
        ("Income tax", pct(tax_in.get("income_tax_rate", 0.0))),
        ("Project length", f"{result['months']} months"),
        ("Time convention", "Month 1 is the land deal; cash flows are monthly, nominal, and summed to years."),
    ]
    return ReportData(
        title=study.name,
        subtitle=f"{land.name}{(' - ' + loc) if loc else ''}",
        prepared=f"{date.today():%d %B %Y}" + (f" - prepared by {prepared_by}" if prepared_by else ""),
        currency=currency, headline=headline, land_facts=facts,
        program_header=prog_head, program_rows=prog,
        years=[f"Y{i + 1}" for i in range(n)], cash_rows=cash_rows,
        net_by_year=net_pre, cumulative=result.get("cumulative_yearly", []),
        scenarios=scenarios or [], residual=residual, market=market_rows,
        assumptions=assumptions, warnings=list(result.get("warnings", [])),
    )
