"""An invented demo land, so a new installation has something to click through.

Every name and number here is made up. It is not a real plot or offer.
"""
from __future__ import annotations

from feasibility import FEDDAN_M2

from . import studies as svc
from .models import Comparable, Land, Screening, StudyRecord
from .screener import ScreenInputs, screen

DEMO_LOCATION = "Demo Bay"


def seed_demo(db) -> Land:
    area = 40 * FEDDAN_M2
    land = Land(
        name="Demo Bay seafront plot (invented)", code="DEMO-1", status="screening",
        region="Demo Coast", area_name=DEMO_LOCATION, governorate="Demo Governorate",
        area_m2=area, frontage_m=250, depth_m=670, frontage_type="sea",
        parcels=[{"name": "Front", "area_m2": area * 0.45, "notes": "sea side"},
                 {"name": "Back", "area_m2": area * 0.55, "notes": "road side"}],
        offer={"deal_type": "sale_and_partnership", "asking_price_per_feddan": 20_000_000, "negotiable": True,
               "purchase_area_m2": area * 0.4, "partnership_owner_share": 0.4, "partnership_basis": "profit",
               "payment_terms": "Invented terms for the demo.", "source": "Demo"},
        legal={"title": "Invented", "betterment_fee_per_m2": 300, "betterment_paid_share": 0.5},
        conditions={"primary_use": "tourism", "permitted_uses": ["residential", "hotel", "commercial"],
                    "footprint_pct": 0.2, "far": 0.4, "max_floors": 3,
                    "services_share_of_land": 0.05, "open_space_share_of_land": 0.3,
                    "share_rules": [{"use": "hotel", "basis": "built_area", "min_share": 0.0}],
                    "setbacks": {"front_m": 6, "side_m": 3, "sea_or_lake_m": 200},
                    "payment_terms": {"down_payment_pct": 0.3, "instalment_count": 4, "instalment_every_months": 12},
                    "build_deadline_months": 84},
        custom_fields=[{"label": "Demo note", "value": "All figures are invented", "unit": ""}],
        notes="Demo record with invented numbers.",
    )
    db.add(land)
    db.flush()
    comps = [
        ("Lagoon Terraces", "Demo Developer A", "chalet", 95_000, 0.10, 8),
        ("Palm Ridge", "Demo Developer B", "chalet", 88_000, 0.05, 10),
        ("Shore Villas", "Demo Developer C", "villa", 120_000, 0.10, 7),
        ("Blue Line", "Demo Developer A", "townhouse", 105_000, 0.10, 8),
        ("Coral Walk", "Demo Developer D", "chalet", 99_000, 0.15, 6),
    ]
    for project, dev, ptype, price, dp, yrs in comps:
        db.add(Comparable(location=DEMO_LOCATION, project=project, developer=dev, product_type=ptype,
                          price_per_m2=price, down_payment_pct=dp, instalment_years=yrs, verified=True,
                          notes="Invented demo row"))
    inputs = ScreenInputs(land_area_m2=area, land_price_per_feddan=20_000_000, purchase_share=0.4,
                          partnership_owner_share=0.4, far=0.4, price_per_m2=95_000, hard_cost_per_m2=28_000,
                          infrastructure_per_m2_land=1_800, land_down_payment_pct=0.3, land_instalment_count=4,
                          betterment_fee_per_m2=150)
    res = screen(inputs)
    db.add(Screening(land_id=land.id, inputs=inputs.model_dump(mode="json"),
                     result=_plain(res), verdict=res["verdict"]))
    data = svc.starter_study(land, "Base case (demo)")
    rec = StudyRecord(land_id=land.id, name="Base case (demo)", data=data)
    try:
        rec.result = _plain(svc.run_study(data))
    except svc.StudyInputError as e:
        rec.error = "\n".join(e.messages)
    db.add(rec)
    db.commit()
    return land


def _plain(x):
    import json
    from .main import _default
    return json.loads(json.dumps(x, default=_default))
