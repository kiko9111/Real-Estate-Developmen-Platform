"""Layout generator: boundaries, limits, share rules, and the hand-off to the engine."""
import io
import json
import math
from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st

from feasibility import Study, run
from layout import BoundaryError, Limits, default_brief, generate, make_site, rectangle
from layout.bestuse import evaluate, option_study
from layout.brief import HeightBand
from layout.geometry import lonlat_to_local, parse_dxf, parse_kml
from layout.limits import ShareLimit

BASE = json.loads((Path(__file__).parent.parent / "examples" / "sample_study.json").read_text())
LAND = sum(p["area_m2"] for p in BASE["parcels"])


def test_site_frame_puts_frontage_on_x_axis():
    # a rectangle given with its frontage as the third edge, rotated 30 degrees
    a = math.radians(30)
    ring = [(x * math.cos(a) - y * math.sin(a), x * math.sin(a) + y * math.cos(a))
            for x, y in [(0, 0), (200, 0), (200, 500), (0, 500)]]
    s = make_site(ring, frontage_edge=2)
    assert s.frontage_m == pytest.approx(200)
    assert s.depth_m == pytest.approx(500)
    assert s.area_m2 == pytest.approx(100_000)
    minx, miny, maxx, maxy = s.polygon.bounds
    assert miny == pytest.approx(0, abs=1e-6) and maxx - minx == pytest.approx(200)


def test_bad_boundaries():
    with pytest.raises(BoundaryError):
        make_site([(0, 0), (1, 0)])
    with pytest.raises(BoundaryError):
        make_site([(0, 0), (5, 0), (5, 5), (0, 5)])       # 25 m2: wrong units
    with pytest.raises(BoundaryError):
        parse_kml(b"<kml></kml>")


def test_kml_and_projection():
    kml = b"""<kml xmlns="http://www.opengis.net/kml/2.2"><Placemark><Polygon><outerBoundaryIs><LinearRing>
    <coordinates>27.0,31.0 27.01,31.0 27.01,31.01 27.0,31.01 27.0,31.0</coordinates></LinearRing></outerBoundaryIs>
    </Polygon></Placemark></kml>"""
    pts, _ = lonlat_to_local(parse_kml(kml))
    s = make_site(pts)
    # 0.01 deg of longitude at 31 N is about 953 m, of latitude about 1112 m
    assert s.frontage_m == pytest.approx(953.3, rel=0.003)
    assert s.depth_m == pytest.approx(1111.9, rel=0.003)


def test_dxf_closed_polyline():
    import ezdxf
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = 6
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (10, 0), (10, 10)], close=True)            # small, ignored
    msp.add_lwpolyline([(0, 0), (300, 0), (300, 400), (0, 400)], close=True)
    buf = io.StringIO()
    doc.write(buf)
    ring = parse_dxf(buf.getvalue().encode())
    assert make_site(ring).area_m2 == pytest.approx(120_000)


def _miny(q):
    x, y, w, d, a = q[:5]
    r = math.radians(a)
    return min(y + u * w / 2 * math.sin(r) + v * d / 2 * math.cos(r) for u in (-1, 1) for v in (-1, 1))


def _ok(o):
    assert "error" not in o, o.get("error")
    return o


def test_limits_hold_and_compliance_reports_them():
    site = make_site(rectangle(400, 600))
    L = Limits(coverage=0.18, far=0.5, max_floors=4, open_space_share=0.15, services_share=0.05,
               setback_sea_m=40, max_units_per_feddan=20)
    for o in generate(site, default_brief(), L):
        o = _ok(o)
        k = o["kpis"]
        assert k["coverage"] <= 0.18 + 1e-9
        assert k["far"] <= 0.5 + 1e-9
        assert k["max_floors"] <= 4
        assert k["units_per_feddan"] <= 20 + 1e-9
        assert k["open_space_share"] >= 0.15 - 1e-9
        assert k["services_share"] >= 0.05 - 1e-9
        assert all(c["ok"] for c in o["compliance"]), o["compliance"]
        # nothing is built inside the sea setback
        for b in o["geometry"]["blocks"]:
            for q in b["b"]:
                assert _miny(q) >= 40 - 0.2


