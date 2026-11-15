from types import SimpleNamespace

import pytest

from webapp.market import market_stats
from webapp.market.web_scan import ScanOutput, clean_row, run_scan, safe_url
from webapp.models import Comparable, MarketScan


def test_manual_comparable_and_stats(app, analyst):
    for project, price, dp in (("A", 100, 0.1), ("B", 200, 0.05), ("C", 300, 10)):
        r = analyst.post("/market/comparables", {"location": "Test Bay", "project": project, "product_type": "Chalet",
                                                 "price_per_m2": str(price), "down_payment_pct": str(dp),
                                                 "instalment_years": "8"})
        assert r.status_code == 303
    db = app.state.Session()
    comps = db.query(Comparable).all()
    s = market_stats(comps)
    db.close()
    ch = s["by_type"]["chalet"]
    assert ch["count"] == 3 and ch["price_per_m2"]["median"] == 200 and ch["price_per_m2"]["p25"] == 150
    assert ch["down_payment_pct"]["max"] == pytest.approx(0.10)      # "10" read as 10%
    page = analyst.get("/market?location=test bay").text
    assert "chalet" in page and "Summary (3 projects)" in page


def test_excluded_and_unverified_rows(app):
    rows = [SimpleNamespace(product_type="villa", price_per_m2=p, down_payment_pct=None, instalment_years=None,
                            verified=v, excluded=e) for p, v, e in ((100, True, False), (900, False, False),
                                                                     (5000, True, True))]
    assert market_stats(rows)["by_type"]["villa"]["price_per_m2"]["max"] == 900
    assert market_stats(rows, verified_only=True)["by_type"]["villa"]["price_per_m2"]["max"] == 100


def test_paste_import(app, analyst):
    text = "Project\tDeveloper\tproduct_type\tprice_per_m2\tsource_url\nX\tDev\tvilla\t1,234\tjavascript:alert(1)\nY\t\tvilla\t2000\thttps://example.com/y\n"
    analyst.post("/market/import", {"location": "Paste Town", "csv_text": text})
    db = app.state.Session()
    comps = {c.project: c for c in db.query(Comparable).filter_by(location="Paste Town")}
    db.close()
    assert comps["X"].price_per_m2 == 1234 and comps["X"].source_url == ""
    assert comps["Y"].source_url == "https://example.com/y"


def test_verify_exclude_delete(app, analyst):
    analyst.post("/market/comparables", {"location": "L", "project": "P", "price_per_m2": "5"})
    db = app.state.Session()
    cid = db.query(Comparable).one().id
    db.close()
    analyst.post(f"/comparables/{cid}", {"action": "verify"})
    analyst.post(f"/comparables/{cid}", {"action": "exclude"})
    db = app.state.Session()
    c = db.get(Comparable, cid)
    assert c.verified is False and c.excluded is True       # manual rows start verified
    db.close()
    analyst.post(f"/comparables/{cid}", {"action": "delete"})
    db = app.state.Session()
    assert db.get(Comparable, cid) is None
    db.close()
    csv_text = analyst.get("/market/export.csv?location=L").text
    assert csv_text.startswith("location,project")


def test_scan_off_without_a_key(app, analyst):
    r = analyst.post("/market/scan", {"location": "Somewhere"})
    assert r.status_code == 303
    assert "not set up" in analyst.get(r.headers["location"]).text


def test_scan_saves_unverified_rows_with_sources(app, analyst):
    app.state.settings.anthropic_api_key = "test"
    calls = {}

    def fake(location, focus, model, currency):
        calls.update(location=location, focus=focus, model=model)
        return ScanOutput(summary="Prices rose.", comparables=[
            {"project": "Seaside", "developer": "D", "product_type": "Chalet", "price_per_m2": 90000,
             "down_payment_pct": 10, "instalment_years": 8, "source_url": "https://example.com/a"},
            {"project": "Bad link", "price_per_m2": -5, "source_url": "javascript:x"},
        ])
    app.state.scan_runner = fake
    r = analyst.post("/market/scan", {"location": "Test Bay", "product_focus": "chalets"})
    assert r.status_code == 303
    app.state.last_scan_thread.join(5)
    db = app.state.Session()
    scan = db.query(MarketScan).one()
    rows = {c.project: c for c in scan.comparables}
    db.close()
    assert scan.status == "done" and scan.summary == "Prices rose." and calls["focus"] == "chalets"
    assert rows["Seaside"].verified is False and rows["Seaside"].down_payment_pct == pytest.approx(0.10)
    assert rows["Bad link"].price_per_m2 is None and rows["Bad link"].source_url == ""


def test_scan_failure_is_recorded(app, analyst):
    from webapp.market.web_scan import ScanError
    app.state.settings.anthropic_api_key = "test"

    def boom(*a, **k):
        raise ScanError("The search service is busy. Try again in a minute.")
    app.state.scan_runner = boom
    analyst.post("/market/scan", {"location": "X"})
    app.state.last_scan_thread.join(5)
    db = app.state.Session()
    scan = db.query(MarketScan).one()
    db.close()
    assert scan.status == "failed" and "busy" in scan.error


# ---------------------------------------------------------------- the Claude loop, with a fake client

class _Block(SimpleNamespace):
    pass


class FakeMessages:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def create(self, **kw):
        self.requests.append(kw)
        return self.responses.pop(0)


def test_run_scan_resumes_paused_turns_and_reads_the_tool_call():
    paused = SimpleNamespace(stop_reason="pause_turn", content=[_Block(type="server_tool_use", name="web_search")])
    done = SimpleNamespace(stop_reason="tool_use", content=[
        _Block(type="server_tool_use", name="web_search"),
        _Block(type="tool_use", name="record_comparables",
               input={"summary": "ok", "comparables": [{"project": "P1"}, {"project": ""}]})])
    msgs = FakeMessages([paused, done])
    client = SimpleNamespace(beta=SimpleNamespace(messages=msgs))
    out = run_scan("Test Bay", "", model="claude-opus-5-5", client=client)
    assert out.summary == "ok" and [c["project"] for c in out.comparables] == ["P1"] and out.searches == 2
    second = msgs.requests[1]["messages"]
    assert second[-1]["role"] == "assistant" and second[-1]["content"] is paused.content   # resumed, not re-asked
    assert msgs.requests[0]["fallbacks"] == "default"


def test_run_scan_refusal_is_an_error():
    from webapp.market.web_scan import ScanError
    client = SimpleNamespace(beta=SimpleNamespace(messages=FakeMessages([SimpleNamespace(stop_reason="refusal", content=[])])))
    with pytest.raises(ScanError):
        run_scan("x", model="m", client=client)


def test_clean_row_and_safe_url():
    r = clean_row({"project": "A", "down_payment_pct": 15, "instalment_years": 99, "unit_size_m2": "120"})
    assert r["down_payment_pct"] == 0.15 and r["instalment_years"] is None and r["unit_size_m2"] == 120
    assert safe_url("HTTPS://x.y") == "HTTPS://x.y" and safe_url("data:text/html,hi") == ""
