"""AI-assisted qualitative analysis via Claude.

Feeds Claude the business summary plus the quantitative picture we already
computed, and asks for a structured moat / management / risk assessment aligned
with a long-term value-investing philosophy. Degrades gracefully to a
rules-based summary when no API key is configured.
"""
from __future__ import annotations

import json
import os
from typing import Any, Optional

# Opus: max depth for moat/management judgment. Swap to "claude-sonnet-5" for a
# cheaper balance or "claude-haiku-4-5-20251001" for the cheapest/fastest reads.
MODEL = "claude-opus-4-8"

SYSTEM_PROMPT = """You are a disciplined long-term value investor in the tradition of \
Warren Buffett and Charlie Munger. You evaluate businesses for a 10-15 year hold, \
prioritizing durable competitive advantages (moats), honest and capable management, \
pricing power, and resilience — NOT short-term momentum or index-tracking. \
You are skeptical, concrete, and you explicitly flag what could go wrong. \
You never give personalized financial advice or tell the user to buy/sell with their \
money; you assess the business. Base your assessment on the data provided; if something \
is unknown, say so rather than inventing specifics."""


def _as_list(x: Any) -> list[str]:
    """Coerce a value to a list of strings. The model sometimes returns an
    array-typed field as a single string (or None); normalize so the frontend
    can always .map() over it."""
    if isinstance(x, list):
        return [str(i) for i in x if i is not None and str(i).strip()]
    if isinstance(x, str):
        return [x.strip()] if x.strip() else []
    return []


def _rules_based_fallback(stock: dict[str, Any], metrics: dict[str, Any]) -> dict[str, Any]:
    """No API key: return a modest, honest placeholder built from the numbers."""
    info = stock["info"]
    g = metrics["growth"]
    r = metrics["returns"]
    notes = []
    if r.get("roic_avg"):
        notes.append("returns on capital suggest " +
                     ("a possible moat" if r["roic_avg"] > 0.15 else "no obvious moat"))
    if g.get("revenue_cagr"):
        notes.append(f"revenue has compounded ~{round(g['revenue_cagr']*100,1)}%/yr")
    return {
        "available": False,
        "business_summary": info.get("summary") or "No business summary available.",
        "moat": {"rating": "Unknown",
                 "reasoning": "AI analysis disabled (no ANTHROPIC_API_KEY set). "
                              + ("Quantitatively, " + "; ".join(notes) + "." if notes else "")},
        "management": {"rating": "Unknown", "reasoning": "Set an API key for AI assessment."},
        "risks": ["Enable AI analysis for a qualitative risk assessment."],
        "bull_case": [],
        "bear_case": [],
        "catalysts": [],
        "cyclicality": "Unknown",
        "investment_thesis": "",
        "thesis_breakers": [],
        "verdict_narrative": "Qualitative AI analysis is disabled. The verdict shown is "
                             "based purely on the quantitative model.",
    }


