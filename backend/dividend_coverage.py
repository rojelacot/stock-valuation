"""Dividend coverage from free cash flow.

For an income-oriented holder the question isn't the yield, it's whether the
dividend is actually *funded by the business* or by debt / asset sales / a
shrinking cash pile. A dividend that free cash flow doesn't cover is a cut
waiting to happen (or a growing reliance on capital markets).

Measured on a multi-year cumulative basis — sum(FCF)/sum(dividends) over ~5 years
— so a single heavy-capex or one-off year (e.g. KO's 2024 tax deposit) doesn't
masquerade as a chronic shortfall. Also surfaces total shareholder payout
(dividends + buybacks) vs FCF, and cross-checks the earnings payout. Non-payers,
financials and REITs (which distribute from AFFO/regulatory capital, not FCF) are
handled as not-applicable.
"""
from __future__ import annotations

from typing import Any, Optional

WINDOW = 5
COMFORTABLE = 1.5
TIGHT = 1.3
TOTAL_STRETCH = 1.15   # (divs + buybacks) / FCF above this = returning more than earned


def _series(d: dict) -> dict:  # magnitudes, numeric-year keys only
    return {str(y): abs(v) for y, v in (d or {}).items()
            if v is not None and str(y).isdigit()}


def _fcf_series(d: dict) -> dict:
    return {str(y): v for y, v in (d or {}).items()
            if v is not None and str(y).isdigit()}


def assess(statements: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:
    st = statements or {}
    info = info or {}
    sector = info.get("sector") or ""

    div = _series(st.get("dividends_paid"))
    # A dividend that's essentially zero across recent years = non-payer.
    recent_div = [v for y, v in sorted(div.items())[-3:]]
    if not recent_div or max(recent_div) < 1e5:
        return {"applicable": False, "level": "none",
                "reason": "Doesn't pay a meaningful dividend — coverage isn't a factor."}

    if sector == "Financial Services":
        return {"applicable": False, "level": "none",
                "reason": "Bank/insurer payouts are governed by regulatory capital, "
                          "not free cash flow — assessed differently."}
    if sector == "Real Estate":
        return {"applicable": False, "level": "none",
                "reason": "REITs distribute from AFFO (and are required to pay out most "
                          "taxable income); an FCF payout ratio understates their coverage."}

    fcf = _fcf_series(st.get("free_cashflow"))
    ni = _fcf_series(st.get("net_income"))
    bb = _series(st.get("buybacks"))

    # Window of years that have both a dividend and an FCF figure.
    years = sorted(y for y in div if y in fcf)[-WINDOW:]
    if len(years) < 3:
        return {"applicable": False, "level": "none",
                "reason": "Not enough overlapping dividend & cash-flow history."}
    # Staleness: a suspended dividend leaves its last *paid* year as the most
    # recent key (suspended years are None → dropped), which would otherwise be
    # graded as a current payer. If the last payout lags the latest cash-flow
    # year, treat it as a former payer.
    latest_fcf_year = max(int(y) for y in fcf)
    if int(years[-1]) < latest_fcf_year - 1:
        return {"applicable": False, "level": "none",
                "reason": "No recent dividend — appears to have suspended or ended its payout."}

    sum_div = sum(div[y] for y in years)
    sum_fcf = sum(fcf[y] for y in years)
    sum_bb = sum(bb.get(y, 0.0) for y in years)
    cum_cov = (sum_fcf / sum_div) if sum_div > 0 else None
    years_covered = sum(1 for y in years if fcf[y] > div[y])

    latest = years[-1]
    latest_cov = (fcf[latest] / div[latest]) if div[latest] > 0 else None
    total_payout = ((sum_div + sum_bb) / sum_fcf) if sum_fcf > 0 else None
    earn_payout = (div[latest] / ni[latest]) if (latest in ni and ni[latest] > 0) else None
    # Is negative FCF driven by heavy capex (utility-like reinvestment)?
    capex_heavy = any(fcf[y] < 0 for y in years)

    fcf_negative = sum_fcf <= 0
    capex_note = (" Heavy capex is the driver (reinvestment), but the payout still "
                  "relies on external funding — sustainable only while capital stays cheap.") \
        if capex_heavy else ""
    reasons: list[str] = []
    positive = None
    if cum_cov is None:
        level = "none"
    elif fcf_negative:
        level = "uncovered"
        reasons.append("Free cash flow was negative across the window — the dividend is "
                       "funded entirely by external sources (debt / equity / cash)."
                       + capex_note)
    elif cum_cov < 1.0:
        level = "uncovered"
        reasons.append(f"Free cash flow covered only {cum_cov:.0%} of dividends over "
                       f"{len(years)} years ({years_covered}/{len(years)} years fully "
                       "covered) — the shortfall is funded by debt, cash, or asset sales."
                       + capex_note)
    elif cum_cov < TIGHT:
        level = "tight"
        reasons.append(f"Free cash flow covers dividends just {cum_cov:.1f}× over "
                       f"{len(years)} years — a thin cushion, little room for a bad year.")
    else:
        level = "comfortable"

    # Secondary: total shareholder returns (divs + buybacks) outrunning FCF.
    if total_payout is not None and total_payout > TOTAL_STRETCH and level != "uncovered":
        reasons.append(f"Dividends + buybacks are {total_payout:.0%} of free cash flow — "
                       "total shareholder returns exceed what the business generates, "
                       "the balance funded externally.")

    # Always vouch for a comfortable dividend, even when a buyback-stretch note is
    # also present — the dividend itself is genuinely covered, so the green badge
    # should carry its checkmark (the note reads as added context, not a mixed
    # signal). The frontend shows the positive and any secondary note together.
    if level == "comfortable":
        positive = (f"Dividend is well-covered — free cash flow is {cum_cov:.1f}× the payout "
                    f"over {len(years)} years"
                    + (f", earnings payout ~{earn_payout:.0%}" if earn_payout is not None else "")
                    + ".")

    return {
        "applicable": True,
        "level": level,                       # comfortable / tight / uncovered
        "cum_coverage": cum_cov,
        "fcf_negative": fcf_negative,
        "latest_coverage": latest_cov,
        "years_covered": years_covered,
        "years_window": len(years),
        "fcf_payout_pct": (sum_div / sum_fcf) if sum_fcf > 0 else None,
        "total_payout_pct": total_payout,
        "earnings_payout_pct": earn_payout,
        "capex_heavy": capex_heavy,
        "series": [{"year": y, "fcf": fcf[y], "dividend": div[y],
                    "covered": fcf[y] > div[y]} for y in years],
        "reasons": reasons,
        "positive": positive,
    }
