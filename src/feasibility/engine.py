"""The monthly feasibility engine.

`run(study)` follows the thirteen steps of the calculation specification and
returns a `Result`. Internally months are 0-based (index 0 is month 1).
Inflows are positive and outflows negative on every line, so the project cash
flow is simply the sum of the lines.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import curves
from .inputs import FEDDAN_M2, Product, Study
from .returns import IRR, irr, npv


class ConditionError(ValueError):
    """The study breaks one of the land's conditions."""


class CheckFailed(RuntimeError):
    """An internal consistency check failed. The result is withheld."""


# Lines that make up the project cash flow, in reporting order.
REVENUE_LINES = ("collections", "cancellation_refunds", "rent", "lease_exit")
DEV_COST_LINES = ("hard_cost", "site_cost", "soft_cost", "contingency", "marketing",
                  "commission", "transaction_tax", "overhead", "lease_opex")
LAND_LINES = ("land_cash", "land_interest", "land_fees", "land_revenue_share",
              "land_partnership_share")
PRE_TAX_LINES = REVENUE_LINES + DEV_COST_LINES + LAND_LINES
FINANCE_LINES = ("debt_draw", "debt_repayment", "debt_interest")


class Ledger:
    """Named monthly lines that grow as far as the cash flows reach."""

    def __init__(self):
        self.lines: dict[str, np.ndarray] = {}

    def add(self, line: str, start: int, values) -> None:
        v = np.atleast_1d(np.asarray(values, dtype=float))
        end = start + len(v)
        cur = self.lines.get(line)
        if cur is None:
            cur = np.zeros(end)
        elif len(cur) < end:
            cur = np.pad(cur, (0, end - len(cur)))
        cur[start:end] += v
        self.lines[line] = cur

    def add_at(self, line: str, idx: np.ndarray, values: np.ndarray) -> None:
        if len(idx) == 0:
            return
        out = np.zeros(int(idx.max()) + 1)
        np.add.at(out, idx, values)
        self.add(line, 0, out)

    def get(self, line: str, n: int) -> np.ndarray:
        cur = self.lines.get(line, np.zeros(0))
        return np.pad(cur, (0, n - len(cur))) if len(cur) < n else cur[:n].copy()

    def horizon(self) -> int:
        n = 1
        for v in self.lines.values():
            nzi = np.flatnonzero(v)
            if len(nzi):
                n = max(n, int(nzi[-1]) + 1)
        return n


@dataclass
class ProductSummary:
    name: str
    category: str
    mode: str
    built_area_m2: float
    sellable_area_m2: float      # the developer's part, after any in-kind share
    units: float
    delivery_month: int
    contracted_value: float = 0.0   # final contracts, after cancellations and resales
    hard_cost: float = 0.0
    average_price_per_m2: float = 0.0


@dataclass
class Result:
    study_name: str
    months: int
    land_area_m2: float
    allowed_built_area_m2: float
    built_area_m2: float
    products: list[ProductSummary]
    lines: dict[str, np.ndarray]           # cash lines, signed
    memo: dict[str, np.ndarray]            # non-cash lines (contracted sales, area sold)
    cash_flow_pre_tax: np.ndarray
    income_tax: np.ndarray
    cash_flow_after_tax: np.ndarray
    equity_cash_flow: np.ndarray
    debt_balance: np.ndarray
    kpis: dict
    irr_pre_tax: IRR
    irr_after_tax: IRR
    irr_equity: IRR
    checks: dict[str, bool] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def table(self, period: str = "month") -> dict[str, np.ndarray]:
        """Cash flow by line, summed to months, quarters or years."""
        step = {"month": 1, "quarter": 3, "year": 12}[period]
        rows = dict(self.lines)
        rows["income_tax"] = self.income_tax
        rows["cash_flow_pre_tax"] = self.cash_flow_pre_tax
        rows["cash_flow_after_tax"] = self.cash_flow_after_tax
        rows["equity_cash_flow"] = self.equity_cash_flow
        return {k: _bucket(v, step) for k, v in rows.items()}


def _bucket(v: np.ndarray, step: int) -> np.ndarray:
    if step == 1:
        return v.copy()
    pad = (-len(v)) % step
    return np.pad(v, (0, pad)).reshape(-1, step).sum(axis=1)


