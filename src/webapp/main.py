"""The web app: team logins, lands, screening, full studies, market scans, reports."""
from __future__ import annotations

import csv
import io
import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy import func, or_, select
from starlette.middleware.sessions import SessionMiddleware

from feasibility import FEDDAN_M2, Study

from types import SimpleNamespace

from . import charts, layouts as layout_pages, studies as svc
from .config import Settings
from .db import Base, make_engine, make_sessionmaker
from .formschema import form_schema
from .market import market_stats, median_price
from .market.web_scan import ScanError, clean_row, run_scan, safe_url
from .models import LAND_STATUSES, ROLES, Comparable, Land, MarketScan, Screening, StudyRecord, User, now
from .reports import build_report_data, render_pdf, render_pptx
from .schemas import CONDITIONS_VERSION, LandConditions, LandForm
from .screener import GROUPS, LABELS, ScreenInputs, build_study, defaults_for_land, screen
from .security import (NotLoggedIn, check_password_rules, csrf_token, hash_password, require_admin,
                       require_editor, verify_csrf, verify_password)

HERE = Path(__file__).parent
STATUS_LABELS = {"new": "New", "screening": "Screening", "studying": "Studying", "go": "Go",
                 "no_go": "No-go", "archived": "Archived"}
VERDICT_LABELS = {"go": "Go", "borderline": "Borderline", "no_go": "No-go"}


# ---------------------------------------------------------------- formatting

def f_money(x, digits: int = 1) -> str:
    if x is None:
        return "n/a"
    a = abs(x)
    if a >= 1e9:
        s = f"{a / 1e9:,.2f} B"
    elif a >= 1e6:
        s = f"{a / 1e6:,.{digits}f} M"
    else:
        s = f"{a:,.0f}"
    return ("-" if x < 0 else "") + s


def f_pct(x, digits: int = 1) -> str:
    return "n/a" if x is None else f"{x * 100:.{digits}f}%"


def f_num(x, digits: int = 0) -> str:
    return "n/a" if x is None else f"{x:,.{digits}f}"


def f_kpi(key: str, v) -> str:
    if v is None:
        return "n/a"
    if "irr" in key or "margin" in key:
        return f_pct(v)
    if "month" in key:
        return f"month {v}"
    if key in ("equity_multiple",):
        return f"{v:.2f}x"
    if key in ("land_area_feddan", "units"):
        return f_num(v, 1)
    return f_money(v)


# ---------------------------------------------------------------- app factory