def test_height_bands_cut_floors():
    site = make_site(rectangle(300, 900))
    brief = default_brief().model_copy(update={"height_bands": [
        HeightBand(name="low", to_depth_m=400, max_height_m=7), HeightBand(name="high", from_depth_m=400, max_floors=6)]})
    for o in generate(site, brief, Limits(coverage=0.25)):
        o = _ok(o)
        for b in o["geometry"]["blocks"]:
            for x, y, w, d, a, f, h in b["b"]:
                cy = sum(p[1] for p in b["ring"]) / len(b["ring"])
                if cy < 380:
                    assert h <= 7
                assert f <= 6


def test_units_share_rule_is_met_by_converting_blocks():
    site = make_site(rectangle(500, 800))
    L = Limits(coverage=0.2, max_floors=5, shares=[ShareLimit("residential", "units", max_share=0.5)])
    for o in generate(site, default_brief(), L):
        o = _ok(o)
        row = next(c for c in o["compliance"] if c["label"].startswith("Residential share of units"))
        assert row["ok"], (o["strategy"], row)


def test_unknown_use_rule_is_reported_not_hidden():
    site = make_site(rectangle(300, 300))
    L = Limits(coverage=0.2, max_floors=3, shares=[ShareLimit("school", "built_area", min_share=0.05)])
    o = _ok(generate(site, default_brief(), L)[0])
    row = next(c for c in o["compliance"] if c["label"].startswith("School"))
    assert row["ok"] is False
    assert any("School".lower() in n.lower() for n in o["notes"])


@settings(max_examples=25, deadline=None)
@given(w=st.floats(150, 900), d=st.floats(150, 1500), cov=st.floats(0.08, 0.5), far=st.floats(0.2, 2.0),
       floors=st.integers(1, 12), sea=st.floats(0, 80))
def test_property_limits_never_exceeded(w, d, cov, far, floors, sea):
    site = make_site(rectangle(w, d))
    L = Limits(coverage=cov, far=far, max_floors=floors, setback_sea_m=sea)
    for o in generate(site, default_brief(), L):
        if "error" in o:
            continue
        k = o["kpis"]
        assert k["coverage"] <= cov + 1e-9
        assert k["far"] <= far + 1e-9
        assert k["max_floors"] <= floors
        total = sum(u["area_m2"] for u in o["land_use"])
        assert total == pytest.approx(site.area_m2, rel=1e-6)


def test_option_study_runs_in_engine_and_matches_layout():
    site = make_site(rectangle(300, LAND / 300))
    brief = default_brief()
    o = _ok(generate(site, brief, Limits(coverage=0.25, far=0.6, max_floors=4), land_area_m2=LAND)[0])
    data = option_study(o, brief, BASE)
    study = Study.model_validate(data)
    res = run(study)
    assert res.built_area_m2 == pytest.approx(o["kpis"]["built_area_m2"])
    by_cat = {}
    for p in res.products:
        by_cat[p.category] = by_cat.get(p.category, 0) + p.built_area_m2
    for p in o["products"]:
        assert by_cat.get(p["category"], 0) > 0
    # the base study's money side is kept
    assert data["land"] == BASE["land"] and data["finance"] == BASE["finance"]
    # frontage premiums become per-unit premiums with shares
    prem = [pr for p in data["products"] for pr in p.get("premiums", [])]
    assert prem and all(0 < pr["share_of_units"] <= 1 for pr in prem)


def test_evaluate_ranks_passing_options_first():
    site = make_site(rectangle(300, LAND / 300))
    brief = default_brief()
    opts = evaluate(generate(site, brief, Limits(coverage=0.25, far=0.6, max_floors=4), land_area_m2=LAND), brief, BASE)
    ranked = sorted([o for o in opts if "rank" in o], key=lambda o: o["rank"])
    assert [o["rank"] for o in ranked] == list(range(1, len(ranked) + 1))
    assert sum(o["best"] for o in opts) == 1
    passing = [o for o in ranked if o["passes"]]
    npvs = [o["feasibility"]["npv_pre_tax"] for o in passing]
    assert npvs == sorted(npvs, reverse=True)


