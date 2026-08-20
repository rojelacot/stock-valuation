"""Debt-maturity & refinancing-risk assessment.

A grounded, self-standing check for a long-term holder: not just "how much debt"
(static leverage the score already flags) but "when does it come due, can the
business cover the near-term wall from cash + free cash flow, and does rolling it
at today's higher rates break interest coverage?" A company can look modestly
levered yet be one refinancing away from distress if a wall lands in a bad market.

Built from the 10-K contractual-maturities ladder (EDGAR XBRL). When a filer
doesn't tag the ladder, it degrades to the current portion of long-term debt plus
the refi-rate stress on total debt. All inputs are None-safe.
"""
from __future__ import annotations

from typing import Any, Optional

STRESS_BPS = 0.03          # refinance the near-term wall at +300bps
NEAR_TERM_YEARS = 2        # "near term" = due within this many years


def _latest(series: dict) -> Optional[float]:
    s = {y: v for y, v in (series or {}).items() if v is not None}
    return s[max(s)] if s else None


def _recent_avg(series: dict, n: int = 3) -> Optional[float]:
    s = [v for _, v in sorted((series or {}).items()) if v is not None]
    s = s[-n:]
    return sum(s) / len(s) if s else None


def assess(statements: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:
    st = statements or {}
    info = info or {}

    # Banks/insurers fund with deposits/liabilities, not a corporate maturity
    # wall, and their interest expense is an operating cost — the same reason
    # Altman/Beneish are N/A for them. Assess their solvency elsewhere.
    if (info.get("sector") or "") == "Financial Services":
        return {"applicable": False, "level": "none", "has_ladder": False,
                "reason": "Financials fund via deposits/liabilities, not a corporate "
                          "debt maturity wall — refinancing risk is assessed differently."}

    ladder = {k: _latest(st.get(k)) for k in
              ("debt_mat_y1", "debt_mat_y2", "debt_mat_y3",
               "debt_mat_y4", "debt_mat_y5", "debt_mat_beyond")}
    ladder_sum = sum(v for v in ladder.values() if v)
    has_ladder = any(v for v in ladder.values())
    debt_current = _latest(st.get("debt_current"))

    # Total debt: prefer the reported figure, but fall back to the ladder sum
    # (some filers, e.g. KHC, leave the balance-sheet debt tag unmatched while
    # still tagging the full ladder).
    reported = _latest(st.get("total_debt")) or info.get("total_debt")
    total_debt = max(reported or 0.0, ladder_sum or 0.0) or None

    # No meaningful debt -> refinancing risk is not a concern (a positive).
    if not total_debt or total_debt < 1e6:
        return {"applicable": False, "reason": "No meaningful debt — refinancing "
                "risk isn't a factor.", "level": "none", "has_ladder": has_ladder}

    # Near-term wall (due within NEAR_TERM_YEARS). Ladder first; else the current
    # portion of long-term debt as the best available proxy.
    if has_ladder:
        near_term = sum(v for v in (ladder["debt_mat_y1"], ladder["debt_mat_y2"]) if v)
    else:
        near_term = debt_current or 0.0
    near_pct = near_term / total_debt if total_debt else None

    # Liquidity to meet it: cash + a conservative two years of free cash flow
    # (only positive FCF counts — a cash-burner can't self-fund a wall).
    cash = _latest(st.get("cash")) or info.get("total_cash") or 0.0
    avg_fcf = _recent_avg(st.get("free_cashflow")) or 0.0
    fcf_runway = 2 * max(avg_fcf, 0.0)
    coverage = ((cash + fcf_runway) / near_term) if near_term else None

    # Refi-rate stress: roll the near-term wall at +300bps and re-test interest
    # coverage. Use a 3-year average EBITDA (operating income + D&A) as the
    # coverage base — EBITDA is the standard credit metric (fair to asset-heavy
    # names and REITs whose D&A is large), and averaging smooths a one-off
    # impairment year (e.g. a goodwill writedown) that would distort a single EBIT.
    interest = _latest(st.get("interest_expense"))
    implied_rate = (interest / total_debt) if (interest and total_debt) else None
    # REIT XBRL EBITDA is unreliable (the same reason we value REITs on FFO), and
    # REITs roll debt rather than repay it from earnings, so interest coverage
    # doesn't translate — grade them on the maturity ladder + cash/FCF instead.
    is_reit = (info.get("sector") or "") == "Real Estate"
    if is_reit:
        cov_earn = base_cover = stress_cover = None
    else:
        cov_earn = _recent_avg(st.get("ebitda"), 3)
        if cov_earn is None:
            cov_earn = _recent_avg(st.get("operating_income"), 3)
        base_cover = (cov_earn / interest) if (cov_earn is not None and interest and interest > 0) else None
        stressed_interest = (interest or 0.0) + (near_term * STRESS_BPS)
        stress_cover = (cov_earn / stressed_interest) if (cov_earn is not None and stressed_interest > 0) else None

    # ---- Grade it ----
    reasons: list[str] = []
    # Coverage of the near-term wall from cash + FCF.
    if coverage is not None:
        if coverage < 1:
            reasons.append(f"Near-term maturities (~${near_term/1e9:.1f}B) exceed cash + "
                           f"two years of free cash flow ({coverage:.1f}× covered).")
        elif coverage < 1.5:
            reasons.append(f"Near-term maturities only {coverage:.1f}× covered by cash + "
                           "2yr free cash flow — thin cushion.")
    # A big share of debt landing soon.
    if near_pct is not None and near_pct > 0.40:
        reasons.append(f"{near_pct*100:.0f}% of total debt comes due within "
                       f"{NEAR_TERM_YEARS} years — a maturity wall, not a ladder.")
    # Refinancing at higher rates breaking interest coverage.
    if base_cover is not None and stress_cover is not None:
        if stress_cover < 1.5:
            reasons.append(f"Rolling the wall at +300bps cuts interest coverage to "
                           f"{stress_cover:.1f}× (from {base_cover:.1f}×) — refinancing "
                           "at today's rates would strain it.")
        elif base_cover < 2:
            reasons.append(f"Interest coverage is already thin ({base_cover:.1f}×); "
                           "little room to absorb higher refinancing rates.")

    # Level from the worst dimension.
    level = "low"
    if (coverage is not None and coverage < 1) or (stress_cover is not None and stress_cover < 1):
        level = "high"
    elif ((coverage is not None and coverage < 1.5)
          or (stress_cover is not None and stress_cover < 1.5)
          or (near_pct is not None and near_pct > 0.40 and (coverage is None or coverage < 2.5))):
        level = "elevated"
    elif ((base_cover is not None and base_cover < 3)
          or (near_pct is not None and near_pct > 0.30)):
        level = "moderate"

    positive = None
    if level == "low" and not reasons:
        parts = []
        if near_pct is not None:
            parts.append(f"only {near_pct*100:.0f}% due within {NEAR_TERM_YEARS}yr")
        if coverage is not None:
            parts.append(f"cash + free cash flow covers the near-term wall {coverage:.1f}×")
        if base_cover is not None:
            parts.append(f"interest covered {base_cover:.1f}×")
        positive = "Debt is well-termed" + (" (" + ", ".join(parts) + ")" if parts else "") + "."

    return {
        "applicable": True,
        "level": level,                      # low / moderate / elevated / high
        "has_ladder": has_ladder,
        "total_debt": total_debt,
        "near_term_wall": near_term,
        "near_term_pct": near_pct,
        "cash": cash,
        "fcf_runway_2yr": fcf_runway,
        "coverage": coverage,                # (cash + 2yr FCF) / near-term wall
        "implied_rate": implied_rate,
        "base_interest_coverage": base_cover,
        "stress_interest_coverage": stress_cover,
        "ladder": ladder,
        "reasons": reasons,
        "positive": positive,
    }
