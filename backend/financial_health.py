"""Distress screen for banks & insurers — the firms Altman/Beneish can't score.

The Altman Z and Beneish M models are calibrated on industrial balance sheets and
are meaningless for a bank or insurer, so `forensics` skips them — leaving the asset
class where solvency risk is deadliest (2008) with no quantitative distress check.
This fills that gap with the signals free XBRL reliably provides for a financial:

  * Capital adequacy — common equity ÷ assets (a crude tangible-common-equity /
    leverage proxy; a thin ratio means a small loss on the asset book wipes out the
    equity cushion). This is the single most important bank-solvency signal.
  * Profitability — return on assets (negative = losing money, the clearest tell).
  * Book-value erosion — is common equity shrinking amid weak earnings (capital
    bleeding, not being returned)?

(A dividend-cut signal was considered but dropped: the cash-flow `dividends_paid`
tag conflates common and preferred and can have a partial latest year, which
false-flags healthy banks that are actually raising the payout.)

Applies only to book-value financials (banks/insurers); capital-light "financials"
(exchanges, asset managers, payment networks) run the ordinary forensic checks and
return not-applicable here. Severity is deliberately conservative: only genuinely
thin capital counts as *distress*; a single loss year is *watch*, so a P&C insurer's
catastrophe year doesn't read as insolvency.
"""
from __future__ import annotations

from typing import Any, Optional

import valuation

THIN_CAPITAL = 0.05     # common equity / assets below this = distress (>20x leverage)
MODEST_CAPITAL = 0.07   # below this = watch
WEAK_ROA = 0.004        # ROA below ~0.4% for a financial = thin earnings on the book


def _series(d) -> list:
    out = []
    for k, v in (d or {}).items():
        if v is None:
            continue
        try:
            out.append((int(k), float(v)))
        except (ValueError, TypeError):
            continue
    return sorted(out)


def _latest(s) -> Optional[float]:
    return s[-1][1] if s else None


def analyze(statements: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:
    if not valuation.needs_earnings_valuation(info):
        return {"applicable": False,
                "reason": "Financial-distress signals apply to banks & insurers; this "
                          "name runs the standard forensic checks instead."}
    assets = _series(statements.get("total_assets"))
    equity = _series(statements.get("total_equity"))
    ni = _series(statements.get("net_income"))
    pref = _series(statements.get("preferred_stock"))

    a_l, e_l, ni_l = _latest(assets), _latest(equity), _latest(ni)
    pref_l = _latest(pref) or 0
    if not a_l or a_l <= 0 or e_l is None:
        return {"applicable": False, "reason": "Insufficient balance-sheet data."}

    common_eq = e_l - abs(pref_l)
    eq_to_assets = common_eq / a_l
    roa = (ni_l / a_l) if (ni_l is not None) else None

    reasons, positives = [], []
    sev = 0  # 0 solid · 1 watch · 2 distress

    # 1) Capital adequacy — the core solvency signal.
    if common_eq <= 0 or eq_to_assets < THIN_CAPITAL:
        sev = max(sev, 2)
        reasons.append(
            f"Thin capital — common equity is just {eq_to_assets*100:.1f}% of assets "
            f"(~{(1/eq_to_assets):.0f}× leverage); a small loss on the asset book would "
            "wipe out the equity cushion." if eq_to_assets > 0 else
            "Negative common equity — the balance sheet is underwater.")
    elif eq_to_assets < MODEST_CAPITAL:
        sev = max(sev, 1)
        reasons.append(
            f"Modest capital — equity/assets ~{eq_to_assets*100:.1f}% "
            f"(~{(1/eq_to_assets):.0f}× leverage); a limited buffer for a downturn.")
    else:
        positives.append(f"Well capitalized — common equity is {eq_to_assets*100:.1f}% of assets.")

    # 2) Profitability (ROA). A single loss year is a warning, not insolvency.
    if roa is not None:
        if roa < 0:
            sev = max(sev, 1)
            reasons.append(f"Unprofitable — ROA is {roa*100:.2f}% (a net loss eroding capital).")
        elif roa < WEAK_ROA:
            sev = max(sev, 1)
            reasons.append(f"Weak profitability — ROA just {roa*100:.2f}%; earns little on its book.")
        else:
            positives.append(f"Sound profitability — ROA {roa*100:.2f}%.")

    # 3) Book-value erosion — only a flag when paired with weak/negative earnings
    #    (otherwise a falling equity base is just buybacks, which is healthy).
    eq_change = None
    if len(equity) >= 4:
        prior = equity[-4][1]
        if prior and prior > 0:
            eq_change = (e_l - prior) / prior
            loss_years = sum(1 for _, v in ni[-3:] if v is not None and v < 0) if ni else 0
            if eq_change < -0.10 and (loss_years or (roa is not None and roa < WEAK_ROA)):
                sev = max(sev, 1)
                reasons.append(
                    f"Book value eroding — common equity fell {abs(eq_change)*100:.0f}% over "
                    "3yr amid weak/negative earnings (capital bleeding, not being returned).")

    level = ["solid", "watch", "distress"][sev]
    return {"applicable": True, "level": level,
            "equity_to_assets": eq_to_assets,
            "leverage": (1 / eq_to_assets) if eq_to_assets > 0 else None,
            "roa": roa, "book_value_change_3yr": eq_change,
            "reasons": reasons, "positives": positives,
            "positive": positives[0] if (positives and not reasons) else None}
