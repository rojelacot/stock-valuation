"""Forensic accounting scores — two classic screens that catch the failure
modes a DCF and a quality score miss:

  * Altman Z-score      — distance-to-bankruptcy. A decade-plus holder never
                          wants to own something that goes to zero, so distress
                          risk is a hard gate, not a nuance.
  * Beneish M-score     — a statistical profile of earnings *manipulators*.
                          Flags cooked books (aggressive revenue recognition,
                          soft accruals) before you commit capital.

Both use only line items we already pull. Both are calibrated on industrial/
commercial firms and are meaningless for banks/insurers/REITs (whose balance
sheets are structurally different) — so we suppress them there and say why.
"""
from __future__ import annotations

from typing import Any, Optional

import valuation  # for needs_earnings_valuation (financial-firm gate)


def _series(d: dict[str, Optional[float]]) -> list[tuple[int, float]]:
    out = []
    for k, v in (d or {}).items():
        if v is None:
            continue
        try:
            out.append((int(k), float(v)))
        except (TypeError, ValueError):
            continue
    return sorted(out)


def _latest(d: dict[str, Optional[float]]) -> Optional[float]:
    s = _series(d)
    return s[-1][1] if s else None


def _two_years(statements: dict[str, Any], keys: list[str]):
    """Return (year_t, year_p) common to every requested series, or (None, None).
    Beneish needs the same two fiscal years present across all its inputs."""
    common: Optional[set] = None
    for k in keys:
        yrs = {y for y, _ in _series(statements.get(k))}
        common = yrs if common is None else (common & yrs)
    if not common or len(common) < 2:
        return None, None
    ordered = sorted(common)
    return ordered[-1], ordered[-2]


