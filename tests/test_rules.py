"""Rule tests: properties that must hold for any study.

They follow the eight principles of the specification. Random studies are
made by varying a study that has every feature switched on.
"""
import copy

import numpy as np
import pytest
from hypothesis import HealthCheck, given, settings, strategies as st
from pydantic import ValidationError

from feasibility import ConditionError, Study, npv, residual_land_value, run, shocked
from helpers import rich, set_path

SETTINGS = settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow])

# Inputs that the random studies vary, with the range of each.
VARIED = {
    "products.0.launch_price_per_m2": st.floats(20_000, 200_000),
    "products.0.price_escalation_annual": st.floats(0.0, 0.30),
    "products.0.sales_months": st.integers(1, 96),
    "products.0.launch_month": st.integers(1, 40),
    "products.0.sellable_share": st.floats(0.5, 1.0),
    "products.0.hard_cost_per_m2": st.floats(0, 60_000),
    "products.1.units_per_month": st.floats(0.3, 20),
    "products.3.launch_price_per_m2": st.floats(20_000, 200_000),
    "plans.1.down_payment": st.floats(0.0, 0.5),
    "plans.1.delivery_payment": st.floats(0.0, 0.4),
    "plans.1.instalment_count": st.integers(1, 48),
    "plans.1.instalment_every_months": st.integers(1, 12),
    "phases.0.duration_months": st.integers(1, 50),
    "phases.1.start_month": st.integers(1, 16),
    "sales_risk.cancellation_rate": st.floats(0.0, 0.5),
    "sales_risk.cancel_after_months": st.integers(1, 40),
    "sales_risk.cancellation_penalty_pct": st.floats(0.0, 1.0),
    "sales_risk.resale_delay_months": st.integers(0, 24),
    "sales_risk.unsold_share": st.floats(0.0, 0.5),
    "sales_risk.clear_unsold": st.booleans(),
    "costs.cost_inflation_annual": st.floats(0.0, 0.35),
    "costs.contingency_pct": st.floats(0.0, 0.2),
    "costs.overhead_monthly": st.floats(0, 5_000_000),
    "land.cash.price_per_m2": st.floats(0, 40_000),
    "land.cash.interest_annual": st.floats(0.0, 0.25),
    "land.in_kind_share": st.floats(0.0, 0.5),
    "land.revenue_share": st.floats(0.0, 0.4),
    "land.partnership.owner_share": st.floats(0.0, 0.9),
    "land.partnership.basis": st.sampled_from(["profit", "revenue"]),
    "finance.discount_rate_annual": st.floats(0.0, 0.40),
    "finance.debt_limit": st.floats(0, 3_000_000_000),
    "finance.debt_interest_annual": st.floats(0.0, 0.35),
    "tax.income_tax_rate": st.floats(0.0, 0.4),
}


@st.composite
def studies(draw):
    d = rich()
    for path, strat in VARIED.items():
        set_path(d, path, draw(strat))
    d["conditions"]["build_deadline_months"] = None
    return d


def close(a, b, scale=None):
    scale = max(1.0, abs(a), abs(b)) if scale is None else scale
    return abs(a - b) <= 1e-7 * scale


# ---------------------------------------------------------------- rules 1 and 2: time

