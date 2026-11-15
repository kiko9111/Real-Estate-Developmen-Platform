"""Investor report as a PDF (A4 landscape)."""
from __future__ import annotations

import io
from xml.sax.saxutils import escape as esc

from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.shapes import Drawing, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

from .common import ReportData, money, pct

INK = colors.HexColor("#1d2a3a")
ACCENT = colors.HexColor("#0f6b6b")
MUTED = colors.HexColor("#5b6675")
RULE = colors.HexColor("#d6dbe1")
SOFT = colors.HexColor("#eef3f3")
NEG = colors.HexColor("#b4472e")


def _styles():
    ss = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("t", parent=ss["Title"], textColor=INK, fontSize=26, leading=31, alignment=TA_LEFT),
        "sub": ParagraphStyle("s", parent=ss["Normal"], textColor=MUTED, fontSize=13, leading=17),
        "h": ParagraphStyle("h", parent=ss["Heading2"], textColor=ACCENT, fontSize=14, spaceBefore=6, spaceAfter=6),
        "body": ParagraphStyle("b", parent=ss["Normal"], textColor=INK, fontSize=9.5, leading=13),
        "small": ParagraphStyle("sm", parent=ss["Normal"], textColor=MUTED, fontSize=8, leading=10.5),
        "tile_label": ParagraphStyle("tl", parent=ss["Normal"], textColor=MUTED, fontSize=8.5, leading=11),
        "tile_value": ParagraphStyle("tv", parent=ss["Normal"], textColor=INK, fontSize=14, leading=18,
                                     fontName="Helvetica-Bold"),
    }


