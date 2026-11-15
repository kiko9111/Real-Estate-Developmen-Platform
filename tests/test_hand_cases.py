"""Cases whose answers are worked out by hand in the test itself.

Each test states the arithmetic in plain numbers. The engine must match to
the pound (relative tolerance 1e-9).
"""
import numpy as np
import pytest
from pydantic import ValidationError

from feasibility import ConditionError, allowed_built_area, irr, npv, residual_land_value, run
from helpers import simple, study

TOTAL = 10_000_000.0   # 1,000 m2 x 10,000 per m2


def approx(x):
    return pytest.approx(x, rel=1e-9, abs=1e-6)


def nonzero(v):
    return {int(i) + 1: float(v[i]) for i in np.flatnonzero(np.abs(v) > 1e-9)}   # {month: amount}


# ---------------------------------------------------------------- sales and collections

def test_01_one_cash_sale_in_month_1():
    r = run(study(simple()))
    assert nonzero(r.lines["collections"]) == {1: TOTAL}
    assert r.kpis["npv_pre_tax"] == approx(TOTAL)       # month 1 is not discounted
    assert r.kpis["units"] == approx(10)
    assert r.months == 1


def test_02_even_sales_over_ten_months():
    r = run(study(simple(**{"products.0.sales_months": 10})))
    assert nonzero(r.lines["collections"]) == {m: approx(TOTAL / 10) for m in range(1, 11)}


def test_03_price_escalates_to_the_month_of_sale():
    # Launch in month 13, 10% a year, measured from launch: launch price holds in month 13.
    r = run(study(simple(**{"products.0.launch_month": 13, "products.0.price_escalation_annual": 0.10})))
    assert nonzero(r.lines["collections"]) == {13: approx(TOTAL)}
    # Sold evenly over months 1 and 13 via a manual curve: the second half sells 12 months later at +10%.
    w = [1] + [0] * 11 + [1]
    r = run(study(simple(**{"products.0.sales_months": 13, "products.0.price_escalation_annual": 0.10,
                            "products.0.sales_curve": {"kind": "manual", "weights": w}})))
    assert nonzero(r.lines["collections"]) == {1: approx(5_000_000), 13: approx(5_500_000)}


def test_04_instalment_plan():
    # 10% down, 8 quarterly instalments of 11.25% each.
    plan = {"name": "cash", "down_payment": 0.10, "instalment_count": 8, "instalment_every_months": 3}
    r = run(study(simple(plans=[plan])))
    expect = {1: approx(1_000_000)}
    expect.update({1 + 3 * k: approx(1_125_000) for k in range(1, 9)})
    assert nonzero(r.lines["collections"]) == expect
    assert r.lines["collections"].sum() == approx(TOTAL)


def test_05_delivery_payment_falls_on_the_delivery_month():
    # Phase runs months 1-12, so delivery is month 12. 50% down, 50% at delivery.
    plan = {"name": "cash", "down_payment": 0.5, "delivery_payment": 0.5}
    r = run(study(simple(plans=[plan])))
    assert nonzero(r.lines["collections"]) == {1: approx(5_000_000), 12: approx(5_000_000)}
    # A longer build moves the payment with it.
    r = run(study(simple(plans=[plan], **{"phases.0.duration_months": 30})))
    assert nonzero(r.lines["collections"]) == {1: approx(5_000_000), 30: approx(5_000_000)}
    # A unit sold after delivery pays the delivery share at sale.
    r = run(study(simple(plans=[plan], **{"products.0.launch_month": 20})))
    assert nonzero(r.lines["collections"]) == {20: approx(TOTAL)}


def test_06_ten_year_plan_has_no_horizon_cut():
    plan = {"name": "cash", "down_payment": 0.0, "instalment_count": 120, "instalment_every_months": 1}
    r = run(study(simple(plans=[plan], **{"products.0.sales_months": 24})))
    # Last sale is month 24, last instalment 120 months later.
    assert r.months == 144
    assert r.lines["collections"].sum() == approx(TOTAL)


def test_07_plan_price_adjustment_and_mix():
    plans = [{"name": "cash", "down_payment": 1.0, "price_adjustment": -0.20},
             {"name": "full", "down_payment": 1.0}]
    r = run(study(simple(plans=plans, **{"products.0.plan_mix": {"cash": 0.25, "full": 0.75}})))
    # 25% of units at 80% of price, 75% at full price.
    assert r.lines["collections"].sum() == approx(0.25 * 8_000_000 + 0.75 * 10_000_000)


