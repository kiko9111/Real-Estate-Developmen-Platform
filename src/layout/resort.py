"""The resort pattern: a master plan the way a coastal resort is planned.

From the frontage inland:

- the beach, then a promenade road along it;
- a first line of plots facing the sea (villas, a beach hotel);
- a main spine from the entrance on the far edge down to a beach plaza, with
  a park in its middle;
- green view corridors to the sea every few hundred metres;
- collector roads parallel to the beach and local roads towards it, with
  gentle curves inland so the plan does not read as a grid;
- optional crystal lagoons running inland along the corridors, with green banks;
- an entrance hub (commercial and services) at the far end of the spine.

Each block is then built according to its building type:

- houses (detached, row): clusters on a loop road, plots on both sides of it,
  a pocket park in the middle when it fits, every house facing the road;
- apartment and chalet buildings: staggered rows parallel to the beach so the
  back rows look between the front ones;
- hotels: a slab with two wings opening to the sea, on the sea side of the
  block; other landmarks a single building.

The limits, mix rules and checks are those of the grid plan (generator.Plan).
"""
from __future__ import annotations

import math

from shapely import affinity
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import unary_union
from shapely.prepared import prep

from .generator import Block, Plan, _first, _polys


MIN_STRIP_M = 70.0      # narrowest strip of blocks between two lines towards the beach

def rect(cx: float, cy: float, w: float, d: float, ang: float = 0.0) -> Polygon:
    r = box(cx - w / 2, cy - d / 2, cx + w / 2, cy + d / 2)
    return affinity.rotate(r, ang, origin=(cx, cy)) if ang else r


def _runs(poly, x0, x1, y0, y1, step=2.0):
    """Stretches along x where the strip from y0 to y1 lies wholly inside the polygon."""
    pp = prep(poly)
    runs, start = [], None
    x = x0
    while x <= x1 + 1e-9:
        ok = pp.contains(box(x, y0, min(x + step, x1 + step), y1)) if x + step <= x1 + 1e-9 else False
        if ok and start is None:
            start = x
        if not ok and start is not None:
            runs.append((start, x))
            start = None
        x += step
    if start is not None:
        runs.append((start, x1))
    return runs


def wavy(x0: float, x1: float, y: float, amp: float, wave: float, phase: float = 0.0, vertical: bool = False,
         step: float = 10.0) -> LineString:
    n = max(2, int(abs(x1 - x0) / step) + 1)
    pts = []
    for i in range(n):
        t = x0 + (x1 - x0) * i / (n - 1)
        o = amp * math.sin(2 * math.pi * (t - x0) / wave + phase) if amp else 0.0
        pts.append((y + o, t) if vertical else (t, y + o))
    return LineString(pts)


