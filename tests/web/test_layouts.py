import json

from .helpers import CONDITIONS, LAND, make_land

KML = b"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document><Placemark><Polygon><outerBoundaryIs><LinearRing>
<coordinates>27.0,31.0,0 27.004,31.0,0 27.004,31.0025,0 27.0,31.0025,0 27.0,31.0,0</coordinates>
</LinearRing></outerBoundaryIs></Polygon></Placemark></Document></kml>"""


def _layout(client, lid):
    r = client.post(f"/lands/{lid}/layouts", {"name": "Options"})
    assert r.status_code == 303
    return int(r.headers["location"].split("/")[2])


def test_layout_flow_rectangle_to_study(analyst, app):
    lid = make_land(analyst)
    rid = _layout(analyst, lid)
    # the land has a frontage, so a rectangle boundary is already there
    page = analyst.get(f"/layouts/{rid}/boundary")
    assert page.status_code == 200 and "Edge 1" in page.text
    r = analyst.post(f"/layouts/{rid}/run")
    assert r.status_code == 303
    page = analyst.get(f"/layouts/{rid}")
    assert page.status_code == 200
    assert "Best use: options ranked" in page.text and "Compliance with the conditions" in page.text
    db = app.state.Session()
    from webapp.models import LayoutRecord, StudyRecord
    rec = db.get(LayoutRecord, rid)
    ok = [o for o in rec.options if "feasibility" in o]
    assert len(ok) >= 4
    # the land's conditions are respected by every option that passes
    for o in ok:
        assert o["kpis"]["coverage"] <= CONDITIONS["footprint_pct"] + 1e-6
        assert o["kpis"]["far"] <= CONDITIONS["far"] + 1e-6
        assert o["kpis"]["max_floors"] <= CONDITIONS["max_floors"]
    best = [i for i, o in enumerate(rec.options) if o.get("best")]
    assert len(best) == 1
    r = analyst.post(f"/layouts/{rid}/options/{best[0]}/study")
    assert r.status_code == 303 and r.headers["location"].startswith("/studies/")
    sid = int(r.headers["location"].rsplit("/", 1)[1])
    st = db.get(StudyRecord, sid)
    assert st.result and not st.error
    assert abs(st.result["built_area_m2"] - rec.options[best[0]]["kpis"]["built_area_m2"]) < 1
    db.close()
    assert "Site layout and best use" in analyst.get(f"/lands/{lid}").text


def test_kml_upload_and_frontage(analyst):
    lid = make_land(analyst, conditions={"footprint_pct": 0.3, "max_floors": 4})
    rid = _layout(analyst, lid)
    r = analyst.post(f"/layouts/{rid}/boundary", {"mode": "file", "frontage_edge": "0"},
                     files={"file": ("plot.kml", KML, "application/vnd.google-earth.kml+xml")})
    assert r.status_code == 303, r.text
    r = analyst.post(f"/layouts/{rid}/frontage", {"frontage_edge": "2"})
    assert r.status_code == 303
    page = analyst.get(f"/layouts/{rid}/boundary").text
    assert "edge 3" in page
    assert analyst.post(f"/layouts/{rid}/run").status_code == 303
    assert "options ranked" in analyst.get(f"/layouts/{rid}").text


def test_bad_boundary_and_dwg_rejected(analyst):
    lid = make_land(analyst)
    rid = _layout(analyst, lid)
    r = analyst.post(f"/layouts/{rid}/boundary", {"mode": "file"}, files={"file": ("plot.dwg", b"AC1027", "x")})
    assert r.status_code == 400 and "DXF" in r.text
    r = analyst.post(f"/layouts/{rid}/boundary", {"mode": "points", "text": "0,0\n1,1"})
    assert r.status_code == 400


def test_brief_edit_and_validation(analyst):
    lid = make_land(analyst)
    rid = _layout(analyst, lid)
    page = analyst.get(f"/layouts/{rid}/brief")
    assert page.status_code == 200
    from webapp.models import LayoutRecord  # noqa: F401
    brief = json.loads(page.text.split('id="sf-value">')[1].split("</script>")[0])
    assert "boundary" not in brief
    brief["strategies"] = brief["strategies"][:1]
    r = analyst.post(f"/layouts/{rid}/brief", {"data": json.dumps(brief), "name": "One option"})
    assert r.status_code == 303
    brief["strategies"][0]["zones"][0]["mix"] = {"castle": 1}
    r = analyst.post(f"/layouts/{rid}/brief", {"data": json.dumps(brief)})
    assert r.status_code == 400 and "castle" in r.text


def test_viewer_cannot_change_layouts(analyst, viewer):
    lid = make_land(analyst)
    rid = _layout(analyst, lid)
    assert viewer.get(f"/layouts/{rid}").status_code == 200
    assert viewer.post(f"/layouts/{rid}/run").status_code == 403
    assert viewer.post(f"/lands/{lid}/layouts").status_code == 403


def test_deleting_land_deletes_layouts(admin):
    lid = make_land(admin)
    rid = _layout(admin, lid)
    assert admin.post(f"/lands/{lid}/delete").status_code == 303
    assert admin.get(f"/layouts/{rid}").status_code == 404


def test_team_edits_a_plan_and_rerun_keeps_it(analyst, viewer, app):
    lid = make_land(analyst)
    rid = _layout(analyst, lid)
    assert analyst.post(f"/layouts/{rid}/run").status_code == 303
    from webapp.models import LayoutRecord
    db = app.state.Session()
    rec = db.get(LayoutRecord, rid)
    i = next(k for k, o in enumerate(rec.options) if "feasibility" in o)
    n = len(rec.options)
    name = rec.options[i]["strategy"]
    blk = next(b for b in rec.options[i]["geometry"]["blocks"] if b["use"] not in ("park", "services", ""))
    db.close()
    page = analyst.get(f"/layouts/{rid}?option={i}").text
    assert "Edit the plan" in page and "Apply and rerun" in page
    edits = json.dumps({str(blk["id"]): {"use": "park"}})
    assert viewer.post(f"/layouts/{rid}/options/{i}/edit", {"edits": edits}).status_code == 403
    r = analyst.post(f"/layouts/{rid}/options/{i}/edit", {"edits": edits})
    assert r.status_code == 303
    j = int(r.headers["location"].split("option=")[1])
    db = app.state.Session()
    rec = db.get(LayoutRecord, rid)
    assert len(rec.options) == n + 1 and rec.options[j]["strategy"] == f"{name} (edited)"
    assert rec.options[j]["edits"] == {str(blk["id"]): {"use": "park"}}
    assert sum(1 for o in rec.options if o.get("best")) == 1
    db.close()
    assert f"{name} (edited)" in analyst.get(f"/layouts/{rid}?option={j}").text
    # a bad edit is refused with a message, nothing saved
    r = analyst.post(f"/layouts/{rid}/options/{i}/edit", {"edits": json.dumps({str(blk["id"]): {"use": "castle"}})})
    assert r.status_code == 303
    assert "could not be applied" in analyst.get(r.headers["location"]).text
    # running again keeps the edited option
    assert analyst.post(f"/layouts/{rid}/run").status_code == 303
    db = app.state.Session()
    rec = db.get(LayoutRecord, rid)
    kept = [o for o in rec.options if o["strategy"] == f"{name} (edited)"]
    assert len(kept) == 1 and kept[0]["edits"] == {str(blk["id"]): {"use": "park"}} and "rank" in kept[0]
    db.close()
