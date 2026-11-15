"""The design brief: what the layout generator is asked to lay out.

The limits come from the land's conditions form (coverage, floor-area ratio,
floors, height, density, open space, services, setbacks, share rules). The
brief adds the design choices: the boundary, height zones on the ground, the
building types the team would build here with their prices and costs, and the
strategies to try. Every number in the default building library is a
placeholder for the team to replace per land.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from feasibility.inputs import Lease


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Boundary(_M):
    """The plot outline. Edge i runs from point i to point i + 1."""
    crs: Literal["lonlat", "metres"] = "metres"
    points: list[tuple[float, float]] = Field(min_length=3)
    frontage_edge: int = Field(default=0, ge=0)      # the sea, lake or main-road edge
    source: str = ""                                 # drawn, kml, dxf, typed, rectangle


class HeightBand(_M):
    """A height limit that applies to a band of the land, measured from the frontage edge."""
    name: str = "zone"
    from_depth_m: float = Field(default=0.0, ge=0)
    to_depth_m: Optional[float] = Field(default=None, gt=0)   # blank = to the far edge
    max_floors: Optional[float] = Field(default=None, gt=0)
    max_height_m: Optional[float] = Field(default=None, gt=0)


class DepthPremium(_M):
    """A price uplift for units whose building lies within a distance of the frontage."""
    name: str
    within_m: float = Field(gt=0)
    uplift: float = Field(gt=-1)


Kind = Literal["detached", "row", "block", "landmark"]
Category = Literal["residential", "serviced", "hotel", "commercial"]


class Typology(_M):
    """A building type and how it sits on its block.

    detached  one building on its own plot (villa, twin house)
    row       a row of attached houses on a plot strip (townhouses)
    block     repeated apartment buildings with a gap between them
    landmark  one large building per block covering a share of it (hotel, mall)
    """
    key: str = Field(min_length=1, max_length=40)
    name: str
    kind: Kind
    category: Category
    mode: Literal["sell", "lease"] = "sell"
    floors: float = Field(gt=0)                    # wanted; cut to the zone limit
    floor_height_m: float = Field(default=3.5, gt=0)
    footprint_w_m: float = Field(default=20, gt=0)   # along the road
    footprint_d_m: float = Field(default=15, gt=0)
    plot_w_m: Optional[float] = Field(default=None, gt=0)   # detached and row: plot size
    plot_d_m: Optional[float] = Field(default=None, gt=0)
    gap_m: float = Field(default=12, ge=0)           # block: between buildings
    block_cover: float = Field(default=0.35, gt=0, le=0.9)   # landmark: share of its block built on
    units_per_building: Optional[int] = Field(default=None, gt=0)   # blank = from the unit size
    unit_size_m2: float = Field(default=120, gt=0)   # sellable m2 per unit
    sellable_share: float = Field(default=0.85, gt=0, le=1)
    persons_per_unit: float = Field(default=4, gt=0)
    price_per_m2: Optional[float] = Field(default=None, ge=0)     # sellable m2, today's money
    price_escalation_annual: Optional[float] = None               # blank = as in the base study
    hard_cost_per_m2: float = Field(ge=0)                         # built m2, today's money
    sales_months: int = Field(default=36, ge=1)
    presale_months: int = Field(default=6, ge=0)                  # launch this long before its phase starts
    plan_mix: dict[str, float] = {}                               # blank = as the base study
    lease: Optional[Lease] = None

    @model_validator(mode="after")
    def _check(self):
        if self.kind in ("detached", "row") and (self.plot_w_m is None or self.plot_d_m is None):
            raise ValueError(f"{self.name}: a {self.kind} type needs a plot width and depth")
        if self.kind in ("detached", "row"):
            if self.footprint_w_m > self.plot_w_m or self.footprint_d_m > self.plot_d_m:
                raise ValueError(f"{self.name}: the building is larger than its plot")
        if self.mode == "sell" and self.price_per_m2 is None:
            raise ValueError(f"{self.name}: a type for sale needs a price")
        if self.mode == "lease" and self.lease is None:
            raise ValueError(f"{self.name}: a leased type needs lease terms")
        return self


class StrategyZone(_M):
    """Which building types go in a band of the land, by share of depth from the frontage."""
    from_share: float = Field(ge=0, le=1)
    to_share: float = Field(gt=0, le=1)
    mix: dict[str, float]                       # typology key -> weight


class Strategy(_M):
    name: str
    description: str = ""
    block_w_m: float = Field(default=140, gt=10)     # along the frontage
    block_d_m: float = Field(default=70, gt=10)
    local_road_m: float = Field(default=14, gt=0)
    main_road_m: float = Field(default=30, gt=0)
    extra_open_space_share: float = Field(default=0.0, ge=0, le=0.5)   # beyond the minimum
    zones: list[StrategyZone] = Field(min_length=1)


class LayoutBrief(_M):
    boundary: Optional[Boundary] = None
    height_bands: list[HeightBand] = []      # blank = the conditions' floors and height everywhere
    default_setback_m: float = Field(default=6.0, ge=0)   # where the conditions give none
    min_block_m2: float = Field(default=1500, gt=0)       # smaller pieces become landscape
    depth_premiums: list[DepthPremium] = []
    site_costs: Literal["from_study", "from_layout"] = "from_study"
    road_cost_per_m2: float = Field(default=3500, ge=0)          # from_layout only
    landscape_cost_per_m2: float = Field(default=900, ge=0)     # from_layout only
    services_typology: str = "commercial"        # what is built on service plots
    typologies: list[Typology] = Field(min_length=1)
    strategies: list[Strategy] = Field(min_length=1)

    @model_validator(mode="after")
    def _check(self):
        keys = [t.key for t in self.typologies]
        if len(set(keys)) != len(keys):
            raise ValueError("two building types share a key")
        names = [s.name for s in self.strategies]
        if len(set(names)) != len(names):
            raise ValueError("two strategies share a name")
        for s in self.strategies:
            for z in s.zones:
                if z.to_share <= z.from_share:
                    raise ValueError(f"{s.name}: a zone ends before it starts")
                for k, w in z.mix.items():
                    if k not in keys:
                        raise ValueError(f"{s.name}: unknown building type '{k}'")
                    if w < 0:
                        raise ValueError(f"{s.name}: negative weight for '{k}'")
                if not z.mix or sum(z.mix.values()) <= 0:
                    raise ValueError(f"{s.name}: a zone has no building types")
        if self.services_typology and self.services_typology not in keys:
            raise ValueError(f"services building type '{self.services_typology}' is not in the library")
        return self

    def typology(self, key: str) -> Typology:
        for t in self.typologies:
            if t.key == key:
                return t
        raise KeyError(key)


# ---------------------------------------------------------------- defaults

_HOTEL_LEASE = Lease(rent_per_m2_month=900, stable_occupancy=0.65, ramp_months=24, rent_growth_annual=0.08,
                     opex_pct_of_rent=0.25, hold_months=120, exit_cap_rate=0.10, exit_cost_pct=0.02)

DEFAULT_TYPOLOGIES = [
    Typology(key="villa", name="Standalone villa", kind="detached", category="residential", floors=2,
             footprint_w_m=15, footprint_d_m=16, plot_w_m=24, plot_d_m=32, units_per_building=1,
             sellable_share=0.95, persons_per_unit=6, price_per_m2=90_000, hard_cost_per_m2=28_000, sales_months=36),
    Typology(key="twin", name="Twin house", kind="detached", category="residential", floors=2,
             footprint_w_m=22, footprint_d_m=14, plot_w_m=30, plot_d_m=30, units_per_building=2,
             sellable_share=0.93, persons_per_unit=5, price_per_m2=80_000, hard_cost_per_m2=26_000, sales_months=36),
    Typology(key="townhouse", name="Townhouse row", kind="row", category="residential", floors=2,
             footprint_w_m=48, footprint_d_m=13, plot_w_m=56, plot_d_m=28, units_per_building=6,
             sellable_share=0.92, persons_per_unit=5, price_per_m2=72_000, hard_cost_per_m2=24_000, sales_months=42),
    Typology(key="chalet", name="Chalet building", kind="block", category="residential", floors=3,
             footprint_w_m=40, footprint_d_m=16, gap_m=14, unit_size_m2=110, sellable_share=0.85,
             persons_per_unit=4, price_per_m2=65_000, hard_cost_per_m2=22_000, sales_months=42),
    Typology(key="apartment", name="Apartment building", kind="block", category="residential", floors=7,
             footprint_w_m=45, footprint_d_m=18, gap_m=22, unit_size_m2=120, sellable_share=0.82,
             persons_per_unit=4, price_per_m2=55_000, hard_cost_per_m2=21_000, sales_months=48),
    Typology(key="serviced", name="Serviced apartments", kind="block", category="serviced", floors=6,
             footprint_w_m=40, footprint_d_m=18, gap_m=20, unit_size_m2=75, sellable_share=0.80,
             persons_per_unit=3, price_per_m2=70_000, hard_cost_per_m2=25_000, sales_months=42),
    Typology(key="hotel", name="Hotel", kind="landmark", category="hotel", mode="lease", floors=5,
             block_cover=0.30, unit_size_m2=45, sellable_share=0.70, persons_per_unit=2,
             hard_cost_per_m2=35_000, lease=_HOTEL_LEASE),
    Typology(key="commercial", name="Commercial and services", kind="landmark", category="commercial", floors=2,
             block_cover=0.35, unit_size_m2=80, sellable_share=0.75, persons_per_unit=0.5,
             price_per_m2=80_000, hard_cost_per_m2=20_000, sales_months=36),
]

DEFAULT_STRATEGIES = [
    Strategy(name="Low-rise resort", description="Villas on the frontage, twin houses and townhouses behind, low chalets at the back.",
             block_w_m=150, block_d_m=64, zones=[
                 StrategyZone(from_share=0, to_share=0.2, mix={"villa": 0.8, "hotel": 0.2}),
                 StrategyZone(from_share=0.2, to_share=0.55, mix={"twin": 0.5, "townhouse": 0.5}),
                 StrategyZone(from_share=0.55, to_share=1, mix={"townhouse": 0.4, "chalet": 0.6})]),
    Strategy(name="Balanced", description="Villas on the frontage, chalets in the middle, apartments and serviced units at the back.",
             block_w_m=140, block_d_m=70, zones=[
                 StrategyZone(from_share=0, to_share=0.15, mix={"villa": 0.7, "hotel": 0.3}),
                 StrategyZone(from_share=0.15, to_share=0.5, mix={"chalet": 0.7, "townhouse": 0.3}),
                 StrategyZone(from_share=0.5, to_share=1, mix={"apartment": 0.6, "serviced": 0.4})]),
    Strategy(name="Dense mid-rise", description="Chalets and apartment buildings throughout at the highest permitted floors, with more open space.",
             block_w_m=130, block_d_m=80, extra_open_space_share=0.05, zones=[
                 StrategyZone(from_share=0, to_share=0.15, mix={"chalet": 0.8, "hotel": 0.2}),
                 StrategyZone(from_share=0.15, to_share=1, mix={"apartment": 0.65, "serviced": 0.35})]),
    Strategy(name="Hotel-led", description="Hotels and villas on the frontage, serviced apartments and chalets behind.",
             block_w_m=140, block_d_m=75, zones=[
                 StrategyZone(from_share=0, to_share=0.2, mix={"hotel": 0.5, "villa": 0.5}),
                 StrategyZone(from_share=0.2, to_share=0.6, mix={"serviced": 0.6, "chalet": 0.4}),
                 StrategyZone(from_share=0.6, to_share=1, mix={"apartment": 0.7, "townhouse": 0.3})]),
]


def default_brief(boundary: Optional[Boundary] = None) -> LayoutBrief:
    return LayoutBrief(boundary=boundary, typologies=DEFAULT_TYPOLOGIES, strategies=DEFAULT_STRATEGIES,
                       depth_premiums=[DepthPremium(name="First line to the frontage", within_m=150, uplift=0.25),
                                       DepthPremium(name="Second line", within_m=400, uplift=0.10)])
