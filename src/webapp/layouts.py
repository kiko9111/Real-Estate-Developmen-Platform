"""Site layout, massing and best use pages (Step 4).

A layout belongs to a land. Its brief holds the boundary and the design
choices; the limits come from the land's conditions form each time it runs.
Every option is run through the feasibility engine using a base study (one of
the land's own studies, or the template when the land has none), and the
best one can be turned into a full study.
"""
from __future__ import annotations

import json
from typing import Optional

from fastapi import Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
from pydantic import ValidationError

from layout import Boundary, BoundaryError, LayoutBrief, Limits, default_brief, generate, make_site, rectangle
from layout.bestuse import edit_option, edited_name, evaluate
from layout.geometry import lonlat_to_local, parse_dxf, parse_kml, parse_points_text

from . import studies as svc
from .formschema import form_schema
from .models import LayoutRecord, Land, StudyRecord, now
from .schemas import LandConditions
from .security import require_editor, verify_csrf

MAX_UPLOAD = 5 * 1024 * 1024


# ---------------------------------------------------------------- service

def starting_brief(land: Land) -> dict:
    """The default brief, with a rectangle boundary when the land record gives a frontage."""
    boundary = None
    if land.frontage_m and land.area_m2:
        depth = land.area_m2 / land.frontage_m
        boundary = Boundary(points=rectangle(land.frontage_m, depth), frontage_edge=0, source="rectangle")
    return default_brief(boundary).model_dump(mode="json")


def brief_schema() -> dict:
    """The brief editor's form: everything except the boundary, which has its own page."""
    s = form_schema(LayoutBrief)
    s["properties"].pop("boundary", None)
    return s


def boundary_points_m(b: Boundary) -> list[tuple[float, float]]:
    if b.crs == "lonlat":
        pts, _ = lonlat_to_local(b.points)
        return pts
    return list(b.points)


def site_of(brief: LayoutBrief):
    if brief.boundary is None:
        raise BoundaryError("draw or upload the plot boundary first")
    return make_site(boundary_points_m(brief.boundary), brief.boundary.frontage_edge)


def base_study(land: Land, rec: LayoutRecord) -> tuple[dict, str]:
    """The study whose land deal, timing, plans and costs every option uses."""
    st: Optional[StudyRecord] = rec.base_study
    if st is None:
        ok = [s for s in land.studies if s.result and not s.error and not (s.description or "").startswith("From layout")]
        st = ok[-1] if ok else None
    if st is not None:
        return st.data, st.name
    return svc.starter_study(land, "template"), "the study template (this land has no study yet)"


def run_layout(rec: LayoutRecord) -> None:
    """Lay out every strategy and run it through the engine. The team's edited options are laid out
    again with their edits, so a rerun after a change to the conditions or the base study keeps them."""
    land = rec.land
    cond = LandConditions.model_validate(land.conditions or {})
    kept = {o["base_strategy"]: o["edits"] for o in (rec.options or []) if o.get("edits") and o.get("base_strategy")}
    rec.conditions_used = land.conditions or {}
    rec.run_at = now()
    try:
        brief = LayoutBrief.model_validate(rec.brief)
        site = site_of(brief)
        limits = Limits.from_conditions(cond)
        options = generate(site, brief, limits, land_area_m2=land.area_m2)
        names = {s.name for s in brief.strategies}
        for strategy, edits in kept.items():
            if strategy not in names:
                continue
            for o in generate(site, brief, limits, land_area_m2=land.area_m2, strategies=[strategy],
                              edits={strategy: edits}):
                o.update({"strategy": edited_name(strategy), "base_strategy": strategy, "edits": edits})
                options.append(o)
        base, base_name = base_study(land, rec)
        options = evaluate(options, brief, base)
        for o in options:
            o["base_study"] = base_name
        rec.options = json.loads(json.dumps(options, default=float))
        rec.error = ""
    except (BoundaryError, ValidationError, ValueError) as e:
        rec.options = None
        rec.error = str(e)


