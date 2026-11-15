"""Monthly real-estate feasibility engine."""
from .analysis import RiskSpec, residual_land_value, risk_ranges, sensitivity, shocked
from .engine import CheckFailed, ConditionError, Result, allowed_built_area, run
from .inputs import FEDDAN_M2, Study
from .returns import IRR, irr, npv

__all__ = ["Study", "run", "Result", "ConditionError", "CheckFailed", "allowed_built_area",
           "npv", "irr", "IRR", "sensitivity", "shocked", "residual_land_value",
           "risk_ranges", "RiskSpec", "FEDDAN_M2"]
