"""Acquisition-accounting / goodwill-impairment risk.

A company that bought its growth rather than building it carries goodwill and
intangibles far above its tangible book. Two dangers follow, and both show up as
disasters: (1) the acquisition accounting can mask organic decline or outright
manipulation (the classic debt-funded roll-up — Valeant carried goodwill +
intangibles at 7x equity before it unravelled); (2) a goodwill writedown wipes
out the equity cushion and often forces a dividend cut or covenant breach
(Kraft Heinz took a ~$15B impairment and cut its dividend the same quarter).

Grounded, fundamentals-only: goodwill + intangibles vs total assets and vs
equity (a negative *tangible* book — goodwill exceeding equity — means one
writedown erases the shareholders' stake). Sector-agnostic; None-safe.
"""
from __future__ import annotations

from typing import Any, Optional

CONCENTRATED = 0.65    # G+I this share of assets = growth mostly bought
HEAVY = 0.50


def _latest(series: dict) -> Optional[float]:
    s = {y: v for y, v in (series or {}).items() if v is not None}
    return s[max(s)] if s else None


def assess(statements: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:
    st = statements or {}
    gw = _latest(st.get("goodwill")) or 0.0
    intan = _latest(st.get("intangibles")) or 0.0
    gi = gw + intan
    assets = _latest(st.get("total_assets"))
    equity = _latest(st.get("total_equity"))

    if not assets or assets <= 0 or gi < 1e6:
        return {"applicable": False, "level": "none",
                "reason": "Light on acquired goodwill/intangibles — growth is "
                          "built, not bought."}

    gi_assets = gi / assets
    tangible_eq = (equity - gi) if equity is not None else None
    tangible_negative = tangible_eq is not None and tangible_eq < 0
    # How much of the equity cushion a full impairment would erase.
    impair_vs_eq = (gi / equity) if (equity and equity > 0) else None

    reasons: list[str] = []
    # Level: bloat + a thin/negative tangible cushion is the dangerous combination.
    if gi_assets >= CONCENTRATED and tangible_negative:
        level = "high"
    elif tangible_negative or gi_assets >= HEAVY:
        level = "elevated"
    elif gi_assets >= 0.35:
        level = "moderate"
    else:
        level = "low"

    if level in ("elevated", "high"):
        reasons.append(f"Goodwill + intangibles are {gi_assets*100:.0f}% of assets"
                       + (f" and {impair_vs_eq:.1f}× book equity" if impair_vs_eq else "")
                       + " — growth was largely acquired, not built.")
        if tangible_negative:
            reasons.append("Tangible book value is negative (goodwill exceeds equity) — "
                           "a writedown would erase the shareholders' cushion, the kind that "
                           "forces dividend cuts and covenant trouble.")

    positive = None
    if level == "low":
        positive = "Balance sheet is real assets, not acquired goodwill — low impairment risk."

    return {
        "applicable": True,
        "level": level,                     # low / moderate / elevated / high
        "goodwill": gw, "intangibles": intan,
        "gi_to_assets": gi_assets,
        "tangible_equity": tangible_eq,
        "tangible_negative": tangible_negative,
        "impair_vs_equity": impair_vs_eq,
        "reasons": reasons,
        "positive": positive,
    }
