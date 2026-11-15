"""The limits a layout must respect, read from the land's conditions form.

The conditions form lives in the web app (`webapp.schemas.LandConditions`).
This module copies the fields the layout uses into a plain object, so the
layout package does not depend on the web app. Anything blank is no limit.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ShareLimit:
    use: str                      # residential, hotel, services, open space, roads, a building type key, ...
    basis: str                    # land, built_area, units
    min_share: Optional[float] = None
    max_share: Optional[float] = None


@dataclass
class ParkingLimit:
    use: str
    spaces: float
    per: str                      # unit, 100 m2, room, ...


@dataclass
class Limits:
    coverage: Optional[float] = None             # footprint / land
    far: Optional[float] = None                  # built area / land
    max_built_area_m2: Optional[float] = None
    max_floors: Optional[float] = None
    max_height_m: Optional[float] = None
    height_zones: list = field(default_factory=list)   # [(name, share_of_footprint, floors)] from the form
    max_units_per_feddan: Optional[float] = None
    max_persons_per_feddan: Optional[float] = None
    persons_per_unit: Optional[float] = None
    services_share: Optional[float] = None       # minimum share of land
    open_space_share: Optional[float] = None     # minimum share of land
    roads_share: Optional[float] = None          # minimum share of land
    shares: list[ShareLimit] = field(default_factory=list)
    setback_front_m: Optional[float] = None
    setback_side_m: Optional[float] = None
    setback_rear_m: Optional[float] = None
    setback_sea_m: Optional[float] = None
    setback_main_road_m: Optional[float] = None
    between_buildings_m: Optional[float] = None
    parking: list[ParkingLimit] = field(default_factory=list)

    @classmethod
    def from_conditions(cls, c) -> "Limits":
        """From a `webapp.schemas.LandConditions` (or anything with the same fields)."""
        sb = c.setbacks
        return cls(
            coverage=c.footprint_pct, far=c.far, max_built_area_m2=c.max_built_area_m2,
            max_floors=c.max_floors, max_height_m=c.max_height_m,
            height_zones=[(z.name, z.share_of_footprint, z.floors) for z in c.height_zones],
            max_units_per_feddan=c.max_units_per_feddan, max_persons_per_feddan=c.max_persons_per_feddan,
            persons_per_unit=c.persons_per_unit,
            services_share=c.services_share_of_land, open_space_share=c.open_space_share_of_land,
            roads_share=c.roads_share_of_land,
            shares=[ShareLimit(use=r.use.strip().lower(), basis=r.basis, min_share=r.min_share, max_share=r.max_share)
                    for r in c.share_rules],
            setback_front_m=sb.front_m, setback_side_m=sb.side_m, setback_rear_m=sb.rear_m,
            setback_sea_m=sb.sea_or_lake_m, setback_main_road_m=sb.main_road_m,
            between_buildings_m=sb.between_buildings_m,
            parking=[ParkingLimit(use=p.use.strip().lower(), spaces=p.spaces, per=p.per) for p in c.parking],
        )
