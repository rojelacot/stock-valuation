"""Working-capital quality check.

Receivables or inventory growing faster than sales is a classic early warning a
DCF and a headline margin miss: cash is being trapped in working capital, and the
cause is usually one of channel-stuffing / aggressive revenue recognition (AR
ballooning), softening demand or obsolescence risk (inventory piling up), or
slipping collections. It's the readable, standalone cousin of Beneish's DSRI.

Measured as intensity trends — receivables/revenue and inventory/COGS (or /revenue
when gross profit isn't tagged) — comparing the latest year to its recent baseline.
A sustained rise is the signal; single-year noise is damped by averaging the base.
Sector-aware and robust to missing series (many filers tag only one of the two).
"""
from __future__ import annotations

from typing import Any, Optional

RISE_ELEVATED = 1.25   # latest intensity >= 1.25x its recent baseline -> elevated
RISE_MODERATE = 1.12   # 1.12-1.25x -> moderate
FALL_GOOD = 0.92       # <= 0.92x -> improving (tightening working capital)
MIN_YEARS = 3
MIN_INTENSITY = 0.02   # ignore trivially small AR/inventory (<2% of revenue)


def _series(d: dict) -> dict:  # numeric-year keys only (a "TTM" key would crash int())
    return {str(y): v for y, v in (d or {}).items()
            if v is not None and str(y).isdigit()}


def _intensity(num: dict, den: dict) -> dict:
    """{year: num/den} over years present in both with den > 0."""
    out = {}
    for y in num:
        dv = den.get(y)
        if dv and dv > 0:
            out[y] = num[y] / dv
    return dict(sorted(out.items()))


def _trend(intensity: dict) -> Optional[dict]:
    """Latest intensity vs the mean of up to 3 prior years."""
    ys = sorted(intensity)
    if len(ys) < 2:
        return None
    latest = intensity[ys[-1]]
    prior = [intensity[y] for y in ys[:-1]][-3:]
    base = sum(prior) / len(prior)
    return {"latest": latest, "base": base,
            "ratio": (latest / base) if base else None,
            "years": len(ys)}


def _grade_one(intensity: dict, days_denom_label: str, name: str,
               latest_rev_year: Optional[int]) -> Optional[dict]:
    """Assess one component (receivables or inventory) as a days metric + trend."""
    ys = sorted(intensity)
    if len(ys) < MIN_YEARS:
        return None
    # Staleness guard: some filers stop tagging a line (e.g. NKE's InventoryNet
    # ends in 2011). Don't trend off ancient data as if it were current.
    if latest_rev_year is not None and int(ys[-1]) < latest_rev_year - 1:
        return None
    if intensity[ys[-1]] < MIN_INTENSITY:
        return None  # trivially small — not a working-capital story
    t = _trend(intensity)
    if not t or t["ratio"] is None:
        return None
    days_latest = t["latest"] * 365
    days_base = t["base"] * 365
    ratio = t["ratio"]
    level = "low"
    reason = None
    if ratio >= RISE_ELEVATED:
        level = "elevated"
        reason = (f"{name} rising fast vs sales — {days_latest:.0f} days "
                  f"(per {days_denom_label}) vs a ~{days_base:.0f}-day recent norm "
                  f"({(ratio-1)*100:.0f}% higher).")
    elif ratio >= RISE_MODERATE:
        level = "moderate"
        reason = (f"{name} creeping up vs sales — {days_latest:.0f} days vs "
                  f"~{days_base:.0f} recently ({(ratio-1)*100:.0f}% higher).")
    return {"available": True, "days_latest": days_latest, "days_base": days_base,
            "ratio": ratio, "level": level, "reason": reason,
            "denom": days_denom_label,
            "series": [{"year": y, "days": intensity[y] * 365} for y in ys]}


_ORDER = {"low": 0, "moderate": 1, "elevated": 2}


def assess(statements: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:
    st = statements or {}
    info = info or {}
    if (info.get("sector") or "") == "Financial Services":
        return {"applicable": False, "level": "none",
                "reason": "Receivables/inventory mean something different for banks "
                          "& insurers (loans, float) — not a working-capital signal."}

    rev = _series(st.get("revenue"))
    rec = _series(st.get("receivables"))
    inv = _series(st.get("inventory"))
    gp = _series(st.get("gross_profit"))
    cogs = {y: rev[y] - gp[y] for y in rev if y in gp and (rev[y] - gp[y]) > 0}
    latest_rev_year = max((int(y) for y in rev), default=None)

    # Receivables intensity is always vs revenue (DSO).
    receivables = _grade_one(_intensity(rec, rev), "revenue", "Receivables",
                             latest_rev_year) if rec else None
    # Inventory intensity vs COGS (proper DIO) when that series is both deep
    # enough AND current; otherwise fall back to a revenue denominator (some
    # filers stop tagging GrossProfit, leaving COGS stale — e.g. Target).
    inventory = None
    if inv:
        inv_cogs = _intensity(inv, cogs)
        cogs_fresh = (inv_cogs and len(inv_cogs) >= MIN_YEARS
                      and (latest_rev_year is None or int(max(inv_cogs)) >= latest_rev_year - 1))
        if cogs_fresh:
            inventory = _grade_one(inv_cogs, "COGS", "Inventory", latest_rev_year)
        else:
            inventory = _grade_one(_intensity(inv, rev), "revenue", "Inventory", latest_rev_year)

    parts = [p for p in (receivables, inventory) if p]
    if not parts:
        return {"applicable": False, "level": "none",
                "reason": "No usable receivables or inventory history to assess."}

    level = max((p["level"] for p in parts), key=lambda lv: _ORDER[lv])
    reasons = [p["reason"] for p in parts if p.get("reason")]

    positive = None
    if level == "low":
        tight = [p for p in parts if p["ratio"] is not None and p["ratio"] <= FALL_GOOD]
        if tight:
            which = " and ".join("receivables" if p is receivables else "inventory" for p in tight)
            positive = (f"Working capital is tightening — {which} falling relative to sales "
                        "(cash freed, not trapped).")
        else:
            positive = "Working capital is stable relative to sales — no build-up in receivables or inventory."

    return {
        "applicable": True,
        "level": level,                      # low / moderate / elevated
        "receivables": receivables,
        "inventory": inventory,
        "reasons": reasons,
        "positive": positive,
    }
