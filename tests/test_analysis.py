"""Sensitivities, risk ranges and the command line."""
import json

import numpy as np
import pytest

from feasibility import RiskSpec, Study, risk_ranges, run, sensitivity, shocked
from feasibility.__main__ import main
from helpers import rich, simple, study


def test_sensitivity_rows_are_full_reruns():
    s = study(rich())
    rows = sensitivity(s, "price", [0.9, 1.0, 1.1])
    base = run(s).kpis
    assert rows[1]["npv_pre_tax"] == pytest.approx(base["npv_pre_tax"])
    assert rows[0]["npv_pre_tax"] < rows[1]["npv_pre_tax"] < rows[2]["npv_pre_tax"]
    # Each row equals an independent run of the changed study.
    assert rows[2]["npv_pre_tax"] == pytest.approx(run(shocked(s, "price", 1.1)).kpis["npv_pre_tax"])


@pytest.mark.parametrize("shock,value", [("price", 1.1), ("sales_pace", 0.7), ("cost", 1.1),
                                         ("cost_inflation", 0.03), ("delay", 6),
                                         ("cancellation_rate", 0.05), ("land_price", 1.2)])
def test_every_sensitivity_moves_the_result(shock, value):
    s = study(rich())
    assert run(shocked(s, shock, value)).kpis["npv_pre_tax"] != pytest.approx(run(s).kpis["npv_pre_tax"], rel=1e-9)


def test_delay_by_hand():
    # Land 5,000,000 on day one, sale of 10,000,000 in month 25. A 12-month delay moves the sale to month 37.
    d = simple(land={"cash": {"price_per_m2": 5000.0, "down_payment_pct": 1.0}}, **{"products.0.launch_month": 25})
    r = run(shocked(study(d), "delay", 12))
    assert r.kpis["irr_pre_tax"] == pytest.approx(2 ** (1 / 3) - 1, rel=1e-9)   # doubles in three years


def test_slower_sales_by_hand():
    # Ten months of even sales at half the pace become twenty months.
    r = run(shocked(study(simple(**{"products.0.sales_months": 10})), "sales_pace", 0.5))
    v = r.lines["collections"]
    assert len(v) == 20 and np.allclose(v, 500_000)


def test_risk_ranges_are_ordered_and_repeatable():
    s = study(rich())
    a = risk_ranges(s, RiskSpec(runs=60, seed=7))
    b = risk_ranges(s, RiskSpec(runs=60, seed=7))
    assert a == b                                              # same seed, same answer
    assert a["npv"]["p10"] <= a["npv"]["p50"] <= a["npv"]["p90"]
    assert 0.0 <= a["probability_target_met"] <= 1.0


def test_risk_with_no_spread_reproduces_the_base_case():
    s = study(rich())
    flat = {"low": 1.0, "mode": 1.0, "high": 1.0}
    zero = {"low": 0.0, "mode": 0.0, "high": 0.0}
    out = risk_ranges(s, RiskSpec(price=flat, sales_pace=flat, cost_inflation=zero, delay_months=zero, runs=3))
    assert out["npv"]["p50"] == pytest.approx(run(s).kpis["npv_pre_tax"])


def test_command_line(tmp_path, capsys):
    path = tmp_path / "study.json"
    path.write_text(json.dumps(simple()))
    assert main([str(path), "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["kpis"]["revenue"] == pytest.approx(10_000_000)
    assert all(out["checks"].values())


def test_example_study_runs():
    import pathlib
    p = pathlib.Path(__file__).parent.parent / "examples" / "sample_study.json"
    r = run(Study.model_validate(json.loads(p.read_text())))
    assert all(r.checks.values())