def test_layout_costs_from_layout_charge_roads_and_green():
    site = make_site(rectangle(300, LAND / 300))
    brief = default_brief().model_copy(update={"site_costs": "from_layout"})
    o = _ok(generate(site, brief, Limits(coverage=0.25, far=0.6, max_floors=4), land_area_m2=LAND)[0])
    data = option_study(o, brief, BASE)
    names = [c["name"] for c in data["costs"]["site_costs"]]
    assert names == ["roads and utilities", "landscape"]
    road = data["costs"]["site_costs"][0]["rate_per_m2_land"] * LAND
    assert road == pytest.approx(brief.road_cost_per_m2 * o["kpis"]["roads_share"] * site.area_m2)


def test_resort_pattern_draws_beach_lagoons_corridors_and_house_clusters():
    site = make_site(rectangle(900, 1500))
    brief = default_brief()
    opts = {o["strategy"]: _ok(o) for o in generate(site, brief, Limits(coverage=0.2, max_floors=6))}
    lagoon = opts["Lagoon resort"]
    g = lagoon["geometry"]
    assert g["beach"] and g["water"] and g["green"]
    uses = {u["use"]: u["share"] for u in lagoon["land_use"]}
    assert uses["Beach"] > 0 and uses["Lagoons and water"] > 0
    # lagoon-side units carry the lagoon premium
    assert any("Lagoon view" in d for d in lagoon["premiums"].values())
    villas = opts["Villa resort"]
    houses = [q for b in villas["geometry"]["blocks"] if b["plots"] for q in b["b"]]
    assert len(houses) > 50
    assert any(abs(q[4]) > 5 for q in houses)           # houses turn to face their loop road
    for o in opts.values():
        total = sum(u["area_m2"] for u in o["land_use"])
        assert total == pytest.approx(site.area_m2, rel=1e-6)


def test_small_plot_skips_lagoons_with_a_note():
    site = make_site(rectangle(250, 500))
    o = _ok(generate(site, default_brief(), Limits(coverage=0.25, max_floors=4), strategies=["Lagoon resort"])[0])
    assert not o["geometry"]["water"]
    assert any("too small for lagoons" in n for n in o["notes"])


def test_team_edits_change_blocks_and_keep_limits():
    site = make_site(rectangle(400, LAND / 400))
    brief = default_brief()
    L = Limits(coverage=0.22, far=0.6, max_floors=5, max_height_m=18)
    opts = evaluate(generate(site, brief, L, land_area_m2=LAND, strategies=["Villa resort"]), brief, BASE)
    o = _ok(opts[0])
    blocks = o["geometry"]["blocks"]
    assert all("id" in b for b in blocks)
    # turn the first house block into apartments and the second into a park; ask for too many floors
    houses = [b for b in blocks if b["use"] in ("villa", "twin", "townhouse")]
    assert len(houses) >= 2
    orig = houses[1]["use"]
    a, p = houses[0]["id"], houses[1]["id"]
    from layout.bestuse import edit_option
    j = edit_option(opts, 0, {a: {"use": "apartment", "floors": 9}, p: {"use": "park"}}, site, brief, L, LAND, BASE)
    e = _ok(opts[j])
    assert e["strategy"] == "Villa resort (edited)" and e["base_strategy"] == "Villa resort"
    assert len(opts) == 2 and sum(x["best"] for x in opts) == 1 and {x["rank"] for x in opts} == {1, 2}
    eb = {b["id"]: b for b in e["geometry"]["blocks"]}
    assert eb[a]["use"] == "apartment" and eb[a]["locked"] and eb[p]["use"] == "park" and not eb[p]["b"]
    # the height limit wins over the edit
    assert all(q[5] <= 5 for q in eb[a]["b"]) and eb[a]["b"]
    assert all(c["ok"] for c in e["compliance"] if c["label"].startswith(("Floors", "Coverage", "Floor-area")))
    assert "feasibility" in e
    # editing the edited option adds to its edits; an empty change puts a block back
    j2 = edit_option(opts, j, {p: {}, a: {"floors": 2}}, site, brief, L, LAND, BASE)
    assert j2 == j and len(opts) == 2
    assert opts[j]["edits"] == {str(a): {"use": "apartment", "floors": 2}}
    eb = {b["id"]: b for b in opts[j]["geometry"]["blocks"]}
    assert eb[p]["use"] == orig and not eb[p]["locked"]
    assert all(q[5] <= 2 for q in eb[a]["b"])
    with pytest.raises(ValueError):
        edit_option(opts, j, {a: {"use": "castle"}}, site, brief, L, LAND, BASE)