def _growth(annual: float, idx) -> np.ndarray:
    return (1.0 + annual) ** (np.asarray(idx, dtype=float) / 12.0)


# ---------------------------------------------------------------- step 1: areas

def allowed_built_area(study: Study) -> float:
    """The most that may be built: the smallest of the caps the conditions give."""
    c, land = study.conditions, study.land_area_m2
    caps = []
    if c.far_cap is not None:
        caps.append(land * c.far_cap)
    if c.height_zones:
        caps.append(land * c.footprint_pct
                    * sum(z.share_of_footprint * z.floors for z in c.height_zones))
    if not caps:
        return float(c.built_area_m2)
    return min(caps)


def _areas(study: Study) -> tuple[float, float, dict[str, float]]:
    c = study.conditions
    allowed = allowed_built_area(study)
    built = allowed if c.built_area_m2 is None else float(c.built_area_m2)
    if built > allowed * (1 + 1e-9):
        raise ConditionError(f"built area {built:,.0f} m2 exceeds the allowed {allowed:,.0f} m2")
    by_cat: dict[str, float] = {}
    for p in study.products:
        by_cat[p.category] = by_cat.get(p.category, 0.0) + p.bua_share
    for cat, lo in c.min_share_by_category.items():
        if by_cat.get(cat, 0.0) < lo - 1e-9:
            raise ConditionError(f"{cat} is {by_cat.get(cat, 0.0):.1%} of built area; "
                                 f"the conditions require at least {lo:.1%}")
    for cat, hi in c.max_share_by_category.items():
        if by_cat.get(cat, 0.0) > hi + 1e-9:
            raise ConditionError(f"{cat} is {by_cat.get(cat, 0.0):.1%} of built area; "
                                 f"the conditions allow at most {hi:.1%}")
    return allowed, built, {p.name: built * p.bua_share for p in study.products}


# ---------------------------------------------------------------- steps 2-6: sales

def premium_factor(p: Product) -> float:
    """Average price factor from per-unit premiums.

    Each unit's price is the base price times (1 + uplift) for every premium
    that unit carries. With a unit list this is the exact average over the
    units. Without one, each premium is assumed to fall on its share of units
    independently of the others.
    """
    up = {q.name: q.uplift for q in p.premiums}
    if p.units:
        return float(np.mean([np.prod([1.0 + up[n] for n in u.premiums]) for u in p.units]))
    f = 1.0
    for q in p.premiums:
        f *= 1.0 + q.share_of_units * q.uplift
    return f


def price_per_m2(p: Product, idx) -> np.ndarray:
    """Average price per m2 of the product in each month index, before plan adjustment."""
    idx = np.asarray(idx, dtype=float)
    launch = p.launch_month - 1
    price = p.launch_price_per_m2 * premium_factor(p) * _growth(p.price_escalation_annual, idx - launch)
    for r in p.releases:
        price = np.where(idx >= r.from_month - 1, price * (1.0 + r.step), price)
    return price


def _plan_schedule(plan) -> np.ndarray:
    """Share of the contract due at each month after sale, excluding the delivery payment."""
    if plan.manual_schedule:
        n = max(q.months_after_sale for q in plan.manual_schedule) + 1
        s = np.zeros(n)
        for q in plan.manual_schedule:
            s[q.months_after_sale] += q.share
        s[0] += plan.down_payment
        return s
    if plan.instalment_count == 0:
        return np.array([plan.down_payment])
    first = plan.first_instalment_after_months or plan.instalment_every_months
    offs = first + plan.instalment_every_months * np.arange(plan.instalment_count)
    s = np.zeros(int(offs[-1]) + 1)
    s[0] = plan.down_payment
    s[offs] += (1.0 - plan.down_payment - plan.delivery_payment) / plan.instalment_count
    return s


def _sales_profile(study: Study, p: Product, area: float) -> tuple[np.ndarray, np.ndarray]:
    """Area sold per month from month 1, and the discount factor on each month's price."""
    risk = study.sales_risk
    launch = p.launch_month - 1
    planned = area * (1.0 - risk.unsold_share)
    if p.units_per_month is not None:
        rate = p.units_per_month * p.unit_size_m2
        n = max(1, int(np.ceil(planned / rate - 1e-9)))
        w = np.full(n, rate)
        w[-1] = planned - rate * (n - 1)
    else:
        w = planned * curves.weights(p.sales_curve, p.sales_months)
    sold = np.concatenate([np.zeros(launch), w])
    factor = np.ones(len(sold))
    if risk.unsold_share > 0 and risk.clear_unsold:
        k = risk.clearance_months
        sold = np.concatenate([sold, np.full(k, area * risk.unsold_share / k)])
        factor = np.concatenate([factor, np.full(k, 1.0 - risk.clearance_discount)])
    return sold, factor


