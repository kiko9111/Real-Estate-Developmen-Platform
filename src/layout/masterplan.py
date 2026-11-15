"""An architect's master plan, imported from DXF, measured and checked like a generated option.

The drawing holds closed polylines on layers. The team says what each layer is:

- boundary   the plot boundary (one closed polyline; the largest is used)
- road, park, water, beach   areas of the plan
- services, or a building type key (villa, chalet, ...)   plots or blocks of that use
- buildings  building footprints; each takes the type of the block it stands in
- ignore

A layer can also carry a floor count. On a buildings layer it is the floors of those footprints; on a
plot layer it caps the floors of the buildings the generator places when the block has no footprints
drawn. Blocks without footprints are filled the same way as the generated options, so a plan that only
shows land uses still gets a built area.

Nothing is moved, thinned or converted to meet the conditions: this is the architect's design, so the
compliance check reports it as drawn, with setbacks and distances between buildings measured.
"""
from __future__ import annotations

import io
import math
import re
from typing import Optional

from shapely.geometry import Polygon, box
from shapely.ops import unary_union
from shapely.prepared import prep
from shapely.strtree import STRtree

from .brief import LayoutBrief, Strategy
from .generator import Block, _polys
from .geometry import BoundaryError, make_site
from .limits import Limits
from .resort import ResortPlan, rect

AREA_USES = ("road", "park", "water", "beach")
MAX_ENTITIES = 60_000

GUESS = [  # first match wins; layer names are compared in lower case
    ("boundary", r"bound|site.?lim|plot.?lim|land.?lim|property|حدود"),
    ("buildings", r"bldg|build|footprint|foot.?print|مبان|مبنى"),
    ("road", r"road|street|st\b|carriage|asphalt|paving|طرق|شارع"),
    ("water", r"water|lagoon|lake|pool|canal|بحير|مياه"),
    ("beach", r"beach|sand|شاطئ"),
    ("park", r"green|park|landscape|garden|lawn|حدائق|خضر"),
    ("services", r"servic|amenit|mosque|school|clinic|club|خدم"),
]


# ---------------------------------------------------------------- reading the drawing

def read_dxf(data: bytes) -> dict:
    """Closed outlines per layer, in metres: {"layers": {name: [[[x, y], ...], ...]}, "units": ...}."""
    from ezdxf import recover

    try:
        doc, _ = recover.read(io.BytesIO(data))
    except Exception as e:  # ezdxf raises several kinds
        raise BoundaryError(f"not a readable DXF file ({e})") from None
    units = doc.header.get("$INSUNITS", 6)
    scale = {1: 0.0254, 2: 0.3048, 4: 0.001, 5: 0.01, 6: 1.0}.get(units, 1.0)
    layers: dict[str, list] = {}
    count = 0

    def take(e, layer):
        nonlocal count
        ring = _ring_of(e)
        if ring is None:
            return
        count += 1
        if count > MAX_ENTITIES:
            raise BoundaryError(f"the drawing has more than {MAX_ENTITIES:,} closed outlines; "
                                "export only the master plan layers")
        layers.setdefault(layer, []).append([[round(x * scale, 3), round(y * scale, 3)] for x, y in ring])

    for e in doc.modelspace():
        if e.dxftype() == "INSERT":
            try:
                subs = list(e.virtual_entities())
            except Exception:
                continue
            for s in subs:
                take(s, s.dxf.layer if s.dxf.layer not in ("0", "") else e.dxf.layer)
        else:
            take(e, e.dxf.layer)
    if not layers:
        raise BoundaryError("no closed polylines found in the drawing; plots, roads and buildings must be "
                            "closed polylines on their own layers")
    return {"layers": layers, "units": units}


def _ring_of(e) -> Optional[list]:
    kind = e.dxftype()
    try:
        if kind == "LWPOLYLINE":
            pts = [(float(p[0]), float(p[1])) for p in e.get_points("xy")]
            closed = e.closed or (len(pts) > 2 and _same(pts[0], pts[-1]))
        elif kind == "POLYLINE":
            pts = [(float(v.dxf.location.x), float(v.dxf.location.y)) for v in e.vertices]
            closed = e.is_closed or (len(pts) > 2 and _same(pts[0], pts[-1]))
        elif kind == "CIRCLE":
            c, r = e.dxf.center, float(e.dxf.radius)
            pts = [(c.x + r * math.cos(a * math.pi / 16), c.y + r * math.sin(a * math.pi / 16)) for a in range(32)]
            closed = True
        else:
            return None
    except Exception:
        return None
    if not closed or len(pts) < 3:
        return None
    if _same(pts[0], pts[-1]):
        pts = pts[:-1]
    return pts if len(pts) >= 3 else None


def _same(a, b):
    return abs(a[0] - b[0]) < 1e-6 and abs(a[1] - b[1]) < 1e-6


