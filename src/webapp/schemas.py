"""Validated shapes for the JSON a land record holds.

`LandConditions` is the per-land conditions form the team fills in by hand
(Step 4). Conditions differ by location, so nothing here assumes a rule
library: every field is optional, and anything that does not fit goes into
`custom` rows. A Ministerial Decree is issued only after the land is designed,
so the form records the allotment terms, not a decree.

Shares are fractions (0.25 = 25%). Areas are m2. Months count from the start
of the land deal.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from feasibility.inputs import Conditions, HeightZone

CONDITIONS_VERSION = 1


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _pct():
    return Field(default=None, ge=0, le=1)


# ---------------------------------------------------------------- offer and legal

class Offer(_M):
    deal_type: Literal["", "sale", "partnership", "sale_and_partnership", "other"] = ""
    asking_price_per_feddan: Optional[float] = Field(default=None, ge=0)
    negotiable: bool = False
    # part bought outright, and the partnership on the rest
    purchase_area_m2: Optional[float] = Field(default=None, ge=0)
    partnership_owner_share: Optional[float] = _pct()
    partnership_basis: Literal["profit", "revenue"] = "profit"
    payment_terms: str = ""
    source: str = ""            # who brought the offer
    received_on: str = ""
    notes: str = ""


class Legal(_M):
    title: str = ""             # registered contract, allocation letter, ...
    encumbrances: str = ""
    disputes: str = ""
    approvals: str = ""         # clearances obtained
    betterment_fee_per_m2: Optional[float] = Field(default=None, ge=0)
    betterment_paid_share: Optional[float] = _pct()
    notes: str = ""


class CustomField(_M):
    label: str
    value: str = ""
    unit: str = ""


# ---------------------------------------------------------------- land conditions (Step 4 form)

class ShareRule(_M):
    """A minimum or maximum share for one use, of land or of built area."""
    use: str                                  # residential, commercial, services, open space, roads, ...
    basis: Literal["land", "built_area"] = "built_area"
    min_share: Optional[float] = _pct()
    max_share: Optional[float] = _pct()


class Setbacks(_M):
    front_m: Optional[float] = Field(default=None, ge=0)
    side_m: Optional[float] = Field(default=None, ge=0)
    rear_m: Optional[float] = Field(default=None, ge=0)
    sea_or_lake_m: Optional[float] = Field(default=None, ge=0)
    main_road_m: Optional[float] = Field(default=None, ge=0)
    between_buildings_m: Optional[float] = Field(default=None, ge=0)


class ParkingRule(_M):
    use: str
    spaces: float = Field(gt=0)
    per: str = "unit"                         # unit, 100 m2, room, ...


class PhaseRule(_M):
    name: str
    share_of_built_area: Optional[float] = _pct()
    deadline_months: Optional[int] = Field(default=None, gt=0)


class PaymentTerms(_M):
    """Land payment terms set by the seller or the authority."""
    down_payment_pct: Optional[float] = _pct()
    instalment_count: Optional[int] = Field(default=None, ge=0)
    instalment_every_months: Optional[int] = Field(default=None, ge=1)
    grace_months: Optional[int] = Field(default=None, ge=0)
    interest_annual: Optional[float] = Field(default=None, ge=0)
    notes: str = ""


class Fee(_M):
    name: str
    amount_per_m2_land: Optional[float] = Field(default=None, ge=0)
    lump_sum: Optional[float] = Field(default=None, ge=0)
    notes: str = ""


class LandConditions(_M):
    # land use
    primary_use: Literal["", "residential", "tourism", "mixed_use", "commercial",
                         "administrative", "industrial", "other"] = ""
    permitted_uses: list[str] = []
    authority: str = ""                       # the body that sets the terms
    # intensity
    footprint_pct: Optional[float] = Field(default=None, gt=0, le=1)   # coverage
    far: Optional[float] = Field(default=None, gt=0)                   # built area / land area
    max_built_area_m2: Optional[float] = Field(default=None, gt=0)
    max_floors: Optional[float] = Field(default=None, gt=0)
    max_height_m: Optional[float] = Field(default=None, gt=0)
    height_zones: list[HeightZone] = []
    # density
    max_units_per_feddan: Optional[float] = Field(default=None, gt=0)
    max_persons_per_feddan: Optional[float] = Field(default=None, gt=0)
    persons_per_unit: Optional[float] = Field(default=None, gt=0)
    # land allocation and mix
    services_share_of_land: Optional[float] = _pct()
    open_space_share_of_land: Optional[float] = _pct()
    roads_share_of_land: Optional[float] = _pct()
    share_rules: list[ShareRule] = []
    # placement
    setbacks: Setbacks = Setbacks()
    parking: list[ParkingRule] = []
    # time
    build_deadline_months: Optional[int] = Field(default=None, gt=0)
    phases: list[PhaseRule] = []
    # money
    payment_terms: PaymentTerms = PaymentTerms()
    fees: list[Fee] = []
    # anything else that applies to this land
    custom: list[CustomField] = []
    notes: str = ""

    def to_engine_conditions(self, land_area_m2: float) -> Conditions:
        """The part of the form the feasibility engine uses today.

        Built-area share rules become the engine's category limits. Rules on
        uses the engine does not know (services, open space, ...) are kept on
        the land and wait for the Step 4 layout.
        """
        zones = list(self.height_zones)
        if not zones and self.max_floors and self.footprint_pct:
            zones = [HeightZone(name="all", share_of_footprint=1.0, floors=self.max_floors)]
        mins, maxs = {}, {}
        for r in self.share_rules:
            cat = r.use.strip().lower()
            if r.basis != "built_area" or cat not in ("residential", "commercial", "hotel", "serviced"):
                continue
            if r.min_share is not None:
                mins[cat] = r.min_share
            if r.max_share is not None:
                maxs[cat] = r.max_share
        # The engine builds up to the smallest cap. A built-area ceiling is a
        # cap too, so it is passed as the built area only when it binds.
        caps = []
        if self.far:
            caps.append(land_area_m2 * self.far)
        if zones:
            caps.append(land_area_m2 * self.footprint_pct * sum(z.share_of_footprint * z.floors for z in zones))
        built = None
        if self.max_built_area_m2 and (not caps or self.max_built_area_m2 < min(caps)):
            built = self.max_built_area_m2
        return Conditions(
            far_cap=self.far,
            footprint_pct=self.footprint_pct,
            height_zones=zones,
            built_area_m2=built,
            min_share_by_category=mins,
            max_share_by_category=maxs,
            build_deadline_months=self.build_deadline_months,
        )


# ---------------------------------------------------------------- land record form

class ParcelRow(_M):
    name: str = ""
    area_m2: float = Field(gt=0)
    notes: str = ""


class LandForm(_M):
    """Everything about a land except its conditions, as edited on one page."""
    name: str = Field(min_length=1, max_length=200)
    code: str = ""
    status: Literal["new", "screening", "studying", "go", "no_go", "archived"] = "new"
    region: str = ""
    area_name: str = ""
    governorate: str = ""
    latitude: Optional[float] = Field(default=None, ge=-90, le=90)
    longitude: Optional[float] = Field(default=None, ge=-180, le=180)
    map_url: str = ""
    area_m2: float = Field(gt=0)
    parcels: list[ParcelRow] = []
    frontage_m: Optional[float] = Field(default=None, ge=0)
    depth_m: Optional[float] = Field(default=None, ge=0)
    frontage_type: str = ""
    offer: Offer = Offer()
    legal: Legal = Legal()
    custom_fields: list[CustomField] = []
    notes: str = ""