def _collect(led: Ledger, value: np.ndarray, sched: np.ndarray, delivery_share: float,
             delivery_idx: int, cancel_after: int | None = None) -> np.ndarray:
    """Post the collections on contracts signed each month; return cash collected per vintage.

    If `cancel_after` is set, only payments due before that many months after
    sale are collected (the contracts cancel then).
    """
    s = sched if cancel_after is None else sched[:cancel_after]
    led.add("collections", 0, np.convolve(value, s))
    collected = value * s.sum()
    if delivery_share > 0:
        sale_idx = np.arange(len(value))
        due = np.maximum(sale_idx, delivery_idx)
        paid = np.ones(len(value), bool) if cancel_after is None else due < sale_idx + cancel_after
        led.add_at("collections", due[paid], value[paid] * delivery_share)
        collected = collected + np.where(paid, value * delivery_share, 0.0)
    return collected


def _sell(study: Study, p: Product, area: float, delivery_idx: int,
          led: Ledger, memo: Ledger, summary: ProductSummary, recon: dict) -> np.ndarray:
    """Post one product's sales cash; return its contracted sales by month,
    net of cancellations (a memo line, not cash)."""
    risk = study.sales_risk
    own = Ledger()
    plans = {pl.name: pl for pl in study.plans}
    sold, factor = _sales_profile(study, p, area)
    n = len(sold)
    idx = np.arange(n)
    base_value = sold * price_per_m2(p, idx) * factor
    memo.add("area_sold", 0, sold)
    c, k = risk.cancellation_rate, risk.cancel_after_months
    final_value = 0.0
    for name, mix in p.plan_mix.items():
        if mix == 0:
            continue
        plan = plans[name]
        sched = _plan_schedule(plan)
        adj = 1.0 + plan.price_adjustment
        value = base_value * mix * adj
        own.add("contracted_sales", 0, value)
        # Contracts that hold.
        _collect(led, value * (1 - c), sched, plan.delivery_payment, delivery_idx)
        final_value += float(value.sum()) * (1 - c)
        if c > 0:
            # Contracts that cancel: collect until cancellation, refund less the penalty.
            collected = _collect(led, value * c, sched, plan.delivery_payment, delivery_idx, k)
            led.add("cancellation_refunds", k, -collected * (1 - risk.cancellation_penalty_pct))
            recon["penalties"] += float(collected.sum()) * risk.cancellation_penalty_pct
            own.add("contracted_sales", k, -value * c)
            # Resale at the price of the resale month, on a fresh plan.
            shift = k + risk.resale_delay_months
            r_idx = idx + shift
            resale = (sold * c * mix * adj * price_per_m2(p, r_idx) * (1 - risk.resale_discount))
            padded = np.concatenate([np.zeros(shift), resale])
            _collect(led, padded, sched, plan.delivery_payment, delivery_idx)
            own.add("contracted_sales", 0, padded)
            final_value += float(resale.sum())
    summary.contracted_value = final_value
    final_area = float(sold.sum())
    summary.average_price_per_m2 = final_value / final_area if final_area else 0.0
    recon["final_contracts"] += final_value
    net = own.get("contracted_sales", len(own.lines["contracted_sales"]))
    memo.add("contracted_sales", 0, net)
    return net


# ---------------------------------------------------------------- step 7: leased products

