"""Investor report as a PowerPoint deck (16:9), with native, editable charts."""
from __future__ import annotations

import io

from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE
from pptx.util import Emu, Inches, Pt

from .common import ReportData, money, pct

INK = RGBColor(0x1D, 0x2A, 0x3A)
ACCENT = RGBColor(0x0F, 0x6B, 0x6B)
MUTED = RGBColor(0x5B, 0x66, 0x75)
SOFT = RGBColor(0xEE, 0xF3, 0xF3)
NEG = RGBColor(0xB4, 0x47, 0x2E)
W, H = Inches(13.333), Inches(7.5)


def _text(slide, x, y, w, h, text, size=14, bold=False, color=INK):
    tb = slide.shapes.add_textbox(x, y, w, h)
    tf = tb.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    r = p.add_run()
    r.text = text
    r.font.size, r.font.bold, r.font.color.rgb = Pt(size), bold, color
    return tb


def _title(slide, text):
    _text(slide, Inches(0.6), Inches(0.35), Inches(12), Inches(0.8), text, 26, True, INK)
    bar = slide.shapes.add_shape(1, Inches(0.6), Inches(1.1), Inches(1.2), Emu(45720))
    bar.fill.solid()
    bar.fill.fore_color.rgb = ACCENT
    bar.line.fill.background()


def _table(slide, rows, x, y, w, row_h=Inches(0.36), font=11, first_col_w=None):
    nr, nc = len(rows), len(rows[0])
    shape = slide.shapes.add_table(nr, nc, x, y, w, row_h * nr)
    tbl = shape.table
    if first_col_w:
        tbl.columns[0].width = first_col_w
        rest = (w - first_col_w) // max(1, nc - 1)
        for j in range(1, nc):
            tbl.columns[j].width = rest
    for i, row in enumerate(rows):
        for j, val in enumerate(row):
            cell = tbl.cell(i, j)
            cell.text = str(val)
            para = cell.text_frame.paragraphs[0]
            para.font.size = Pt(font)
            para.font.bold = i == 0
            para.font.color.rgb = INK
            cell.fill.solid()
            cell.fill.fore_color.rgb = SOFT if i == 0 else RGBColor(0xFF, 0xFF, 0xFF)
    return tbl


