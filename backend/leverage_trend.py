"""Covenant / leverage-trend deterioration check.

Static leverage (a D/E snapshot) and Altman Z (distress) miss the *trajectory* —
the slow climb in Net Debt/EBITDA and the compression in interest coverage that
precede trouble by years. A holder for a decade cares less about "is it levered
today" than "is it getting worse, and how much headroom is left before it hits
the levels where lenders set covenants."

We compute Net Debt/EBITDA and EBITDA/interest over time, compare the latest year
to a ~4-year-ago baseline (deterioration = leverage up and/or coverage down), and
measure headroom to *conventional* covenant thresholds (generic loan-market levels
— ~4x/5x leverage, ~2.5x coverage — NOT this filer's actual terms, which live in
the credit agreement). Sector-aware; None-safe throughout.
"""
from __future__ import annotations

from typing import Any, Optional

LOOKBACK = 4                 # compare latest vs ~this many years earlier
LEV_STRESS = 5.0             # Net Debt/EBITDA at/above this = typical HY-covenant stress
LEV_ELEVATED = 4.0           # conventional maintenance-covenant zone
COV_FLOOR = 2.5              # EBITDA/interest below this = tight vs typical covenants
COV_STRESS = 2.0
LEV_RISE = 1.0               # a +1.0x turn over the window is a material climb
COV_DROP = 0.25             # a 25% compression in coverage is material


def _series(d: dict) -> dict:
    return {str(y): v for y, v in (d or {}).items() if v is not None}


def _ratio_series(num: dict, den: dict, require_pos_den=True,
                  require_pos_num=False, lo=None, hi=None) -> dict:
    """{year: num/den}. Filters out years whose denominator (and optionally
    numerator) isn't positive, and clamps out implausible values — a barely-
    positive or collapsed EBITDA (e.g. a COVID year) yields an astronomical
    leverage or a negative coverage that would pollute a trend baseline."""
    out = {}
    for y in num:
        dv, nv = den.get(y), num[y]
        if dv is None or nv is None:
            continue
        if require_pos_den and dv <= 0:
            continue
        if require_pos_num and nv <= 0:
            continue
        r = nv / dv
        if (lo is not None and r < lo) or (hi is not None and r > hi):
            continue  # denominator-distorted year — not representative
        out[y] = r
    return dict(sorted(out.items()))


def _span(series: dict) -> Optional[dict]:
    """Latest value vs the value ~LOOKBACK years earlier (or the earliest in a
    5-year window)."""
    ys = sorted(series)
    if len(ys) < 3:
        return None
    latest_y = ys[-1]
    target = str(int(latest_y) - LOOKBACK)
    prior_y = target if target in series else min(
        (y for y in ys if int(latest_y) - int(y) >= 2), default=ys[0], key=lambda y: abs(int(latest_y) - int(y) - LOOKBACK))
    return {"latest": series[latest_y], "prior": series[prior_y],
            "latest_year": latest_y, "prior_year": prior_y}


