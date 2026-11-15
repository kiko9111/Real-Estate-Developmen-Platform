"""Command line: python -m feasibility study.json [--residual] [--risk N]"""
from __future__ import annotations

import argparse
import json
import sys

from . import RiskSpec, Study, residual_land_value, risk_ranges, run


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="feasibility")
    ap.add_argument("study", help="path to a study JSON file")
    ap.add_argument("--residual", action="store_true", help="also solve the residual land value")
    ap.add_argument("--risk", type=int, metavar="RUNS", help="also run a Monte Carlo with this many runs")
    ap.add_argument("--json", action="store_true", help="print machine-readable JSON")
    a = ap.parse_args(argv)
    with open(a.study, encoding="utf-8") as fh:
        study = Study.model_validate(json.load(fh))
    res = run(study)
    out = {"study": res.study_name, "months": res.months, "built_area_m2": res.built_area_m2,
           "kpis": res.kpis, "checks": res.checks, "warnings": res.warnings}
    if a.residual:
        out["residual_land"] = residual_land_value(study).__dict__
    if a.risk:
        out["risk"] = risk_ranges(study, RiskSpec(runs=a.risk))
    if a.json:
        json.dump(out, sys.stdout, indent=2)
        print()
        return 0
    print(f"{res.study_name}: {res.months} months, built area {res.built_area_m2:,.0f} m2")
    for k, v in res.kpis.items():
        if v is None:
            txt = "n/a"
        elif "irr" in k or "margin" in k:
            txt = f"{v:.2%}"
        elif "month" in k:
            txt = f"{v}"
        else:
            txt = f"{v:,.2f}"
        print(f"  {k:<22} {txt}")
    for w in res.warnings:
        print("  warning:", w)
    for key in ("residual_land", "risk"):
        if key in out:
            print(f"  {key}: {json.dumps(out[key])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