def parse_boundary(mode: str, data: bytes, filename: str, text: str, points_json: str,
                   frontage: Optional[float], depth: Optional[float], edge: int) -> Boundary:
    if mode == "file":
        if not data:
            raise BoundaryError("choose a KML, KMZ or DXF file")
        name = (filename or "").lower()
        if name.endswith((".kml", ".kmz")):
            return Boundary(crs="lonlat", points=parse_kml(data), frontage_edge=edge, source="kml")
        if name.endswith(".dxf"):
            return Boundary(crs="metres", points=parse_dxf(data), frontage_edge=edge, source="dxf")
        if name.endswith(".dwg"):
            raise BoundaryError("DWG files cannot be read; save the drawing as DXF in AutoCAD and upload that")
        raise BoundaryError("upload a .kml, .kmz or .dxf file")
    if mode == "map":
        pts = json.loads(points_json or "[]")
        if len(pts) < 3:
            raise BoundaryError("click at least three corners on the map")
        return Boundary(crs="lonlat", points=[(float(p[0]), float(p[1])) for p in pts], frontage_edge=edge, source="drawn")
    if mode == "points":
        return Boundary(crs="metres", points=parse_points_text(text), frontage_edge=edge, source="typed")
    if mode == "lonlat":
        return Boundary(crs="lonlat", points=parse_points_text(text), frontage_edge=edge, source="typed")
    if mode == "rectangle":
        if not frontage or not depth:
            raise BoundaryError("give the frontage and the depth")
        return Boundary(points=rectangle(frontage, depth), frontage_edge=0, source="rectangle")
    raise BoundaryError("unknown boundary input")


def boundary_preview(brief: dict) -> Optional[dict]:
    """Points in metres and edge lengths, for drawing the outline with its edge numbers."""
    try:
        b = LayoutBrief.model_validate(brief).boundary
        if b is None:
            return None
        pts = boundary_points_m(b)
        site = make_site(pts, b.frontage_edge)
    except (BoundaryError, ValidationError, ValueError):
        return None
    return {"points": [[round(x, 2), round(y, 2)] for x, y in pts], "edges": [round(e, 1) for e in site.edge_lengths],
            "frontage_edge": b.frontage_edge, "area_m2": site.area_m2, "frontage_m": site.frontage_m,
            "depth_m": site.depth_m, "source": b.source, "crs": b.crs,
            "lonlat": b.points if b.crs == "lonlat" else None}


# ---------------------------------------------------------------- routes

