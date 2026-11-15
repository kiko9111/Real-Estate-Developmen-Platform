"""Generating layout and massing options for a plot.

For each strategy in the brief the generator:

1. takes the setbacks off the plot (the frontage setback is a band along the
   frontage; the rest is an inset from the other edges);
2. lays a road grid aligned with the frontage: a main spine road from the
   frontage to the far edge, local roads parallel and across it;
3. cuts the buildable land into blocks; pieces too small to build become
   landscape;
4. reserves blocks for open space (central parks) and services (near the
   entrance, at the far end of the spine) until the minimum shares are met;
5. gives every other block a building type, by the strategy's zones (bands of
   depth from the frontage) and their weights;
6. places buildings on each block, with floors cut to the height limit of the
   block's zone;
7. converts blocks between building types until the share rules on built area
   and units hold, where the library allows it;
8. thins buildings evenly if coverage, floor-area ratio, built-area ceiling or
   density is exceeded.

All geometry is in metres in the site's working frame (see geometry.py).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from shapely.geometry import MultiPolygon, Point, Polygon, box
from shapely.ops import unary_union
from shapely.prepared import prep

from feasibility.inputs import FEDDAN_M2

from .brief import HeightBand, LayoutBrief, Strategy, Typology
from .geometry import Site
from .limits import Limits

PARKING_M2_PER_SPACE = 25.0
CATEGORY_LABELS = {"residential": "Residential", "serviced": "Serviced apartments", "hotel": "Hotel",
                   "commercial": "Commercial"}


@dataclass
class Block:
    id: int
    poly: Polygon
    use: str = ""                    # "park", "services", or a typology key
    buildings: list = field(default_factory=list)   # [cx, cy, w, d, angle_deg, floors]
    plots: list = field(default_factory=list)       # plot outlines for houses, [[x, y], ...]
    kind: str = ""                                  # what the block is in the plan: superblock, front, ...
    road_poly: Optional[Polygon] = None             # a loop road inside the block (house clusters)
    green_poly: Optional[Polygon] = None            # a pocket park inside the block
    locked: bool = False                            # set by the team: the mix repair leaves it alone
    floors_cap: Optional[int] = None                # set by the team: no more floors than this

    @property
    def area(self) -> float:
        return self.poly.area

    @property
    def road_area(self) -> float:
        return self.road_poly.area if self.road_poly is not None else 0.0

    @property
    def green_area(self) -> float:
        return self.green_poly.area if self.green_poly is not None else 0.0

    @property
    def net_area(self) -> float:
        """The block less its inner road and pocket park: the plots themselves."""
        return self.area - self.road_area - self.green_area

    @property
    def depth(self) -> float:
        return max(0.0, self.poly.centroid.y)


class Plan:
    """The working state of one option."""

    def __init__(self, site: Site, brief: LayoutBrief, strategy: Strategy, limits: Limits, land_area_m2: float):
        self.site, self.brief, self.strategy, self.limits = site, brief, strategy, limits
        self.land = land_area_m2
        self.notes: list[str] = []
        self.blocks: list[Block] = []
        self.roads = Polygon()
        self.setbacks = Polygon()
        self.leftover = Polygon()
        self.beach = Polygon()
        self.water = Polygon()
        self.green = Polygon()      # parks that are part of the plan's structure (spine, corridors, plaza)
        self.gate = None            # where the entrance gate is, if the plan has one
        self.bands = self._bands()

    # ------------------------------------------------------------ areas

    def fixed_open(self) -> float:
        return self.setbacks.area + self.leftover.area + self.beach.area + self.water.area + self.green.area

    def roads_area(self) -> float:
        return self.roads.area + sum(b.road_area for b in self.blocks)

    def open_area(self) -> float:
        return (self.fixed_open() + sum(b.area for b in self.blocks if b.use == "park")
                + sum(b.green_area for b in self.blocks if b.use != "park"))

    # ------------------------------------------------------------ height

    def _bands(self) -> list[HeightBand]:
        if self.brief.height_bands:
            return list(self.brief.height_bands)
        if self.limits.height_zones:
            # The form gives zones as shares of footprint. Without zones drawn on
            # the ground, lay them as bands from the frontage in the order given.
            out, d0 = [], 0.0
            for name, share, floors in self.limits.height_zones:
                d1 = d0 + share * self.site.depth_m
                out.append(HeightBand(name=name, from_depth_m=d0, to_depth_m=d1, max_floors=floors))
                d0 = d1
            out[-1].to_depth_m = None
            self.notes.append("Height zones from the conditions form were laid as bands from the frontage in the "
                              "order entered; draw them as bands in the brief if they sit differently.")
            return out
        return []

    def band_at(self, depth: float) -> Optional[HeightBand]:
        for b in self.bands:
            if depth >= b.from_depth_m - 1e-6 and (b.to_depth_m is None or depth < b.to_depth_m):
                return b
        return self.bands[-1] if self.bands else None

    def floors_for(self, t: Typology, depth: float, cap: Optional[int] = None) -> int:
        f = t.floors
        caps = [self.limits.max_floors, cap]
        if self.limits.max_height_m:
            caps.append(math.floor(self.limits.max_height_m / t.floor_height_m + 1e-9))
        b = self.band_at(depth)
        if b is not None:
            caps.append(b.max_floors)
            if b.max_height_m:
                caps.append(math.floor(b.max_height_m / t.floor_height_m + 1e-9))
        for c in caps:
            if c is not None:
                f = min(f, c)
        return max(0, int(math.floor(f + 1e-9)))

    # ------------------------------------------------------------ steps 1-3

    def cut(self):
        L, s, site = self.limits, self.strategy, self.site
        poly = site.polygon
        minx, miny, maxx, maxy = poly.bounds
        front = _first(L.setback_sea_m, L.setback_front_m, self.brief.default_setback_m)
        other = _first(max([v for v in (L.setback_side_m, L.setback_rear_m) if v is not None], default=None),
                       self.brief.default_setback_m)
        inner = poly.buffer(-other, join_style=2) if other > 0 else poly
        cutters = [box(minx - 1, miny - 1, maxx + 1, front)]
        if L.setback_main_road_m:
            cutters.append(box(minx - 1, maxy - L.setback_main_road_m, maxx + 1, maxy + 1))
        buildable = inner.difference(unary_union(cutters))
        if buildable.is_empty or buildable.area < 0.05 * poly.area:
            raise ValueError("the setbacks leave almost nothing to build on")
        bminx, bminy, bmaxx, bmaxy = buildable.bounds
        cx = (minx + maxx) / 2
        rects = [box(cx - s.main_road_m / 2, miny - 1, cx + s.main_road_m / 2, maxy + 1)]   # spine, edge to edge
        step = s.block_w_m + s.local_road_m
        x = cx + s.main_road_m / 2 + s.block_w_m
        while x < bmaxx:
            rects.append(box(x, bminy, x + s.local_road_m, bmaxy))
            x += step
        x = cx - s.main_road_m / 2 - s.block_w_m
        while x > bminx:
            rects.append(box(x - s.local_road_m, bminy, x, bmaxy))
            x -= step
        y = bminy + s.block_d_m
        while y < bmaxy - 0.5 * s.block_d_m:
            rects.append(box(bminx, y, bmaxx, y + s.local_road_m))
            y += s.block_d_m + s.local_road_m
        grid = unary_union(rects)
        spine = rects[0].intersection(poly)
        self.roads = unary_union([grid.intersection(buildable), spine])
        self.setbacks = poly.difference(buildable).difference(self.roads)
        pieces = buildable.difference(self.roads)
        small = []
        for g in _polys(pieces):
            if g.area >= self.brief.min_block_m2:
                self.blocks.append(Block(id=len(self.blocks), poly=g))
            else:
                small.append(g)
        self.leftover = unary_union(small) if small else Polygon()
        if not self.blocks:
            raise ValueError("no block is large enough to build on; use larger blocks or a smaller minimum")

    # ------------------------------------------------------------ step 4

    def reserve(self):
        L, land = self.limits, self.land
        scale = land / self.site.area_m2       # shares are of the legal land area
        open_min = max(L.open_space_share or 0.0, self._land_min("open space")) + self.strategy.extra_open_space_share
        serv_min = max(L.services_share or 0.0, self._land_min("services"))
        have_open = self.fixed_open() * scale
        minx, miny, maxx, maxy = self.site.polygon.bounds
        cx, cy = (minx + maxx) / 2, (miny + maxy) / 2
        free = list(self.blocks)
        if serv_min > 0:
            # near the entrance: the far end of the spine
            free.sort(key=lambda b: (-b.poly.centroid.y + 0.4 * abs(b.poly.centroid.x - cx)))
            got = 0.0
            for b in list(free):
                if got >= serv_min * land:
                    break
                b.use = "services"
                got += b.area * scale
                free.remove(b)
        if open_min * land > have_open:
            free.sort(key=lambda b: abs(b.poly.centroid.x - cx) + 0.35 * abs(b.poly.centroid.y - cy))
            got = have_open
            for b in list(free):
                if got >= open_min * land:
                    break
                b.use = "park"
                got += b.area * scale
                free.remove(b)

    def _land_min(self, use: str) -> float:
        vals = [r.min_share for r in self.limits.shares
                if r.basis == "land" and r.min_share is not None and _same_use(r.use, use)]
        return max(vals, default=0.0)

    # ------------------------------------------------------------ step 5

    def assign(self):
        zones = self.strategy.zones
        groups: dict[int, list[Block]] = {i: [] for i in range(len(zones))}
        for b in self.blocks:
            if b.use:
                continue
            share = b.depth / max(self.site.depth_m, 1e-9)
            idx = next((i for i, z in enumerate(zones) if z.from_share - 1e-9 <= share < z.to_share + 1e-9), None)
            if idx is None:
                idx = min(range(len(zones)), key=lambda i: min(abs(share - zones[i].from_share), abs(share - zones[i].to_share)))
            groups[idx].append(b)
        for i, blocks in groups.items():
            mix = {k: w for k, w in zones[i].mix.items() if w > 0}
            tot = sum(mix.values())
            done = {k: 0.0 for k in mix}
            placed = 0.0
            for b in sorted(blocks, key=lambda b: (round(b.poly.centroid.y, -1), b.poly.centroid.x)):
                placed += b.area
                k = max(mix, key=lambda k: mix[k] / tot * placed - done[k])
                b.use = k
                done[k] += b.area

    # ------------------------------------------------------------ step 6

    def fill(self, b: Block):
        b.buildings, b.plots, b.road_poly, b.green_poly = [], [], None, None
        if b.use == "park":
            return
        key = self.brief.services_typology if b.use == "services" else b.use
        if not key:
            return
        t = self.brief.typology(key)
        floors = self.floors_for(t, b.depth, b.floors_cap)
        if floors <= 0:
            return
        gap = max(t.gap_m, self.limits.between_buildings_m or 0.0)
        pb = prep(b.poly.buffer(0.01))
        minx, miny, maxx, maxy = b.poly.bounds
        if t.kind == "landmark":
            w0, d0 = maxx - minx, maxy - miny
            k = math.sqrt(t.block_cover)
            c = b.poly.centroid
            for _ in range(6):            # give up rather than draw a token building
                w, d = w0 * k, d0 * k
                r = box(c.x - w / 2, c.y - d / 2, c.x + w / 2, c.y + d / 2)
                if pb.contains(r):
                    b.buildings.append([c.x, c.y, w, d, 0.0, floors])
                    return
                k *= 0.88
            return
        if t.kind in ("detached", "row"):
            cw, cd = t.plot_w_m, t.plot_d_m
            fw, fd = t.footprint_w_m, t.footprint_d_m
        else:
            fw, fd = t.footprint_w_m, t.footprint_d_m
            cw, cd = fw + gap, fd + gap
        def grid(cw, cd, angle):
            out = []
            nx, ny = int((maxx - minx) // cw), int((maxy - miny) // cd)
            ox = minx + ((maxx - minx) - nx * cw) / 2
            oy = miny + ((maxy - miny) - ny * cd) / 2
            for j in range(ny):
                for i in range(nx):
                    x0, y0 = ox + i * cw, oy + j * cd
                    if pb.contains(box(x0, y0, x0 + cw, y0 + cd)):
                        out.append([x0 + cw / 2, y0 + cd / 2, fw, fd, angle, floors])
            return out

        b.buildings = grid(cw, cd, 0.0)           # long side to the frontage
        if not b.buildings:                       # a narrow block: turn the buildings end-on
            b.buildings = grid(cd, cw, 90.0)
        if not b.buildings and t.kind == "block":
            c = b.poly.centroid
            r = box(c.x - fw / 2, c.y - fd / 2, c.x + fw / 2, c.y + fd / 2)
            if pb.contains(r):
                b.buildings.append([c.x, c.y, fw, fd, 0.0, floors])

    def fill_all(self):
        for b in self.blocks:
            self.fill(b)

    # ------------------------------------------------------------ measuring

    def typ_of(self, b: Block) -> Optional[Typology]:
        if b.use == "park" or not b.use:
            return None
        return self.brief.typology(self.brief.services_typology if b.use == "services" else b.use)

    def measure(self) -> dict:
        """Totals by building type and by category."""
        by_t: dict[str, dict] = {}
        for b in self.blocks:
            t = self.typ_of(b)
            if t is None:
                continue
            r = by_t.setdefault(t.key, {"buildings": 0, "footprint": 0.0, "bua": 0.0, "plots": 0.0, "units": 0.0,
                                        "floors_max": 0, "floors_min": 99})
            r["plots"] += b.net_area
            for x, y, w, d, a, f in b.buildings:
                r["buildings"] += 1
                r["footprint"] += w * d
                r["bua"] += w * d * f
                r["floors_max"] = max(r["floors_max"], f)
                r["floors_min"] = min(r["floors_min"], f)
        for k, r in by_t.items():
            t = self.brief.typology(k)
            sellable = r["bua"] * t.sellable_share
            r["sellable"] = sellable
            r["units"] = r["buildings"] * t.units_per_building if t.units_per_building else sellable / t.unit_size_m2
            if r["floors_min"] == 99:
                r["floors_min"] = 0
        return by_t

    def totals(self, by_t: dict) -> dict:
        fp = sum(r["footprint"] for r in by_t.values())
        bua = sum(r["bua"] for r in by_t.values())
        units = sum(r["units"] for r in by_t.values())
        persons = sum(r["units"] * (self.limits.persons_per_unit or self.brief.typology(k).persons_per_unit)
                      for k, r in by_t.items())
        return {"footprint": fp, "bua": bua, "units": units, "persons": persons}

    def share(self, use: str, basis: str, by_t: dict) -> float:
        if basis == "land":
            if _same_use(use, "roads"):
                a = self.roads_area()
            elif _same_use(use, "open space"):
                a = self.open_area()
            elif _same_use(use, "services"):
                a = sum(b.net_area for b in self.blocks if b.use == "services")
            else:
                a = sum(b.net_area for b in self.blocks if b.use not in ("park", "services", "")
                        and _matches(self.brief.typology(b.use), use))
            return a / self.site.area_m2
        key = "bua" if basis == "built_area" else "units"
        tot = sum(r[key] for r in by_t.values())
        if tot <= 0:
            return 0.0
        return sum(r[key] for k, r in by_t.items() if _matches(self.brief.typology(k), use)) / tot

    # ------------------------------------------------------------ step 7

    def repair_shares(self):
        rules = [r for r in self.limits.shares if r.basis in ("built_area", "units")]
        if not rules:
            return
        strategy_keys = {k for z in self.strategy.zones for k, w in z.mix.items() if w > 0}
        for _ in range(400):
            by_t = self.measure()
            bad = None
            for r in rules:
                v = self.share(r.use, r.basis, by_t)
                if r.min_share is not None and v < r.min_share - 1e-9:
                    bad = (r, "min")
                    break
                if r.max_share is not None and v > r.max_share + 1e-9:
                    bad = (r, "max")
                    break
            if bad is None:
                return
            r, side = bad
            if side == "min":
                cands = [t for t in self.brief.typologies if _matches(t, r.use)]
                cands.sort(key=self._preference(strategy_keys))
                donors = [b for b in self.blocks if b.use not in ("park", "services", "") and not b.locked
                          and not _matches(self.brief.typology(b.use), r.use)
                          and not self._would_break_min(b, rules, by_t)]
                if not cands or not donors:
                    self.notes.append(f"Could not reach the minimum {r.use} share of {r.basis.replace('_', ' ')}: "
                                      "no building type of that use in the library." if not cands else
                                      f"Could not reach the minimum {r.use} share of {r.basis.replace('_', ' ')}.")
                    return
                new = cands[0].key
            else:
                donors = [b for b in self.blocks if b.use not in ("park", "services", "") and not b.locked
                          and _matches(self.brief.typology(b.use), r.use)]
                if not donors:
                    return
                alts = [t for t in self.brief.typologies if t.key in strategy_keys and not _matches(t, r.use)]
                alts.sort(key=self._preference(strategy_keys))
                new = alts[0].key if alts else "park"
            # give up the least valuable land first: cheapest type, then the block furthest from the frontage
            donors.sort(key=lambda b: (self.brief.typology(b.use).price_per_m2 or 0.0, -b.depth))
            b = donors[0]
            b.use = new
            self.fill(b)
        self.notes.append("Stopped adjusting the mix after 400 changes; check the share rules.")

    def _preference(self, strategy_keys):
        """Types the strategy uses first, then types for sale, then the highest price."""
        return lambda t: (t.key not in strategy_keys, t.mode != "sell", -(t.price_per_m2 or 0.0), t.key)

    def _would_break_min(self, b: Block, rules, by_t) -> bool:
        t = self.brief.typology(b.use)
        return any(r.min_share is not None and _matches(t, r.use) for r in rules)

    # ------------------------------------------------------------ step 8

    def enforce_caps(self):
        L = self.limits
        for _ in range(10):
            tot = self.totals(self.measure())
            ratios = []
            if L.coverage and tot["footprint"] > 0:
                ratios.append(("coverage", L.coverage * self.land / tot["footprint"]))
            caps = [c for c in ((L.far * self.land) if L.far else None, L.max_built_area_m2) if c]
            if caps and tot["bua"] > 0:
                ratios.append(("built area", min(caps) / tot["bua"]))
            feddan = self.land / FEDDAN_M2
            if L.max_units_per_feddan and tot["units"] > 0:
                ratios.append(("density", L.max_units_per_feddan * feddan / tot["units"]))
            if L.max_persons_per_feddan and tot["persons"] > 0:
                ratios.append(("population", L.max_persons_per_feddan * feddan / tot["persons"]))
            if not ratios:
                return
            name, s = min(ratios, key=lambda x: x[1])
            if s >= 1 - 1e-9:
                return
            if not any(n.startswith("Buildings thinned") for n in self.notes):
                self.notes.append(f"Buildings thinned evenly to keep within the {name} limit.")
            for b in self.blocks:
                _thin(b, s * 0.995)

    # ------------------------------------------------------------ output

    def result(self) -> dict:
        by_t = self.measure()
        tot = self.totals(by_t)
        land, site_area = self.land, self.site.area_m2
        L = self.limits
        products = []
        for k, r in sorted(by_t.items(), key=lambda kv: -kv[1]["bua"]):
            if r["bua"] <= 0:
                continue
            t = self.brief.typology(k)
            products.append({"key": k, "name": t.name, "category": t.category, "mode": t.mode,
                             "buildings": r["buildings"], "units": r["units"], "plots_m2": r["plots"],
                             "footprint_m2": r["footprint"], "built_area_m2": r["bua"], "sellable_m2": r["sellable"],
                             "floors": [r["floors_min"], r["floors_max"]],
                             "unit_size_m2": (r["sellable"] / r["units"]) if r["units"] else t.unit_size_m2})
        bua = tot["bua"] or 1.0
        for p in products:
            p["share_of_built_area"] = p["built_area_m2"] / bua
        use = [
            ("Roads", self.roads_area()),
            ("Beach", self.beach.area),
            ("Lagoons and water", self.water.area),
            ("Parks, green spine and view corridors", self.green.area + sum(b.area for b in self.blocks if b.use == "park")
             + sum(b.green_area for b in self.blocks if b.use != "park")),
            ("Setbacks and landscape strips", self.setbacks.area + self.leftover.area),
            ("Services plots", sum(b.net_area for b in self.blocks if b.use == "services")),
        ]
        use = [u for u in use if u[1] > 0.5 or u[0] == "Roads"]
        for t in self.brief.typologies:
            a = sum(b.net_area for b in self.blocks if b.use == t.key)
            if a > 0:
                use.append((f"{t.name} plots", a))
        land_use = [{"use": u, "area_m2": a, "share": a / site_area} for u, a in use]
        feddan = land / FEDDAN_M2
        kpis = {
            "site_area_m2": site_area, "land_area_m2": land,
            "footprint_m2": tot["footprint"], "coverage": tot["footprint"] / land,
            "built_area_m2": tot["bua"], "far": tot["bua"] / land,
            "units": tot["units"], "units_per_feddan": tot["units"] / feddan,
            "persons": tot["persons"], "persons_per_feddan": tot["persons"] / feddan,
            "sellable_m2": sum(p["sellable_m2"] for p in products),
            "roads_share": self.roads_area() / site_area,
            "open_space_share": self.share("open space", "land", by_t),
            "services_share": self.share("services", "land", by_t),
            "buildings": sum(p["buildings"] for p in products),
            "max_floors": max((p["floors"][1] for p in products), default=0),
        }
        return {
            "strategy": self.strategy.name, "description": self.strategy.description,
            "kpis": kpis, "products": products, "land_use": land_use,
            "compliance": compliance(self, by_t, kpis),
            "parking": parking(self, by_t),
            "premiums": self.premium_shares(),
            "phasing": self.depth_order(),
            "notes": self.notes,
            "geometry": self.geometry(),
        }

    def premium_shares(self) -> dict:
        """Per building type: the share of its units carrying each view premium.

        A unit carries at most one premium: the largest that applies to its
        building (frontage distance, or distance to water or a park).
        """
        rules = [(p.name, "frontage", p.within_m, p.uplift) for p in self.brief.depth_premiums]
        rules += [(p.name, p.feature, p.within_m, p.uplift) for p in self.brief.view_premiums]
        out: dict[str, dict[str, float]] = {}
        if not rules:
            return out
        from shapely.geometry import Point
        water = self.water if not self.water.is_empty else None
        parks = unary_union([self.green] + [b.poly for b in self.blocks if b.use == "park"]
                            + [b.green_poly for b in self.blocks if b.green_poly is not None])
        parks = None if parks.is_empty else parks
        tot: dict[str, float] = {}
        for b in self.blocks:
            t = self.typ_of(b)
            if t is None:
                continue
            for x, y, w, d, a, f in b.buildings:
                weight = w * d * f
                tot[t.key] = tot.get(t.key, 0.0) + weight
                reach = max(w, d) / 2
                best = None
                for name, feature, within, uplift in rules:
                    if feature == "frontage":
                        dist = max(0.0, y - reach)
                    elif feature == "water" and water is not None:
                        dist = water.distance(Point(x, y)) - reach
                    elif feature == "park" and parks is not None:
                        dist = parks.distance(Point(x, y)) - reach
                    else:
                        continue
                    if dist < within and (best is None or uplift > best[1]):
                        best = (name, uplift)
                if best is not None:
                    o = out.setdefault(t.key, {})
                    o[best[0]] = o.get(best[0], 0.0) + weight
        return {k: {n: v / tot[k] for n, v in d.items()} for k, d in out.items() if tot.get(k)}

    def depth_order(self) -> list[dict]:
        """Built area by building type for each block, frontage first: used to phase the option."""
        rows = []
        for b in sorted(self.blocks, key=lambda b: b.depth):
            t = self.typ_of(b)
            if t is None or not b.buildings:
                continue
            rows.append({"key": t.key, "depth": round(b.depth, 1),
                         "bua": sum(w * d * f for x, y, w, d, a, f in b.buildings)})
        return rows

    def geometry(self) -> dict:
        typ_index = {t.key: i for i, t in enumerate(self.brief.typologies)}
        blocks = []
        for b in self.blocks:
            t = self.typ_of(b)
            blocks.append({"id": b.id, "use": b.use or "", "locked": b.locked, "cap": b.floors_cap,
                           "floors": max((x[5] for x in b.buildings), default=0), "typ": typ_index[t.key] if t else None,
                           "ring": _ring(b.poly),
                           "plots": [[[round(x, 1), round(y, 1)] for x, y in q] for q in b.plots],
                           "b": [[round(x, 1), round(y, 1), round(w, 1), round(d, 1), round(a, 1), f,
                                  round(f * (t.floor_height_m if t else 3.5), 1)] for x, y, w, d, a, f in b.buildings]})
        inner_roads = [b.road_poly for b in self.blocks if b.road_poly is not None]
        greens = [self.green] + [b.green_poly for b in self.blocks if b.green_poly is not None]
        parks = greens + [b.poly for b in self.blocks if b.use == "park"]
        return {
            "site": _ring(self.site.polygon),
            "roads": [_rings(g) for g in _polys(unary_union([self.roads] + inner_roads))],
            "landscape": [_rings(g) for g in _polys(unary_union([self.setbacks, self.leftover]))],
            "beach": [_rings(g) for g in _polys(self.beach)],
            "water": [_rings(g) for g in _polys(self.water)],
            "green": [_rings(g) for g in _polys(unary_union(greens))],
            "blocks": blocks,
            "bounds": [round(v, 1) for v in self.site.polygon.bounds],
            "typologies": [{"key": t.key, "name": t.name, "category": t.category, "kind": t.kind}
                           for t in self.brief.typologies],
            "bands": [{"name": b.name, "from": b.from_depth_m, "to": b.to_depth_m} for b in self.bands],
            "trees": _trees(unary_union(parks)),
            "gate": [round(v, 1) for v in self.gate] if self.gate else None,
            "clubs": [b.id for b in self.blocks if b.kind == "club"],
        }


# ---------------------------------------------------------------- checks

def compliance(plan: Plan, by_t: dict, k: dict) -> list[dict]:
    """The post-design check against the land's conditions.

    This replaces the old decree sheet: the ministerial decree is issued after
    the design, so the design is checked against the allotment terms here.
    """
    L, out = plan.limits, []

    def row(label, required, actual, ok, fmt="pct"):
        out.append({"label": label, "required": required, "actual": actual, "ok": ok, "format": fmt})

    tol = 1e-6
    if L.coverage:
        row("Coverage (footprint / land)", f"at most {L.coverage:.1%}", k["coverage"], k["coverage"] <= L.coverage + tol)
    if L.far:
        row("Floor-area ratio", f"at most {L.far:.2f}", k["far"], k["far"] <= L.far + tol, "ratio")
    if L.max_built_area_m2:
        row("Built area", f"at most {L.max_built_area_m2:,.0f} m2", k["built_area_m2"],
            k["built_area_m2"] <= L.max_built_area_m2 * (1 + tol), "m2")
    if L.max_floors:
        row("Floors", f"at most {L.max_floors:g}", k["max_floors"], k["max_floors"] <= L.max_floors + tol, "num")
    tallest = max((f * plan.typ_of(b).floor_height_m for b in plan.blocks if plan.typ_of(b) for *_, f in b.buildings),
                  default=0.0)
    if L.max_height_m:
        row("Height", f"at most {L.max_height_m:g} m", tallest, tallest <= L.max_height_m + tol, "m")
    for band in plan.bands:
        if band.max_floors or band.max_height_m:
            worst_f, worst_h = 0, 0.0
            for b in plan.blocks:
                t = plan.typ_of(b)
                if t is None or plan.band_at(b.depth) is not band:
                    continue
                for *_, f in b.buildings:
                    worst_f, worst_h = max(worst_f, f), max(worst_h, f * t.floor_height_m)
            req = " and ".join(x for x in (f"{band.max_floors:g} floors" if band.max_floors else "",
                                           f"{band.max_height_m:g} m" if band.max_height_m else "") if x)
            ok = (not band.max_floors or worst_f <= band.max_floors) and (not band.max_height_m or worst_h <= band.max_height_m + tol)
            row(f"Height in {band.name}", f"at most {req}", worst_h, ok, "m")
    if L.max_units_per_feddan:
        row("Density", f"at most {L.max_units_per_feddan:g} units per feddan", k["units_per_feddan"],
            k["units_per_feddan"] <= L.max_units_per_feddan + tol, "num1")
    if L.max_persons_per_feddan:
        row("Population density", f"at most {L.max_persons_per_feddan:g} persons per feddan", k["persons_per_feddan"],
            k["persons_per_feddan"] <= L.max_persons_per_feddan + tol, "num1")
    if L.services_share:
        row("Services land", f"at least {L.services_share:.1%}", k["services_share"], k["services_share"] >= L.services_share - tol)
    if L.open_space_share:
        row("Open space", f"at least {L.open_space_share:.1%}", k["open_space_share"],
            k["open_space_share"] >= L.open_space_share - tol)
    if L.roads_share:
        row("Roads", f"at least {L.roads_share:.1%}", k["roads_share"], k["roads_share"] >= L.roads_share - tol)
    for r in L.shares:
        v = plan.share(r.use, r.basis, by_t)
        parts, ok = [], True
        if r.min_share is not None:
            parts.append(f"at least {r.min_share:.1%}")
            ok = ok and v >= r.min_share - tol
        if r.max_share is not None:
            parts.append(f"at most {r.max_share:.1%}")
            ok = ok and v <= r.max_share + tol
        if parts:
            row(f"{r.use.title()} share of {r.basis.replace('_', ' ')}", " and ".join(parts), v, ok)
    sb = [(n, v) for n, v in (("frontage", _first(L.setback_sea_m, L.setback_front_m)),
                               ("side and rear", max([x for x in (L.setback_side_m, L.setback_rear_m) if x is not None], default=None)),
                               ("main road", L.setback_main_road_m)) if v]
    if getattr(plan, "measured", False):
        # a drawn plan: measure what the architect drew
        from .masterplan import measured_setbacks
        m = measured_setbacks(plan)
        for n, v in sb:
            if n in m and m[n] is not None:
                row(f"Setback from {n}", f"at least {v:g} m", m[n], m[n] >= v - 0.05, "m")
            else:
                row(f"Setback from {n}", f"at least {v:g} m (not measured)", None, True, "m")
        if L.between_buildings_m and m.get("between") is not None:
            row("Distance between buildings", f"at least {L.between_buildings_m:g} m", m["between"],
                m["between"] >= L.between_buildings_m - 0.05, "m")
        return out
    for n, v in sb:
        row(f"Setback from {n}", f"at least {v:g} m", v, True, "m")
    if L.between_buildings_m:
        row("Distance between buildings", f"at least {L.between_buildings_m:g} m", L.between_buildings_m, True, "m")
    return out


def parking(plan: Plan, by_t: dict) -> dict:
    rows, total = [], 0.0
    for p in plan.limits.parking:
        n = 0.0
        for k, r in by_t.items():
            t = plan.brief.typology(k)
            if p.use not in ("all", "", "any") and not _matches(t, p.use):
                continue
            per = p.per.strip().lower()
            if "100" in per:
                n += r["bua"] / 100 * p.spaces
            elif "1000" in per:
                n += r["bua"] / 1000 * p.spaces
            else:
                n += r["units"] * p.spaces
        rows.append({"use": p.use, "rule": f"{p.spaces:g} per {p.per}", "spaces": n})
        total += n
    return {"rows": rows, "spaces": total, "area_m2": total * PARKING_M2_PER_SPACE}


# ---------------------------------------------------------------- entry point

def generate(site: Site, brief: LayoutBrief, limits: Limits, land_area_m2: Optional[float] = None,
             strategies: Optional[list[str]] = None, edits: Optional[dict] = None) -> list[dict]:
    """One option per strategy. A strategy that cannot be laid out returns an error entry.

    `edits` holds the team's changes to blocks, {strategy name: {block id: {"use": ..., "floors": ...}}}.
    They are applied after the mix is assigned; edited blocks are locked, so the mix repair works around
    them. The limits still hold: an edit asking for more floors than a height limit allows is capped, and
    a cap on built area or density still thins every block evenly.
    """
    land = land_area_m2 or site.area_m2
    out = []
    for s in brief.strategies:
        if strategies and s.name not in strategies:
            continue
        try:
            from .resort import ResortPlan
            p = (ResortPlan if s.pattern == "resort" else Plan)(site, brief, s, limits, land)
            p.cut()
            p.reserve()
            p.assign()
            if edits and s.name in edits:
                apply_edits(p, edits[s.name])
            p.fill_all()
            p.repair_shares()
            p.enforce_caps()
            r = p.result()
            if abs(site.area_m2 - land) > 0.02 * land:
                r["notes"].append(f"The drawn boundary encloses {site.area_m2:,.0f} m2 but the land record says "
                                  f"{land:,.0f} m2; ratios use the land record's area.")
            out.append(r)
        except ValueError as e:
            out.append({"strategy": s.name, "description": s.description, "error": str(e)})
    return out


def apply_edits(p: Plan, edits: dict) -> None:
    """Set the use and the floor cap of the blocks the team changed, and lock them."""
    by_id = {b.id: b for b in p.blocks}
    keys = {t.key for t in p.brief.typologies}
    missing = []
    for bid, e in edits.items():
        b = by_id.get(int(bid))
        if b is None:
            missing.append(str(bid))
            continue
        use = e.get("use")
        if use:
            if use not in keys and use not in ("park", "services"):
                raise ValueError(f"block {bid}: unknown use '{use}'")
            b.use = use
        if e.get("floors") is not None:
            f = int(e["floors"])
            if f < 1:
                raise ValueError(f"block {bid}: floors must be at least 1")
            b.floors_cap = f
        b.locked = True
    if missing:
        p.notes.append("Edits for blocks " + ", ".join(missing) + " were skipped: the plan no longer has them "
                       "(the boundary or the strategy changed).")
    if edits:
        p.notes.append(f"{len(edits) - len(missing)} block(s) edited by the team; the mix repair leaves them as set.")


# ---------------------------------------------------------------- helpers

def _trees(g, spacing: float = 14.0, cap: int = 6000) -> list:
    """Tree positions for the drawing: a loose staggered grid over the parks, a little uneven."""
    if g.is_empty:
        return []
    area = g.area
    if area / (spacing * spacing) > cap:
        spacing = math.sqrt(area / cap)
    inner = g.buffer(-2.5)
    if inner.is_empty:
        return []
    pg = prep(inner)
    minx, miny, maxx, maxy = inner.bounds
    out, j = [], 0
    y = miny + spacing / 2
    while y < maxy:
        x = minx + (spacing / 2 if j % 2 else 0.0) + spacing / 4
        i = 0
        while x < maxx:
            # a fixed jitter, so the drawing is the same each time
            h = math.sin(i * 12.9898 + j * 78.233) * 43758.5453
            jx, jy = (h - math.floor(h) - 0.5) * spacing * 0.5, (math.sin(h) * 0.5) * spacing * 0.4
            if pg.contains(Point(x + jx, y + jy)):
                out.append([round(x + jx, 1), round(y + jy, 1), round(2.6 + (h - math.floor(h)) * 2.0, 1)])
            x += spacing
            i += 1
        y += spacing * 0.87
        j += 1
    return out


def _thin(b: Block, s: float):
    n = len(b.buildings)
    if n == 0:
        return
    if n == 1:
        cx, cy, w, d, a, f = b.buildings[0]
        k = math.sqrt(max(s, 0.0))
        b.buildings = [[cx, cy, w * k, d * k, a, f]]
        return
    keep = int(math.floor(n * s + 1e-9))
    if keep >= n:
        return
    idx = sorted({min(n - 1, int((i + 0.5) * n / keep)) for i in range(keep)}) if keep > 0 else []
    b.buildings = [b.buildings[i] for i in idx]


def _matches(t: Typology, use: str) -> bool:
    u = use.strip().lower()
    return u in (t.category, t.key.lower(), t.name.lower()) or (u in ("hotels", "touristic", "tourism") and t.category in ("hotel", "serviced"))


def _same_use(a: str, b: str) -> bool:
    norm = {"open space": "open space", "open spaces": "open space", "green": "open space", "green areas": "open space",
            "parks": "open space", "landscape": "open space", "services": "services", "service": "services",
            "roads": "roads", "road": "roads", "streets": "roads"}
    return norm.get(a.strip().lower(), a.strip().lower()) == norm.get(b.strip().lower(), b.strip().lower())


def _first(*vals):
    for v in vals:
        if v is not None:
            return v
    return None


def _polys(g) -> list[Polygon]:
    if g.is_empty:
        return []
    if isinstance(g, Polygon):
        return [g]
    if isinstance(g, MultiPolygon):
        return list(g.geoms)
    return [x for x in getattr(g, "geoms", []) if isinstance(x, Polygon) and not x.is_empty]


def _rings(p: Polygon) -> list:
    """The outline and its holes, for drawing with the even-odd rule."""
    return [_ring(p)] + [[[round(x, 1), round(y, 1)] for x, y in r.coords[:-1]] for r in p.interiors]


def _ring(p: Polygon) -> list:
    return [[round(x, 1), round(y, 1)] for x, y in p.exterior.coords[:-1]]
