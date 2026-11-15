# Real Estate Development Platform

A platform for studying new land: screening, full feasibility, site layout and
massing, best use, and investor reports.

This repository currently holds the first part, the **feasibility engine**. It
is a Python library that takes one land and one set of assumptions and returns
the monthly cash flow and the returns. The web app will sit on top of it.

## What the engine does

It follows the thirteen steps of the calculation specification:

1. **Areas.** Built area is the smaller of the floor-area cap and footprint
   times permitted floors. Products share it. A mix that breaks a land
   condition is refused.
2. **Sales** by month, from a curve, a fixed pace, or a manual schedule.
3. **Price at sale**: launch price, per-unit premiums, escalation to the month
   of sale, release price steps, payment-plan adjustment.
4. **Collections** on each contract's own plan. Delivery payments fall on the
   real delivery month.
5. **Cancellations**: refund less penalty, then resale on a fresh plan.
6. **Unsold stock** cleared at a discount, or left unsold.
7. **Leased products**: rent, ramp-up, operating cost, and a sale at a cap rate.
8. **Construction** by phase, inflated to the month of spend.
9. **Other costs**: soft costs, one contingency, marketing and commission on
   contracted sales, monthly overhead.
10. **Land**: cash with instalments and interest, fees, in-kind share, revenue
    share, and part purchase with a profit or revenue partnership on the rest.
11. **Taxes**: transaction tax, and income tax paid the following year.
12. **Project cash flow**: NPV and IRR before and after tax.
13. **Financing**: debt up to a limit, equity beyond it, equity returns.

On top of that: sensitivities, residual land value and Monte Carlo risk
ranges. Each of these re-runs the full engine. None uses a shortcut formula.

## Rules the engine keeps

- Monthly time steps. Quarters and years are sums of months.
- No fixed horizon. The timeline runs until the last pound moves.
- Each input lives in one place, and unknown fields are refused.
- Every input changes a result. A test fails if one does not.
- Cancellations, contingency and sellability are each applied once.
- Money is nominal. The discount rate is nominal.
- IRR is a true IRR. Month 1 is time zero. If no single IRR exists, the engine
  says so and reports none.
- Area is conserved. Products can never exceed what the land allows.

## Use

```bash
pip install -e ".[dev]"
python -m feasibility examples/sample_study.json --residual --risk 500
pytest -q
```

```python
import json
from feasibility import Study, run, residual_land_value

study = Study.model_validate(json.load(open("examples/sample_study.json")))
result = run(study)
print(result.kpis["npv_pre_tax"], result.kpis["irr_pre_tax"])
print(result.table("year")["collections"])
print(residual_land_value(study).price_per_feddan)
```

`examples/sample_study.json` uses invented numbers. It is not a real land.
All inputs are described in `src/feasibility/inputs.py`.

## How it is proven

- `tests/test_hand_cases.py`: 36 small cases with the arithmetic written out
  in the test, such as one unit sold for cash in month 1, or land paid in three
  instalments with interest. The engine must match each to the pound.
- `tests/test_rules.py`: rules checked on thousands of random studies. For
  example, a land twice the size on the same terms gives exactly twice every
  amount and the same IRR; a higher cost never raises NPV; every input moves a
  result; every land condition refuses a study that breaks it.
- `tests/test_analysis.py`: sensitivities, risk ranges and the command line.

## Conventions worth knowing

- Month 1 is the month the land deal starts and is not discounted.
- Prices escalate from each product's launch month. Costs and rents are quoted
  in month-1 money and inflate from month 1.
- Premiums belong to units. With a unit list the average is exact. Without one,
  each premium is spread over its share of units independently of the others.
- Income tax is on each 12-month year's net cash profit, with earlier losses
  carried forward. This is a cash-basis simplification of the accounting rule.
- In a profit partnership the owner is paid a share of the shared part's
  running net cash once it is positive. Amounts paid are not clawed back; the
  engine warns if that running total later falls.
- Resold units after a cancellation are assumed not to cancel a second time.
- When a cash flow has several rates that make NPV zero, the engine reports
  one only if it is the single rate on the same side of zero as the project's
  result, and lists the others. Otherwise it reports none.

## Not built yet

Unit-by-unit inventory with a sales order, revenue recognised at delivery for
tax, VAT, debt grace periods and scheduled repayment, the comparison against
the Excel model, and the web app.
