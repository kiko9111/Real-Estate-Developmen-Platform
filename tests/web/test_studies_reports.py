import io
import json

import pytest
from pptx import Presentation

from feasibility import Study, run, shocked
from webapp.models import StudyRecord

from .helpers import make_land


def _study(app, sid) -> StudyRecord:
    db = app.state.Session()
    rec = db.get(StudyRecord, sid)
    db.close()
    return rec


def new_study(client, lid) -> int:
    r = client.post(f"/lands/{lid}/studies", {"name": "Base case"})
    assert r.status_code == 303
    return int(r.headers["location"].split("/")[2])


def test_template_study_uses_the_land(app, analyst):
    lid = make_land(analyst)
    sid = new_study(analyst, lid)
    rec = _study(app, sid)
    assert rec.error == "", rec.error
    assert sum(p["area_m2"] for p in rec.data["parcels"]) == pytest.approx(84016.6)
    assert rec.data["conditions"]["far_cap"] == 0.5
    assert rec.result["allowed_built_area_m2"] == pytest.approx(min(84016.6 * 0.5, 84016.6 * 0.25 * 3))
    # stored KPIs are exactly the engine's
    assert rec.result["kpis"]["npv_pre_tax"] == run(Study.model_validate(rec.data)).kpis["npv_pre_tax"]
    page = analyst.get(f"/studies/{sid}").text
    assert "Development programme" in page and "Cash flow by year" in page
    assert "Study inputs" in analyst.get(f"/studies/{sid}/edit").text


def test_invalid_edit_is_not_saved(app, analyst):
    lid = make_land(analyst)
    sid = new_study(analyst, lid)
    before = _study(app, sid).data
    bad = json.loads(json.dumps(before))
    bad["products"][0]["bua_share"] = 0.9
    r = analyst.post(f"/studies/{sid}/edit", {"data": json.dumps(bad), "name": "x"})
    assert r.status_code == 400 and "shares of built area" in r.text
    assert _study(app, sid).data == before


def test_condition_breach_is_reported_not_saved(app, analyst):
    lid = make_land(analyst)
    sid = new_study(analyst, lid)
    d = _study(app, sid).data
    d["conditions"]["min_share_by_category"] = {"hotel": 0.5}
    r = analyst.post(f"/studies/{sid}/edit", {"data": json.dumps(d), "name": "x"})
    assert r.status_code == 400 and "at least 50.0%" in r.text


def test_edit_saves_and_reruns(app, analyst):
    lid = make_land(analyst)
    sid = new_study(analyst, lid)
    d = _study(app, sid).data
    d["finance"]["discount_rate_annual"] = 0.3
    r = analyst.post(f"/studies/{sid}/edit", {"data": json.dumps(d), "name": "Higher target"})
    assert r.status_code == 303
    rec = _study(app, sid)
    assert rec.name == "Higher target" and rec.data["finance"]["discount_rate_annual"] == 0.3
    assert rec.result["kpis"]["npv_pre_tax"] == run(Study.model_validate(rec.data)).kpis["npv_pre_tax"]


def test_analyses_are_full_reruns(app, analyst):
    lid = make_land(analyst)
    sid = new_study(analyst, lid)
    for kind in ("scenarios", "residual"):
        assert analyst.post(f"/studies/{sid}/analysis", {"kind": kind}).status_code == 303
    analyst.post(f"/studies/{sid}/analysis", {"kind": "risk", "runs": "25"})
    analyst.post(f"/studies/{sid}/analysis", {"kind": "sensitivity", "shock": "price", "values": "0.9, 1.1"})
    rec = _study(app, sid)
    a = rec.result["analysis"]
    st = Study.model_validate(rec.data)
    by_label = {r["label"]: r for r in a["scenarios"]}
    assert by_label["Costs +10%"]["npv_pre_tax"] == run(shocked(st, "cost", 1.1)).kpis["npv_pre_tax"]
    assert a["residual"]["feasible"] in (True, False)
    assert a["risk"]["runs"] == 25
    assert [r["value"] for r in a["sensitivity"]["rows"]] == [0.9, 1.1]
    page = analyst.get(f"/studies/{sid}").text
    assert "Standard scenarios" in page and "Risk ranges" in page and "Sensitivity" in page


def test_scenario_copy_and_compare(app, analyst):
    lid = make_land(analyst)
    sid = new_study(analyst, lid)
    r = analyst.post(f"/studies/{sid}/scenario", {"name": "Cheaper build"})
    child_id = int(r.headers["location"].split("/")[2])
    child = _study(app, child_id)
    assert child.parent_id == sid and child.data["products"] == _study(app, sid).data["products"]
    page = analyst.get(f"/lands/{lid}/compare").text
    assert "Cheaper build" in page and "Base case" in page


def test_reports(app, viewer, analyst):
    lid = make_land(analyst)
    sid = new_study(analyst, lid)
    pdf = viewer.get(f"/studies/{sid}/report.pdf")
    assert pdf.status_code == 200 and pdf.content[:5] == b"%PDF-" and len(pdf.content) > 5000
    pp = viewer.get(f"/studies/{sid}/report.pptx")
    assert pp.status_code == 200
    deck = Presentation(io.BytesIO(pp.content))
    texts = " ".join(sh.text_frame.text for s in deck.slides for sh in s.shapes if sh.has_text_frame)
    assert len(deck.slides) >= 7 and "Headline results" in texts and "Base case" in texts
    js = viewer.get(f"/studies/{sid}/study.json")
    assert Study.model_validate(js.json())


def test_report_refused_for_a_study_that_did_not_run(app, analyst):
    lid = make_land(analyst)
    sid = new_study(analyst, lid)
    db = app.state.Session()
    rec = db.get(StudyRecord, sid)
    rec.result, rec.error = None, "broken"
    db.commit()
    db.close()
    assert analyst.get(f"/studies/{sid}/report.pdf").status_code == 400


def test_delete_study(app, analyst, viewer):
    lid = make_land(analyst)
    sid = new_study(analyst, lid)
    assert viewer.post(f"/studies/{sid}/delete").status_code == 403
    assert analyst.post(f"/studies/{sid}/delete").status_code == 303
    assert _study(app, sid) is None
