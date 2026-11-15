"""Study inputs.

A study is one land plus one set of assumptions. Every number lives in exactly
one field here. Months are 1-based: month 1 is the month the land deal starts.
Rates are annual fractions (0.10 = 10% a year). Money is nominal, in the study
currency; prices and costs are quoted in month-1 money unless stated otherwise.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

FEDDAN_M2 = 4200.83
TOL = 1e-9



def _share(default=...):
    return Field(default=default, ge=0.0, le=1.0)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- land

class Parcel(_Model):
    name: str
    area_m2: float = Field(gt=0)


class HeightZone(_Model):
    """A part of the building footprint with its own permitted floor count."""
    name: str = "zone"
    share_of_footprint: float = _share()
    floors: float = Field(gt=0)


class Conditions(_Model):
    """Allotment conditions of the land. Entered by hand; any field may be blank."""
    far_cap: Optional[float] = Field(default=None, gt=0)
    footprint_pct: Optional[float] = Field(default=None, gt=0, le=1)
    height_zones: list[HeightZone] = []
    # Built area the team chooses to develop. Blank = build up to the cap.
    built_area_m2: Optional[float] = Field(default=None, gt=0)
    min_share_by_category: dict[str, float] = {}
    max_share_by_category: dict[str, float] = {}
    build_deadline_months: Optional[int] = Field(default=None, gt=0)
    notes: dict[str, str] = {}

    @model_validator(mode="after")
    def _zones(self):
        if self.height_zones:
            if self.footprint_pct is None:
                raise ValueError("height zones need a footprint percentage")
            s = sum(z.share_of_footprint for z in self.height_zones)
            if abs(s - 1.0) > 1e-6:
                raise ValueError(f"height zone shares total {s:.4f}, not 1")
        return self


# ---------------------------------------------------------------- curves

class Curve(_Model):
    """How a quantity is spread over a number of months."""
    kind: Literal["even", "front_loaded", "s_curve", "manual"] = "even"
    weights: list[float] = []  # manual only; one weight per month, any scale

    @model_validator(mode="after")
    def _check(self):
        if self.kind == "manual":
            if not self.weights or min(self.weights) < 0 or sum(self.weights) <= 0:
                raise ValueError("manual curve needs non-negative weights with a positive total")
        elif self.weights:
            raise ValueError("weights are only used by a manual curve")
        return self


# ---------------------------------------------------------------- products

class Premium(_Model):
    """A named price premium, for example sea view or corner.

    Premiums belong to units. If the product has a unit list, each unit names
    the premiums it carries. Before a unit list exists, give the share of
    units that carry the premium.
    """
    name: str
    uplift: float = Field(gt=-1)
    share_of_units: Optional[float] = Field(default=None, ge=0, le=1)


class Unit(_Model):
    id: str
    premiums: list[str] = []


class Release(_Model):
    """A price step that applies from a month onward."""
    from_month: int = Field(ge=1)
    step: float = Field(gt=-1)


class Lease(_Model):
    rent_per_m2_month: float = Field(ge=0)   # month-1 money
    stable_occupancy: float = _share()
    ramp_months: int = Field(ge=0)
    rent_growth_annual: float = 0.0          # applied once a year
    opex_pct_of_rent: float = _share()
    hold_months: int = Field(gt=0)           # counted from the first rent month
    exit_cap_rate: float = Field(gt=0)
    exit_cost_pct: float = _share()


class Product(_Model):
    name: str
    category: Literal["residential", "commercial", "hotel", "serviced"]
    mode: Literal["sell", "lease"] = "sell"
    bua_share: float = _share()                 # share of the built area
    sellable_share: float = Field(gt=0, le=1)
    unit_size_m2: float = Field(gt=0)        # sellable m2 per unit
    phase: str                               # construction phase that builds it
    hard_cost_per_m2: float = Field(ge=0)    # per m2 of built area, month-1 money
    # sale
    launch_price_per_m2: Optional[float] = Field(default=None, ge=0)
    price_escalation_annual: float = 0.0
    launch_month: int = Field(default=1, ge=1)
    sales_months: Optional[int] = Field(default=None, ge=1)
    sales_curve: Curve = Curve()
    units_per_month: Optional[float] = Field(default=None, gt=0)
    premiums: list[Premium] = []
    units: list[Unit] = []
    releases: list[Release] = []
    plan_mix: dict[str, float] = {}
    transaction_tax_pct: float = _share(0.0)
    # lease
    lease: Optional[Lease] = None

    @model_validator(mode="after")
    def _check(self):
        if self.mode == "sell":
            if self.launch_price_per_m2 is None:
                raise ValueError(f"{self.name}: a product for sale needs a launch price")
            if (self.sales_months is None) == (self.units_per_month is None):
                raise ValueError(f"{self.name}: give either sales_months or units_per_month")
            if self.units_per_month is not None and self.sales_curve.kind != "even":
                raise ValueError(f"{self.name}: units_per_month cannot be combined with a sales curve")
            if not self.plan_mix or abs(sum(self.plan_mix.values()) - 1) > 1e-6:
                raise ValueError(f"{self.name}: plan mix must total 100%")
            if self.lease is not None:
                raise ValueError(f"{self.name}: lease terms on a product for sale")
            names = {p.name for p in self.premiums}
            if self.units:
                for p in self.premiums:
                    if p.share_of_units is not None:
                        raise ValueError(f"{self.name}: with a unit list, premium '{p.name}' takes no share")
                for u in self.units:
                    for n in u.premiums:
                        if n not in names:
                            raise ValueError(f"{self.name}: unit {u.id} names unknown premium '{n}'")
            else:
                for p in self.premiums:
                    if p.share_of_units is None:
                        raise ValueError(f"{self.name}: premium '{p.name}' needs a share of units or a unit list")
        else:
            if self.lease is None:
                raise ValueError(f"{self.name}: a leased product needs lease terms")
            used = (self.launch_price_per_m2 is not None or self.sales_months is not None
                    or self.units_per_month is not None or self.premiums or self.units
                    or self.releases or self.plan_mix or self.transaction_tax_pct
                    or self.price_escalation_annual or self.launch_month != 1
                    or self.sales_curve.kind != "even")
            if used:
                raise ValueError(f"{self.name}: sale inputs on a leased product")
        return self


# ---------------------------------------------------------------- payment plans

class ScheduledPayment(_Model):
    months_after_sale: int = Field(ge=0)
    share: float = _share()


class PaymentPlan(_Model):
    """Down payment at sale, equal instalments, and a payment at delivery.

    The instalments share whatever is left after the down payment and the
    delivery payment. `manual_schedule` replaces the instalments when given.
    """
    name: str
    down_payment: float = _share()
    delivery_payment: float = _share(0.0)
    instalment_count: int = Field(default=0, ge=0)
    instalment_every_months: int = Field(default=3, ge=1)
    first_instalment_after_months: Optional[int] = Field(default=None, ge=1)
    price_adjustment: float = Field(default=0.0, gt=-1)
    manual_schedule: list[ScheduledPayment] = []

    @model_validator(mode="after")
    def _check(self):
        fixed = self.down_payment + self.delivery_payment
        if fixed > 1 + 1e-9:
            raise ValueError(f"plan {self.name}: down and delivery payments exceed 100%")
        if self.manual_schedule:
            if self.instalment_count:
                raise ValueError(f"plan {self.name}: manual schedule and instalments both given")
            tot = fixed + sum(p.share for p in self.manual_schedule)
            if abs(tot - 1) > 1e-6:
                raise ValueError(f"plan {self.name}: payments total {tot:.4f}, not 1")
        elif self.instalment_count == 0 and abs(fixed - 1) > 1e-6:
            raise ValueError(f"plan {self.name}: no instalments, so down and delivery must total 100%")
        elif self.instalment_count > 0 and fixed > 1 - 1e-9:
            raise ValueError(f"plan {self.name}: nothing left for the instalments")
        return self


# ---------------------------------------------------------------- sales risk

class SalesRisk(_Model):
    cancellation_rate: float = _share(0.0)
    cancel_after_months: int = Field(default=12, ge=1)
    cancellation_penalty_pct: float = _share(0.0)   # share of collected cash kept
    resale_delay_months: int = Field(default=3, ge=0)
    resale_discount: float = _share(0.0)
    unsold_share: float = Field(default=0.0, ge=0, lt=1)
    clear_unsold: bool = True
    clearance_months: int = Field(default=12, ge=1)
    clearance_discount: float = _share(0.0)


# ---------------------------------------------------------------- costs

CostBase = Literal["building", "building_and_site"]


class Phase(_Model):
    name: str
    start_month: int = Field(ge=1)
    duration_months: int = Field(ge=1)
    spend_curve: Curve = Curve()


class SiteCost(_Model):
    """A cost per m2 of land, such as infrastructure or landscape."""
    name: str
    rate_per_m2_land: float = Field(ge=0)
    start_month: int = Field(ge=1)
    duration_months: int = Field(ge=1)
    spend_curve: Curve = Curve()


class SoftCost(_Model):
    name: str
    pct: float = Field(ge=0)
    base: CostBase = "building_and_site"


class Costs(_Model):
    cost_inflation_annual: float = 0.0
    site_costs: list[SiteCost] = []
    soft_costs: list[SoftCost] = []
    contingency_pct: float = Field(default=0.0, ge=0)
    contingency_base: CostBase = "building_and_site"
    marketing_pct_of_sales: float = _share(0.0)
    commission_pct_of_sales: float = _share(0.0)
    overhead_monthly: float = Field(default=0.0, ge=0)   # month-1 money
    overhead_start_month: int = Field(default=1, ge=1)
    # Blank = until the later of the last delivery and the last sale.
    overhead_end_month: Optional[int] = Field(default=None, ge=1)


# ---------------------------------------------------------------- land deal

class CashPurchase(_Model):
    price_per_m2: float = Field(ge=0)
    area_share: float = Field(default=1.0, gt=0, le=1)   # part of the land bought
    down_payment_pct: float = _share()
    instalment_count: int = Field(default=0, ge=0)
    instalment_every_months: int = Field(default=12, ge=1)
    first_instalment_after_months: Optional[int] = Field(default=None, ge=1)
    interest_annual: float = Field(default=0.0, ge=0)    # on the unpaid balance
    fx_share: float = _share(0.0)                              # part owed in foreign currency
    fx_depreciation_annual: float = 0.0

    @model_validator(mode="after")
    def _check(self):
        if self.instalment_count == 0 and abs(self.down_payment_pct - 1) > 1e-9:
            raise ValueError("land: no instalments, so the down payment must be 100%")
        if self.instalment_count > 0 and self.down_payment_pct >= 1:
            raise ValueError("land: nothing left for the instalments")
        return self


class FeePayment(_Model):
    month: int = Field(ge=1)
    share: float = _share()


class Fee(_Model):
    """An extra land charge with its own schedule, such as a betterment fee."""
    name: str
    amount_per_m2_land: float = Field(ge=0)
    schedule: list[FeePayment]

    @model_validator(mode="after")
    def _check(self):
        s = sum(p.share for p in self.schedule)
        if abs(s - 1) > 1e-6:
            raise ValueError(f"fee {self.name}: schedule totals {s:.4f}, not 1")
        return self


class Partnership(_Model):
    """The owner keeps part of the land and shares in what is built on it."""
    area_share: float = Field(gt=0, le=1)     # part of the project in partnership
    owner_share: float = _share()
    basis: Literal["profit", "revenue"] = "profit"


class LandDeal(_Model):
    cash: Optional[CashPurchase] = None
    fees: list[Fee] = []
    in_kind_share: float = Field(default=0.0, ge=0, lt=1)   # sellable area handed to the owner
    revenue_share: float = Field(default=0.0, ge=0, lt=1)   # of revenue as it is collected
    partnership: Optional[Partnership] = None

    @model_validator(mode="after")
    def _check(self):
        if self.cash and self.partnership:
            if self.cash.area_share + self.partnership.area_share > 1 + 1e-9:
                raise ValueError("land: purchased and partnership parts exceed the whole land")
        return self


# ---------------------------------------------------------------- finance and tax

class Finance(_Model):
    discount_rate_annual: float = Field(gt=-1)
    debt_limit: float = Field(default=0.0, ge=0)
    debt_interest_annual: float = Field(default=0.0, ge=0)


class Tax(_Model):
    income_tax_rate: float = _share(0.0)
    payment_lag_months: int = Field(default=4, ge=1)   # after each 12-month year


# ---------------------------------------------------------------- study

class Study(_Model):
    name: str
    parcels: list[Parcel] = Field(min_length=1)
    conditions: Conditions
    products: list[Product] = Field(min_length=1)
    plans: list[PaymentPlan] = []
    phases: list[Phase] = Field(min_length=1)
    sales_risk: SalesRisk = SalesRisk()
    costs: Costs = Costs()
    land: LandDeal = LandDeal()
    finance: Finance
    tax: Tax = Tax()

    @model_validator(mode="after")
    def _check(self):
        s = sum(p.bua_share for p in self.products)
        if abs(s - 1) > 1e-6:
            raise ValueError(f"product shares of built area total {s:.4f}, not 1")
        for group, label in ((self.products, "product"), (self.plans, "plan"), (self.phases, "phase")):
            names = [x.name for x in group]
            if len(set(names)) != len(names):
                raise ValueError(f"duplicate {label} name")
        plan_names = {p.name for p in self.plans}
        phase_names = {p.name for p in self.phases}
        for p in self.products:
            if p.phase not in phase_names:
                raise ValueError(f"{p.name}: unknown phase '{p.phase}'")
            for n in p.plan_mix:
                if n not in plan_names:
                    raise ValueError(f"{p.name}: unknown payment plan '{n}'")
        c = self.conditions
        if c.far_cap is None and not c.height_zones and c.built_area_m2 is None:
            raise ValueError("conditions give no way to set the built area: "
                             "enter a floor-area cap, height zones, or a built area")
        return self

    @property
    def land_area_m2(self) -> float:
        return sum(p.area_m2 for p in self.parcels)