def analyze(stock: dict[str, Any], metrics: dict[str, Any],
            api_key: Optional[str] = None) -> dict[str, Any]:
    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return _rules_based_fallback(stock, metrics)

    try:
        from anthropic import Anthropic
    except ImportError:
        return _rules_based_fallback(stock, metrics)

    info = stock["info"]
    # Compact context: don't dump raw statements, hand over the computed picture.
    context = {
        "name": info.get("name"),
        "ticker": stock["ticker"],
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "business_summary": (info.get("summary") or "")[:2500],
        "growth": metrics["growth"],
        "returns": metrics["returns"],
        "margins": {k: v.get("latest") for k, v in metrics["margins"].items()},
        "balance": {k: metrics["balance"].get(k)
                    for k in ("debt_to_equity", "interest_coverage", "net_cash")},
        "dcf_upside_midpoint": metrics.get("valuation", {}).get("upside_mid"),
        "valuation_range": {
            "conservative_fcf": metrics.get("valuation", {}).get("conservative_iv"),
            "adjusted_owner_earnings": metrics.get("valuation", {}).get("adjusted_iv"),
        },
        "expected_annual_return": metrics["expected_return"].get("expected_annual_return"),
        "multiples": metrics["multiples"],
        "earnings_quality": {
            "phase": metrics.get("earnings_quality", {}).get("phase"),
            "capex_to_depreciation": metrics.get("earnings_quality", {}).get("capex_to_dep"),
            "heavy_capex_cycle": metrics.get("earnings_quality", {}).get("heavy_capex"),
            "owner_earnings": metrics.get("earnings_quality", {}).get("owner_earnings"),
            "net_income": metrics.get("earnings_quality", {}).get("net_income_latest"),
            "cash_conversion": metrics.get("earnings_quality", {}).get("cash_conversion_avg"),
        },
    }

    # NOTE: keep this schema FLAT (scalars + arrays of strings only). Nested
    # object properties get mis-serialized by the model into XML-ish tag soup,
    # so we expose rating/reasoning as separate top-level fields and reassemble
    # the nested shape in code below.
    tool = {
        "name": "record_qualitative_assessment",
        "description": "Record a structured qualitative assessment of the business.",
        "input_schema": {
            "type": "object",
            "properties": {
                "business_summary": {"type": "string",
                    "description": "2-3 sentence plain-English description of how the company makes money."},
                "moat_rating": {"type": "string", "enum": ["Wide", "Narrow", "None", "Unknown"],
                    "description": "Width of the economic moat."},
                "moat_reasoning": {"type": "string",
                    "description": "Why that moat rating — sources of durable competitive advantage (or lack)."},
                "management_rating": {"type": "string",
                    "enum": ["Strong", "Adequate", "Concerns", "Unknown"]},
                "management_reasoning": {"type": "string",
                    "description": "Capital allocation, alignment, track record."},
                "risks": {"type": "array", "items": {"type": "string"},
                          "description": "3-5 concrete, company-specific risks to a 10-15yr hold."},
                "cyclicality": {"type": "string",
                    "enum": ["Defensive", "Moderately cyclical", "Highly cyclical", "Unknown"],
                    "description": "How sensitive earnings are to the economic cycle."},
                "bull_case": {"type": "array", "items": {"type": "string"},
                    "description": "2-4 reasons the 10-15yr thesis works out."},
                "bear_case": {"type": "array", "items": {"type": "string"},
                    "description": "2-4 reasons it disappoints or the thesis breaks."},
                "catalysts": {"type": "array", "items": {"type": "string"},
                    "description": "2-4 things that could make the market recognize the value."},
                "investment_thesis": {"type": "string",
                    "description": "A 3-5 sentence thesis: why own it, why the market may be "
                                   "wrong, and the kind of price that offers a margin of safety."},
                "thesis_breakers": {"type": "array", "items": {"type": "string"},
                    "description": "2-4 concrete things that would invalidate the thesis."},
                "verdict_narrative": {"type": "string",
                    "description": "3-5 sentences: would a patient value investor want to own "
                                   "this for 10-15 years, and at what kind of price?"},
            },
            "required": ["business_summary", "moat_rating", "moat_reasoning",
                         "management_rating", "management_reasoning", "risks",
                         "bull_case", "bear_case", "catalysts", "cyclicality",
                         "investment_thesis", "thesis_breakers", "verdict_narrative"],
        },
    }

    user_msg = (
        "Assess the following business for a 10-15 year value-investing hold. "
        "Use the quantitative picture provided, but reason qualitatively about moat, "
        "management, pricing power, and durability. If the data shows a heavy capex "
        "cycle, explicitly address earnings quality: today's reported earnings (and P/E) "
        "may be flattered by depreciation lagging the current build-out, while free cash "
        "flow is temporarily depressed — weigh whether that spending is productive growth "
        "investment or value-destructive. Also classify cyclicality, list concrete catalysts, "
        "write a 3-5 sentence investment thesis (why own it, why the market may be wrong), and "
        "list what would break that thesis. Call the tool exactly once.\n\n"
        f"DATA:\n{json.dumps(context, indent=2, default=str)}"
    )

    try:
        client = Anthropic(api_key=key)
        resp = client.messages.create(
            model=MODEL,
            max_tokens=3000,
            system=SYSTEM_PROMPT,
            tools=[tool],
            tool_choice={"type": "tool", "name": "record_qualitative_assessment"},
            messages=[{"role": "user", "content": user_msg}],
        )
        for block in resp.content:
            if getattr(block, "type", None) == "tool_use":
                flat = block.input or {}
                return {
                    "available": True,
                    "business_summary": flat.get("business_summary"),
                    "moat": {"rating": flat.get("moat_rating", "Unknown"),
                             "reasoning": flat.get("moat_reasoning", "")},
                    "management": {"rating": flat.get("management_rating", "Unknown"),
                                   "reasoning": flat.get("management_reasoning", "")},
                    # Coerce to lists — the model occasionally returns an array
                    "risks": _as_list(flat.get("risks")),           # field as a bare string.
                    "bull_case": _as_list(flat.get("bull_case")),
                    "bear_case": _as_list(flat.get("bear_case")),
                    "catalysts": _as_list(flat.get("catalysts")),
                    "cyclicality": flat.get("cyclicality"),
                    "investment_thesis": flat.get("investment_thesis"),
                    "thesis_breakers": _as_list(flat.get("thesis_breakers")),
                    "verdict_narrative": flat.get("verdict_narrative"),
                }
        return _rules_based_fallback(stock, metrics)
    except Exception as e:
        fb = _rules_based_fallback(stock, metrics)
        fb["error"] = f"AI analysis failed: {e}"
        return fb