def guess_mapping(layers: dict, brief: LayoutBrief) -> dict:
    """A first guess at what each layer is, from its name. The team checks it."""
    out = {}
    for name in layers:
        low = name.lower()
        use = "ignore"
        for t in brief.typologies:
            if re.search(rf"\b{re.escape(t.key)}", low) or t.name.lower() in low:
                use = t.key
                break
        else:
            for u, pat in GUESS:
                if re.search(pat, low):
                    use = u
                    break
        out[name] = {"use": use, "floors": _floors_in(low)}
    if not any(m["use"] == "boundary" for m in out.values()):
        # the layer holding the largest outline is most likely the boundary
        big = max(layers, key=lambda n: max(abs(Polygon(r).area) for r in layers[n]))
        out[big]["use"] = "boundary"
    return out


def _floors_in(name: str) -> Optional[int]:
    m = re.search(r"(?:^|[^a-z])(?:g\+(\d+)|(\d+)\s*(?:f|fl|floors?)\b)", name)
    if not m:
        return None
    return int(m.group(1)) + 1 if m.group(1) else int(m.group(2))


def layer_summary(layers: dict) -> list[dict]:
    return [{"name": n, "count": len(rs), "area_m2": sum(abs(Polygon(r).area) for r in rs if len(r) >= 3)}
            for n, rs in sorted(layers.items())]


def boundary_ring(layers: dict, mapping: dict) -> list:
    rings = [r for n, rs in layers.items() if mapping.get(n, {}).get("use") == "boundary" for r in rs]
    if not rings:
        raise BoundaryError("say which layer holds the plot boundary")
    return max(rings, key=lambda r: abs(Polygon(r).area))


# ---------------------------------------------------------------- the plan

class ImportedPlan(ResortPlan):
    """A plan whose roads, parks and blocks come from the drawing instead of being generated."""

    measured = True

    def load(self, layers: dict, mapping: dict, uses: set):
        site = self.site.polygon
        polys: dict[str, list] = {}
        floors_of: dict[str, Optional[int]] = {}
        dropped = 0
        for name, rings in layers.items():
            m = mapping.get(name) or {}
            use = m.get("use", "ignore")
            if use in ("ignore", "boundary", ""):
                continue
            if use not in uses:
                raise ValueError(f"layer '{name}': unknown use '{use}'")
            for r in rings:
                g = _clean(self.site.to_frame(Polygon(r)))
                if g is None:
                    continue
                inside = g.intersection(site)
                if inside.area < 1:
                    dropped += 1
                    continue
                polys.setdefault(use, []).append((inside, m.get("floors"), name))
        if dropped:
            self.notes.append(f"{dropped} outline(s) lie outside the boundary and were left out.")
        area = {u: unary_union([g for g, *_ in polys.get(u, [])]) for u in AREA_USES}
        self.roads, self.water, self.beach, self.green = area["road"], area["water"], area["beach"], area["park"]
        taken = unary_union(list(area.values()))
        # blocks: plots of services or a building type, less any road, water or park drawn over them
        bid = 0
        for use, items in polys.items():
            if use in AREA_USES or use == "buildings":
                continue
            for g, floors, layer in items:
                for part in _polys(g.difference(taken)):
                    if part.area < 20:
                        continue
                    self.blocks.append(Block(id=bid, poly=part, use=use, kind="drawn",
                                             floors_cap=int(floors) if floors else None))
                    bid += 1
        if not self.blocks:
            raise ValueError("no plots found: map at least one layer to a building type or to services")
        # a later layer drawn over an earlier one wins; keep blocks from overlapping
        seen = Polygon()
        for b in self.blocks:
            if not seen.is_empty and b.poly.intersects(seen):
                rest = [p for p in _polys(b.poly.difference(seen)) if p.area >= 20]
                b.poly = max(rest, key=lambda p: p.area) if rest else Polygon()
            seen = seen.union(b.poly)
        self.blocks = [b for b in self.blocks if not b.poly.is_empty]
        used = unary_union([taken] + [b.poly for b in self.blocks])
        self.leftover = site.difference(used)
        self.setbacks = Polygon()
        # footprints drawn by the architect
        self.drawn: dict[int, list] = {}
        foot = polys.get("buildings", [])
        tree = STRtree([b.poly for b in self.blocks])
        orphans = 0
        default_floors = []
        for g, floors, layer in foot:
            for part in _polys(g):
                if part.area < 10:
                    continue
                c = part.representative_point()
                hits = [i for i in tree.query(c) if self.blocks[i].poly.buffer(0.5).contains(c)]
                if not hits:
                    orphans += 1
                    continue
                b = self.blocks[hits[0]]
                if b.use == "park":
                    continue
                if not floors:
                    default_floors.append(layer)
                self.drawn.setdefault(b.id, []).append((part, int(floors) if floors else None))
        if orphans:
            self.notes.append(f"{orphans} building footprint(s) stand outside every plot, so they have no building "
                              "type and were left out. Draw the plot around them on a plot layer.")
        if default_floors:
            self.notes.append("Footprints on " + ", ".join(sorted(set(default_floors))) + " have no floor count; "
                              "each takes its building type's floors within the height limits.")
        n_drawn = sum(len(v) for v in self.drawn.values())
        n_filled = sum(1 for b in self.blocks if b.use != "park" and b.id not in self.drawn)
        self.notes.append(f"Imported from the drawing: {len(self.blocks)} plots, {n_drawn} building footprints as drawn"
                          + (f"; {n_filled} plots without footprints were filled with the building library's types"
                             if n_filled else "") + ". Nothing was moved or thinned to meet the conditions.")

    def fill(self, b: Block):
        drawn = self.drawn.get(b.id)
        t = self.typ_of(b)
        if not drawn or t is None:
            if t is not None and t.kind in ("detached", "row") and b.area < 2.5 * t.plot_w_m * t.plot_d_m:
                return self._one_house(b, t)
            return super().fill(b)
        b.buildings, b.plots, b.road_poly, b.green_poly = [], [], None, None
        allowed = self.floors_for(t, b.depth, b.floors_cap)
        for part, floors in drawn:
            r = part.minimum_rotated_rectangle
            cs = list(r.exterior.coords)[:4]
            e1, e2 = math.dist(cs[0], cs[1]), math.dist(cs[1], cs[2])
            ang = math.degrees(math.atan2(cs[1][1] - cs[0][1], cs[1][0] - cs[0][0]))
            # the footprint's own area, kept by shrinking the bounding rectangle evenly
            k = math.sqrt(part.area / max(e1 * e2, 1e-9))
            c = r.centroid
            # drawn floors are the design as drawn: the check reports them, they are not cut
            f = floors if floors else allowed
            b.buildings.append([c.x, c.y, e1 * k, e2 * k, ang, f])

    def _one_house(self, b: Block, t):
        """A plot drawn for one house: the house in the middle, turned to the plot."""
        b.buildings, b.plots, b.road_poly, b.green_poly = [], [], None, None
        floors = self.floors_for(t, b.depth, b.floors_cap)
        if floors <= 0:
            return
        r = b.poly.minimum_rotated_rectangle
        cs = list(r.exterior.coords)[:4]
        e1, e2 = math.dist(cs[0], cs[1]), math.dist(cs[1], cs[2])
        ang = math.degrees(math.atan2(cs[1][1] - cs[0][1], cs[1][0] - cs[0][0]))
        if e2 > e1:
            ang += 90
        c = b.poly.representative_point()
        pb = prep(b.poly)
        for k in (1.0, 0.85, 0.7, 0.55):
            w, d = t.footprint_w_m * k, t.footprint_d_m * k
            if pb.contains(rect(c.x, c.y, w, d, ang)):
                b.buildings.append([c.x, c.y, w, d, ang, floors])
                b.plots.append([[round(x, 1), round(y, 1)] for x, y in b.poly.exterior.coords])
                return


