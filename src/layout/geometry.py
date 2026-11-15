"""Reading a plot boundary and putting it in a working frame.

A boundary arrives as a ring of points: longitude/latitude from the map or a
KML file, or metres from a DXF drawing or typed in. The layout works in metres
in a frame where the frontage edge (sea, lake or main road) lies along the
x axis at y = 0 and the land lies at y > 0. "Depth" is the distance from the
frontage line.
"""
from __future__ import annotations

import io
import math
import re
import zipfile
from dataclasses import dataclass
from xml.etree import ElementTree as ET

from shapely import affinity
from shapely.geometry import Polygon
from shapely.validation import make_valid

EARTH_R = 6_371_008.8


class BoundaryError(ValueError):
    pass


# ---------------------------------------------------------------- reading files

def parse_kml(data: bytes) -> list[tuple[float, float]]:
    """The largest polygon (or closed line) in a KML or KMZ file, as (lon, lat)."""
    if data[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = [n for n in z.namelist() if n.lower().endswith(".kml")]
            if not names:
                raise BoundaryError("the KMZ file holds no KML")
            data = z.read(names[0])
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        raise BoundaryError(f"not a readable KML file ({e})") from None
    rings = []
    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag == "coordinates" and el.text:
            pts = []
            for tok in el.text.split():
                parts = tok.split(",")
                if len(parts) >= 2:
                    pts.append((float(parts[0]), float(parts[1])))
            if len(pts) >= 3:
                rings.append(pts)
    if not rings:
        raise BoundaryError("no polygon found in the KML file")
    return max(rings, key=lambda r: abs(_shoelace(r)))


def parse_dxf(data: bytes) -> list[tuple[float, float]]:
    """The largest closed polyline in a DXF drawing, in drawing units (taken as metres)."""
    from ezdxf import recover

    try:
        doc, _ = recover.read(io.BytesIO(data))
    except Exception as e:  # ezdxf raises several kinds
        raise BoundaryError(f"not a readable DXF file ({e})") from None
    rings = []
    for e in doc.modelspace():
        kind = e.dxftype()
        try:
            if kind == "LWPOLYLINE":
                pts = [(float(p[0]), float(p[1])) for p in e.get_points("xy")]
                closed = e.closed or (len(pts) > 2 and _same(pts[0], pts[-1]))
            elif kind == "POLYLINE":
                pts = [(float(v.dxf.location.x), float(v.dxf.location.y)) for v in e.vertices]
                closed = e.is_closed or (len(pts) > 2 and _same(pts[0], pts[-1]))
            else:
                continue
        except Exception:
            continue
        if closed and len(pts) >= 3:
            rings.append(pts)
    if not rings:
        raise BoundaryError("no closed polyline found in the DXF drawing; draw the plot boundary as one closed polyline")
    ring = max(rings, key=lambda r: abs(_shoelace(r)))
    units = doc.header.get("$INSUNITS", 6)
    scale = {1: 0.0254, 2: 0.3048, 4: 0.001, 5: 0.01, 6: 1.0}.get(units, 1.0)
    return [(x * scale, y * scale) for x, y in ring]


def parse_points_text(text: str) -> list[tuple[float, float]]:
    """Points typed one per line as "x, y" (or "lon, lat")."""
    pts = []
    for line in text.strip().splitlines():
        nums = re.findall(r"-?\d+(?:\.\d+)?", line)
        if len(nums) >= 2:
            pts.append((float(nums[0]), float(nums[1])))
    if len(pts) < 3:
        raise BoundaryError("give at least three points")
    return pts


def rectangle(frontage_m: float, depth_m: float) -> list[tuple[float, float]]:
    """A rectangle with the frontage as its first edge."""
    if frontage_m <= 0 or depth_m <= 0:
        raise BoundaryError("frontage and depth must be positive")
    return [(0.0, 0.0), (frontage_m, 0.0), (frontage_m, depth_m), (0.0, depth_m)]


# ---------------------------------------------------------------- frames

def lonlat_to_local(ring: list[tuple[float, float]]) -> tuple[list[tuple[float, float]], tuple[float, float]]:
    """Project (lon, lat) to metres east/north of the ring's mean point.

    An equirectangular projection; over a few kilometres the error is far
    below the precision of a land card.
    """
    lon0 = sum(p[0] for p in ring) / len(ring)
    lat0 = sum(p[1] for p in ring) / len(ring)
    k = math.cos(math.radians(lat0))
    out = [(math.radians(lon - lon0) * EARTH_R * k, math.radians(lat - lat0) * EARTH_R) for lon, lat in ring]
    return out, (lon0, lat0)


def local_to_lonlat(pts, origin):
    lon0, lat0 = origin
    k = math.cos(math.radians(lat0))
    return [(lon0 + math.degrees(x / (EARTH_R * k)), lat0 + math.degrees(y / EARTH_R)) for x, y in pts]


@dataclass
class Site:
    polygon: Polygon            # working frame: frontage along y = 0, land at y > 0
    area_m2: float
    frontage_m: float
    depth_m: float              # deepest point from the frontage line
    angle_deg: float            # rotation applied to the input ring
    edge_lengths: list[float]   # of the input ring, edge i runs from point i to point i+1


def make_site(ring_m: list[tuple[float, float]], frontage_edge: int = 0) -> Site:
    pts = list(ring_m)
    if len(pts) > 3 and _same(pts[0], pts[-1]):
        pts = pts[:-1]
    if len(pts) < 3:
        raise BoundaryError("a boundary needs at least three points")
    poly = Polygon(pts)
    if not poly.is_valid:
        fixed = make_valid(poly)
        polys = [g for g in getattr(fixed, "geoms", [fixed]) if isinstance(g, Polygon)]
        if not polys:
            raise BoundaryError("the boundary crosses itself and cannot be repaired")
        poly = max(polys, key=lambda g: g.area)
    if poly.area < 100:
        raise BoundaryError("the boundary encloses less than 100 m2; check the units")
    n = len(pts)
    edges = [math.dist(pts[i], pts[(i + 1) % n]) for i in range(n)]
    i = frontage_edge % n
    (x0, y0), (x1, y1) = pts[i], pts[(i + 1) % n]
    ang = math.degrees(math.atan2(y1 - y0, x1 - x0))
    g = affinity.rotate(affinity.translate(poly, -x0, -y0), -ang, origin=(0, 0))
    if g.centroid.y < 0:                 # land lies on the other side of the edge
        g = affinity.rotate(g, 180, origin=(0, 0))
        ang += 180
    minx = g.bounds[0]
    g = affinity.translate(g, -minx, 0)
    return Site(polygon=g, area_m2=g.area, frontage_m=edges[i], depth_m=g.bounds[3], angle_deg=ang % 360,
                edge_lengths=edges)


def _shoelace(r):
    return 0.5 * sum(r[i][0] * r[(i + 1) % len(r)][1] - r[(i + 1) % len(r)][0] * r[i][1] for i in range(len(r)))


def _same(a, b):
    return abs(a[0] - b[0]) < 1e-9 and abs(a[1] - b[1]) < 1e-9