def create_app(settings: Optional[Settings] = None) -> FastAPI:
    settings = settings or Settings()
    engine = make_engine(settings.database_url)
    Base.metadata.create_all(engine)
    Session = make_sessionmaker(engine)

    app = FastAPI(title="Development Platform", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.Session = Session
    app.state.scan_runner = run_scan          # replaced in tests
    app.add_middleware(SessionMiddleware, secret_key=settings.secret_key, same_site="lax",
                       https_only=settings.secure_cookies, max_age=60 * 60 * 12)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.filters.update(money=f_money, pct=f_pct, num=f_num)
    templates.env.globals.update(kpi=f_kpi, STATUS_LABELS=STATUS_LABELS, VERDICT_LABELS=VERDICT_LABELS,
                                 LINE_LABELS=svc.LINE_LABELS, KPI_LABELS=svc.KPI_LABELS, FEDDAN_M2=FEDDAN_M2,
                                 currency=settings.currency)

    _bootstrap_admin(Session, settings)

    # ------------------------------------------------------------ plumbing

    def get_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    def current_user(request: Request, db=Depends(get_db)) -> User:
        uid = request.session.get("uid")
        user = db.get(User, uid) if uid else None
        if not user or not user.active:
            request.session.pop("uid", None)
            raise NotLoggedIn()
        return user

    def render(request: Request, name: str, ctx: dict | None = None, status: int = 200, user: User | None = None):
        c = {"request": request, "user": user, "csrf": csrf_token(request),
             "flash": request.session.pop("flash", None), "settings": settings}
        c.update(ctx or {})
        return templates.TemplateResponse(request, name, c, status_code=status)

    def flash(request: Request, msg: str, kind: str = "ok"):
        request.session["flash"] = {"msg": msg, "kind": kind}

    def redirect(url: str):
        return RedirectResponse(url, status_code=303)

    def get_or_404(db, model, id_):
        obj = db.get(model, id_)
        if obj is None:
            raise HTTPException(404, "Not found.")
        return obj

    @app.exception_handler(NotLoggedIn)
    async def _login_needed(request: Request, exc):
        nxt = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        return redirect(f"/login?next={quote(nxt)}")

    @app.exception_handler(HTTPException)
    async def _http_error(request: Request, exc: HTTPException):
        return render(request, "error.html", {"status": exc.status_code, "detail": exc.detail}, exc.status_code)

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    # ------------------------------------------------------------ auth

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request, next: str = "/"):
        return render(request, "login.html", {"next": next})

    @app.post("/login", dependencies=[Depends(verify_csrf)])
    def login(request: Request, email: str = Form(...), password: str = Form(...), next: str = Form("/"),
              db=Depends(get_db)):
        user = db.scalar(select(User).where(func.lower(User.email) == email.strip().lower()))
        if not user or not user.active or not verify_password(password, user.password_hash):
            return render(request, "login.html", {"next": next, "error": "Email or password is wrong."}, 401)
        request.session.clear()
        request.session["uid"] = user.id
        user.last_login_at = now()
        db.commit()
        return redirect(next if next.startswith("/") and not next.startswith("//") else "/")

    @app.post("/logout", dependencies=[Depends(verify_csrf)])
    def logout(request: Request):
        request.session.clear()
        return redirect("/login")

    @app.get("/account", response_class=HTMLResponse)
    def account(request: Request, user=Depends(current_user)):
        return render(request, "account.html", user=user)

    @app.post("/account/password", dependencies=[Depends(verify_csrf)])
    def change_password(request: Request, current: str = Form(...), new: str = Form(...),
                        user=Depends(current_user), db=Depends(get_db)):
        if not verify_password(current, user.password_hash):
            return render(request, "account.html", {"error": "Current password is wrong."}, 400, user=user)
        if err := check_password_rules(new):
            return render(request, "account.html", {"error": err}, 400, user=user)
        user.password_hash = hash_password(new)
        db.commit()
        flash(request, "Password changed.")
        return redirect("/account")

    # ------------------------------------------------------------ users (admin)

    @app.get("/users", response_class=HTMLResponse)
    def users_page(request: Request, user=Depends(current_user), db=Depends(get_db)):
        require_admin(user)
        return render(request, "users.html", {"users": db.scalars(select(User).order_by(User.name)).all(),
                                              "roles": ROLES}, user=user)

    @app.post("/users", dependencies=[Depends(verify_csrf)])
    def create_user(request: Request, name: str = Form(...), email: str = Form(...), role: str = Form("analyst"),
                    password: str = Form(...), user=Depends(current_user), db=Depends(get_db)):
        require_admin(user)
        email = email.strip().lower()
        err = check_password_rules(password)
        if role not in ROLES:
            err = "Unknown role."
        if db.scalar(select(User).where(func.lower(User.email) == email)):
            err = "A user with that email already exists."
        if err:
            flash(request, err, "error")
            return redirect("/users")
        db.add(User(name=name.strip(), email=email, role=role, password_hash=hash_password(password)))
        db.commit()
        flash(request, f"Added {name}.")
        return redirect("/users")

    @app.post("/users/{uid}", dependencies=[Depends(verify_csrf)])
    def update_user(request: Request, uid: int, role: str = Form(...), active: Optional[str] = Form(None),
                    password: str = Form(""), user=Depends(current_user), db=Depends(get_db)):
        require_admin(user)
        target = get_or_404(db, User, uid)
        if role not in ROLES:
            raise HTTPException(400, "Unknown role.")
        if target.id == user.id and (role != "admin" or not active):
            flash(request, "You cannot remove your own admin access.", "error")
            return redirect("/users")
        target.role, target.active = role, bool(active)
        if password:
            if err := check_password_rules(password):
                flash(request, err, "error")
                return redirect("/users")
            target.password_hash = hash_password(password)
        db.commit()
        flash(request, f"Updated {target.name}.")
        return redirect("/users")

    # ------------------------------------------------------------ lands

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request, status: str = "", q: str = "", user=Depends(current_user), db=Depends(get_db)):
        stmt = select(Land).order_by(Land.updated_at.desc())
        if status in LAND_STATUSES:
            stmt = stmt.where(Land.status == status)
        elif status != "all":
            stmt = stmt.where(Land.status != "archived")
        if q:
            like = f"%{q.strip()}%"
            stmt = stmt.where(or_(Land.name.ilike(like), Land.region.ilike(like), Land.area_name.ilike(like),
                                  Land.code.ilike(like)))
        lands = db.scalars(stmt).all()
        counts = dict(db.execute(select(Land.status, func.count()).group_by(Land.status)).all())
        return render(request, "dashboard.html", {"lands": lands, "status": status, "q": q, "counts": counts},
                      user=user)

    @app.get("/lands/new", response_class=HTMLResponse)
    def new_land(request: Request, user=Depends(current_user)):
        require_editor(user)
        blank = {"name": "", "area_m2": None, "status": "new"}
        return render(request, "land_form.html", {"land": None, "schema": form_schema(LandForm), "value": blank},
                      user=user)

    def _land_from_form(data: str) -> LandForm:
        return LandForm.model_validate(json.loads(data))

    def _errors(e: Exception) -> list[str]:
        if isinstance(e, ValidationError):
            return [(" > ".join(str(x) for x in err["loc"]) + ": " if err["loc"] else "") + err["msg"]
                    for err in e.errors()]
        if isinstance(e, svc.StudyInputError):
            return e.messages
        return [str(e)]

    @app.post("/lands", dependencies=[Depends(verify_csrf)])
    def create_land(request: Request, data: str = Form(...), user=Depends(current_user), db=Depends(get_db)):
        require_editor(user)
        try:
            f = _land_from_form(data)
        except (ValidationError, ValueError) as e:
            return render(request, "land_form.html", {"land": None, "schema": form_schema(LandForm),
                                                      "value": _safe_json(data), "errors": _errors(e)}, 400, user=user)
        land = Land(**_land_columns(f), created_by_id=user.id, conditions={}, conditions_version=CONDITIONS_VERSION)
        db.add(land)
        db.commit()
        flash(request, "Land saved. Fill in its conditions next, or run a quick screening.")
        return redirect(f"/lands/{land.id}")

    @app.get("/lands/{lid}", response_class=HTMLResponse)
    def land_detail(request: Request, lid: int, user=Depends(current_user), db=Depends(get_db)):
        land = get_or_404(db, Land, lid)
        cond = LandConditions.model_validate(land.conditions or {})
        comps = _comps_for_land(db, land)
        stats = market_stats(comps)
        return render(request, "land_detail.html", {
            "land": land, "cond": cond, "cond_filled": bool(land.conditions),
            "comps": comps[:12], "stats": stats, "market_location": _market_location(land),
        }, user=user)

    @app.get("/lands/{lid}/edit", response_class=HTMLResponse)
    def edit_land(request: Request, lid: int, user=Depends(current_user), db=Depends(get_db)):
        require_editor(user)
        land = get_or_404(db, Land, lid)
        return render(request, "land_form.html", {"land": land, "schema": form_schema(LandForm),
                                                  "value": _land_value(land)}, user=user)

    @app.post("/lands/{lid}/edit", dependencies=[Depends(verify_csrf)])
    def save_land(request: Request, lid: int, data: str = Form(...), user=Depends(current_user), db=Depends(get_db)):
        require_editor(user)
        land = get_or_404(db, Land, lid)
        try:
            f = _land_from_form(data)
        except (ValidationError, ValueError) as e:
            return render(request, "land_form.html", {"land": land, "schema": form_schema(LandForm),
                                                      "value": _safe_json(data), "errors": _errors(e)}, 400, user=user)
        for k, v in _land_columns(f).items():
            setattr(land, k, v)
        db.commit()
        flash(request, "Land saved.")
        return redirect(f"/lands/{land.id}")

    @app.get("/lands/{lid}/conditions", response_class=HTMLResponse)
    def conditions_page(request: Request, lid: int, user=Depends(current_user), db=Depends(get_db)):
        land = get_or_404(db, Land, lid)
        return render(request, "conditions.html", {"land": land, "schema": form_schema(LandConditions),
                                                   "value": land.conditions or {}}, user=user)

    @app.post("/lands/{lid}/conditions", dependencies=[Depends(verify_csrf)])
    def save_conditions(request: Request, lid: int, data: str = Form(...), user=Depends(current_user),
                        db=Depends(get_db)):
        require_editor(user)
        land = get_or_404(db, Land, lid)
        try:
            cond = LandConditions.model_validate(json.loads(data))
            cond.to_engine_conditions(land.area_m2)
        except (ValidationError, ValueError) as e:
            return render(request, "conditions.html", {"land": land, "schema": form_schema(LandConditions),
                                                       "value": _safe_json(data), "errors": _errors(e)}, 400, user=user)
        land.conditions = cond.model_dump(mode="json", exclude_defaults=True)
        land.conditions_version = CONDITIONS_VERSION
        db.commit()
        flash(request, "Conditions saved.")
        return redirect(f"/lands/{land.id}")

    @app.post("/lands/{lid}/status", dependencies=[Depends(verify_csrf)])
    def set_status(request: Request, lid: int, status: str = Form(...), user=Depends(current_user), db=Depends(get_db)):
        require_editor(user)
        land = get_or_404(db, Land, lid)
        if status not in LAND_STATUSES:
            raise HTTPException(400, "Unknown status.")
        land.status = status
        db.commit()
        return redirect(f"/lands/{lid}")

    @app.post("/lands/{lid}/delete", dependencies=[Depends(verify_csrf)])
    def delete_land(request: Request, lid: int, user=Depends(current_user), db=Depends(get_db)):
        require_admin(user)
        land = get_or_404(db, Land, lid)
        db.delete(land)
        db.commit()
        flash(request, "Land deleted.")
        return redirect("/")

    # ------------------------------------------------------------ screening

    @app.get("/lands/{lid}/screen", response_class=HTMLResponse)
    def screen_page(request: Request, lid: int, user=Depends(current_user), db=Depends(get_db)):
        land = get_or_404(db, Land, lid)
        src = request.query_params.get("from")
        values = None
        if src and src.isdigit():
            s = db.get(Screening, int(src))
            if s and s.land_id == land.id:
                values = s.inputs
        if values is None:
            values = defaults_for_land(land, median_price(_comps_for_land(db, land)))
        return render(request, "screen_form.html", {"land": land, "values": values, "groups": GROUPS,
                                                    "labels": LABELS, "fields": ScreenInputs.model_fields}, user=user)

    @app.post("/lands/{lid}/screen", dependencies=[Depends(verify_csrf)])
    async def run_screen(request: Request, lid: int, user=Depends(current_user), db=Depends(get_db)):
        require_editor(user)
        land = get_or_404(db, Land, lid)
        form = await request.form()
        raw = {k: v for k, v in form.items() if k in ScreenInputs.model_fields and str(v).strip() != ""}
        try:
            inputs = ScreenInputs.model_validate(raw)
            result = screen(inputs)
        except (ValidationError, ValueError) as e:
            return render(request, "screen_form.html", {"land": land, "values": raw, "groups": GROUPS, "labels": LABELS,
                                                        "fields": ScreenInputs.model_fields, "errors": _errors(e)},
                          400, user=user)
        s = Screening(land_id=land.id, inputs=inputs.model_dump(mode="json"), result=_jsonable(result),
                      verdict=result["verdict"], created_by_id=user.id)
        db.add(s)
        if land.status == "new":
            land.status = "screening"
        db.commit()
        return redirect(f"/screenings/{s.id}")

    @app.get("/screenings/{sid}", response_class=HTMLResponse)
    def screening_page(request: Request, sid: int, user=Depends(current_user), db=Depends(get_db)):
        s = get_or_404(db, Screening, sid)
        r = s.result
        chart = charts.cash_flow_chart(r.get("yearly_net_cash", []), svc._cum(r.get("yearly_net_cash", [])))
        return render(request, "screening.html", {"s": s, "land": s.land, "r": r, "chart": chart,
                                                  "labels": LABELS, "groups": GROUPS}, user=user)

    @app.post("/screenings/{sid}/to-study", dependencies=[Depends(verify_csrf)])
    def screening_to_study(request: Request, sid: int, user=Depends(current_user), db=Depends(get_db)):
        require_editor(user)
        s = get_or_404(db, Screening, sid)
        name = f"Full study from screening {s.created_at:%d %b %Y}"
        data = build_study(ScreenInputs.model_validate(s.inputs), name=name).model_dump(mode="json")
        rec = _new_study(db, s.land, name, data, user)
        flash(request, "Study created from the screening. Add products, phases and plans, then run it.")
        return redirect(f"/studies/{rec.id}")

    # ------------------------------------------------------------ studies

    def _new_study(db, land, name, data, user, parent_id=None, description="") -> StudyRecord:
        rec = StudyRecord(land_id=land.id, name=name, data=data, parent_id=parent_id, description=description,
                          created_by_id=user.id)
        _run_into(rec)
        db.add(rec)
        if land.status in ("new", "screening"):
            land.status = "studying"
        db.commit()
        return rec

    def _run_into(rec: StudyRecord):
        try:
            rec.result = _jsonable(svc.run_study(rec.data))
            rec.error = ""
        except svc.StudyInputError as e:
            rec.result = None
            rec.error = "\n".join(e.messages)
        rec.run_at = now()

    @app.post("/lands/{lid}/studies", dependencies=[Depends(verify_csrf)])
    def create_study(request: Request, lid: int, name: str = Form("Base case"), user=Depends(current_user),
                     db=Depends(get_db)):
        require_editor(user)
        land = get_or_404(db, Land, lid)
        rec = _new_study(db, land, name.strip() or "Base case", svc.starter_study(land, name.strip() or "Base case"), user)
        flash(request, "Study created from the template with this land's area and conditions. "
                       "Replace the template's products, prices and costs with this land's own.")
        return redirect(f"/studies/{rec.id}/edit")

    @app.get("/studies/{sid}", response_class=HTMLResponse)
    def study_page(request: Request, sid: int, user=Depends(current_user), db=Depends(get_db)):
        rec = get_or_404(db, StudyRecord, sid)
        r = rec.result
        chart = charts.cash_flow_chart(r["yearly"]["cash_flow_pre_tax"], r["cumulative_yearly"]) if r else ""
        children = db.scalars(select(StudyRecord).where(StudyRecord.parent_id == rec.id)).all()
        return render(request, "study.html", {"rec": rec, "land": rec.land, "r": r, "chart": chart,
                                              "children": children, "analysis": (r or {}).get("analysis", {}),
                                              "shocks": svc.SCENARIO_SHOCKS}, user=user)

    @app.get("/studies/{sid}/edit", response_class=HTMLResponse)
    def study_edit(request: Request, sid: int, user=Depends(current_user), db=Depends(get_db)):
        require_editor(user)
        rec = get_or_404(db, StudyRecord, sid)
        return render(request, "study_edit.html", {"rec": rec, "land": rec.land, "schema": form_schema(Study),
                                                   "value": rec.data}, user=user)

    @app.post("/studies/{sid}/edit", dependencies=[Depends(verify_csrf)])
    def study_save(request: Request, sid: int, data: str = Form(...), name: str = Form(...),
                   description: str = Form(""), user=Depends(current_user), db=Depends(get_db)):
        require_editor(user)
        rec = get_or_404(db, StudyRecord, sid)
        try:
            parsed = json.loads(data)
            svc.validate_study(parsed)
            result = svc.run_study(parsed)
        except (ValueError, svc.StudyInputError) as e:
            return render(request, "study_edit.html", {"rec": rec, "land": rec.land, "schema": form_schema(Study),
                                                       "value": _safe_json(data), "errors": _errors(e)}, 400, user=user)
        parsed["name"] = name.strip() or rec.name
        rec.name, rec.description, rec.data = parsed["name"], description, parsed
        rec.result, rec.error, rec.run_at = _jsonable(result), "", now()
        db.commit()
        flash(request, "Saved and run.")
        return redirect(f"/studies/{rec.id}")

    @app.post("/studies/{sid}/scenario", dependencies=[Depends(verify_csrf)])
    def make_scenario(request: Request, sid: int, name: str = Form(...), user=Depends(current_user),
                      db=Depends(get_db)):
        require_editor(user)
        rec = get_or_404(db, StudyRecord, sid)
        data = json.loads(json.dumps(rec.data))
        data["name"] = name.strip() or f"{rec.name} (scenario)"
        child = _new_study(db, rec.land, data["name"], data, user, parent_id=rec.id,
                           description=f"Scenario of {rec.name}")
        flash(request, "Scenario created as a copy. Change what this scenario tests and save.")
        return redirect(f"/studies/{child.id}/edit")

    @app.post("/studies/{sid}/analysis", dependencies=[Depends(verify_csrf)])
    async def study_analysis(request: Request, sid: int, user=Depends(current_user), db=Depends(get_db)):
        require_editor(user)
        rec = get_or_404(db, StudyRecord, sid)
        if not rec.result:
            raise HTTPException(400, "Run the study successfully first.")
        form = await request.form()
        kind = form.get("kind")
        analysis = dict(rec.result.get("analysis", {}))
        try:
            if kind == "scenarios":
                analysis["scenarios"] = svc.quick_scenarios(rec.data)
            elif kind == "residual":
                analysis["residual"] = svc.residual(rec.data)
                if analysis["residual"] is None:
                    flash(request, "Residual land value needs a cash purchase in the land deal.", "error")
            elif kind == "risk":
                runs = max(20, min(1000, int(form.get("runs") or 200)))
                analysis["risk"] = svc.risk(rec.data, runs)
            elif kind == "sensitivity":
                shock = str(form.get("shock") or "price")
                values = [float(x) for x in str(form.get("values") or "").replace(";", ",").split(",") if x.strip()]
                if not values:
                    values = {"price": [0.8, 0.9, 1.0, 1.1, 1.2], "cost": [0.8, 0.9, 1.0, 1.1, 1.2],
                              "sales_pace": [0.6, 0.8, 1.0, 1.2], "delay": [0, 6, 12, 18],
                              "cost_inflation": [-0.02, 0, 0.02, 0.04], "cancellation_rate": [0, 0.05, 0.1],
                              "land_price": [0.8, 0.9, 1.0, 1.1, 1.2]}.get(shock, [1.0])
                analysis["sensitivity"] = {"shock": shock, "rows": svc.sensitivity_table(rec.data, shock, values[:12])}
            else:
                raise HTTPException(400, "Unknown analysis.")
        except (svc.StudyInputError, ValueError) as e:
            flash(request, "; ".join(_errors(e)), "error")
            return redirect(f"/studies/{sid}")
        result = dict(rec.result)
        result["analysis"] = _jsonable(analysis)
        rec.result = result
        db.commit()
        return redirect(f"/studies/{sid}#analysis")

    @app.post("/studies/{sid}/delete", dependencies=[Depends(verify_csrf)])
    def delete_study(request: Request, sid: int, user=Depends(current_user), db=Depends(get_db)):
        require_editor(user)
        rec = get_or_404(db, StudyRecord, sid)
        lid = rec.land_id
        db.delete(rec)
        db.commit()
        flash(request, "Study deleted.")
        return redirect(f"/lands/{lid}")

    @app.get("/studies/{sid}/study.json")
    def study_json(sid: int, user=Depends(current_user), db=Depends(get_db)):
        rec = get_or_404(db, StudyRecord, sid)
        return Response(json.dumps(rec.data, indent=2), media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="{_fname(rec.name)}.json"'})

    @app.get("/lands/{lid}/compare", response_class=HTMLResponse)
    def compare(request: Request, lid: int, user=Depends(current_user), db=Depends(get_db)):
        land = get_or_404(db, Land, lid)
        recs = [s for s in land.studies if s.result]
        keys = ["revenue", "development_cost", "land_cost", "profit_pre_tax", "margin_on_revenue", "npv_pre_tax",
                "irr_pre_tax", "npv_after_tax", "irr_after_tax", "peak_funding_need", "payback_month", "equity_irr"]
        return render(request, "compare.html", {"land": land, "recs": recs, "keys": keys}, user=user)

    # ------------------------------------------------------------ reports

    def _report(rec: StudyRecord, db, user) -> object:
        if not rec.result:
            raise HTTPException(400, "Run the study successfully before exporting a report.")
        analysis = rec.result.get("analysis", {})
        scen = analysis.get("scenarios") or svc.quick_scenarios(rec.data)
        resid = analysis.get("residual") if "residual" in analysis else svc.residual(rec.data)
        stats = market_stats(_comps_for_land(db, rec.land), verified_only=False)
        return build_report_data(rec.land, rec, rec.result, rec.data, scen, resid, stats,
                                 currency=settings.currency, prepared_by=user.name)

    @app.get("/studies/{sid}/report.pdf")
    def report_pdf(sid: int, user=Depends(current_user), db=Depends(get_db)):
        rec = get_or_404(db, StudyRecord, sid)
        pdf = render_pdf(_report(rec, db, user))
        return Response(pdf, media_type="application/pdf",
                        headers={"Content-Disposition": f'attachment; filename="{_fname(rec.name)}.pdf"'})

    @app.get("/studies/{sid}/report.pptx")
    def report_pptx(sid: int, user=Depends(current_user), db=Depends(get_db)):
        rec = get_or_404(db, StudyRecord, sid)
        data = render_pptx(_report(rec, db, user))
        return Response(data, media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                        headers={"Content-Disposition": f'attachment; filename="{_fname(rec.name)}.pptx"'})

    # ------------------------------------------------------------ market

    @app.get("/market", response_class=HTMLResponse)
    def market(request: Request, location: str = "", verified: int = 0, user=Depends(current_user), db=Depends(get_db)):
        locations = db.execute(select(Comparable.location, func.count()).group_by(Comparable.location)
                               .order_by(Comparable.location)).all()
        comps, scans, stats = [], [], None
        if location:
            comps = db.scalars(select(Comparable).where(func.lower(Comparable.location) == location.strip().lower())
                               .order_by(Comparable.product_type, Comparable.price_per_m2.desc())).all()
            scans = db.scalars(select(MarketScan).where(func.lower(MarketScan.location) == location.strip().lower())
                               .order_by(MarketScan.created_at.desc())).all()
            stats = market_stats(comps, verified_only=bool(verified))
        ranges = {}
        if stats:
            for t, srow in stats["by_type"].items():
                p = srow["price_per_m2"]
                if p:
                    ranges[t] = charts.range_bar(p["min"], p["median"], p["max"])
        running = any(s.status == "running" for s in scans)
        lands = db.scalars(select(Land).where(Land.status != "archived").order_by(Land.name)).all()
        return render(request, "market.html", {"location": location, "locations": locations, "comps": comps,
                                               "scans": scans, "stats": stats, "verified": verified, "ranges": ranges,
                                               "running": running, "lands": lands,
                                               "land_id": request.query_params.get("land_id", "")}, user=user)

    def _scan_job(scan_id: int, location: str, focus: str):
        db = Session()
        try:
            scan = db.get(MarketScan, scan_id)
            try:
                out = app.state.scan_runner(location, focus, model=settings.market_scan_model,
                                            currency=settings.currency)
                for row in out.comparables:
                    db.add(Comparable(scan_id=scan.id, location=scan.location, created_by_id=scan.created_by_id,
                                      **clean_row(row)))
                scan.summary = out.summary
                scan.status = "done"
            except ScanError as e:
                scan.status, scan.error = "failed", str(e)
            except Exception as e:  # keep the app alive; record what happened
                scan.status, scan.error = "failed", f"Unexpected error: {type(e).__name__}"
            db.commit()
        finally:
            db.close()

    @app.post("/market/scan", dependencies=[Depends(verify_csrf)])
    def start_scan(request: Request, location: str = Form(...), product_focus: str = Form(""),
                   land_id: str = Form(""), user=Depends(current_user), db=Depends(get_db)):
        require_editor(user)
        location = location.strip()
        if not location:
            raise HTTPException(400, "Enter a location.")
        if not settings.market_scan_enabled:
            flash(request, "The web scan is not set up on this server (it needs an Anthropic API key). "
                           "You can still add comparables by hand below.", "error")
            return redirect(f"/market?location={quote(location)}")
        scan = MarketScan(location=location, product_focus=product_focus.strip(), provider="claude_web_search",
                          status="running", created_by_id=user.id,
                          land_id=int(land_id) if land_id.isdigit() else None)
        db.add(scan)
        db.commit()
        t = threading.Thread(target=_scan_job, args=(scan.id, location, scan.product_focus), daemon=True)
        t.start()
        app.state.last_scan_thread = t
        flash(request, "Market scan started. It usually takes one to three minutes; this page refreshes itself.")
        return redirect(f"/market?location={quote(location)}")

    def _comp_from_form(form) -> dict:
        def num(k, scale=1.0):
            v = str(form.get(k) or "").replace(",", "").strip()
            if not v:
                return None
            try:
                return float(v) * scale
            except ValueError:
                raise HTTPException(400, f"'{k.replace('_', ' ')}' must be a number.")
        dp = num("down_payment_pct")
        return {
            "project": str(form.get("project") or "").strip()[:200],
            "developer": str(form.get("developer") or "").strip()[:200],
            "product_type": str(form.get("product_type") or "").strip().lower()[:60],
            "price_per_m2": num("price_per_m2"), "unit_price_from": num("unit_price_from"),
            "unit_size_m2": num("unit_size_m2"),
            "down_payment_pct": dp / 100 if dp is not None and dp > 1 else dp,
            "instalment_years": num("instalment_years"),
            "delivery": str(form.get("delivery") or "")[:60], "finishing": str(form.get("finishing") or "")[:60],
            "source_url": safe_url(form.get("source_url")), "source_date": str(form.get("source_date") or "")[:40],
            "notes": str(form.get("notes") or ""),
        }

    @app.post("/market/comparables", dependencies=[Depends(verify_csrf)])
    async def add_comparable(request: Request, user=Depends(current_user), db=Depends(get_db)):
        require_editor(user)
        form = await request.form()
        location = str(form.get("location") or "").strip()
        row = _comp_from_form(form)
        if not location or not row["project"]:
            raise HTTPException(400, "Location and project are required.")
        db.add(Comparable(location=location, verified=True, created_by_id=user.id, **row))
        db.commit()
        flash(request, "Comparable added.")
        return redirect(f"/market?location={quote(location)}")

    @app.post("/market/import", dependencies=[Depends(verify_csrf)])
    def import_comparables(request: Request, location: str = Form(...), csv_text: str = Form(...),
                           user=Depends(current_user), db=Depends(get_db)):
        """Paste rows with a header line, comma or tab separated."""
        require_editor(user)
        location = location.strip()
        text = csv_text.strip()
        dialect = "excel-tab" if "\t" in text.splitlines()[0] else "excel"
        reader = csv.DictReader(io.StringIO(text), dialect=dialect)
        n = 0
        for r in reader:
            r = {str(k).strip().lower().replace(" ", "_"): v for k, v in r.items() if k}
            if not r.get("project"):
                continue
            db.add(Comparable(location=location, verified=True, created_by_id=user.id, **_comp_from_form(r)))
            n += 1
        db.commit()
        flash(request, f"Imported {n} comparables.")
        return redirect(f"/market?location={quote(location)}")

    @app.post("/comparables/{cid}", dependencies=[Depends(verify_csrf)])
    def update_comparable(request: Request, cid: int, action: str = Form(...), user=Depends(current_user),
                          db=Depends(get_db)):
        require_editor(user)
        c = get_or_404(db, Comparable, cid)
        loc = c.location
        if action == "verify":
            c.verified = not c.verified
        elif action == "exclude":
            c.excluded = not c.excluded
        elif action == "delete":
            db.delete(c)
        else:
            raise HTTPException(400, "Unknown action.")
        db.commit()
        return redirect(f"/market?location={quote(loc)}")

    @app.get("/market/export.csv")
    def export_market(location: str, user=Depends(current_user), db=Depends(get_db)):
        comps = db.scalars(select(Comparable).where(func.lower(Comparable.location) == location.strip().lower())).all()
        buf = io.StringIO()
        cols = ["location", "project", "developer", "product_type", "price_per_m2", "unit_price_from", "unit_size_m2",
                "down_payment_pct", "instalment_years", "delivery", "finishing", "source_url", "source_date",
                "verified", "excluded", "notes"]
        w = csv.writer(buf)
        w.writerow(cols)
        for c in comps:
            w.writerow([getattr(c, k) for k in cols])
        return Response(buf.getvalue(), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="market-{_fname(location)}.csv"'})

    # ------------------------------------------------------------ helpers

    def _market_location(land: Land) -> str:
        return land.area_name or land.region

    def _comps_for_land(db, land: Land) -> list[Comparable]:
        keys = [k.strip().lower() for k in (land.area_name, land.region) if k and k.strip()]
        if not keys:
            return []
        return db.scalars(select(Comparable).where(func.lower(Comparable.location).in_(keys))
                          .order_by(Comparable.created_at.desc())).all()

    # ------------------------------------------------------------ site layout (Step 4)

    layout_pages.add_routes(app, SimpleNamespace(
        render=render, flash=flash, redirect=redirect, get_or_404=get_or_404, current_user=current_user,
        get_db=get_db, new_study=_new_study, errors=_errors, safe_json=_safe_json))

    return app


# ---------------------------------------------------------------- module helpers

def _land_columns(f: LandForm) -> dict:
    d = f.model_dump(mode="json")
    return {k: d[k] for k in ("name", "code", "status", "region", "area_name", "governorate", "latitude", "longitude",
                              "map_url", "area_m2", "parcels", "frontage_m", "depth_m", "frontage_type", "offer",
                              "legal", "custom_fields", "notes")} | {"map_url": safe_url(d["map_url"])}


def _land_value(land: Land) -> dict:
    return {k: getattr(land, k) for k in LandForm.model_fields} | {"code": land.code or ""}


def _safe_json(text: str):
    try:
        return json.loads(text)
    except ValueError:
        return {}


def _jsonable(x):
    """Plain JSON types only (numpy scalars and dataclasses become floats and dicts)."""
    return json.loads(json.dumps(x, default=_default))


def _default(o):
    try:
        import numpy as np
        if isinstance(o, np.generic):
            return o.item()
        if isinstance(o, np.ndarray):
            return o.tolist()
    except ImportError:
        pass
    if hasattr(o, "__dict__"):
        return o.__dict__
    if isinstance(o, datetime):
        return o.isoformat()
    raise TypeError(type(o).__name__)


def _fname(s: str) -> str:
    keep = "".join(ch if ch.isalnum() or ch in "-_ " else "" for ch in s).strip().replace(" ", "-")
    return (keep or "export")[:80]


def _bootstrap_admin(Session, settings: Settings) -> None:
    if not (settings.bootstrap_admin_email and settings.bootstrap_admin_password):
        return
    db = Session()
    try:
        if db.scalar(select(func.count()).select_from(User)) == 0:
            db.add(User(email=settings.bootstrap_admin_email.strip().lower(), name="Admin", role="admin",
                        password_hash=hash_password(settings.bootstrap_admin_password)))
            db.commit()
    finally:
        db.close()