def test_edited_blocks_are_not_donated_by_the_mix_repair():
    site = make_site(rectangle(500, 800))
    brief = default_brief()
    L = Limits(coverage=0.2, max_floors=5, shares=[ShareLimit("residential", "units", max_share=0.5)])
    plain = _ok(generate(site, brief, L, strategies=["Compact chalets"])[0])
    res = [b for b in plain["geometry"]["blocks"] if b["use"] in ("chalet", "apartment")]
    edits = {str(b["id"]): {"use": "chalet"} for b in res[:3]}
    o = _ok(generate(site, brief, L, strategies=["Compact chalets"], edits={"Compact chalets": edits})[0])
    eb = {str(b["id"]): b for b in o["geometry"]["blocks"]}
    assert all(eb[k]["use"] == "chalet" and eb[k]["locked"] for k in edits)
    row = next(c for c in o["compliance"] if c["label"].startswith("Residential share of units"))
    assert row["ok"]


def test_narrow_plot_still_fills_its_blocks():
    # 250 m of frontage: too narrow for lines across each half, and blocks too narrow for long buildings
    site = make_site(rectangle(250, 672))
    L = Limits(coverage=0.2, far=0.4, max_floors=3, services_share=0.05, open_space_share=0.3, setback_sea_m=200)
    opts = {o["strategy"]: _ok(o) for o in generate(site, default_brief(), L)}
    grid = opts.pop("Simple grid")["kpis"]["built_area_m2"]
    for name, o in opts.items():
        assert o["kpis"]["built_area_m2"] >= 0.6 * grid, name
        empty = [b for b in o["geometry"]["blocks"] if b["use"] not in ("park", "") and not b["b"]]
        assert len(empty) <= 1, (name, [b["use"] for b in empty])


def test_master_plan_modules_terraces_sea_front_gate_and_trees():
    site = make_site(rectangle(600, 1400))
    L = Limits(coverage=0.2, max_height_m=24)
    o = _ok(generate(site, default_brief(), L, strategies=["Lagoon resort"])[0])
    geo = o["geometry"]
    typ = {i: t["key"] for i, t in enumerate(geo["typologies"])}
    chalets = [q for b in geo["blocks"] if b["typ"] is not None and typ[b["typ"]] == "chalet" for q in b["b"]]
    straight = [q for q in chalets if q[4] == 0]
    # terraced clusters of two or more units parallel to the sea, no longer than the type allows
    # (real North Coast plans use short clusters around the lagoons, not long slabs)
    assert straight and max(q[2] for q in straight) <= 64 + 0.1 and sum(q[2] >= 20 for q in straight) > len(straight) * 0.8
    # lagoon-front buildings turned to the water
    assert any(q[4] != 0 for q in chalets)
    # the lagoons start behind the first line: nothing but beach, roads and the promenade below it
    rs = default_brief().strategies[0].resort
    first_line = 40 + rs.promenade_m + rs.first_line_m
    assert all(min(p[1] for p in (r[0] if isinstance(r[0][0], list) else r)) > first_line - 1 for r in geo["water"])
    # sea-front lots in rows
    villas = [b for b in geo["blocks"] if b["typ"] is not None and typ[b["typ"]] == "villa"]
    assert villas and all(len(b["plots"]) == len(b["b"]) for b in villas)
    assert geo["gate"] and len(geo["trees"]) > 100


def test_resort_follows_north_coast_references():
    # published Ras El Hekma / Sahel plans: about 12.5-14% coverage, low-rise, large lagoons
    site = make_site(rectangle(600, 1400))
    L = Limits(coverage=0.2, max_height_m=24)
    brief = default_brief()
    o = _ok(generate(site, brief, L, strategies=["Lagoon resort"])[0])
    k = o["kpis"]
    assert k["coverage"] <= brief.design_coverage + 0.005
    assert 8 <= k["units_per_feddan"] <= 16
    water = next(u for u in o["land_use"] if u["use"] == "Lagoons and water")
    assert water["share"] >= 0.08
    assert max(t.floors for t in brief.typologies if t.key in ("apartment", "serviced")) <= 4