def test_08_sellable_share_and_units():
    r = run(study(simple(**{"products.0.sellable_share": 0.8})))
    assert r.products[0].sellable_area_m2 == approx(800)
    assert r.kpis["units"] == approx(8)
    assert r.kpis["revenue"] == approx(8_000_000)


def test_09_premiums_are_per_unit():
    # Four units; one has sea view (+20%), one has sea view and corner (+20% then +5%).
    prem = [{"name": "sea", "uplift": 0.20}, {"name": "corner", "uplift": 0.05}]
    units = [{"id": "1", "premiums": ["sea"]}, {"id": "2", "premiums": ["sea", "corner"]},
             {"id": "3"}, {"id": "4"}]
    r = run(study(simple(**{"products.0.premiums": prem, "products.0.units": units,
                            "products.0.unit_size_m2": 250.0})))
    one = 2_500_000.0
    assert r.kpis["revenue"] == approx(one * 1.20 + one * 1.20 * 1.05 + one + one)
    # A unit with no premium gets nothing added.
    r0 = run(study(simple(**{"products.0.premiums": prem, "products.0.unit_size_m2": 250.0,
                             "products.0.units": [{"id": str(i)} for i in range(4)]})))
    assert r0.kpis["revenue"] == approx(TOTAL)


def test_10_release_price_step():
    r = run(study(simple(**{"products.0.sales_months": 2, "products.0.releases": [{"from_month": 2, "step": 0.10}]})))
    assert nonzero(r.lines["collections"]) == {1: approx(5_000_000), 2: approx(5_500_000)}


def test_11_units_per_month():
    # 10 units at 4 a month: 4, 4, 2.
    r = run(study(simple(**{"products.0.sales_months": None, "products.0.units_per_month": 4.0})))
    assert nonzero(r.lines["collections"]) == {1: approx(4_000_000), 2: approx(4_000_000), 3: approx(2_000_000)}


# ---------------------------------------------------------------- sales risk

def test_12_cancellation_refund_and_resale():
    # 50% down, 50% twelve months later. 20% of contracts cancel after 6 months,
    # having paid only the down payment. 10% of it is kept, 90% refunded.
    # The units resell 3 months later (month 10) at a 5% discount, no escalation.
    plan = {"name": "cash", "down_payment": 0.5, "instalment_count": 1, "instalment_every_months": 12}
    risk = {"cancellation_rate": 0.2, "cancel_after_months": 6, "cancellation_penalty_pct": 0.10,
            "resale_delay_months": 3, "resale_discount": 0.05}
    r = run(study(simple(plans=[plan], sales_risk=risk)))
    resale = 0.2 * TOTAL * 0.95                       # 1,900,000
    assert nonzero(r.lines["collections"]) == {
        1: approx(0.5 * TOTAL),                       # all buyers pay the down payment
        10: approx(0.5 * resale),                     # resale down payment
        13: approx(0.5 * 0.8 * TOTAL),                # holding buyers' second half
        22: approx(0.5 * resale),                     # resale second half
    }
    assert nonzero(r.lines["cancellation_refunds"]) == {7: approx(-0.9 * 0.5 * 0.2 * TOTAL)}
    # Money kept in the end: holding contracts + resales + the penalty.
    assert r.kpis["revenue"] == approx(0.8 * TOTAL + resale + 0.1 * 0.5 * 0.2 * TOTAL)
    # Cancellation is counted once: contracted sales net out to the final contracts.
    assert r.memo["contracted_sales"].sum() == approx(0.8 * TOTAL + resale)


def test_13_unsold_stock():
    risk = {"unsold_share": 0.10, "clear_unsold": True, "clearance_months": 2, "clearance_discount": 0.30}
    r = run(study(simple(sales_risk=risk)))
    # 90% sells in month 1; 10% clears over months 2-3 at 70% of price.
    assert nonzero(r.lines["collections"]) == {1: approx(9_000_000), 2: approx(350_000), 3: approx(350_000)}
    risk["clear_unsold"] = False
    r = run(study(simple(sales_risk=risk)))
    assert nonzero(r.lines["collections"]) == {1: approx(9_000_000)}


