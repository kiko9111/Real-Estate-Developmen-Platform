"""Market scan: competitor prices and payment plans by location.

Comparables come from two sources:
- the team, entering or pasting what they find (always available);
- a web scan that asks Claude to search the web and return each competitor
  project it finds with its source link (needs ANTHROPIC_API_KEY).

Web results arrive unverified. A team member marks each one verified or
excluded; statistics can be limited to verified rows.
"""
from .stats import market_stats, median_price  # noqa: F401
