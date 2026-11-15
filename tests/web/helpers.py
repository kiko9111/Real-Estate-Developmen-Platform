import json

LAND = {
    "name": "Test plot (invented)", "status": "new", "region": "Test Coast", "area_name": "Test Bay",
    "area_m2": 84016.6, "frontage_m": 200, "frontage_type": "sea",
    "parcels": [{"name": "A", "area_m2": 40000}, {"name": "B", "area_m2": 44016.6}],
    "offer": {"deal_type": "sale_and_partnership", "asking_price_per_feddan": 15000000,
              "purchase_area_m2": 42008.3, "partnership_owner_share": 0.4},
    "legal": {"betterment_fee_per_m2": 200, "betterment_paid_share": 0.5},
    "custom_fields": [{"label": "Soil", "value": "sand"}],
}

CONDITIONS = {"primary_use": "tourism", "far": 0.5, "footprint_pct": 0.25, "max_floors": 3,
              "share_rules": [{"use": "hotel", "basis": "built_area", "min_share": 0.05},
                              {"use": "services", "basis": "land", "min_share": 0.05}],
              "payment_terms": {"down_payment_pct": 0.25, "instalment_count": 3, "instalment_every_months": 12},
              "build_deadline_months": 96,
              "custom": [{"label": "Beach access", "value": "public walkway required"}]}


def make_land(client, land=None, conditions=None) -> int:
    r = client.post("/lands", {"data": json.dumps(land or LAND)})
    assert r.status_code == 303, r.text
    lid = int(r.headers["location"].rsplit("/", 1)[1])
    if conditions is not False:
        r = client.post(f"/lands/{lid}/conditions", {"data": json.dumps(conditions or CONDITIONS)})
        assert r.status_code == 303, r.text
    return lid