# ---------------------------------------------------------------- costs

def test_14_hard_cost_spread_and_inflation():
    # 1,000 m2 at 6,000 = 6,000,000 over 12 months, no inflation: 500,000 a month.
    r = run(study(simple(**{"products.0.hard_cost_per_m2": 6000.0})))
    assert nonzero(r.lines["hard_cost"]) == {m: approx(-500_000) for m in range(1, 13)}
    # With 12% inflation, the spend in month m is 500,000 x 1.12^((m-1)/12).
    r = run(study(simple(costs={"cost_inflation_annual": 0.12}, **{"products.0.hard_cost_per_m2": 6000.0})))
    assert nonzero(r.lines["hard_cost"]) == {m: approx(-500_000 * 1.12 ** ((m - 1) / 12)) for m in range(1, 13)}


def test_15_soft_cost_and_contingency_charged_once():
    costs = {"soft_costs": [{"name": "design", "pct": 0.05}], "contingency_pct": 0.10,
             "site_costs": [{"name": "infra", "rate_per_m2_land": 1000.0, "start_month": 1, "duration_months": 4}]}
    r = run(study(simple(costs=costs, **{"products.0.hard_cost_per_m2": 6000.0})))
    hard, site = 6_000_000.0, 1_000_000.0
    assert r.lines["hard_cost"].sum() == approx(-hard)
    assert r.lines["site_cost"].sum() == approx(-site)
    assert r.lines["soft_cost"].sum() == approx(-0.05 * (hard + site))
    assert r.lines["contingency"].sum() == approx(-0.10 * (hard + site))
    assert r.kpis["development_cost"] == approx(1.15 * (hard + site))
    # Contingency on the building only, when the team sets that base.
    costs["contingency_base"] = "building"
    r = run(study(simple(costs=costs, **{"products.0.hard_cost_per_m2": 6000.0})))
    assert r.lines["contingency"].sum() == approx(-0.10 * hard)


def test_16_commission_follows_contracts_not_collections():
    plan = {"name": "cash", "down_payment": 0.10, "instalment_count": 9, "instalment_every_months": 12}
    costs = {"commission_pct_of_sales": 0.03, "marketing_pct_of_sales": 0.02}
    r = run(study(simple(plans=[plan], costs=costs)))
    # The contract is signed in month 1, so both are paid in full in month 1.
    assert nonzero(r.lines["commission"]) == {1: approx(-300_000)}
    assert nonzero(r.lines["marketing"]) == {1: approx(-200_000)}


def test_17_overhead_runs_monthly():
    costs = {"overhead_monthly": 10_000.0, "overhead_start_month": 3, "overhead_end_month": 8}
    r = run(study(simple(costs=costs)))
    assert nonzero(r.lines["overhead"]) == {m: approx(-10_000) for m in range(3, 9)}
    # With no end month it runs to the later of last delivery (12) and last sale (1).
    r = run(study(simple(costs={"overhead_monthly": 10_000.0})))
    assert r.lines["overhead"].sum() == approx(-120_000)


# ---------------------------------------------------------------- land

def test_18_land_paid_in_full_on_day_one():
    land = {"cash": {"price_per_m2": 2000.0, "down_payment_pct": 1.0}}
    r = run(study(simple(land=land)))
    assert nonzero(r.lines["land_cash"]) == {1: approx(-2_000_000)}
    assert r.kpis["npv_pre_tax"] == approx(TOTAL - 2_000_000)


def test_19_land_instalments_with_interest():
    # 2,000,000: 25% down, three yearly instalments of 500,000, 10% a year on the unpaid balance.
    land = {"cash": {"price_per_m2": 2000.0, "down_payment_pct": 0.25, "instalment_count": 3,
                     "instalment_every_months": 12, "interest_annual": 0.10}}
    r = run(study(simple(land=land)))
    assert nonzero(r.lines["land_cash"]) == {1: approx(-500_000), 13: approx(-500_000),
                                             25: approx(-500_000), 37: approx(-500_000)}
    # A year on the unpaid balance costs exactly 10% of it.
    assert nonzero(r.lines["land_interest"]) == {13: approx(-150_000), 25: approx(-100_000),
                                                 37: approx(-50_000)}