def _clean(g) -> Optional[Polygon]:
    if not g.is_valid:
        g = g.buffer(0)
    return g if not g.is_empty and g.area > 1 else None


def import_option(source: dict, mapping: dict, frontage_edge: int, brief: LayoutBrief, limits: Limits,
                  land_area_m2: Optional[float] = None, name: str = "Architect's plan") -> dict:
    """Measure the drawing like a generated option: area schedule, compliance, premiums, phasing."""
    layers = source["layers"]
    ring = boundary_ring(layers, mapping)
    site = make_site([tuple(p) for p in ring], frontage_edge)
    land = land_area_m2 or site.area_m2
    uses = set(AREA_USES) | {"buildings", "services"} | {t.key for t in brief.typologies}
    strategy = Strategy(name=name, description="Imported from the architect's drawing and measured as drawn.",
                        zones=[{"from_share": 0, "to_share": 1, "mix": {brief.typologies[0].key: 1}}])
    p = ImportedPlan(site, brief, strategy, limits, land)
    p.load(layers, mapping, uses)
    p.fill_all()
    r = p.result()
    if abs(site.area_m2 - land) > 0.02 * land:
        r["notes"].append(f"The drawn boundary encloses {site.area_m2:,.0f} m2 but the land record says "
                          f"{land:,.0f} m2; ratios use the land record's area.")
    r["imported"] = True
    return r


# ---------------------------------------------------------------- measured setbacks

def measured_setbacks(plan) -> dict:
    """The smallest distances in the drawing: buildings to the frontage, to the other edges, and to each other."""
    foot = []
    for b in plan.blocks:
        for q in b.buildings:
            x, y, w, d, a = q[:5]
            foot.append(rect(x, y, w, d, a))
    if not foot:
        return {}
    site = plan.site.polygon
    other = site.exterior.difference(box(-1e7, -1.0, 1e7, 0.01))     # every edge but the frontage
    front = min(g.bounds[1] for g in foot)
    side = min(g.distance(other) for g in foot) if not other.is_empty else None
    tree = STRtree(foot)
    between = None
    for i, g in enumerate(foot):
        for j in tree.query(g.buffer(60)):
            if j <= i:
                continue
            dist = g.distance(foot[j])
            between = dist if between is None else min(between, dist)
    return {"frontage": front, "side and rear": side, "between": between}


__all__ = ["read_dxf", "guess_mapping", "layer_summary", "import_option", "boundary_ring", "measured_setbacks"]