def _table(rows, col_widths=None, header=True, align_right_from=1):
    t = Table(rows, colWidths=col_widths, repeatRows=1 if header else 0)
    style = [
        ("FONT", (0, 0), (-1, -1), "Helvetica", 8.5),
        ("TEXTCOLOR", (0, 0), (-1, -1), INK),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, RULE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    if align_right_from is not None:
        style.append(("ALIGN", (align_right_from, 0), (-1, -1), "RIGHT"))
    if header:
        style += [("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8.5), ("BACKGROUND", (0, 0), (-1, 0), SOFT)]
    t.setStyle(TableStyle(style))
    return t


def _chart(d: ReportData, width: float, height: float = 62 * mm) -> Drawing:
    dr = Drawing(width, height)
    vals = [v / 1e6 for v in d.net_by_year] or [0.0]
    bc = VerticalBarChart()
    bc.x, bc.y, bc.width, bc.height = 40, 20, width - 60, height - 34
    bc.data = [vals]
    bc.categoryAxis.categoryNames = d.years or ["Y1"]
    bc.categoryAxis.labels.fontSize = 7
    bc.valueAxis.labels.fontSize = 7
    bc.valueAxis.labelTextFormat = lambda v: f"{v:,.0f}"
    bc.bars[0].fillColor = ACCENT
    bc.bars[0].strokeColor = None
    for i, v in enumerate(vals):
        if v < 0:
            bc.bars[(0, i)].fillColor = NEG
    dr.add(bc)
    dr.add(String(40, height - 10, f"Net cash flow before tax by year ({d.currency} millions)", fontSize=8,
                  fillColor=MUTED))
    return dr


def _footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(15 * mm, 9 * mm, "Confidential. Figures come from the feasibility model and depend on the assumptions listed.")
    canvas.drawRightString(landscape(A4)[0] - 15 * mm, 9 * mm, f"Page {doc.page}")
    canvas.restoreState()


def render_pdf(d: ReportData) -> bytes:
    buf = io.BytesIO()
    page_w, _ = landscape(A4)
    margin = 15 * mm
    usable = page_w - 2 * margin
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=margin, rightMargin=margin,
                            topMargin=14 * mm, bottomMargin=16 * mm, title=d.title, author="Development platform")
    s = _styles()
    el = []
    # cover and headline
    el += [Spacer(1, 18 * mm), Paragraph(esc(d.title), s["title"]), Paragraph(esc(d.subtitle), s["sub"]),
           Spacer(1, 3 * mm), Paragraph(esc(d.prepared), s["small"]), Spacer(1, 12 * mm),
           Paragraph("Headline results", s["h"])]
    tiles = [[Paragraph(esc(lbl), s["tile_label"]) for lbl, _ in d.headline[i:i + 4]] for i in (0, 4)]
    vals = [[Paragraph(esc(v), s["tile_value"]) for _, v in d.headline[i:i + 4]] for i in (0, 4)]
    grid = [tiles[0], vals[0], tiles[1], vals[1]]
    t = Table(grid, colWidths=[usable / 4] * 4)
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), SOFT), ("LINEBELOW", (0, 1), (-1, 1), 6, colors.white),
                           ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                           ("LEFTPADDING", (0, 0), (-1, -1), 8)]))
    el.append(t)
    el.append(PageBreak())

    # land and programme
    el.append(Paragraph("The land", s["h"]))
    el.append(_table([[a, b] for a, b in d.land_facts], [70 * mm, usable - 70 * mm], header=False, align_right_from=None))
    el.append(Spacer(1, 6 * mm))
    el.append(Paragraph("Development programme", s["h"]))
    el.append(_table([d.program_header] + d.program_rows))
    el.append(PageBreak())

    # cash flow
    el.append(Paragraph("Cash flow", s["h"]))
    el.append(_chart(d, usable))
    chunk = 12
    for start in range(0, len(d.years), chunk):
        cols = d.years[start:start + chunk]
        rows = [[f"{d.currency} millions"] + cols]
        for label, vals_ in d.cash_rows:
            rows.append([label] + [f"{v / 1e6:,.1f}" for v in vals_[start:start + chunk]])
        first = 52 * mm
        el.append(Spacer(1, 3 * mm))
        el.append(_table(rows, [first] + [(usable - first) / chunk] * len(cols)))

    # scenarios, land value, market
    el.append(PageBreak())
    if d.scenarios:
        rows = [["Scenario", "Revenue", "Profit before tax", "Margin", "NPV", "IRR", "Peak funding"]]
        for r in d.scenarios:
            if r.get("error"):
                rows.append([r["label"], r["error"], "", "", "", "", ""])
            else:
                rows.append([r["label"], money(r["revenue"]), money(r["profit_pre_tax"]), pct(r["margin_on_revenue"]),
                             money(r["npv_pre_tax"]), pct(r["irr_pre_tax"]), money(r["peak_funding_need"])])
        el.append(KeepTogether([Paragraph("Scenarios", s["h"]),
                                Paragraph("Each row is a full re-run of the model with one assumption changed.", s["small"]),
                                Spacer(1, 2 * mm), _table(rows)]))
    if d.residual:
        r = d.residual
        txt = (f"At a {r['target_rate']:.0%} target return the land can cost up to "
               f"<b>{money(r['price_per_feddan'], d.currency)} per feddan</b> "
               f"({money(r['total'], d.currency)} for the part bought) with the rest of the deal unchanged."
               if r["feasible"] else "Even free land would miss the target return on these assumptions.")
        el += [Paragraph("Residual land value", s["h"]), Paragraph(txt, s["body"])]
    if d.market:
        rows = [["Product", "Projects", "Median price / m2", "Range", "Median down payment", "Median years"]] + d.market
        el += [Paragraph("Market comparables", s["h"]), _table(rows)]
    el.append(Paragraph("Assumptions", s["h"]))
    el.append(_table([[a, b] for a, b in d.assumptions], [70 * mm, usable - 70 * mm], header=False, align_right_from=None))
    if d.warnings:
        el.append(Paragraph("Model notes", s["h"]))
        for w in d.warnings:
            el.append(Paragraph(esc(w), s["body"]))
    doc.build(el, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()
