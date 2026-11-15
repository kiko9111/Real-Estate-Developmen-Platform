# Real Estate Development Platform

A platform for studying new land: screening, full feasibility, site layout and
massing, best use, and investor reports.

This repository holds three parts:

- the **feasibility engine** (`src/feasibility`), a Python library that takes one
  land and one set of assumptions and returns the monthly cash flow and the returns;
- the **web app** (`src/webapp`), which the team uses to record lands, screen them,
  run full studies and scenarios, scan the market and export investor reports;
- the **layout generator** (`src/layout`), which lays out roads, plots and buildings
  on a plot within its conditions and hands each option to the engine.

## The web app

- **Team logins** with three roles: admin (manages accounts), analyst (creates and
  edits), viewer (reads and downloads reports).
- **Lands**: location, size, parcels, frontage, the offer, legal facts, custom fields,
  and a full **land-conditions form** (use, coverage, floor area ratio, floors, height
  zones, density, service and open-space shares, share rules, setbacks, parking,
  phasing, land payment terms, fees, custom rows). The parts the engine understands
  feed the studies; the rest is kept for the layout and massing step.
- **Quick screening**: about twenty numbers, a go / borderline / no-go verdict with
  reasons, and the most the land can cost. It builds a one-product study and runs the
  full engine, so it agrees with a full study of the same inputs.
- **Full studies**: every engine input in a generated form (or as JSON), results,
  yearly cash flow, standard scenarios, sensitivity, residual land value, risk ranges,
  scenario copies and side-by-side comparison.
- **Market scan**: competitor prices and payment plans by location. A web scan (Claude
  with web search, needs `ANTHROPIC_API_KEY`) returns each project with its source
  link, unverified until someone checks it; rows can also be added by hand or pasted
  from a spreadsheet. Statistics per product type feed the screening price.
- **Investor reports**: PDF and PowerPoint (native, editable charts) for any study.
- **Site layout, massing and best use**: draw the plot on a satellite map, upload KML/KMZ
  or DXF, or give frontage and depth. The generator lays out a road grid, parks, service
  plots and building plots, and places villas, townhouses, chalet and apartment buildings,
  hotels and commercial buildings within the land's coverage, floor-area ratio, height
  (including height bands from the frontage), density, setback, open-space, services and
  share rules. Each strategy (four to start, editable per land with the building library's
  prices and costs) gives a plan view, a rotatable 3D massing view, an area schedule and a
  compliance check against the conditions. The ministerial decree comes after design, so
  the decree logic is only this check, never an input. Every option runs through the
  engine with a base study's land deal, timing, plans and costs, and options are ranked
  by NPV, IRR and margin; the best one becomes a full study in one click.
  The team can edit any option on the plan: click a block, change what it holds (any
  building type, a park or a services plot) or cap its floors, and apply. The option is
  laid out again with those blocks locked, checked against the conditions, run through
  the engine and ranked as "<strategy> (edited)" beside the original. Height limits still
  win over an edit, and edits are kept when the layout is run again.

### Run it

```bash
pip install -e ".[web]"
python -m webapp create-user you@example.com "Your Name" --role admin
python -m webapp seed-demo          # optional: one invented demo land
python -m webapp serve              # http://127.0.0.1:8000
```

Settings come from the environment: `DATABASE_URL` (default SQLite in `./data`),
`SECRET_KEY` (required with `APP_ENV=production`), `ANTHROPIC_API_KEY` and
`MARKET_SCAN_MODEL` for the web scan, and `BOOTSTRAP_ADMIN_EMAIL` /
`BOOTSTRAP_ADMIN_PASSWORD` to create the first admin on an empty database. A
`Dockerfile` is included.

### Put it online for the team

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/kiko9111/Real-Estate-Developmen-Platform)

The button reads `render.yaml`: one always-on web service in Frankfurt with a 1 GB
disk for the database. Render asks for three values:

- `ANTHROPIC_API_KEY`: turns on the web market scan (create one at console.anthropic.com).
- `BOOTSTRAP_ADMIN_EMAIL` and `BOOTSTRAP_ADMIN_PASSWORD`: the first admin, created on
  the first start. Sign in, add the team on the Team page, then change this password.

The app gets an address like `https://development-platform.onrender.com` that anyone
on the team can open; a custom domain can be added in Render's settings. Each push to
`main` redeploys it. Keys live only in Render's settings, never in this repository.

Do not commit real land data or offers to this repository; it is public.

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
pip install -e ".[web,dev]"
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
- `tests/web/`: the web app: logins and roles, CSRF, lands and conditions, the
  screener against the engine, studies and analyses, reports, and the market scan
  (with a fake search client).

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
tax, VAT, debt grace periods and scheduled repayment. The layout is a concept
test-fit (blocks and building footprints on a grid aligned with the frontage),
not a submission design: curved roads, drawn zones other than bands from the
frontage, and parking structures are not modelled.
