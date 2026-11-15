import json

import pytest

from feasibility import FEDDAN_M2, run
from webapp.models import Land, Screening, StudyRecord
from webapp.schemas import LandConditions
from webapp.screener import ScreenInputs, build_study, defaults_for_land, screen

from .helpers import CONDITIONS, LAND, make_land


def test_create_land_and_view(analyst):
    lid = make_land(analyst)
    html = analyst.get(f"/lands/{lid}").text
    assert "Test plot (invented)" in html and "Beach access" in html and "Soil" in html
    assert "Test plot" in analyst.get("/").text


def test_invalid_land_is_not_saved(app, analyst):
    bad = dict(LAND, area_m2=-5)
    r = analyst.post("/lands", {"data": json.dumps(bad)})
    assert r.status_code == 400 and "area_m2" in r.text
    db = app.state.Session()
    assert db.query(Land).count() == 0
    db.close()


def test_javascript_map_links_are_dropped(app, analyst):
    lid = make_land(analyst, dict(LAND, map_url="javascript:alert(1)"), conditions=False)
    db = app.state.Session()
    assert db.get(Land, lid).map_url == ""
    db.close()


def test_conditions_round_trip_and_reach_the_engine(app, analyst):
    lid = make_land(analyst)
    db = app.state.Session()
    land = db.get(Land, lid)
    cond = LandConditions.model_validate(land.conditions)
    db.close()
    assert cond.far == 0.5 and cond.setbacks.front_m is None and cond.custom[0].label == "Beach access"
    ec = cond.to_engine_conditions(LAND["area_m2"])
    assert ec.far_cap == 0.5 and ec.footprint_pct == 0.25
    assert ec.height_zones[0].floors == 3 and ec.height_zones[0].share_of_footprint == 1.0
    assert ec.min_share_by_category == {"hotel": 0.05}        # the land-basis services rule waits for Step 4
    assert ec.build_deadline_months == 96 and ec.built_area_m2 is None


def test_built_area_ceiling_passes_only_when_it_binds():
    c = LandConditions(far=0.5, max_built_area_m2=30000)
    assert c.to_engine_conditions(100000).built_area_m2 == 30000   # far allows 50,000
    assert c.to_engine_conditions(40000).built_area_m2 is None     # far allows 20,000, the tighter cap
    assert LandConditions(max_built_area_m2=9000).to_engine_conditions(1).built_area_m2 == 9000


def test_bad_conditions_are_refused(analyst):
    lid = make_land(analyst, conditions=False)
    bad = {"footprint_pct": 0.3, "height_zones": [{"name": "a", "share_of_footprint": 0.5, "floors": 2}]}
    r = analyst.post(f"/lands/{lid}/conditions", {"data": json.dumps(bad)})
    assert r.status_code == 400 and "height zone shares" in r.text


# ---------------------------------------------------------------- screener

BASE = dict(land_area_m2=100_000, land_price_per_feddan=10e6, far=0.6, price_per_m2=60_000,
            hard_cost_per_m2=20_000, infrastructure_per_m2_land=1_500)


def test_screening_is_the_engine_not_a_shortcut():
    i = ScreenInputs(**BASE)
    r = screen(i)
    k = run(build_study(i)).kpis
    assert r["kpis"]["npv_pre_tax"] == k["npv_pre_tax"] and r["kpis"]["irr_pre_tax"] == k["irr_pre_tax"]


def test_screening_built_area_follows_far():
    i = ScreenInputs(**BASE)
    assert screen(i)["built_area_m2"] == pytest.approx(60_000)


def test_residual_price_is_where_npv_crosses_zero():
    i = ScreenInputs(**dict(BASE, price_per_m2=80_000))
    r = screen(i)
    res = r["residual_price_per_feddan"]
    assert res > 0
    at = run(build_study(i.model_copy(update={"land_price_per_feddan": res * 0.999}))).kpis["npv_pre_tax"]
    above = run(build_study(i.model_copy(update={"land_price_per_feddan": res * 1.01}))).kpis["npv_pre_tax"]
    assert at >= 0 > above