def _lease(study: Study, p: Product, area: float, delivery_idx: int, led: Ledger) -> None:
    L = p.lease
    start = delivery_idx + 1
    n = L.hold_months
    idx = start + np.arange(n + 12)                       # 12 extra months to value the exit
    ramp = np.minimum(1.0, (np.arange(n + 12) + 1) / L.ramp_months) if L.ramp_months else np.ones(n + 12)
    growth = (1.0 + L.rent_growth_annual) ** (idx // 12)   # steps up once a year
    full_rent = area * L.rent_per_m2_month * growth * L.stable_occupancy
    rent = full_rent * ramp
    led.add("rent", start, rent[:n])
    led.add("lease_opex", start, -rent[:n] * L.opex_pct_of_rent)
    forward_noi = float(full_rent[n:].sum()) * (1 - L.opex_pct_of_rent)
    led.add("lease_exit", start + n - 1, forward_noi / L.exit_cap_rate * (1 - L.exit_cost_pct))


# ---------------------------------------------------------------- step 10: land

def _land_cash(study: Study, led: Ledger) -> None:
    cash = study.land.cash
    if cash is None:
        return
    price = cash.price_per_m2 * study.land_area_m2 * cash.area_share
    fx = lambda i: (1 - cash.fx_share) + cash.fx_share * (1 + cash.fx_depreciation_annual) ** (i / 12.0)
    down = price * cash.down_payment_pct
    led.add("land_cash", 0, -down * fx(0))
    if cash.instalment_count == 0:
        return
    first = cash.first_instalment_after_months or cash.instalment_every_months
    due = first + cash.instalment_every_months * np.arange(cash.instalment_count)
    principal = (price - down) / cash.instalment_count
    i_m = (1 + cash.interest_annual) ** (1 / 12.0) - 1
    balance, last = price - down, 0
    for d in due:
        d = int(d)
        interest = balance * i_m * (d - last)   # accrued on the unpaid balance since the last payment
        led.add("land_cash", d, -principal * fx(d))
        led.add("land_interest", d, -interest * fx(d))
        balance -= principal
        last = d


# ---------------------------------------------------------------- the run

def run(study: Study) -> Result:
    led, memo = Ledger(), Ledger()
    land = study.land_area_m2
    warnings: list[str] = []

    # 1. Areas
    allowed, built, bua = _areas(study)
    phases = {ph.name: ph for ph in study.phases}
    delivery = {name: ph.start_month - 1 + ph.duration_months - 1 for name, ph in phases.items()}
    used_phases = {p.phase for p in study.products}
    last_delivery = max(delivery[n] for n in used_phases)
    dl = study.conditions.build_deadline_months
    if dl is not None and last_delivery + 1 > dl:
        raise ConditionError(f"construction ends in month {last_delivery + 1}; "
                             f"the conditions require completion by month {dl}")

    # 2-7. Sales, collections, cancellations, unsold stock, leases
    recon = {"final_contracts": 0.0, "penalties": 0.0}
    summaries: list[ProductSummary] = []
    cost = study.costs
    for p in study.products:
        area = bua[p.name] * p.sellable_share * (1 - study.land.in_kind_share)
        s = ProductSummary(p.name, p.category, p.mode, bua[p.name], area,
                           area / p.unit_size_m2, delivery[p.phase] + 1)
        if p.mode == "sell":
            own = _sell(study, p, area, delivery[p.phase], led, memo, s, recon)
            # 9. Costs that follow this product's contracted sales, net of cancellations.
            led.add("marketing", 0, -own * cost.marketing_pct_of_sales)
            led.add("commission", 0, -own * cost.commission_pct_of_sales)
            led.add("transaction_tax", 0, -own * p.transaction_tax_pct)
        else:
            _lease(study, p, area, delivery[p.phase], led)
        # 8. Construction
        ph = phases[p.phase]
        start = ph.start_month - 1
        w = curves.weights(ph.spend_curve, ph.duration_months)
        spend = bua[p.name] * p.hard_cost_per_m2 * w * _growth(
            cost.cost_inflation_annual, start + np.arange(ph.duration_months))
        led.add("hard_cost", start, -spend)
        s.hard_cost = float(spend.sum())
        summaries.append(s)

    for sc in cost.site_costs:
        start = sc.start_month - 1
        w = curves.weights(sc.spend_curve, sc.duration_months)
        led.add("site_cost", start, -land * sc.rate_per_m2_land * w * _growth(
            cost.cost_inflation_annual, start + np.arange(sc.duration_months)))

    # 9. Other costs
    n0 = led.horizon()
    building = led.get("hard_cost", n0)
    both = building + led.get("site_cost", n0)
    base = {"building": building, "building_and_site": both}
    for sc in cost.soft_costs:
        led.add("soft_cost", 0, base[sc.base] * sc.pct)
    led.add("contingency", 0, base[cost.contingency_base] * cost.contingency_pct)
    if cost.overhead_monthly > 0:
        area_sold = memo.lines.get("area_sold", np.zeros(1))
        last_sale = int(np.flatnonzero(area_sold)[-1]) if np.any(area_sold) else 0
        end = cost.overhead_end_month - 1 if cost.overhead_end_month else max(last_delivery, last_sale)
        start = cost.overhead_start_month - 1
        if end >= start:
            i = np.arange(start, end + 1)
            led.add("overhead", start, -cost.overhead_monthly * _growth(cost.cost_inflation_annual, i))

    # 10. Land
    _land_cash(study, led)
    for fee in study.land.fees:
        for pay in fee.schedule:
            led.add("land_fees", pay.month - 1, -fee.amount_per_m2_land * land * pay.share)
    n1 = led.horizon()
    revenue = sum(led.get(k, n1) for k in REVENUE_LINES)
    dev_cost = sum(led.get(k, n1) for k in DEV_COST_LINES)
    if study.land.revenue_share > 0:
        led.add("land_revenue_share", 0, -revenue * study.land.revenue_share)
    ps = study.land.partnership
    if ps is not None:
        if ps.basis == "revenue":
            led.add("land_partnership_share", 0, -revenue * ps.area_share * ps.owner_share)
        else:
            # Profit split: the shared part's costs are funded first. Its net
            # cash is divided once the running total is positive.
            cum = np.cumsum((revenue + dev_cost) * ps.area_share)
            peak = np.maximum.accumulate(np.maximum(cum, 0.0))
            led.add("land_partnership_share", 0, -ps.owner_share * np.diff(peak, prepend=0.0))
            if cum[-1] < peak[-1] - 1e-6 * max(1.0, peak[-1]):
                warnings.append("Partnership: the shared part's cash peaks and then falls, so the "
                                "owner has been paid on a profit that later shrank.")

    # 11-12. Taxes and project cash flow
    n = led.horizon()
    lines = {k: led.get(k, n) for k in PRE_TAX_LINES if k in led.lines}
    pre_tax = sum(lines.values()) if lines else np.zeros(n)
    tax = _income_tax(pre_tax, study.tax.income_tax_rate, study.tax.payment_lag_months)
    n = max(n, len(tax))
    pad = lambda v: np.pad(v, (0, n - len(v)))
    lines = {k: pad(v) for k, v in lines.items()}
    pre_tax, tax = pad(pre_tax), pad(tax)
    after_tax = pre_tax + tax

    # 13. Financing
    fin = _finance(after_tax, study.finance.debt_limit, study.finance.debt_interest_annual)
    lines.update({k: fin[k] for k in FINANCE_LINES if np.any(fin[k])})

    # Checks
    rev_total = float(revenue.sum())
    lease_total = float(led.get("rent", n).sum() + led.get("lease_exit", n).sum())
    sale_cash = rev_total - lease_total
    expect = recon["final_contracts"] + recon["penalties"]
    scale = max(1.0, abs(expect))
    checks = {
        "area_conserved": abs(sum(bua.values()) - built) <= 1e-6 * max(1.0, built) and built <= allowed * (1 + 1e-9),
        "collections_match_contracts": abs(sale_cash - expect) <= 1e-9 * scale,
        "lines_sum_to_total": bool(np.allclose(sum(lines[k] for k in lines if k not in FINANCE_LINES),
                                                pre_tax, rtol=0, atol=1e-6 * max(1.0, np.abs(pre_tax).max()))),
        "debt_repaid": abs(fin["balance"][-1]) <= 1e-6 * max(1.0, study.finance.debt_limit),
    }
    checks = {k: bool(v) for k, v in checks.items()}
    failed = [k for k, ok in checks.items() if not ok]
    if failed:
        raise CheckFailed("failed checks: " + ", ".join(failed))

    rate = study.finance.discount_rate_annual
    r_pre, r_post, r_eq = irr(pre_tax), irr(after_tax), irr(fin["equity"])
    for label, r in (("project", r_pre), ("after-tax", r_post), ("equity", r_eq)):
        if r.status == "multiple":
            warnings.append(f"The {label} cash flow has more than one IRR "
                            f"({', '.join(f'{x:.1%}' for x in r.candidates)}); none is reported.")
        elif len(r.candidates) > 1:
            other = ", ".join(f"{x:.1%}" for x in r.candidates if x != r.annual)
            warnings.append(f"The {label} cash flow also has NPV of zero at {other}. The reported IRR "
                            f"is the only one on the same side of zero as the project's result.")
    cum = np.cumsum(after_tax)
    trough = int(np.argmin(cum))
    never_neg_after = np.flatnonzero(np.minimum.accumulate(cum[::-1])[::-1] >= -1e-6)
    payback = int(never_neg_after[0]) + 1 if len(never_neg_after) and cum.min() < 0 else None
    contrib = float(-fin["equity"][fin["equity"] < 0].sum())
    distrib = float(fin["equity"][fin["equity"] > 0].sum())
    eq_cum = np.cumsum(-fin["equity"])
    costs_total = float(-(dev_cost.sum()) - sum(led.get(k, n).sum() for k in LAND_LINES))
    kpis = {
        "revenue": rev_total,
        "development_cost": float(-dev_cost.sum()) + 0.0,
        "land_cost": float(-sum(led.get(k, n).sum() for k in LAND_LINES)) + 0.0,
        "profit_pre_tax": float(pre_tax.sum()),
        "income_tax": float(-tax.sum()) + 0.0,
        "profit_after_tax": float(after_tax.sum()),
        "margin_on_revenue": float(pre_tax.sum()) / rev_total if rev_total else None,
        "margin_on_cost": float(pre_tax.sum()) / costs_total if costs_total else None,
        "npv_pre_tax": npv(pre_tax, rate),
        "npv_after_tax": npv(after_tax, rate),
        "irr_pre_tax": r_pre.annual,
        "irr_after_tax": r_post.annual,
        "peak_funding_need": float(max(0.0, -cum.min())),
        "peak_funding_month": trough + 1 if cum.min() < 0 else None,
        "payback_month": payback,
        "equity_irr": r_eq.annual,
        "equity_multiple": distrib / contrib if contrib else None,
        "peak_equity": float(max(0.0, eq_cum.max())),
        "peak_debt": float(fin["balance"].max()),
        "land_area_feddan": land / FEDDAN_M2,
        "units": float(sum(s.units for s in summaries if s.mode == "sell")),
    }
    memo_n = {k: memo.get(k, max(n, len(v))) for k, v in memo.lines.items()}
    return Result(study.name, n, land, allowed, built, summaries, lines, memo_n,
                  pre_tax, tax, after_tax, fin["equity"], fin["balance"], kpis,
                  r_pre, r_post, r_eq, checks, warnings)


def _income_tax(pre_tax: np.ndarray, rate: float, lag: int) -> np.ndarray:
    """Tax per 12-month year on that year's net cash profit, after carrying
    earlier losses forward, paid `lag` months after the year ends."""
    if rate <= 0:
        return np.zeros(len(pre_tax))
    yearly = _bucket(pre_tax, 12)
    out = np.zeros(len(yearly) * 12 + lag)
    losses = 0.0
    for y, profit in enumerate(yearly):
        taxable = profit - losses
        if taxable > 0:
            out[(y + 1) * 12 - 1 + lag] = -rate * taxable
            losses = 0.0
        else:
            losses = -taxable
    last = np.flatnonzero(out)
    return out[: int(last[-1]) + 1] if len(last) else np.zeros(len(pre_tax))


def _finance(cf: np.ndarray, limit: float, interest_annual: float) -> dict[str, np.ndarray]:
    """Debt fills a cash shortfall up to the limit, equity fills the rest.
    Surplus cash repays debt first and then goes to equity."""
    n = len(cf)
    i_m = (1 + interest_annual) ** (1 / 12.0) - 1
    draw, repay, interest, equity, bal = (np.zeros(n) for _ in range(5))
    b = 0.0
    for t in range(n):
        interest[t] = -b * i_m
        net = cf[t] + interest[t]
        if net < 0:
            d = min(-net, max(0.0, limit - b))
            draw[t] = d
            b += d
            equity[t] = net + d
        else:
            r = min(net, b)
            repay[t] = -r
            b -= r
            equity[t] = net - r
        if t == n - 1 and b > 0:        # anything still owed is settled by equity
            repay[t] -= b
            equity[t] -= b
            b = 0.0
        bal[t] = b
    return {"debt_draw": draw, "debt_repayment": repay, "debt_interest": interest,
            "equity": equity, "balance": bal}