def add_routes(app, h) -> None:
    """h holds the app's helpers: render, flash, redirect, get_or_404, current_user, get_db, new_study, errors."""
    current_user, get_db = h.current_user, h.get_db

    @app.get("/lands/{lid}/layouts", response_class=HTMLResponse)
    def layouts_page(request: Request, lid: int, user=Depends(current_user), db=Depends(get_db)):
        land = h.get_or_404(db, Land, lid)
        return h.render(request, "layouts.html", {"land": land}, user=user)

    @app.post("/lands/{lid}/layouts", dependencies=[Depends(verify_csrf)])
    def create_layout(request: Request, lid: int, name: str = Form("Layout options"), user=Depends(current_user),
                      db=Depends(get_db)):
        require_editor(user)
        land = h.get_or_404(db, Land, lid)
        rec = LayoutRecord(land_id=land.id, name=name.strip() or "Layout options", brief=starting_brief(land),
                           created_by_id=user.id)
        db.add(rec)
        db.commit()
        h.flash(request, "Layout created. Set the plot boundary first.")
        return h.redirect(f"/layouts/{rec.id}/boundary")

    @app.get("/layouts/{rid}", response_class=HTMLResponse)
    def layout_page(request: Request, rid: int, option: int = -1, user=Depends(current_user), db=Depends(get_db)):
        rec = h.get_or_404(db, LayoutRecord, rid)
        opts = rec.options or []
        if option < 0:
            option = next((i for i, o in enumerate(opts) if o.get("best")), 0)
        option = max(0, min(option, len(opts) - 1)) if opts else 0
        ranked = sorted([(o.get("rank", 99), i, o) for i, o in enumerate(opts)], key=lambda x: (x[0], x[1]))
        studies = [s for s in rec.land.studies]
        return h.render(request, "layout.html", {
            "rec": rec, "land": rec.land, "opts": opts, "sel": option, "o": opts[option] if opts else None,
            "ranked": ranked, "preview": boundary_preview(rec.brief), "studies": studies,
            "stale": rec.run_at is not None and (rec.conditions_used or {}) != (rec.land.conditions or {}),
        }, user=user)

    @app.get("/layouts/{rid}/boundary", response_class=HTMLResponse)
    def boundary_page(request: Request, rid: int, user=Depends(current_user), db=Depends(get_db)):
        rec = h.get_or_404(db, LayoutRecord, rid)
        return h.render(request, "layout_boundary.html", {"rec": rec, "land": rec.land,
                                                          "preview": boundary_preview(rec.brief)}, user=user)

    @app.post("/layouts/{rid}/boundary", dependencies=[Depends(verify_csrf)])
    async def save_boundary(request: Request, rid: int, mode: str = Form(...), text: str = Form(""),
                            points_json: str = Form(""), frontage_m: Optional[float] = Form(None),
                            depth_m: Optional[float] = Form(None), frontage_edge: int = Form(0),
                            file: Optional[UploadFile] = File(None), user=Depends(current_user), db=Depends(get_db)):
        require_editor(user)
        rec = h.get_or_404(db, LayoutRecord, rid)
        data, fname = b"", ""
        if file is not None and file.filename:
            data = await file.read(MAX_UPLOAD + 1)
            fname = file.filename
            if len(data) > MAX_UPLOAD:
                return _boundary_error(request, h, rec, user, "the file is larger than 5 MB")
        try:
            b = parse_boundary(mode, data, fname, text, points_json, frontage_m, depth_m, max(0, frontage_edge))
            b.frontage_edge = b.frontage_edge % len(b.points)
            make_site(boundary_points_m(b), b.frontage_edge)
        except (BoundaryError, ValidationError, ValueError) as e:
            return _boundary_error(request, h, rec, user, str(e))
        brief = dict(rec.brief)
        brief["boundary"] = b.model_dump(mode="json")
        rec.brief = brief
        db.commit()
        h.flash(request, "Boundary saved. Check which edge is the frontage, then run the layout.")
        return h.redirect(f"/layouts/{rec.id}/boundary")

    @app.post("/layouts/{rid}/frontage", dependencies=[Depends(verify_csrf)])
    def set_frontage(request: Request, rid: int, frontage_edge: int = Form(...), user=Depends(current_user),
                     db=Depends(get_db)):
        require_editor(user)
        rec = h.get_or_404(db, LayoutRecord, rid)
        brief = json.loads(json.dumps(rec.brief))
        if not brief.get("boundary"):
            raise HTTPException(400, "No boundary yet.")
        brief["boundary"]["frontage_edge"] = frontage_edge % len(brief["boundary"]["points"])
        rec.brief = brief
        db.commit()
        return h.redirect(f"/layouts/{rec.id}/boundary")

    @app.get("/layouts/{rid}/brief", response_class=HTMLResponse)
    def brief_page(request: Request, rid: int, user=Depends(current_user), db=Depends(get_db)):
        rec = h.get_or_404(db, LayoutRecord, rid)
        value = {k: v for k, v in rec.brief.items() if k != "boundary"}
        return h.render(request, "layout_brief.html", {"rec": rec, "land": rec.land, "schema": brief_schema(),
                                                       "value": value}, user=user)

    @app.post("/layouts/{rid}/brief", dependencies=[Depends(verify_csrf)])
    def save_brief(request: Request, rid: int, data: str = Form(...), name: str = Form(""),
                   base_study_id: str = Form(""), user=Depends(current_user), db=Depends(get_db)):
        require_editor(user)
        rec = h.get_or_404(db, LayoutRecord, rid)
        try:
            new = json.loads(data)
            new["boundary"] = rec.brief.get("boundary")
            brief = LayoutBrief.model_validate(new)
        except (ValidationError, ValueError) as e:
            value = h.safe_json(data)
            return h.render(request, "layout_brief.html", {"rec": rec, "land": rec.land, "schema": brief_schema(),
                                                           "value": value, "errors": h.errors(e)}, 400, user=user)
        rec.brief = brief.model_dump(mode="json")
        if name.strip():
            rec.name = name.strip()
        if base_study_id.isdigit():
            st = db.get(StudyRecord, int(base_study_id))
            rec.base_study_id = st.id if st is not None and st.land_id == rec.land_id else None
        else:
            rec.base_study_id = None
        db.commit()
        h.flash(request, "Brief saved. Run the layout to see the options.")
        return h.redirect(f"/layouts/{rec.id}")

    @app.post("/layouts/{rid}/run", dependencies=[Depends(verify_csrf)])
    def run_layout_route(request: Request, rid: int, user=Depends(current_user), db=Depends(get_db)):
        require_editor(user)
        rec = h.get_or_404(db, LayoutRecord, rid)
        run_layout(rec)
        db.commit()
        if rec.error:
            h.flash(request, f"The layout could not run: {rec.error}", "error")
        else:
            n = len([o for o in rec.options if "error" not in o])
            h.flash(request, f"{n} options laid out and run through the feasibility engine.")
        return h.redirect(f"/layouts/{rec.id}")

    @app.post("/layouts/{rid}/options/{i}/study", dependencies=[Depends(verify_csrf)])
    def option_to_study(request: Request, rid: int, i: int, user=Depends(current_user), db=Depends(get_db)):
        require_editor(user)
        rec = h.get_or_404(db, LayoutRecord, rid)
        opts = rec.options or []
        if not (0 <= i < len(opts)) or "study" not in opts[i]:
            raise HTTPException(404, "That option has no study.")
        o = opts[i]
        k = o["kpis"]
        desc = (f"From layout '{rec.name}', option '{o['strategy']}': built area {k['built_area_m2']:,.0f} m2, "
                f"coverage {k['coverage']:.1%}, floor-area ratio {k['far']:.2f}, {k['units']:,.0f} units. "
                f"Land deal, timing, plans and costs from {o.get('base_study', 'the base study')}.")
        data = dict(o["study"])
        data["name"] = f"{o['strategy']} (layout)"
        st = h.new_study(db, rec.land, data["name"], data, user, description=desc)
        h.flash(request, "Study created from the layout option. Its products and built area come from the layout.")
        return h.redirect(f"/studies/{st.id}")

    @app.post("/layouts/{rid}/options/{i}/edit", dependencies=[Depends(verify_csrf)])
    def edit_option_route(request: Request, rid: int, i: int, edits: str = Form(...), user=Depends(current_user),
                          db=Depends(get_db)):
        """Apply the team's block changes ({block id: {"use": ..., "floors": ...}}) to option i."""
        require_editor(user)
        rec = h.get_or_404(db, LayoutRecord, rid)
        opts = json.loads(json.dumps(rec.options or []))
        if not (0 <= i < len(opts)) or "error" in opts[i]:
            raise HTTPException(404, "No such option.")
        try:
            changes = json.loads(edits)
            if not isinstance(changes, dict) or not changes:
                raise ValueError("no changes to apply")
            land = rec.land
            brief = LayoutBrief.model_validate(rec.brief)
            cond = LandConditions.model_validate(rec.conditions_used or land.conditions or {})
            base, base_name = base_study(land, rec)
            j = edit_option(opts, i, changes, site_of(brief), brief, Limits.from_conditions(cond), land.area_m2, base)
            opts[j]["base_study"] = base_name
        except (BoundaryError, ValidationError, ValueError) as e:
            h.flash(request, f"The edit could not be applied: {e}", "error")
            return h.redirect(f"/layouts/{rec.id}?option={i}")
        rec.options = json.loads(json.dumps(opts, default=float))
        db.commit()
        o = opts[j]
        msg = f"Edited plan saved as '{o['strategy']}'"
        msg += f", ranked {o['rank']} of {sum(1 for x in opts if 'rank' in x)}." if "rank" in o else "."
        if not o.get("passes", True):
            msg += " It breaks a condition; see the compliance check."
        h.flash(request, msg)
        return h.redirect(f"/layouts/{rec.id}?option={j}")

    @app.post("/layouts/{rid}/delete", dependencies=[Depends(verify_csrf)])
    def delete_layout(request: Request, rid: int, user=Depends(current_user), db=Depends(get_db)):
        require_editor(user)
        rec = h.get_or_404(db, LayoutRecord, rid)
        lid = rec.land_id
        db.delete(rec)
        db.commit()
        h.flash(request, "Layout deleted.")
        return h.redirect(f"/lands/{lid}")


def _boundary_error(request, h, rec, user, msg):
    return h.render(request, "layout_boundary.html", {"rec": rec, "land": rec.land, "errors": [msg],
                                                      "preview": boundary_preview(rec.brief)}, 400, user=user)