def assess(statements: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:
    st = statements or {}
    info = info or {}
    sector = info.get("sector") or ""
    if sector == "Financial Services":
        return {"applicable": False, "level": "none",
                "reason": "Leverage for banks/insurers is regulatory-capital driven — "
                          "not a Net Debt/EBITDA story."}

    rev = _series(st.get("revenue"))
    debt = _series(st.get("total_debt"))
    cash = _series(st.get("cash"))
    ebitda = _series(st.get("ebitda"))
    interest = _series(st.get("interest_expense"))
    equity = _series(st.get("total_equity"))
    latest_rev_year = max((int(y) for y in rev), default=None)

    net_debt = {y: (debt[y] - (cash.get(y) or 0.0)) for y in debt}

    # REIT EBITDA from XBRL is unreliable (we value REITs on FFO) — assess their
    # leverage trajectory on Debt/Equity instead of Net Debt/EBITDA.
    is_reit = sector == "Real Estate"

    # Leverage: net debt / EBITDA, dropping distorted (>15x) years. Coverage:
    # EBITDA / interest, dropping loss years (require positive EBITDA).
    lev = None if is_reit else _ratio_series(net_debt, ebitda, lo=-5, hi=15)
    cov = None if is_reit else _ratio_series(ebitda, interest, require_pos_num=True)
    de = _ratio_series(debt, equity, require_pos_den=True) if equity else None

    # Freshness: don't trend off a stale debt line.
    def fresh(s):
        return s and (latest_rev_year is None or int(max(s)) >= latest_rev_year - 1)

    lev_span = _span(lev) if fresh(lev) else None
    cov_span = _span(cov) if fresh(cov) else None
    de_span = _span(de) if fresh(de) else None

    # Net cash and no leverage series -> nothing to worry about.
    nd_latest = net_debt.get(max(net_debt), None) if net_debt else None
    if not lev_span and not de_span:
        if nd_latest is not None and nd_latest <= 0:
            return {"applicable": True, "level": "none",
                    "positive": "Net cash — no leverage trajectory to worry about.",
                    "reasons": [], "leverage": None, "coverage": None, "de": None}
        return {"applicable": False, "level": "none",
                "reason": "Not enough debt/earnings history to judge a leverage trend."}

    reasons: list[str] = []          # concerns (drive the grade toward worse)
    concern = "stable"               # escalates: stable -> deteriorating -> stressed
    improving_note = None            # good news, kept out of the concerns list

    def worse(new):
        order = {"stable": 0, "deteriorating": 1, "stressed": 2}
        return new if order[new] > order[concern] else concern

    # --- Leverage trajectory (Net Debt/EBITDA) ---
    if lev_span:
        lv, lp = lev_span["latest"], lev_span["prior"]
        rose = lv - lp
        if lv >= LEV_STRESS:
            concern = worse("stressed")
            reasons.append(f"Net Debt/EBITDA is {lv:.1f}× — at/above the ~{LEV_STRESS:.0f}× "
                           "level where high-yield covenants bite.")
        elif rose >= LEV_RISE:
            concern = worse("deteriorating")
            reasons.append(f"Leverage has climbed from {lp:.1f}× to {lv:.1f}× Net Debt/EBITDA "
                           f"since {lev_span['prior_year']} — a {rose:+.1f}× deterioration.")
        elif lv >= LEV_ELEVATED:
            concern = worse("deteriorating")
            reasons.append(f"Net Debt/EBITDA is {lv:.1f}× — in the elevated "
                           f"maintenance-covenant zone (~{LEV_ELEVATED:.0f}×).")
        elif rose <= -LEV_RISE:
            improving_note = (f"Deleveraging — Net Debt/EBITDA down from {lp:.1f}× to {lv:.1f}× "
                              f"since {lev_span['prior_year']}.")

    # --- Coverage trajectory (EBITDA/interest) ---
    # Compression only matters as it approaches the floor. A drop from a high
    # level to a still-comfortable one isn't a warning — and if the company is
    # simultaneously deleveraging, that offsets it.
    deleveraging = bool(lev_span and (lev_span["latest"] - lev_span["prior"]) <= -LEV_RISE)
    if cov_span:
        cv, cp = cov_span["latest"], cov_span["prior"]
        if cv < COV_STRESS:
            concern = worse("stressed")
            reasons.append(f"Interest coverage is {cv:.1f}× EBITDA — below the ~{COV_STRESS:.1f}× "
                           "floor typical covenants demand.")
        elif (cp and (cp - cv) / cp >= COV_DROP and cv < COV_FLOOR and not deleveraging):
            concern = worse("deteriorating")
            reasons.append(f"Interest coverage has compressed from {cp:.1f}× to {cv:.1f}× "
                           f"since {cov_span['prior_year']} — now near the ~{COV_FLOOR:.1f}× "
                           "covenant zone.")

    # --- REIT / no-EBITDA fallback: Debt/Equity trajectory ---
    if is_reit and de_span:
        dv, dp = de_span["latest"], de_span["prior"]
        if dp and dv - dp >= 0.5:
            concern = worse("deteriorating")
            reasons.append(f"Debt/Equity has risen from {dp:.1f}× to {dv:.1f}× since "
                           f"{de_span['prior_year']} (REIT leverage on a book basis).")

    # Concerns win; otherwise a deleveraging trend reads as "improving".
    level = concern
    positive = None
    if concern == "stable":
        if improving_note:
            level = "improving"
            positive = improving_note
        else:
            lv = lev_span["latest"] if lev_span else None
            positive = ("Leverage is stable and moderate"
                        + (f" (Net Debt/EBITDA ~{lv:.1f}×)" if lv is not None else "") + ".")

    return {
        "applicable": True,
        "level": level,                       # improving / stable / deteriorating / stressed
        "leverage": lev_span and {**lev_span, "series": [{"year": y, "x": v} for y, v in (lev or {}).items()]},
        "coverage": cov_span and {**cov_span, "series": [{"year": y, "x": v} for y, v in (cov or {}).items()]},
        "de": de_span,
        "thresholds": {"lev_elevated": LEV_ELEVATED, "lev_stress": LEV_STRESS, "cov_floor": COV_FLOOR},
        "reasons": reasons,
        "positive": positive,
    }