def altman_z(statements: dict[str, Any], info: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Original 5-factor Altman Z (public industrials). Higher = safer.

    Z = 1.2·WC/TA + 1.4·RE/TA + 3.3·EBIT/TA + 0.6·MVE/TL + 1.0·Sales/TA
    Zones: >2.99 safe · 1.81-2.99 grey · <1.81 distress.
    """
    ta = _latest(statements.get("total_assets"))
    te = _latest(statements.get("total_equity"))
    if not ta or ta <= 0 or te is None:
        return None
    tl = ta - te  # total liabilities
    if tl <= 0:
        return None  # net-cash/no-liability firm — Z not meaningful
    ca = _latest(statements.get("current_assets"))
    cl = _latest(statements.get("current_liabilities"))
    re = _latest(statements.get("retained_earnings"))
    ebit = _latest(statements.get("operating_income"))
    rev = _latest(statements.get("revenue"))
    mve = info.get("market_cap")
    if None in (ca, cl, re, ebit, rev, mve):
        return None

    x1 = (ca - cl) / ta           # working capital / total assets
    x2 = re / ta                  # retained earnings / total assets
    x3 = ebit / ta                # EBIT / total assets
    x4 = mve / tl                 # market value of equity / total liabilities
    x5 = rev / ta                 # sales / total assets
    z = 1.2 * x1 + 1.4 * x2 + 3.3 * x3 + 0.6 * x4 + 1.0 * x5

    if z > 2.99:
        zone, distress = "safe", False
    elif z >= 1.81:
        zone, distress = "grey", False
    else:
        zone, distress = "distress", True
    return {"z": z, "zone": zone, "distress": distress,
            "components": {"wc_ta": x1, "re_ta": x2, "ebit_ta": x3,
                           "mve_tl": x4, "sales_ta": x5}}


# Beneish coefficients (1999).
def beneish_m(statements: dict[str, Any]) -> Optional[dict[str, Any]]:
    """8-index Beneish M-score. M > -1.78 => profile resembles manipulators.

    SG&A is optional (its index is neutralized to 1.0 when absent); every other
    input is required for two consecutive years.
    """
    need = ["revenue", "receivables", "gross_profit", "current_assets", "net_ppe",
            "total_assets", "depreciation", "net_income", "operating_cashflow",
            "current_liabilities", "long_term_debt"]
    t, p = _two_years(statements, ["revenue", "receivables", "total_assets",
                                   "net_income", "operating_cashflow"])
    if t is None:
        return None

    def g(key, yr):
        return dict(_series(statements.get(key))).get(yr)

    # Pull the two-year pairs; bail if a required denominator is missing/zero.
    try:
        sales_t, sales_p = g("revenue", t), g("revenue", p)
        recv_t, recv_p = g("receivables", t), g("receivables", p)
        gp_t, gp_p = g("gross_profit", t), g("gross_profit", p)
        ca_t, ca_p = g("current_assets", t), g("current_assets", p)
        ppe_t, ppe_p = g("net_ppe", t), g("net_ppe", p)
        ta_t, ta_p = g("total_assets", t), g("total_assets", p)
        dep_t, dep_p = g("depreciation", t), g("depreciation", p)
        ni_t = g("net_income", t)
        ocf_t = g("operating_cashflow", t)
        cl_t, cl_p = g("current_liabilities", t), g("current_liabilities", p)
        ltd_t, ltd_p = g("long_term_debt", t), g("long_term_debt", p)

        if not all(v not in (None, 0) for v in
                   (sales_t, sales_p, ta_t, ta_p, ppe_t, ppe_p)):
            return None

        # DSRI — days sales in receivables index
        dsri = ((recv_t / sales_t) / (recv_p / sales_p)
                if recv_t is not None and recv_p not in (None, 0) else 1.0)
        # GMI — gross margin index (prior/current; >1 = margins deteriorating)
        gm_t = (gp_t / sales_t) if gp_t is not None else None
        gm_p = (gp_p / sales_p) if gp_p is not None else None
        gmi = (gm_p / gm_t) if (gm_t and gm_p and gm_t != 0) else 1.0
        # AQI — asset quality index (non-current, non-PP&E assets share)
        aq_t = 1 - ((ca_t or 0) + (ppe_t or 0)) / ta_t
        aq_p = 1 - ((ca_p or 0) + (ppe_p or 0)) / ta_p
        aqi = (aq_t / aq_p) if aq_p not in (0, None) else 1.0
        # SGI — sales growth index
        sgi = sales_t / sales_p
        # DEPI — depreciation rate index (prior/current)
        dr_t = (dep_t / (dep_t + ppe_t)) if dep_t is not None else None
        dr_p = (dep_p / (dep_p + ppe_p)) if dep_p is not None else None
        depi = (dr_p / dr_t) if (dr_t and dr_p and dr_t != 0) else 1.0
        # SGAI — SG&A index (neutral if SG&A unavailable)
        sga_t, sga_p = g("sga", t), g("sga", p)
        sgai = (((sga_t / sales_t) / (sga_p / sales_p))
                if (sga_t is not None and sga_p not in (None, 0)) else 1.0)
        # TATA — total accruals to total assets
        tata = ((ni_t - ocf_t) / ta_t
                if (ni_t is not None and ocf_t is not None) else 0.0)
        # LVGI — leverage index
        lev_t = ((cl_t or 0) + (ltd_t or 0)) / ta_t
        lev_p = ((cl_p or 0) + (ltd_p or 0)) / ta_p
        lvgi = (lev_t / lev_p) if lev_p not in (0, None) else 1.0
    except (TypeError, ZeroDivisionError):
        return None

    m = (-4.84 + 0.920 * dsri + 0.528 * gmi + 0.404 * aqi + 0.892 * sgi
         + 0.115 * depi - 0.172 * sgai + 4.679 * tata - 0.327 * lvgi)

    # -1.78 is Beneish's headline cutoff; -2.22 is a more conservative watch line.
    if m > -1.78:
        flag, level = True, "likely"
    elif m > -2.22:
        flag, level = False, "elevated"
    else:
        flag, level = False, "clean"
    return {"m": m, "manipulator": flag, "level": level,
            "sga_used": g("sga", t) is not None,
            "indices": {"DSRI": dsri, "GMI": gmi, "AQI": aqi, "SGI": sgi,
                        "DEPI": depi, "SGAI": sgai, "TATA": tata, "LVGI": lvgi}}


def analyze(statements: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:
    """Both forensic scores, or a 'not applicable' note for financial firms."""
    if valuation.needs_earnings_valuation(info):
        return {"applicable": False,
                "reason": "Altman/Beneish are calibrated on industrial & commercial "
                          "firms; a bank/insurer/REIT balance sheet makes them "
                          "meaningless, so they're not shown here.",
                "altman": None, "beneish": None}
    return {"applicable": True,
            "altman": altman_z(statements, info),
            "beneish": beneish_m(statements)}
