"""Web market scan with Claude.

Claude searches the web for residential projects selling in the location,
then calls the `record_comparables` tool once with every project it found:
price per m2, starting unit price, down payment, instalment years, delivery,
and the page each figure came from. Rows are saved unverified.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional

import anthropic

RECORD_TOOL = {
    "name": "record_comparables",
    "description": ("Record the competitor projects found in the search. Call this exactly once, at the end, "
                    "with every project for which you found a price or a payment plan."),
    "strict": True,
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["summary", "comparables"],
        "properties": {
            "summary": {"type": "string",
                        "description": "Three to six sentences on price levels, payment plans and trends in the location."},
            "comparables": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["project", "developer", "product_type", "price_per_m2", "unit_price_from",
                                 "unit_size_m2", "down_payment_pct", "instalment_years", "delivery",
                                 "finishing", "source_url", "source_date", "notes"],
                    "properties": {
                        "project": {"type": "string"},
                        "developer": {"type": "string"},
                        "product_type": {"type": "string",
                                         "description": "apartment, chalet, villa, townhouse, twin house, serviced apartment, retail, office or other"},
                        "price_per_m2": {"type": ["number", "null"], "description": "In the local currency per m2 of unit area."},
                        "unit_price_from": {"type": ["number", "null"], "description": "Lowest unit price quoted, local currency."},
                        "unit_size_m2": {"type": ["number", "null"]},
                        "down_payment_pct": {"type": ["number", "null"], "description": "As a fraction: 10% is 0.10."},
                        "instalment_years": {"type": ["number", "null"]},
                        "delivery": {"type": "string", "description": "Delivery year or period, or empty."},
                        "finishing": {"type": "string", "description": "core and shell, semi-finished, fully finished, or empty."},
                        "source_url": {"type": "string"},
                        "source_date": {"type": "string", "description": "Date of the source page if shown, or empty."},
                        "notes": {"type": "string"},
                    },
                },
            },
        },
    },
}

SYSTEM = (
    "You are a real-estate market analyst helping a development team price a new project. "
    "You search the web for competitor projects and report only what the sources say. "
    "Never invent a number: leave a field null when no source gives it. "
    "Prefer recent sources (the last 12 months), developer sites, and established property portals. "
    "Give every row the URL of the page its figures came from. "
    "If a source gives only a unit price and a unit size, you may compute price per m2 from them and say so in notes."
)


@dataclass
class ScanOutput:
    summary: str = ""
    comparables: list[dict] = field(default_factory=list)
    searches: int = 0


class ScanError(Exception):
    pass


def _prompt(location: str, product_focus: str, currency: str) -> str:
    focus = f" Focus on: {product_focus}." if product_focus else ""
    return (f"Run a market scan for {location}.{focus} Find projects currently selling in or near this location "
            f"and collect, for each: project, developer, product type, price per m2 ({currency}), starting unit price, "
            f"typical unit size, down payment, instalment years, delivery, finishing, and the source page. "
            f"Aim for 8 to 20 projects. Search in English and in Arabic. When you are done, call record_comparables once.")


def run_scan(location: str, product_focus: str = "", *, model: str, currency: str = "EGP",
             client: Optional[anthropic.Anthropic] = None, max_rounds: int = 6) -> ScanOutput:
    client = client or anthropic.Anthropic()
    tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": 15}, RECORD_TOOL]
    messages: list = [{"role": "user", "content": _prompt(location, product_focus, currency)}]
    out = ScanOutput()
    nudged = False
    for _ in range(max_rounds):
        try:
            resp = client.beta.messages.create(
                model=model, max_tokens=16000, system=SYSTEM, tools=tools, messages=messages,
                output_config={"effort": "medium"},
                betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            )
        except anthropic.AuthenticationError as e:
            raise ScanError("The Anthropic API key was rejected.") from e
        except anthropic.RateLimitError as e:
            raise ScanError("The search service is busy. Try again in a minute.") from e
        except anthropic.APIStatusError as e:
            raise ScanError(f"The search service returned an error ({e.status_code}).") from e
        except anthropic.APIConnectionError as e:
            raise ScanError("Could not reach the search service.") from e
        for b in resp.content:
            if b.type == "server_tool_use":
                out.searches += 1
        if resp.stop_reason == "refusal":
            raise ScanError("The search was declined. Try rewording the location or focus.")
        call = next((b for b in resp.content if b.type == "tool_use" and b.name == "record_comparables"), None)
        if call is not None:
            data = call.input if isinstance(call.input, dict) else json.loads(call.input)
            out.summary = str(data.get("summary", ""))
            out.comparables = [c for c in data.get("comparables", []) if isinstance(c, dict) and c.get("project")]
            return out
        messages.append({"role": "assistant", "content": resp.content})
        if resp.stop_reason == "pause_turn":
            continue                         # the server resumes the search on the next request
        if resp.stop_reason == "max_tokens" or nudged:
            break
        messages.append({"role": "user", "content": "Call record_comparables now with what you found."})
        nudged = True
    raise ScanError("The scan ended without returning results.")


def _num(v, lo=None, hi=None) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if x != x or (lo is not None and x < lo) or (hi is not None and x > hi):
        return None
    return x


def clean_row(c: dict) -> dict:
    """Map one tool row to Comparable columns, dropping impossible values."""
    dp = _num(c.get("down_payment_pct"), 0, 100)
    if dp is not None and dp > 1:
        dp = dp / 100.0                       # tolerate 10 meaning 10%
    return {
        "project": str(c.get("project", ""))[:200],
        "developer": str(c.get("developer", "") or "")[:200],
        "product_type": str(c.get("product_type", "") or "").lower()[:60],
        "price_per_m2": _num(c.get("price_per_m2"), 1),
        "unit_price_from": _num(c.get("unit_price_from"), 1),
        "unit_size_m2": _num(c.get("unit_size_m2"), 1, 100000),
        "down_payment_pct": dp,
        "instalment_years": _num(c.get("instalment_years"), 0, 30),
        "delivery": str(c.get("delivery", "") or "")[:60],
        "finishing": str(c.get("finishing", "") or "")[:60],
        "source_url": safe_url(c.get("source_url")),
        "source_date": str(c.get("source_date", "") or "")[:40],
        "notes": str(c.get("notes", "") or ""),
    }


def safe_url(v) -> str:
    u = str(v or "").strip()[:800]
    return u if u.lower().startswith(("http://", "https://")) else ""
