"""Small inline SVG charts, so pages need no chart library."""
from __future__ import annotations

from html import escape


def fmt_money_short(x: float) -> str:
    a = abs(x)
    if a >= 1e9:
        s = f"{a / 1e9:,.1f}B"
    elif a >= 1e6:
        s = f"{a / 1e6:,.0f}M"
    elif a >= 1e3:
        s = f"{a / 1e3:,.0f}K"
    else:
        s = f"{a:,.0f}"
    return ("-" if x < 0 else "") + s


def cash_flow_chart(values: list[float], cumulative: list[float] | None = None,
                    width: int = 760, height: int = 260, label_prefix: str = "Y") -> str:
    """Bars for each period's net cash flow; a line for the running total."""
    if not values:
        return ""
    cumulative = cumulative or []
    pad_l, pad_r, pad_t, pad_b = 64, 12, 14, 28
    w, h = width - pad_l - pad_r, height - pad_t - pad_b
    series = list(values) + list(cumulative)
    lo, hi = min(0.0, min(series)), max(0.0, max(series))
    if hi == lo:
        hi = lo + 1
    y = lambda v: pad_t + h * (hi - v) / (hi - lo)
    n = len(values)
    step = w / n
    bw = max(2.0, step * 0.62)
    parts = [f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" '
             f'aria-label="Net cash flow by period" xmlns="http://www.w3.org/2000/svg">']
    for t in range(5):
        v = lo + (hi - lo) * t / 4
        yy = y(v)
        parts.append(f'<line x1="{pad_l}" x2="{width - pad_r}" y1="{yy:.1f}" y2="{yy:.1f}" class="grid"/>')
        parts.append(f'<text x="{pad_l - 6}" y="{yy + 4:.1f}" text-anchor="end" class="axis">{fmt_money_short(v)}</text>')
    y0 = y(0)
    for i, v in enumerate(values):
        x = pad_l + i * step + (step - bw) / 2
        top, bot = (y(v), y0) if v >= 0 else (y0, y(v))
        cls = "bar-pos" if v >= 0 else "bar-neg"
        parts.append(f'<rect x="{x:.1f}" y="{top:.1f}" width="{bw:.1f}" height="{max(0.5, bot - top):.1f}" class="{cls}">'
                     f'<title>{label_prefix}{i + 1}: {escape(fmt_money_short(v))}</title></rect>')
        if n <= 30 or i % max(1, n // 20) == 0:
            parts.append(f'<text x="{pad_l + i * step + step / 2:.1f}" y="{height - 8}" text-anchor="middle" class="axis">'
                         f'{label_prefix}{i + 1}</text>')
    parts.append(f'<line x1="{pad_l}" x2="{width - pad_r}" y1="{y0:.1f}" y2="{y0:.1f}" class="zero"/>')
    if cumulative:
        pts = " ".join(f"{pad_l + i * step + step / 2:.1f},{y(v):.1f}" for i, v in enumerate(cumulative))
        parts.append(f'<polyline points="{pts}" class="cum"/>')
    parts.append("</svg>")
    return "".join(parts)


def range_bar(lo: float, mid: float, hi: float, mark: float | None = None, width: int = 260) -> str:
    """A min/median/max strip, with an optional marker (for example our price)."""
    a, b = min(lo, mark if mark is not None else lo), max(hi, mark if mark is not None else hi)
    if b == a:
        b = a + 1
    x = lambda v: 6 + (width - 12) * (v - a) / (b - a)
    s = [f'<svg class="range" viewBox="0 0 {width} 18" xmlns="http://www.w3.org/2000/svg">',
         f'<line x1="{x(lo):.1f}" x2="{x(hi):.1f}" y1="9" y2="9" class="range-line"/>',
         f'<circle cx="{x(mid):.1f}" cy="9" r="4" class="range-mid"/>']
    if mark is not None:
        s.append(f'<line x1="{x(mark):.1f}" x2="{x(mark):.1f}" y1="1" y2="17" class="range-mark"/>')
    s.append("</svg>")
    return "".join(s)