def render_pptx(d: ReportData) -> bytes:
    prs = Presentation()
    prs.slide_width, prs.slide_height = W, H
    blank = prs.slide_layouts[6]

    # 1. cover
    s = prs.slides.add_slide(blank)
    band = s.shapes.add_shape(1, 0, 0, W, Inches(0.25))
    band.fill.solid(); band.fill.fore_color.rgb = ACCENT; band.line.fill.background()
    _text(s, Inches(0.8), Inches(2.4), Inches(11.5), Inches(1.2), d.title, 40, True)
    _text(s, Inches(0.8), Inches(3.6), Inches(11.5), Inches(0.8), d.subtitle, 20, False, MUTED)
    _text(s, Inches(0.8), Inches(6.5), Inches(11.5), Inches(0.5), d.prepared + "  |  Confidential", 12, False, MUTED)

    # 2. headline
    s = prs.slides.add_slide(blank)
    _title(s, "Headline results")
    tw, th = Inches(2.9), Inches(1.35)
    for i, (label, value) in enumerate(d.headline):
        x = Inches(0.6) + (i % 4) * (tw + Inches(0.2))
        y = Inches(1.6) + (i // 4) * (th + Inches(0.3))
        box = s.shapes.add_shape(1, x, y, tw, th)
        box.fill.solid(); box.fill.fore_color.rgb = SOFT; box.line.fill.background()
        _text(s, x + Inches(0.2), y + Inches(0.15), tw - Inches(0.4), Inches(0.4), label, 12, False, MUTED)
        _text(s, x + Inches(0.2), y + Inches(0.55), tw - Inches(0.4), Inches(0.6), value, 22, True)
    _text(s, Inches(0.6), Inches(5.0), Inches(12), Inches(1.5),
          "Figures are nominal and come from a monthly cash-flow model. NPV is at the target return shown; "
          "IRR is reported only where a single rate exists.", 12, False, MUTED)

    # 3. land
    s = prs.slides.add_slide(blank)
    _title(s, "The land")
    _table(s, [["Item", "Detail"]] + [[a, b] for a, b in d.land_facts], Inches(0.6), Inches(1.5), Inches(12.1),
           font=13, first_col_w=Inches(3.6))

    # 4. programme
    s = prs.slides.add_slide(blank)
    _title(s, "Development programme")
    _table(s, [d.program_header] + d.program_rows, Inches(0.6), Inches(1.5), Inches(12.1), font=11)

    # 5. cash flow chart
    s = prs.slides.add_slide(blank)
    _title(s, "Cash flow by year")
    cd = CategoryChartData()
    cd.categories = d.years or ["Y1"]
    cd.add_series(f"Net cash flow before tax ({d.currency} M)", [round(v / 1e6, 1) for v in d.net_by_year] or [0])
    if d.cumulative:
        cd.add_series(f"Cumulative ({d.currency} M)", [round(v / 1e6, 1) for v in d.cumulative])
    gf = s.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(0.6), Inches(1.4), Inches(12.1), Inches(5.6), cd)
    ch = gf.chart
    ch.has_legend = True
    ch.legend.include_in_layout = False
    ch.value_axis.tick_labels.font.size = Pt(10)
    ch.category_axis.tick_labels.font.size = Pt(10)
    ser = ch.plots[0].series[0]
    ser.format.fill.solid(); ser.format.fill.fore_color.rgb = ACCENT
    ser.invert_if_negative = False
    for i, v in enumerate(d.net_by_year):
        if v < 0:
            pt = ser.points[i]
            pt.format.fill.solid(); pt.format.fill.fore_color.rgb = NEG
    if d.cumulative and len(ch.plots[0].series) > 1:
        cum = ch.plots[0].series[1]
        cum.format.fill.solid(); cum.format.fill.fore_color.rgb = RGBColor(0xB8, 0xC4, 0xCC)

    # 6. scenarios and land value
    if d.scenarios or d.residual:
        s = prs.slides.add_slide(blank)
        _title(s, "Scenarios and land value")
        y = Inches(1.5)
        if d.scenarios:
            rows = [["Scenario", "Profit before tax", "Margin", "NPV", "IRR", "Peak funding"]]
            for r in d.scenarios:
                if r.get("error"):
                    rows.append([r["label"], "not run", "", "", "", ""])
                else:
                    rows.append([r["label"], money(r["profit_pre_tax"]), pct(r["margin_on_revenue"]),
                                 money(r["npv_pre_tax"]), pct(r["irr_pre_tax"]), money(r["peak_funding_need"])])
            _table(s, rows, Inches(0.6), y, Inches(12.1), font=12, first_col_w=Inches(3.2))
            y += Inches(0.36) * len(rows) + Inches(0.4)
        if d.residual:
            r = d.residual
            txt = (f"Residual land value at a {r['target_rate']:.0%} target: {money(r['price_per_feddan'], d.currency)} "
                   f"per feddan ({money(r['total'], d.currency)} for the part bought)."
                   if r["feasible"] else "Even free land would miss the target return on these assumptions.")
            _text(s, Inches(0.6), y, Inches(12), Inches(0.8), txt, 16, True, ACCENT)

    # 7. market
    if d.market:
        s = prs.slides.add_slide(blank)
        _title(s, "Market comparables")
        _table(s, [["Product", "Projects", "Median price / m2", "Range", "Median down payment", "Median years"]] + d.market,
               Inches(0.6), Inches(1.5), Inches(12.1), font=12)

    # 8. assumptions
    s = prs.slides.add_slide(blank)
    _title(s, "Assumptions and notes")
    _table(s, [["Assumption", "Value"]] + [[a, b] for a, b in d.assumptions], Inches(0.6), Inches(1.5),
           Inches(12.1), font=12, first_col_w=Inches(4))
    if d.warnings:
        _text(s, Inches(0.6), Inches(1.5) + Inches(0.36) * (len(d.assumptions) + 1) + Inches(0.3), Inches(12),
              Inches(2), " ".join(d.warnings), 11, False, MUTED)

    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()
