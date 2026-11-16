"""An architect's DXF master plan: reading layers, guessing what they are, measuring it as drawn."""
import io
import json
import math
from pathlib import Path

import ezdxf
import pytest

from layout import BoundaryError, Limits, default_brief
from layout.bestuse import evaluate
from layout.masterplan import guess_mapping, import_option, layer_summary, read_dxf

BASE = json.loads((Path(__file__).parent.parent / "examples" / "sample_study.json").read_text())


def drawing(rot=20.0, footprint_layer="BLDG G+2", gap=60):
    """A 400 x 600 m plot, sea along the first edge, turned and moved like a real survey drawing."""
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = 6
    msp = doc.modelspace()
    a = math.radians(rot)

    def R(x0, y0, x1, y1):
        return [(x * math.cos(a) - y * math.sin(a) + 512000, x * math.sin(a) + y * math.cos(a) + 3400000)
                for x, y in [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]]

    def add(layer, *box):
        msp.add_lwpolyline(R(*box), close=True, dxfattribs={"layer": layer})

    add("SITE-BOUNDARY", 0, 0, 400, 600)
    add("BEACH", 0, 0, 400, 50)
    add("ROADS", 0, 50, 400, 64)
    add("ROADS", 190, 64, 210, 600)
    add("ROADS", 0, 300, 400, 312)
    add("CHALET", 10, 70, 185, 295)
    add("GREEN-PARK", 215, 70, 390, 295)
    for i in range(6):
        for j in range(4):
            add("VILLA", 10 + i * 29, 320 + j * 35, 37 + i * 29, 352 + j * 35)
    add("SERVICES", 215, 320, 390, 590)
    for k in range(3):
        add(footprint_layer, 20, 90 + k * gap, 170, 106 + k * gap)
    add(footprint_layer, 230, 100, 240, 110)      # in the park: no building type
    msp.add_text("note", dxfattribs={"layer": "TEXT"})
    s = io.StringIO()
    doc.write(s)
    return s.getvalue().encode()


L = Limits(coverage=0.3, far=0.8, max_floors=4, setback_sea_m=60, setback_side_m=6, between_buildings_m=10)


def test_layers_are_read_and_guessed():
    src = read_dxf(drawing())
    m = guess_mapping(src["layers"], default_brief())
    assert m["SITE-BOUNDARY"]["use"] == "boundary" and m["ROADS"]["use"] == "road"
    assert m["CHALET"]["use"] == "chalet" and m["VILLA"]["use"] == "villa" and m["GREEN-PARK"]["use"] == "park"
    assert m["SERVICES"]["use"] == "services" and m["BEACH"]["use"] == "beach"
    assert m["BLDG G+2"] == {"use": "buildings", "floors": 3}
    rows = {r["name"]: r for r in layer_summary(src["layers"])}
    assert rows["VILLA"]["count"] == 24 and rows["SITE-BOUNDARY"]["area_m2"] == pytest.approx(240000, rel=1e-6)


def test_plan_is_measured_as_drawn():
    src = read_dxf(drawing())
    brief = default_brief()
    o = import_option(src, guess_mapping(src["layers"], brief), 0, brief, L, 240000)
    k = o["kpis"]
    assert k["site_area_m2"] == pytest.approx(240000, rel=1e-6)
    prods = {p["key"]: p for p in o["products"]}
    # three chalet buildings as drawn: 150 x 16 m, 3 floors
    assert prods["chalet"]["buildings"] == 3
    assert prods["chalet"]["footprint_m2"] == pytest.approx(3 * 150 * 16, rel=1e-3)
    assert prods["chalet"]["floors"] == [3, 3]
    # 24 villa plots, one house each
    assert prods["villa"]["buildings"] == 24
    uses = {u["use"]: u["area_m2"] for u in o["land_use"]}
    assert uses["Beach"] == pytest.approx(20000, rel=1e-3)
    assert sum(u["area_m2"] for u in o["land_use"]) == pytest.approx(240000, rel=1e-3)
    assert any("outside every plot" in n for n in o["notes"])
    rows = {c["label"]: c for c in o["compliance"]}
    # measured: the first chalet building is 90 m from the sea, buildings 44 m apart
    assert rows["Setback from frontage"]["actual"] == pytest.approx(90, abs=0.1) and rows["Setback from frontage"]["ok"]
    assert rows["Distance between buildings"]["ok"]
    # through the engine like any option
    evaluate([o], brief, BASE)
    assert "feasibility" in o and o["rank"] == 1


def test_breaches_are_reported_not_fixed():
    src = read_dxf(drawing(footprint_layer="BLDG 6F", gap=20))     # 6 floors, 4 m apart
    brief = default_brief()
    o = import_option(src, guess_mapping(src["layers"], brief), 0, brief, L, 240000)
    rows = {c["label"]: c for c in o["compliance"]}
    assert not rows["Floors"]["ok"] and rows["Floors"]["actual"] == 6
    assert not rows["Distance between buildings"]["ok"]
    assert rows["Distance between buildings"]["actual"] == pytest.approx(4, abs=0.1)


def test_frontage_edge_and_bad_mappings():
    src = read_dxf(drawing())
    brief = default_brief()
    m = guess_mapping(src["layers"], brief)
    # facing edge 2 (the 600 m side) makes the plot 600 m wide and 400 m deep
    o = import_option(src, m, 1, brief, L, 240000)
    assert o["geometry"]["bounds"][2] == pytest.approx(600, abs=0.5)
    with pytest.raises(BoundaryError):
        import_option(src, {**m, "SITE-BOUNDARY": {"use": "ignore"}}, 0, brief, L)
    with pytest.raises(ValueError):
        import_option(src, {**m, "CHALET": {"use": "castle"}}, 0, brief, L)
    only = {n: {"use": "boundary" if n == "SITE-BOUNDARY" else "road" if n == "ROADS" else "ignore"} for n in m}
    with pytest.raises(ValueError):
        import_option(src, only, 0, brief, L)
    with pytest.raises(BoundaryError):
        read_dxf(b"not a drawing")