@SETTINGS
@given(studies())
def test_periods_are_sums_of_months_and_nothing_is_cut_off(d):
    r = run(Study.model_validate(d))
    m, q, y = r.table("month"), r.table("quarter"), r.table("year")
    for line, v in m.items():
        scale = max(1.0, float(np.abs(v).sum()))
        assert close(q[line].sum(), v.sum(), scale) and close(y[line].sum(), v.sum(), scale)
        assert len(q[line]) == -(-len(v) // 3) and len(y[line]) == -(-len(v) // 12)
    # No horizon: every contract is collected in full, however long the plan runs.
    assert r.checks["collections_match_contracts"]
    sale_cash = r.lines["collections"].sum() + r.lines.get("cancellation_refunds", np.zeros(1)).sum()
    final = sum(p.contracted_value for p in r.products)
    assert sale_cash >= final * (1 - 1e-9)          # the only extra is cancellation penalties


# ---------------------------------------------------------------- rule 5: applied once

@SETTINGS
@given(studies())
def test_each_adjustment_is_applied_once(d):
    r = run(Study.model_validate(d))
    s = Study.model_validate(d)
    # Contingency and soft costs are exactly their percentage of their base.
    base = r.lines["hard_cost"].sum() + r.lines["site_cost"].sum()
    assert close(r.lines.get("contingency", np.zeros(1)).sum(), s.costs.contingency_pct * base, abs(base))
    # Contracted sales net of cancellations equal the final contracts.
    final = sum(p.contracted_value for p in r.products)
    assert close(r.memo["contracted_sales"].sum(), final)
    # Sold area never exceeds sellable area, and equals it when unsold stock is cleared.
    sellable = sum(p.sellable_area_m2 for p in r.products if p.mode == "sell")
    sold = r.memo["area_sold"].sum()
    if s.sales_risk.clear_unsold:
        assert close(sold, sellable)
    else:
        assert close(sold, sellable * (1 - s.sales_risk.unsold_share))


# ---------------------------------------------------------------- rule 6 and 7: money and returns

@SETTINGS
@given(studies())
def test_cash_flow_identities(d):
    r = run(Study.model_validate(d))
    k = r.kpis
    scale = max(1.0, k["revenue"])
    assert close(k["profit_pre_tax"], k["revenue"] - k["development_cost"] - k["land_cost"], scale)
    assert close(k["profit_after_tax"], k["profit_pre_tax"] - k["income_tax"], scale)
    assert close(npv(r.cash_flow_pre_tax, 0.0), k["profit_pre_tax"], scale)
    assert k["income_tax"] >= 0
    # Equity gets the after-tax cash flow less interest; debt is fully repaid.
    interest = -r.lines["debt_interest"].sum() if "debt_interest" in r.lines else 0.0
    assert close(r.equity_cash_flow.sum(), k["profit_after_tax"] - interest, scale)
    assert r.debt_balance[-1] == 0 and r.debt_balance.max() <= d["finance"]["debt_limit"] * (1 + 1e-9) + 1e-6
    # A reported IRR really does bring NPV to zero (measured against the
    # discounted size of the flows, which is the fair yardstick at extreme rates).
    for cf, res in ((r.cash_flow_pre_tax, r.irr_pre_tax), (r.cash_flow_after_tax, r.irr_after_tax),
                    (r.equity_cash_flow, r.irr_equity)):
        if res.status == "ok":
            assert abs(npv(cf, res.annual)) <= 1e-6 * npv(np.abs(cf), res.annual)


@SETTINGS
@given(studies(), st.floats(1.01, 3.0))
def test_costs_never_help_and_prices_never_hurt(d, f):
    base = run(Study.model_validate(d)).kpis
    tol = 1e-7 * max(1.0, base["revenue"])
    for shock in ("cost", "land_price"):
        k = run(shocked(Study.model_validate(d), shock, f)).kpis
        assert k["npv_pre_tax"] <= base["npv_pre_tax"] + tol
        assert k["npv_after_tax"] <= base["npv_after_tax"] + tol
    k = run(shocked(Study.model_validate(d), "price", f)).kpis
    assert k["npv_pre_tax"] >= base["npv_pre_tax"] - tol
    assert k["npv_after_tax"] >= base["npv_after_tax"] - tol


# ---------------------------------------------------------------- rule 8 and generality

@SETTINGS
@given(studies(), st.floats(0.01, 100.0))
def test_scaling_the_land_scales_every_amount_and_leaves_returns_alone(d, f):
    """A land f times the size, on the same terms, is the same project f times over.
    This is what makes the engine general across plots."""
    a = run(Study.model_validate(d))
    big = copy.deepcopy(d)
    for p in big["parcels"]:
        p["area_m2"] *= f
    big["conditions"]["built_area_m2"] *= f
    big["products"][1]["units_per_month"] *= f
    big["costs"]["overhead_monthly"] *= f
    big["finance"]["debt_limit"] *= f
    b = run(Study.model_validate(big))
    assert a.months == b.months
    for name in ("cash_flow_pre_tax", "cash_flow_after_tax", "equity_cash_flow"):
        x, y = getattr(a, name), getattr(b, name)
        assert np.allclose(x * f, y, rtol=1e-7, atol=1e-7 * f * max(1.0, np.abs(x).max()))
    assert close(a.built_area_m2 * f, b.built_area_m2)
    assert close(a.kpis["units"] * f, b.kpis["units"])
    for key in ("irr_pre_tax", "irr_after_tax", "equity_irr", "margin_on_revenue"):
        if a.kpis[key] is None or b.kpis[key] is None:
            assert a.kpis[key] == b.kpis[key]
        else:
            assert abs(a.kpis[key] - b.kpis[key]) <= 1e-6


@SETTINGS
@given(studies())
def test_area_is_conserved(d):
    r = run(Study.model_validate(d))
    assert close(sum(p.built_area_m2 for p in r.products), r.built_area_m2)
    assert r.built_area_m2 <= r.allowed_built_area_m2 * (1 + 1e-9)
    s = Study.model_validate(d)
    for p, q in zip(r.products, s.products):
        assert p.sellable_area_m2 <= p.built_area_m2 * (1 + 1e-12)
        assert close(p.units * q.unit_size_m2, p.sellable_area_m2)


def test_products_can_never_exceed_the_allowed_built_area():
    d = rich()
    d["conditions"]["built_area_m2"] = 105_000.01     # allowed is 105,000 (footprint x floors)
    with pytest.raises(ConditionError):
        run(Study.model_validate(d))
    d = rich()
    d["products"][0]["bua_share"] = 0.60              # shares would total 105%
    with pytest.raises(ValidationError):
        Study.model_validate(d)


# ---------------------------------------------------------------- residual land value

@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(studies())
def test_residual_land_value_sets_npv_to_zero(d):
    s = Study.model_validate(d)
    res = residual_land_value(s)
    d2 = s.model_dump()
    d2["land"]["cash"]["price_per_m2"] = res.price_per_m2
    k = run(Study.model_validate(d2)).kpis
    if res.feasible:
        assert abs(k["npv_pre_tax"]) <= 1e-6 * max(1.0, k["revenue"])
    else:
        assert k["npv_pre_tax"] < 0        # even free land misses the target


# ---------------------------------------------------------------- rules 3 and 4: inputs

# Limits do not move a result; they refuse a study that breaks them. Tested below.
LIMITS = ("conditions.far_cap", "conditions.footprint_pct", "conditions.height_zones",
          "conditions.min_share_by_category", "conditions.max_share_by_category",
          "conditions.build_deadline_months")
# Shares that must total 100% cannot be changed one at a time.
TIED = ("bua_share", "plan_mix", "share_of_footprint", "schedule")
# Fields that do not apply to that item, where the model refuses any other value:
# sale inputs on the leased product, and instalment terms on the pay-in-full cash plan.
NOT_APPLICABLE = ("products.2.price_escalation_annual", "products.2.launch_month",
                  "products.2.transaction_tax_pct", "plans.0.down_payment",
                  "plans.0.delivery_payment", "plans.0.instalment_count",
                  "plans.0.instalment_every_months")


def _leaves(node, path=""):
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _leaves(v, f"{path}.{k}" if path else k)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _leaves(v, f"{path}.{i}")
    elif isinstance(node, (bool, int, float)):
        yield path, node


def _fingerprint(r):
    return (r.cash_flow_pre_tax, r.cash_flow_after_tax, r.equity_cash_flow,
            np.array([r.kpis["npv_pre_tax"], r.kpis["npv_after_tax"], r.built_area_m2]),
            np.array([p.units for p in r.products]))


def _same(a, b):
    return all(len(x) == len(y) and np.allclose(x, y, rtol=1e-12, atol=1e-9) for x, y in zip(a, b))


def test_every_input_changes_a_result():
    """Principle 4: change each number in a full study, one at a time. The
    result must move. A number that moves nothing is a dead input."""
    d = Study.model_validate(rich()).model_dump()
    base = _fingerprint(run(Study.model_validate(d)))
    dead, tested = [], 0
    for path, value in _leaves(d):
        if path.startswith(LIMITS) or path in NOT_APPLICABLE or any(t in path.split(".") for t in TIED):
            continue
        if isinstance(value, bool):
            trials = [not value]
        elif isinstance(value, int):
            trials = [value + 1, value - 1]
        else:
            trials = [value * 1.1, value * 0.9] if value else [0.01]
        for new in trials:
            e = copy.deepcopy(d)
            set_path(e, path, new)
            try:
                r = run(Study.model_validate(e))
            except (ValidationError, ConditionError):
                continue
            tested += 1
            if _same(base, _fingerprint(r)):
                dead.append(path)
            break
        else:
            dead.append(path + " (could not be varied)")
    assert not dead, f"dead inputs: {dead}"
    assert tested > 100


def test_every_limit_refuses_a_study_that_breaks_it():
    for path, value in (("conditions.far_cap", 0.89),                 # 89,000 m2 < 90,000 built
                        ("conditions.footprint_pct", 0.20),           # 84,000 m2
                        ("conditions.height_zones.1.floors", 3),      # 75,000 m2
                        ("conditions.min_share_by_category", {"hotel": 0.11}),
                        ("conditions.max_share_by_category", {"residential": 0.74}),
                        ("conditions.build_deadline_months", 53)):
        d = rich()
        set_path(d, path, value)
        with pytest.raises(ConditionError):
            run(Study.model_validate(d))
    run(Study.model_validate(rich()))   # and the unbroken study runs


def test_no_input_is_entered_twice():
    """Principle 3: unknown or duplicate fields are refused, so a number cannot
    be given a second home by accident."""
    d = rich()
    d["costs"]["contingency_percent"] = 0.15
    with pytest.raises(ValidationError):
        Study.model_validate(d)