def test_20_land_foreign_currency_tranche():
    # Half the price is owed in foreign currency that gains 20% a year. Paid at month 13.
    land = {"cash": {"price_per_m2": 2000.0, "down_payment_pct": 0.0, "instalment_count": 1,
                     "instalment_every_months": 12, "fx_share": 0.5, "fx_depreciation_annual": 0.20}}
    r = run(study(simple(land=land)))
    assert nonzero(r.lines["land_cash"]) == {13: approx(-(1_000_000 + 1_000_000 * 1.20))}


def test_21_land_fee_schedule():
    land = {"fees": [{"name": "betterment", "amount_per_m2_land": 1200.0,
                      "schedule": [{"month": 1, "share": 0.5}, {"month": 7, "share": 0.5}]}]}
    r = run(study(simple(land=land)))
    assert nonzero(r.lines["land_fees"]) == {1: approx(-600_000), 7: approx(-600_000)}


def test_22_revenue_share_and_in_kind():
    r = run(study(simple(land={"revenue_share": 0.25}, **{"products.0.sales_months": 2})))
    assert nonzero(r.lines["land_revenue_share"]) == {1: approx(-1_250_000), 2: approx(-1_250_000)}
    # In kind: the owner takes 30% of the sellable area; the developer still builds all of it.
    r = run(study(simple(land={"in_kind_share": 0.30}, **{"products.0.hard_cost_per_m2": 6000.0})))
    assert r.kpis["revenue"] == approx(7_000_000)
    assert r.lines["hard_cost"].sum() == approx(-6_000_000)


def test_23_profit_partnership_costs_first():
    # Whole project in partnership, owner gets 40% of profit.
    # Build cost 6,000,000 over months 1-12 (500,000 a month). Half sells in month 1 for cash,
    # half in month 13. Running net cash: +4.5m after month 1, falling to -1.0m by month 12,
    # then +4.0m after month 13.
    w = [1] + [0] * 11 + [1]
    r = run(study(simple(land={"partnership": {"area_share": 1.0, "owner_share": 0.4}},
                         **{"products.0.hard_cost_per_m2": 6000.0, "products.0.sales_months": 13,
                            "products.0.sales_curve": {"kind": "manual", "weights": w}})))
    # Month 1: the running total is +4.5m, so the owner is paid 40% of it. Nothing more is due
    # until the running total passes that peak; it ends at 4.0m, below it.
    assert nonzero(r.lines["land_partnership_share"]) == {1: approx(-0.4 * 4_500_000)}
    assert any("peaks and then falls" in w for w in r.warnings)
    # Sell everything in month 13 instead: costs are funded first, then 40% of the 4.0m profit.
    r = run(study(simple(land={"partnership": {"area_share": 1.0, "owner_share": 0.4}},
                         **{"products.0.hard_cost_per_m2": 6000.0, "products.0.launch_month": 13})))
    assert nonzero(r.lines["land_partnership_share"]) == {13: approx(-0.4 * 4_000_000)}
    assert r.kpis["profit_pre_tax"] == approx(0.6 * 4_000_000)


def test_24_part_purchase_with_partnership_on_the_rest():
    # Buy 40% of the land at 2,000 per m2; partner on the other 60% with the owner taking 40% of its profit.
    land = {"cash": {"price_per_m2": 2000.0, "area_share": 0.4, "down_payment_pct": 1.0},
            "partnership": {"area_share": 0.6, "owner_share": 0.4}}
    r = run(study(simple(land=land, **{"products.0.hard_cost_per_m2": 6000.0, "products.0.launch_month": 13})))
    profit_before_land = TOTAL - 6_000_000
    assert r.lines["land_cash"].sum() == approx(-0.4 * 1000 * 2000)
    assert r.lines["land_partnership_share"].sum() == approx(-0.4 * 0.6 * profit_before_land)
    # A revenue split takes its share of collections instead.
    land["partnership"]["basis"] = "revenue"
    r = run(study(simple(land=land, **{"products.0.hard_cost_per_m2": 6000.0})))
    assert r.lines["land_partnership_share"].sum() == approx(-0.4 * 0.6 * TOTAL)


# ---------------------------------------------------------------- leases, tax, finance