def test_verdicts():
    rich = ScreenInputs(**dict(BASE, price_per_m2=120_000, land_price_per_feddan=1e6, discount_rate_annual=0.15))
    assert screen(rich)["verdict"] == "go"
    poor = ScreenInputs(**dict(BASE, price_per_m2=25_000))
    assert screen(poor)["verdict"] == "no_go"
    # asking price just above the residual value is borderline
    base = ScreenInputs(**dict(BASE, price_per_m2=80_000, min_margin=0.0))
    resid = screen(base)["residual_price_per_feddan"]
    near = base.model_copy(update={"land_price_per_feddan": resid * 1.05})
    out = screen(near)
    assert out["verdict"] == "borderline", out["reasons"]


def test_partnership_deal_is_modelled():
    i = ScreenInputs(**dict(BASE, purchase_share=0.4, partnership_owner_share=0.4))
    st = build_study(i)
    assert st.land.cash.area_share == 0.4 and st.land.partnership.area_share == pytest.approx(0.6)
    assert st.land.partnership.owner_share == 0.4


def test_defaults_come_from_the_land_record(app, analyst):
    lid = make_land(analyst)
    db = app.state.Session()
    d = defaults_for_land(db.get(Land, lid), market_price=91_234)
    db.close()
    assert d["land_area_m2"] == LAND["area_m2"] and d["land_price_per_feddan"] == 15e6
    assert d["purchase_share"] == pytest.approx(0.5) and d["partnership_owner_share"] == 0.4
    assert d["betterment_fee_per_m2"] == pytest.approx(100)        # half already paid
    assert d["far"] == 0.5 and d["land_down_payment_pct"] == 0.25 and d["land_instalment_count"] == 3
    assert d["price_per_m2"] == 91_200


def test_screening_flow_and_turning_it_into_a_study(app, analyst):
    lid = make_land(analyst)
    form = {k: v for k, v in BASE.items()}
    form["land_area_m2"] = LAND["area_m2"]
    r = analyst.post(f"/lands/{lid}/screen", form)
    assert r.status_code == 303
    sid = int(r.headers["location"].rsplit("/", 1)[1])
    page = analyst.get(f"/screenings/{sid}").text
    assert "Max land price" in page and "<svg" in page
    assert "Quick screening" in analyst.get(f"/lands/{lid}/screen").text
    r = analyst.post(f"/screenings/{sid}/to-study")
    assert r.status_code == 303
    db = app.state.Session()
    s = db.get(Screening, sid)
    rec = db.query(StudyRecord).one()
    assert rec.error == "" and rec.result["kpis"]["npv_pre_tax"] == pytest.approx(s.result["kpis"]["npv_pre_tax"])
    assert db.get(Land, lid).status == "studying"
    db.close()


def test_screening_form_errors_are_shown(analyst):
    lid = make_land(analyst)
    r = analyst.post(f"/lands/{lid}/screen", {"land_area_m2": "100", "far": "-1"})
    assert r.status_code == 400 and "Please fix" in r.text


def test_land_status_and_delete(app, admin, analyst):
    lid = make_land(analyst)
    analyst.post(f"/lands/{lid}/status", {"status": "go"})
    assert analyst.post(f"/lands/{lid}/delete").status_code == 403
    assert admin.post(f"/lands/{lid}/delete").status_code == 303
    db = app.state.Session()
    assert db.get(Land, lid) is None
    db.close()


def test_demo_seed(app):
    from webapp.demo import seed_demo
    db = app.state.Session()
    land = seed_demo(db)
    assert land.studies[0].result is not None, land.studies[0].error
    assert land.screenings[0].verdict in ("go", "borderline", "no_go")
    db.close()