class ResortPlan(Plan):

    # ------------------------------------------------------------ the plan's structure

    def cut(self):
        L, rs, site = self.limits, self.strategy.resort, self.site
        P = site.polygon
        minx, miny, maxx, maxy = P.bounds
        cx = (minx + maxx) / 2
        W, D = maxx - minx, maxy
        other = _first(max([v for v in (L.setback_side_m, L.setback_rear_m) if v is not None], default=None),
                       self.brief.default_setback_m)
        inner = P.buffer(-other, join_style=2) if other > 0 else P
        if inner.is_empty:
            raise ValueError("the setbacks leave nothing to build on")
        beach_d = max(rs.beach_m, _first(L.setback_sea_m, L.setback_front_m, 0.0))
        y_prom = beach_d + rs.promenade_m
        y_first = y_prom + rs.first_line_m
        back = maxy - (L.setback_main_road_m or 0.0)
        if y_first + 60 > back:
            raise ValueError("the plot is too shallow for a resort plan; try the simple grid")

        roads = []
        if rs.promenade_m > 0:
            roads.append(box(minx - 1, beach_d, maxx + 1, y_prom))
        # spine: two carriageways around a park, from the beach to the far edge
        half_g = rs.spine_green_m / 2
        cw = rs.boulevard_m / 2
        spine_l = box(cx - half_g - cw, beach_d, cx - half_g, maxy + 1)
        spine_r = box(cx + half_g, beach_d, cx + half_g + cw, maxy + 1)
        roads += [spine_l, spine_r]
        self.gate = None
        gate_green = None
        if rs.gate_r_m > 0 and back - y_first > 5 * rs.gate_r_m:
            # the gate: an entrance roundabout where the spine meets the road behind the plot
            gc = Point(cx, back - rs.gate_r_m - 6)
            inner_r = max(rs.gate_r_m - rs.collector_m * 0.7, rs.gate_r_m * 0.5)
            gate_green = gc.buffer(inner_r)
            beyond = box(minx - 1, gc.y, maxx + 1, maxy + 1)
            roads[-2:] = [spine_l.difference(gate_green).difference(beyond),
                          spine_r.difference(gate_green).difference(beyond),
                          gc.buffer(rs.gate_r_m).difference(gate_green),
                          box(cx - cw, gc.y, cx + cw, maxy + 1).difference(gate_green)]
            self.gate = (cx, back - 2)
        greens = []
        if rs.spine_green_m > 0:
            greens.append(box(cx - half_g, y_prom, cx + half_g, back))
        if rs.plaza_r_m > 0:
            greens.append(Point(cx, y_prom + rs.plaza_r_m * 0.8).buffer(rs.plaza_r_m))
        if gate_green is not None:
            greens.append(gate_green)
        # lines towards the beach, spread evenly across each half of the plot
        spine_edge = half_g + cw
        half_w = (maxx - minx) / 2 - spine_edge - other
        # no strip narrower than MIN_STRIP_M between lines: a narrow plot gets fewer lines, or none
        n = max(1, int(round(half_w / rs.local_every_m)))
        while n > 0 and half_w / (n + 1) < MIN_STRIP_M:
            n -= 1
        if n == 0:
            self.notes.append("The plot is narrow, so there are no sea-view corridors or lines across each half; "
                              "blocks run from the spine to the side.")
        offs = [spine_edge + half_w * i / (n + 1) for i in range(1, n + 1)]
        k = max(1, int(round(rs.corridor_every_m / (half_w / (n + 1)))))
        pick = ([i for i in range(1, n + 1) if i % k == 0] or [int(math.ceil(n / 2))]) if n else []
        corridor_off = [offs[i - 1] for i in pick] if (rs.corridor_m > 0 or rs.lagoons) else []
        corridor_x = [cx + sg * o for o in corridor_off for sg in (-1, 1)]
        local_x = [cx + sg * o for o in offs if o not in corridor_off for sg in (-1, 1)]
        reach = min(back - 40, max(y_first + 60, rs.corridor_reach * D))
        lag_end = min(back - 40, y_first + rs.lagoon_reach_m)
        if rs.lagoon_share > 0:
            lag_end = min(back - 60, y_first + max(rs.lagoon_reach_m, 0.75 * (back - y_first)))
        water_parts = []
        lagoons = rs.lagoons and n > 0 and half_w / (n + 1) >= 2.4 * rs.lagoon_w_m and lag_end - y_first > 150
        if rs.lagoons and not lagoons:
            self.notes.append("The plot is too small for lagoons in this pattern, so none were drawn.")
        for j, x in enumerate(corridor_x):
            if lagoons:
                # a lagoon runs inland along the corridor, with green banks, ending in a pool
                # it starts behind the first line, so the beach front stays whole for the best products
                y0 = y_first + rs.collector_m / 2 + 9 + rs.lagoon_w_m * 0.6
                line = wavy(y0, lag_end, x, min(rs.lagoon_w_m * 0.45, 25.0), 170, phase=j * 0.7, vertical=True)
                if rs.lagoon_share > 0 and corridor_x:
                    # a large lagoon basin with an uneven shore, sized to its share of the land
                    target = rs.lagoon_share * site.area_m2 / len(corridor_x)
                    r = min(max(target / (2 * line.length), rs.lagoon_w_m / 2), 0.3 * half_w)
                    blobs, d = [], 0.0
                    while d <= line.length:
                        p_ = line.interpolate(d)
                        k_ = 0.75 + 0.5 * abs(math.sin(d / (2.3 * r) + j))
                        blobs.append(Point(p_.x + 0.25 * r * math.sin(d / (1.7 * r) + j), p_.y).buffer(r * k_))
                        d += r * 0.5
                    basin = unary_union(blobs).buffer(4).buffer(-4)
                    water_parts.append(basin)
                    greens.append(basin.buffer(9))
                    start = lag_end + r
                else:
                    water_parts.append(line.buffer(rs.lagoon_w_m / 2))
                    water_parts.append(Point(line.coords[-1]).buffer(rs.lagoon_w_m * 0.95))
                    water_parts.append(Point(line.coords[0]).buffer(rs.lagoon_w_m * 0.6))
                    greens.append(line.buffer(rs.lagoon_w_m / 2 + 9))
                    greens.append(Point(line.coords[-1]).buffer(rs.lagoon_w_m * 0.95 + 9))
                    greens.append(Point(line.coords[0]).buffer(rs.lagoon_w_m * 0.6 + 9))
                    start = lag_end + rs.lagoon_w_m
            else:
                start = y_prom
            if rs.corridor_m > 0 and reach > start:
                greens.append(box(x - rs.corridor_m / 2, start, x + rs.corridor_m / 2, reach))
            top = max(reach, lag_end + rs.lagoon_w_m) if lagoons else reach
            if top < back - 60:
                roads.append(box(x - rs.local_m / 2, top, x + rs.local_m / 2, back))
        # collector roads parallel to the beach: the first one, straight, closes the first line
        y = y_first
        i = 0
        while y < back - 0.45 * rs.collector_every_m:
            amp = 0.0 if i == 0 else rs.waviness_m
            line = wavy(minx - 5, maxx + 5, y, amp, max(2.2 * rs.local_every_m, 120), phase=i * 1.3)
            roads.append(line.buffer(rs.collector_m / 2, cap_style=2))
            y += rs.collector_every_m
            i += 1
        for j, x in enumerate(local_x):
            line = wavy(y_first, back, x, rs.waviness_m * 0.6, max(2.4 * rs.collector_every_m, 150),
                        phase=j * 0.9, vertical=True)
            roads.append(line.buffer(rs.local_m / 2, cap_style=2))

        road_u = unary_union(roads)
        spine_u = unary_union([spine_l, spine_r]).intersection(P)
        self.roads = unary_union([road_u.intersection(inner), spine_u])
        land_side = inner.difference(box(minx - 1, miny - 1, maxx + 1, y_prom))
        water = Polygon()
        if water_parts:
            water = unary_union(water_parts).intersection(land_side).difference(self.roads)
        green_u = unary_union(greens).intersection(land_side).difference(self.roads).difference(water)
        self.water = water
        self.green = green_u
        self.beach = P.intersection(box(minx - 1, miny - 1, maxx + 1, beach_d)).difference(self.roads)
        buildable = inner.difference(box(minx - 1, miny - 1, maxx + 1, y_prom))
        if L.setback_main_road_m:
            buildable = buildable.difference(box(minx - 1, back, maxx + 1, maxy + 1))
        pieces = buildable.difference(self.roads).difference(green_u).difference(water)
        self.setbacks = P.difference(inner).difference(self.roads).difference(self.beach)
        if L.setback_main_road_m:
            self.setbacks = unary_union([self.setbacks, P.intersection(box(minx - 1, back, maxx + 1, maxy + 1))
                                         .difference(self.roads)])
        small = []
        for g in _polys(pieces):
            g = g.buffer(0)
            if g.area >= self.brief.min_block_m2:
                kind = "front" if g.centroid.y < y_first else ("hub" if g.centroid.y > back - rs.hub_depth_m
                                                              and abs(g.centroid.x - cx) < half_w * 0.75 + spine_edge else "inner")
                self.blocks.append(Block(id=len(self.blocks), poly=g, kind=kind))
            else:
                small.append(g)
        self.leftover = unary_union(small) if small else Polygon()
        if not self.blocks:
            raise ValueError("no block is large enough to build on")

    def reserve(self):
        # the entrance hub takes the services share first
        L, land = self.limits, self.land
        serv_min = max(L.services_share or 0.0, self._land_min("services"))
        scale = land / self.site.area_m2
        hub = sorted([b for b in self.blocks if b.kind == "hub"], key=lambda b: -b.poly.centroid.y)
        got = 0.0
        for b in hub:
            if serv_min > 0 and got >= serv_min * land:
                break
            b.use = "services"
            got += b.area * scale
            if serv_min == 0:
                break               # one hub block for shops and a clubhouse even without a rule
        rs = self.strategy.resort
        if rs.beach_club:
            # a beach club on the first line, on the smallest block beside the plaza
            cx = (self.site.polygon.bounds[0] + self.site.polygon.bounds[2]) / 2
            front = [b for b in self.blocks if b.kind == "front" and not b.use and b.area <= 15_000]
            near = sorted(front, key=lambda b: (abs(b.poly.centroid.x - cx) > rs.spine_green_m + rs.boulevard_m + 80,
                                                b.area))
            if near:
                near[0].use = "services"
                near[0].kind = "club"
        super().reserve()

    # ------------------------------------------------------------ building on a block

    def fill(self, b: Block):
        b.buildings, b.plots, b.road_poly, b.green_poly = [], [], None, None
        t = self.typ_of(b)
        if t is None:
            return
        floors = self.floors_for(t, b.depth, b.floors_cap)
        if floors <= 0:
            return
        if t.kind in ("detached", "row"):
            if b.kind == "front" and self._sea_rows(b, t, floors):
                return
            if not self._cluster(b, t, floors):
                super().fill(b)
        elif t.kind == "block":
            if not (t.max_length_m and self._terraces(b, t, floors)):
                self._staggered(b, t, floors)
        elif t.category == "hotel":
            if not self._hotel(b, t, floors):
                super().fill(b)
        else:
            super().fill(b)

    def _sea_rows(self, b: Block, t, floors) -> bool:
        """Sea-front lots in rows facing the beach: the first row on the promenade road, a second row
        behind it on a lane, each house at the sea side of its lot with the garden in front."""
        rs = self.strategy.resort
        pw, pd = t.plot_w_m, t.plot_d_m
        fw, fd = t.footprint_w_m, t.footprint_d_m
        minx, miny, maxx, maxy = b.poly.bounds
        pb = prep(b.poly.buffer(0.3))
        rows = [miny]
        lane = None
        if maxy - miny >= 2 * pd + rs.second_row_lane_m:
            y_lane = miny + pd
            lane = box(minx - 1, y_lane, maxx + 1, y_lane + rs.second_row_lane_m).intersection(b.poly)
            rows.append(y_lane + rs.second_row_lane_m)
        plots, houses = [], []
        front_garden = max(3.0, min(8.0, (pd - fd) * 0.6))
        for k, y0 in enumerate(rows):
            n = int((maxx - minx) // pw)
            ox = minx + ((maxx - minx) - n * pw) / 2 + (pw / 2 if k % 2 and n > 1 else 0.0)
            for i in range(n):
                x0 = ox + i * pw
                q = box(x0, y0, x0 + pw, y0 + pd)
                if not pb.contains(q):
                    continue
                plots.append(q)
                houses.append([x0 + pw / 2, y0 + front_garden + fd / 2, fw, fd, 0.0, floors])
        if len(houses) < 2:
            return False
        b.plots = [list(q.exterior.coords)[:-1] for q in plots]
        b.buildings = houses
        if lane is not None and not lane.is_empty:
            b.road_poly = lane
        return True

    def _terraces(self, b: Block, t, floors) -> bool:
        """Terraced rows parallel to the beach, broken into buildings of up to the type's length with
        passages between, the rows far enough apart for the back rows to see over the front ones."""
        rs = self.strategy.resort
        area = b.poly.buffer(-rs.block_margin_m, join_style=2)
        if area.is_empty:
            return False
        fd = t.footprint_d_m
        height = floors * t.floor_height_m
        spacing = fd + max(t.gap_m, self.limits.between_buildings_m or 0.0, 0.8 * height)
        passage = max(8.0, self.limits.between_buildings_m or 0.0)
        min_len = min(t.footprint_w_m, t.max_length_m) * 0.5
        minx, miny, maxx, maxy = area.bounds
        pa = prep(area)
        # lagoon front: a row of buildings along the edge facing the water
        out, taken = [], []
        if not self.water.is_empty and b.poly.distance(self.water) < 25:
            near = self.water.buffer(b.poly.distance(self.water) + 16)
            edge = b.poly.exterior.intersection(near)
            if edge.geom_type == "MultiLineString":
                from shapely.ops import linemerge
                edge = linemerge(edge)
            for line in [g for g in getattr(edge, "geoms", [edge]) if g.geom_type == "LineString"]:
                L = line.length
                if L < min_len:
                    continue
                # shorter buildings follow the curve of the bank
                n = max(1, math.ceil((L + passage) / (min(t.max_length_m, 34.0) + passage)))
                seg = (L - (n - 1) * passage) / n
                for i in range(n):
                    a = line.interpolate(i * (seg + passage))
                    c = line.interpolate(i * (seg + passage) + seg)
                    dx, dy = c.x - a.x, c.y - a.y
                    ln = math.hypot(dx, dy)
                    if ln < min_len:
                        continue
                    nx, ny = -dy / ln, dx / ln
                    mx, my = (a.x + c.x) / 2, (a.y + c.y) / 2
                    from shapely.ops import substring
                    chunk = substring(line, i * (seg + passage), i * (seg + passage) + seg)
                    chord = LineString([(a.x, a.y), (c.x, c.y)])
                    sag = max((Point(q).distance(chord) for q in chunk.coords), default=0.0)
                    off = rs.block_margin_m + fd / 2 + 0.5 + sag
                    if not b.poly.contains(Point(mx + nx * off, my + ny * off)):
                        nx, ny = -nx, -ny
                    ang = math.degrees(math.atan2(dy, dx))
                    for k_ in (1.0, 0.85, 0.7):
                        w = ln * k_ - 2
                        r = rect(mx + nx * off, my + ny * off, w, fd, ang)
                        if w >= min_len and pa.contains(r) and not any(r.intersects(o) for o in taken):
                            out.append([mx + nx * off, my + ny * off, w, fd, ang, floors])
                            taken.append(r.buffer(max(t.gap_m, self.limits.between_buildings_m or 0.0) * 0.8))
                            break
        y = miny + fd / 2
        k = 0
        while y + fd / 2 <= maxy + 1e-6:
            band = area.intersection(box(minx - 1, y - fd / 2, maxx + 1, y + fd / 2))
            for part in _polys(band):
                # the stretches of the band where the whole depth of the row fits
                x0, _, x1, _ = part.bounds
                for s0, s1 in _runs(area, x0, x1, y - fd / 2, y + fd / 2):
                    L = s1 - s0
                    if L < min_len:
                        continue
                    n = max(1, math.ceil((L + passage) / (t.max_length_m + passage))) + (k % 2)
                    seg = (L - (n - 1) * passage) / n
                    if seg < min_len:
                        n = max(1, int((L + passage) // (min_len + passage)))
                        seg = (L - (n - 1) * passage) / n
                    for i in range(n):
                        cxs = s0 + seg / 2 + i * (seg + passage)
                        r = rect(cxs, y, seg, fd)
                        if taken and any(r.intersects(o) for o in taken):
                            continue
                        out.append([cxs, y, seg, fd, 0.0, floors])
            y += spacing
            k += 1
        if not out:
            return False
        b.buildings = out
        return True

    def _staggered(self, b: Block, t, floors):
        rs = self.strategy.resort
        gap = max(t.gap_m, self.limits.between_buildings_m or 0.0)
        area = b.poly.buffer(-rs.block_margin_m, join_style=2)
        if area.is_empty:
            return
        pa = prep(area)
        minx, miny, maxx, maxy = area.bounds

        def rows(ex, ey, angle):
            """Staggered rows of buildings taking ex by ey on the ground."""
            out = []
            px, py = ex + gap, ey + gap
            ny = int((maxy - miny - ey) // py) + 1
            oy = miny + ((maxy - miny) - (ey + (ny - 1) * py)) / 2 + ey / 2
            for j in range(max(ny, 0)):
                y = oy + j * py
                nx = int((maxx - minx - ex) // px) + 2
                ox = minx + ex / 2 - (px / 2 if j % 2 else 0.0)
                for i in range(nx + 1):
                    x = ox + i * px
                    if pa.contains(rect(x, y, ex, ey)):
                        out.append([x, y, t.footprint_w_m, t.footprint_d_m, angle, floors])
            return out

        fw, fd = t.footprint_w_m, t.footprint_d_m
        b.buildings = rows(fw, fd, 0.0)          # long side to the sea
        if not b.buildings:                       # a narrow block: turn the buildings end-on to the sea
            b.buildings = rows(fd, fw, 90.0)
        if not b.buildings:
            c = area.representative_point()
            if pa.contains(rect(c.x, c.y, fw, fd)):
                b.buildings.append([c.x, c.y, fw, fd, 0.0, floors])

    def _hotel(self, b: Block, t, floors) -> bool:
        """A slab parallel to the beach with two wings opening to the sea."""
        rs = self.strategy.resort
        area = b.poly.buffer(-rs.block_margin_m, join_style=2)
        if area.is_empty:
            return False
        pa = prep(area)
        minx, miny, maxx, maxy = area.bounds
        target = t.block_cover * b.area
        bw, bd = maxx - minx, maxy - miny
        for k in (1.0, 0.85, 0.7, 0.55, 0.4):
            Wd = 0.8 * bw * k
            slab_d = min(20.0, bd * 0.3)
            wing_w = min(18.0, Wd * 0.2)
            wing_l = max(0.0, min((target * k * k - Wd * slab_d) / (2 * wing_w), bd * 0.7 - slab_d))
            cx = (minx + maxx) / 2
            y_slab = miny + wing_l + slab_d / 2 + 2
            parts = [[cx, y_slab, Wd, slab_d, 0.0, floors]]
            if wing_l > 8:
                yw = y_slab - slab_d / 2 - wing_l / 2
                parts += [[cx - Wd / 2 + wing_w / 2, yw, wing_w, wing_l, 0.0, max(1, floors - 1)],
                          [cx + Wd / 2 - wing_w / 2, yw, wing_w, wing_l, 0.0, max(1, floors - 1)]]
            if all(pa.contains(rect(p[0], p[1], p[2], p[3])) for p in parts):
                b.buildings = parts
                return True
        return False

    def _cluster(self, b: Block, t, floors) -> bool:
        """Plots on both sides of a loop road, a pocket park in the middle when it fits."""
        rs = self.strategy.resort
        road_w = rs.loop_road_m
        pw, pd = t.plot_w_m, t.plot_d_m
        fw, fd = t.footprint_w_m, t.footprint_d_m
        core = b.poly.buffer(-(pd + road_w / 2), join_style=1)
        loops = [g for g in _polys(core) if g.area > 50 and g.length > 4 * pw]
        if not loops:
            return False
        road_parts, accepted, buildings = [], [], []
        block_p = prep(b.poly.buffer(0.5))
        for lp in loops:
            ring = LineString(lp.exterior.coords)
            road = ring.buffer(road_w / 2)
            # a link from the loop to the street on the block's edge nearest the beach side road
            pt = min(lp.exterior.coords, key=lambda c: -c[1])
            link = box(pt[0] - road_w / 2, pt[1], pt[0] + road_w / 2, b.poly.bounds[3] + 1)
            road = unary_union([road, link]).intersection(b.poly)
            road_parts.append(road)
            road_p = prep(road.buffer(-0.3)) if not road.buffer(-0.3).is_empty else None
            inside = Polygon(lp.exterior)
            for side in (1, -1):        # 1: outer ring, -1: inner ring
                L = ring.length
                n = int(L // pw)
                if n < 1:
                    continue
                step = L / n
                for i in range(n):
                    s = (i + 0.5) * step
                    p = ring.interpolate(s)
                    a = ring.interpolate((s - 1.0) % L)
                    c = ring.interpolate((s + 1.0) % L)
                    tx, ty = c.x - a.x, c.y - a.y
                    tl = math.hypot(tx, ty) or 1.0
                    tx, ty = tx / tl, ty / tl
                    nx, ny = -ty, tx
                    if inside.contains(Point(p.x + nx * 0.5, p.y + ny * 0.5)):
                        nx, ny = -nx, -ny          # make n point out of the loop
                    nx, ny = nx * side, ny * side
                    off0, off1 = road_w / 2, road_w / 2 + pd
                    q = Polygon([(p.x - tx * pw / 2 + nx * off0, p.y - ty * pw / 2 + ny * off0),
                                 (p.x + tx * pw / 2 + nx * off0, p.y + ty * pw / 2 + ny * off0),
                                 (p.x + tx * pw / 2 + nx * off1, p.y + ty * pw / 2 + ny * off1),
                                 (p.x - tx * pw / 2 + nx * off1, p.y - ty * pw / 2 + ny * off1)])
                    if not q.is_valid or not block_p.contains(q):
                        continue
                    if road_p is not None and road_p.intersects(q):
                        continue
                    if any(q.intersection(o).area > 1.0 for o in accepted):
                        continue
                    accepted.append(q)
                    mid = road_w / 2 + (pd - fd) / 2 + fd / 2
                    ang = math.degrees(math.atan2(ty, tx))
                    buildings.append([p.x + nx * mid, p.y + ny * mid, fw, fd, ang, floors])
        if len(buildings) < 2:
            return False
        b.road_poly = unary_union(road_parts)
        plots_u = unary_union(accepted)
        pocket = b.poly.difference(b.road_poly).difference(plots_u.buffer(0.5))
        pockets = [g for g in _polys(pocket) if g.area > 400 and g.minimum_rotated_rectangle.length > 0
                   and g.buffer(-8).area > 0]
        b.green_poly = unary_union(pockets) if pockets else None
        b.plots = [list(q.exterior.coords)[:-1] for q in accepted]
        b.buildings = buildings
        return True
