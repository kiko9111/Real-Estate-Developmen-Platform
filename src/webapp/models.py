"""Database tables.

A Land is the centre of everything. It holds the team's own records about the
plot, the offer, and the land-conditions form (Step 4) as validated JSON, so
the form can grow without a migration for each new field. Screenings, studies
and market scans hang off a land.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, backref, mapped_column, relationship

from .db import Base


def now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


ROLES = ("admin", "analyst", "viewer")
LAND_STATUSES = ("new", "screening", "studying", "go", "no_go", "archived")


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str] = mapped_column(String(300))
    role: Mapped[str] = mapped_column(String(20), default="analyst")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    @property
    def can_edit(self) -> bool:
        return self.role in ("admin", "analyst")

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


class Land(Base):
    __tablename__ = "lands"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    code: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="new")
    # location
    region: Mapped[str] = mapped_column(String(120), default="")      # e.g. North Coast
    area_name: Mapped[str] = mapped_column(String(200), default="")   # e.g. a bay, district or km marker
    governorate: Mapped[str] = mapped_column(String(120), default="")
    latitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    longitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    map_url: Mapped[str] = mapped_column(String(500), default="")
    # size and shape
    area_m2: Mapped[float] = mapped_column(Float)
    parcels: Mapped[list] = mapped_column(JSON, default=list)          # [{name, area_m2, notes}]
    frontage_m: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    depth_m: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    frontage_type: Mapped[str] = mapped_column(String(60), default="")  # sea, lake, main road, ...
    # the team's records, validated by schemas.py
    offer: Mapped[dict] = mapped_column(JSON, default=dict)            # schemas.Offer
    legal: Mapped[dict] = mapped_column(JSON, default=dict)            # schemas.Legal
    conditions: Mapped[dict] = mapped_column(JSON, default=dict)       # schemas.LandConditions (Step 4 form)
    conditions_version: Mapped[int] = mapped_column(Integer, default=1)
    custom_fields: Mapped[list] = mapped_column(JSON, default=list)    # [{label, value, unit}]
    notes: Mapped[str] = mapped_column(Text, default="")
    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)

    created_by: Mapped[Optional[User]] = relationship()
    screenings: Mapped[list["Screening"]] = relationship(back_populates="land", cascade="all, delete-orphan",
                                                         order_by="Screening.created_at.desc()")
    studies: Mapped[list["StudyRecord"]] = relationship(back_populates="land", cascade="all, delete-orphan",
                                                        order_by="StudyRecord.created_at")

    @property
    def area_feddan(self) -> float:
        from feasibility import FEDDAN_M2
        return self.area_m2 / FEDDAN_M2


class Screening(Base):
    """One quick go/no-go check. Inputs and outputs are kept as they were."""
    __tablename__ = "screenings"
    id: Mapped[int] = mapped_column(primary_key=True)
    land_id: Mapped[int] = mapped_column(ForeignKey("lands.id", ondelete="CASCADE"), index=True)
    inputs: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict] = mapped_column(JSON)
    verdict: Mapped[str] = mapped_column(String(20))      # go, borderline, no_go
    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    land: Mapped[Land] = relationship(back_populates="screenings")
    created_by: Mapped[Optional[User]] = relationship()


class StudyRecord(Base):
    """A full feasibility study: one engine Study as JSON.

    A scenario is a study with a parent; it starts as a copy of the parent and
    is edited freely. The latest run's results are cached on the record.
    """
    __tablename__ = "studies"
    id: Mapped[int] = mapped_column(primary_key=True)
    land_id: Mapped[int] = mapped_column(ForeignKey("lands.id", ondelete="CASCADE"), index=True)
    parent_id: Mapped[Optional[int]] = mapped_column(ForeignKey("studies.id", ondelete="SET NULL"), nullable=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    data: Mapped[dict] = mapped_column(JSON)               # feasibility.Study
    result: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    run_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    error: Mapped[str] = mapped_column(Text, default="")
    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)

    land: Mapped[Land] = relationship(back_populates="studies")
    parent: Mapped[Optional["StudyRecord"]] = relationship(remote_side=[id])
    created_by: Mapped[Optional[User]] = relationship()

    @property
    def is_scenario(self) -> bool:
        return self.parent_id is not None


class MarketScan(Base):
    """A search for competitor prices and payment plans in one location."""
    __tablename__ = "market_scans"
    id: Mapped[int] = mapped_column(primary_key=True)
    land_id: Mapped[Optional[int]] = mapped_column(ForeignKey("lands.id", ondelete="SET NULL"), nullable=True, index=True)
    location: Mapped[str] = mapped_column(String(200), index=True)
    product_focus: Mapped[str] = mapped_column(String(200), default="")
    provider: Mapped[str] = mapped_column(String(60))      # "manual" or "claude_web_search"
    status: Mapped[str] = mapped_column(String(20), default="done")   # done, failed
    summary: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(Text, default="")
    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    comparables: Mapped[list["Comparable"]] = relationship(back_populates="scan", cascade="all, delete-orphan")
    created_by: Mapped[Optional[User]] = relationship()


class Comparable(Base):
    """One competitor project's price and payment terms, with its source."""
    __tablename__ = "comparables"
    id: Mapped[int] = mapped_column(primary_key=True)
    scan_id: Mapped[Optional[int]] = mapped_column(ForeignKey("market_scans.id", ondelete="CASCADE"), nullable=True, index=True)
    location: Mapped[str] = mapped_column(String(200), index=True)
    project: Mapped[str] = mapped_column(String(200))
    developer: Mapped[str] = mapped_column(String(200), default="")
    product_type: Mapped[str] = mapped_column(String(60), default="")   # apartment, chalet, villa, townhouse, ...
    price_per_m2: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    unit_price_from: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    unit_size_m2: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    down_payment_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    instalment_years: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    delivery: Mapped[str] = mapped_column(String(60), default="")
    finishing: Mapped[str] = mapped_column(String(60), default="")
    source_url: Mapped[str] = mapped_column(String(800), default="")
    source_date: Mapped[str] = mapped_column(String(40), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    verified: Mapped[bool] = mapped_column(Boolean, default=False)     # a team member checked it
    excluded: Mapped[bool] = mapped_column(Boolean, default=False)     # left out of the statistics
    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    scan: Mapped[Optional[MarketScan]] = relationship(back_populates="comparables")


class LayoutRecord(Base):
    """A site layout run (Step 4): the brief, and the options it produced.

    The brief holds the boundary, height bands, building types and strategies
    (layout.LayoutBrief). Options are kept as JSON, with their geometry, area
    schedule, compliance check and feasibility result, so the page can be
    rebuilt without running anything again.
    """
    __tablename__ = "layouts"
    id: Mapped[int] = mapped_column(primary_key=True)
    land_id: Mapped[int] = mapped_column(ForeignKey("lands.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    brief: Mapped[dict] = mapped_column(JSON)
    base_study_id: Mapped[Optional[int]] = mapped_column(ForeignKey("studies.id", ondelete="SET NULL"), nullable=True)
    options: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    conditions_used: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    error: Mapped[str] = mapped_column(Text, default="")
    run_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)

    land: Mapped[Land] = relationship(backref=backref("layouts", cascade="all, delete-orphan",
                                                         order_by="LayoutRecord.created_at.desc()"))
    base_study: Mapped[Optional[StudyRecord]] = relationship(foreign_keys=[base_study_id])
    created_by: Mapped[Optional[User]] = relationship()


class MasterPlanImport(Base):
    """An architect's master plan, read from DXF: closed outlines per layer, and what each layer is.

    The outlines are kept (in metres, in the drawing's own coordinates) so the plan is measured again
    each time the layout runs, against the conditions and base study of that moment.
    """
    __tablename__ = "masterplans"
    id: Mapped[int] = mapped_column(primary_key=True)
    layout_id: Mapped[int] = mapped_column(ForeignKey("layouts.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    filename: Mapped[str] = mapped_column(String(300), default="")
    source: Mapped[dict] = mapped_column(JSON)            # {"layers": {name: [ring, ...]}, "units": ...}
    mapping: Mapped[dict] = mapped_column(JSON)           # {layer: {"use": ..., "floors": ...}}
    frontage_edge: Mapped[int] = mapped_column(Integer, default=0)
    ready: Mapped[bool] = mapped_column(Boolean, default=False)   # the team has checked the layers
    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)

    layout: Mapped[LayoutRecord] = relationship(backref=backref("masterplans", cascade="all, delete-orphan",
                                                                   order_by="MasterPlanImport.created_at"))