LEASE = {"name": "retail", "category": "commercial", "mode": "lease", "bua_share": 1.0,
         "sellable_share": 1.0, "unit_size_m2": 100.0, "phase": "P1", "hard_cost_per_m2": 0.0,
         "lease": {"rent_per_m2_month": 100.0, "stable_occupancy": 0.9, "ramp_months": 0,
                   "rent_growth_annual": 0.0, "opex_pct_of_rent": 0.2, "hold_months": 24,
                   "exit_cap_rate": 0.10, "exit_cost_pct": 0.02}}


def test_25_leased_product_is_rent_then_exit():
    r = run(study(simple(products=[LEASE], plans=[])))
    rent = 1000 * 100 * 0.9                 # 90,000 a month from month 13 (delivery is month 12)
    assert nonzero(r.lines["rent"]) == {m: approx(rent) for m in range(13, 37)}
    assert r.lines["lease_opex"].sum() == approx(-0.2 * rent * 24)
    # Exit in month 36 at next year's net income over the cap rate, less 2% selling cost.
    assert nonzero(r.lines["lease_exit"]) == {36: approx(12 * rent * 0.8 / 0.10 * 0.98)}


def test_26_lease_ramp_and_growth():
    lease = dict(LEASE, lease=dict(LEASE["lease"], ramp_months=4, rent_growth_annual=0.10, hold_months=14))
    r = run(study(simple(products=[lease], plans=[])))
    full = 90_000.0
    v = r.lines["rent"]
    # Rent is quoted in month-1 money and steps up 10% at each anniversary; months 13-24 are in year 2.
    assert v[12] == approx(full * 1.10 * 1 / 4)
    assert v[13] == approx(full * 1.10 * 2 / 4)
    assert v[15] == approx(full * 1.10)
    assert v[24] == approx(full * 1.10 ** 2)   # month 25 starts year 3


def test_27_income_tax_paid_the_following_year():
    # Profit 10,000,000 in year 1, taxed at 20%, paid 4 months after the year ends (month 16).
    r = run(study(simple(tax={"income_tax_rate": 0.20})))
    assert nonzero(r.income_tax) == {16: approx(-2_000_000)}
    assert r.kpis["profit_after_tax"] == approx(8_000_000)
    assert r.kpis["npv_after_tax"] == approx(TOTAL - 2_000_000 / 1.10 ** (15 / 12))


def test_28_tax_losses_carry_forward():
    # Year 1: build cost of 6,000,000 and no sales. Year 2: 10,000,000 of sales.
    # Taxable profit in year 2 is 4,000,000 after the carried loss.
    r = run(study(simple(tax={"income_tax_rate": 0.20},
                         **{"products.0.hard_cost_per_m2": 6000.0, "products.0.launch_month": 13})))
    assert nonzero(r.income_tax) == {28: approx(-800_000)}


def test_29_debt_first_then_equity_and_repayment():
    # Month 1: land 3,000,000 out. Debt limit 2,000,000 at 12% a year. Sale of 10,000,000 in month 13.
    land = {"cash": {"price_per_m2": 3000.0, "down_payment_pct": 1.0}}
    fin = {"discount_rate_annual": 0.10, "debt_limit": 2_000_000.0, "debt_interest_annual": 0.12}
    r = run(study(simple(land=land, finance=fin, **{"products.0.launch_month": 13})))
    i_m = 1.12 ** (1 / 12) - 1
    assert r.lines["debt_draw"][0] == approx(2_000_000)
    assert r.equity_cash_flow[0] == approx(-1_000_000)
    # Months 2-12: the limit is used up, so equity pays the interest.
    for t in range(1, 12):
        assert r.equity_cash_flow[t] == approx(-2_000_000 * i_m)
    # Month 13: collections repay the debt and the interest, the rest goes to equity.
    assert r.lines["debt_repayment"][12] == approx(-2_000_000)
    assert r.equity_cash_flow[12] == approx(TOTAL - 2_000_000 - 2_000_000 * i_m)
    assert r.debt_balance[-1] == 0
    assert r.kpis["peak_debt"] == approx(2_000_000)
    assert r.kpis["peak_equity"] == approx(1_000_000 + 11 * 2_000_000 * i_m)


# ---------------------------------------------------------------- returns

def test_30_npv_and_irr_by_hand():
    cf = np.zeros(25)
    cf[0], cf[24] = -100.0, 121.0
    assert irr(cf).annual == approx(0.10)          # 100 grows to 121 in two years
    assert npv(cf, 0.10) == approx(0.0)
    assert npv(cf, 0.0) == approx(21.0)
    assert npv([0.0] * 12 + [110.0], 0.10) == approx(100.0)


def test_31_irr_is_never_substituted():
    assert irr([100.0, 50.0]).status == "none"     # no outflow: no IRR
    assert irr([-100.0, -50.0]).status == "none"
    # -100, +230, -132 a year apart: NPV is zero at both 10% and 20%.
    cf = np.zeros(25)
    cf[0], cf[12], cf[24] = -100.0, 230.0, -132.0
    res = irr(cf)
    assert res.status == "multiple" and res.annual is None
    assert sorted(res.candidates) == [pytest.approx(0.10, abs=1e-9), pytest.approx(0.20, abs=1e-9)]


def test_32_project_irr_through_the_engine():
    # Land 5,000,000 in month 1, sale of 10,000,000 in month 25: money doubles in two years.
    land = {"cash": {"price_per_m2": 5000.0, "down_payment_pct": 1.0}}
    r = run(study(simple(land=land, **{"products.0.launch_month": 25})))
    assert r.kpis["irr_pre_tax"] == approx(2 ** 0.5 - 1)
    assert r.kpis["peak_funding_need"] == approx(5_000_000)
    assert r.kpis["peak_funding_month"] == 1
    assert r.kpis["payback_month"] == 25


def test_33_residual_land_value_by_rerun():
    # Sale of 10,000,000 in month 25, discounted two years at 10%: land paid on day one can be
    # worth at most 10,000,000 / 1.21.
    land = {"cash": {"price_per_m2": 1.0, "down_payment_pct": 1.0}}
    res = residual_land_value(study(simple(land=land, **{"products.0.launch_month": 25})))
    assert res.feasible
    assert res.total == pytest.approx(TOTAL / 1.21, rel=1e-8)
    assert res.price_per_m2 == pytest.approx(TOTAL / 1.21 / 1000, rel=1e-8)


# ---------------------------------------------------------------- areas and conditions

def test_34_allowed_area_is_the_smaller_cap():
    # Floor-area cap: 1,000 x 1.5 = 1,500. Footprint 20% x (half at 4 floors, half at 8) = 1,200.
    cond = {"far_cap": 1.5, "footprint_pct": 0.20,
            "height_zones": [{"share_of_footprint": 0.5, "floors": 4}, {"share_of_footprint": 0.5, "floors": 8}]}
    s = study(simple(conditions=cond))
    assert allowed_built_area(s) == approx(1200)
    assert run(s).built_area_m2 == approx(1200)
    cond["far_cap"] = 1.0
    assert allowed_built_area(study(simple(conditions=cond))) == approx(1000)


def test_35_conditions_are_enforced():
    with pytest.raises(ConditionError):      # more built area than allowed
        run(study(simple(conditions={"far_cap": 1.0, "built_area_m2": 1001.0})))
    with pytest.raises(ConditionError):      # required hotel share missing
        run(study(simple(conditions={"far_cap": 1.0, "min_share_by_category": {"hotel": 0.2}})))
    with pytest.raises(ConditionError):      # residential above its ceiling
        run(study(simple(conditions={"far_cap": 1.0, "max_share_by_category": {"residential": 0.5}})))
    with pytest.raises(ConditionError):      # build runs past the deadline
        run(study(simple(conditions={"far_cap": 1.0, "build_deadline_months": 11})))
    with pytest.raises(ValidationError):     # product shares must total 100%
        study(simple(**{"products.0.bua_share": 0.9}))


def test_36_quarters_and_years_are_sums_of_months():
    r = run(study(simple(**{"products.0.sales_months": 10, "products.0.hard_cost_per_m2": 6000.0})))
    m, q, y = r.table("month"), r.table("quarter"), r.table("year")
    for line in m:
        assert q[line].sum() == approx(m[line].sum())
        assert y[line].sum() == approx(m[line].sum())
    assert q["collections"][0] == approx(3_000_000)
    assert y["hard_cost"][0] == approx(-6_000_000)
