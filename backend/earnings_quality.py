"""Earnings quality & the capex-cycle distortion.

Motivation (especially relevant in the 2025-26 AI-datacenter build-out):
capital expenditure hits the income statement slowly, as *depreciation* spread
over an asset's life. When a company is investing heavily right now, today's
earnings carry only a fraction of that spend — so:

  * reported earnings look FLATTERED  → trailing P/E looks artificially cheap;
  * free cash flow looks DEPRESSED     → a naive FCF-DCF looks too expensive.

Both distortions point in opposite directions, so for heavy-capex names a single
"intrinsic value" is false precision. We instead compute:

  * capex-to-depreciation ratio         (the diagnostic for how big the gap is)
  * maintenance vs growth capex         (Greenwald method + depreciation proxy)
  * owner earnings = NI + D&A - maint.  (Buffett's measure; credits growth capex)
  * cash conversion = FCF / net income  (do earnings turn into cash?)

Downstream, the DCF is run twice — on FCF (conservative) and on owner earnings
(adjusted) — and the intrinsic value is shown as a range whose width IS the
capex distortion made visible.
"""
from __future__ import annotations

from typing import Any, Optional

HEAVY_CAPEX_RATIO = 1.5   # capex > 1.5x depreciation => meaningful build phase
INTENSE_CAPEX_RATIO = 2.5  # capex > 2.5x depreciation => intense (AI-hyperscaler-like)


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


def _latest(pts: list[tuple[int, float]]) -> Optional[float]:
    return pts[-1][1] if pts else None


def _avg_tail(pts: list[tuple[int, float]], n: int = 3) -> Optional[float]:
    vals = [v for _, v in pts][-n:]
    return sum(vals) / len(vals) if vals else None


def analyze(statements: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:
    revenue = _series(statements.get("revenue"))
    net_income = _series(statements.get("net_income"))
    capex = _series(statements.get("capex"))            # negative in Yahoo
    ocf = _series(statements.get("operating_cashflow"))
    gross_ppe = _series(statements.get("gross_ppe"))
    sbc = _series(statements.get("stock_based_comp"))

    # Depreciation: Yahoo's two D&A fields are each sometimes sparse, so merge
    # them per-year (ReconciledDepreciation tends to be the most complete).
    dep_recon = dict(_series(statements.get("depreciation_recon")))
    dep_da = dict(_series(statements.get("depreciation")))
    dep_merged = {}
    for y in set(dep_recon) | set(dep_da):
        dep_merged[y] = dep_recon.get(y) if dep_recon.get(y) is not None else dep_da.get(y)
    dep = sorted(dep_merged.items())

    # Absolute capex (Yahoo stores it negative).
    capex_abs = [(y, abs(v)) for y, v in capex]

    dep_latest = _latest(dep)
    capex_latest = _latest(capex_abs)
    ni_latest = _latest(net_income)
    sbc_latest = _latest(sbc)
    # SBC as a share of operating cash flow — how much "cash flow" is really
    # non-cash dilution. High means reported FCF flatters the business.
    ocf_l = _latest(ocf)
    sbc_pct_ocf = (sbc_latest / ocf_l) if (sbc_latest and ocf_l and ocf_l > 0) else None

    # ---- Capex-to-depreciation ratio (the core diagnostic) ----
    capex_to_dep = None
    capex_to_dep_avg = None
    if dep_latest and dep_latest > 0 and capex_latest is not None:
        capex_to_dep = capex_latest / dep_latest
    dep_avg = _avg_tail(dep)
    capex_avg = _avg_tail(capex_abs)
    if dep_avg and dep_avg > 0 and capex_avg is not None:
        capex_to_dep_avg = capex_avg / dep_avg

    # ---- Maintenance vs growth capex ----
    # (a) Depreciation proxy: maintenance ~= depreciation, bounded by actual capex.
    maint_dep_proxy = None
    if dep_latest is not None and capex_latest is not None:
        maint_dep_proxy = min(dep_latest, capex_latest)

    # (b) Greenwald: growth capex = avg(grossPPE/sales, prior yrs) * (delta sales).
    maint_greenwald = None
    growth_capex_greenwald = None
    if len(gross_ppe) >= 2 and len(revenue) >= 2 and capex_latest is not None:
        ppe_map = dict(gross_ppe)
        rev_map = dict(revenue)
        ratios = [ppe_map[y] / rev_map[y] for y in ppe_map
                  if y in rev_map and rev_map[y]]
        # use prior-years average (exclude the most recent, which may be inflated)
        prior = ratios[:-1] or ratios
        avg_ratio = sum(prior) / len(prior) if prior else None
        rev_pts = revenue
        delta_sales = rev_pts[-1][1] - rev_pts[-2][1] if len(rev_pts) >= 2 else 0
        if avg_ratio is not None:
            gc = max(0.0, avg_ratio * delta_sales)
            gc = min(gc, capex_latest)              # can't exceed total capex
            growth_capex_greenwald = gc
            maint_greenwald = capex_latest - gc

    # Headline maintenance capex: prefer Greenwald, but keep it sane by bounding
    # to [0.6*depreciation, total capex]; fall back to the depreciation proxy.
    maintenance_capex = None
    if maint_greenwald is not None and dep_latest:
        lo = 0.6 * dep_latest
        maintenance_capex = min(max(maint_greenwald, lo), capex_latest)
    elif maint_dep_proxy is not None:
        maintenance_capex = maint_dep_proxy
    growth_capex = (capex_latest - maintenance_capex) if (
        capex_latest is not None and maintenance_capex is not None) else None

    # ---- Owner earnings (normalized over last 3yr) ----
    # Per year: NI + D&A - maintenance capex. Using the depreciation proxy for
    # maintenance keeps it stable and is the classic Buffett approximation.
    ni_map = dict(net_income)
    dep_map = dict(dep)
    capex_map = dict(capex_abs)
    oe_by_year = {}
    for y, ni in net_income:
        d = dep_map.get(y)
        cx = capex_map.get(y)
        if d is None or cx is None:
            continue
        maint = min(d, cx)
        oe_by_year[y] = ni + d - maint
    oe_series = sorted(oe_by_year.items())
    owner_earnings = _avg_tail([(y, v) for y, v in oe_series]) if oe_series else None
    owner_earnings_latest = oe_series[-1][1] if oe_series else None

    # ---- Cash conversion: FCF / net income ----
    fcf_by_year = {}
    ocf_map = dict(ocf)
    for y in set(ocf_map) | set(capex):
        o = ocf_map.get(y)
        c = dict(capex).get(y)
        if o is not None:
            fcf_by_year[y] = o + (c or 0.0)   # c negative
    cash_conv = []
    for y, ni in net_income:
        if ni and ni > 0 and y in fcf_by_year:
            cash_conv.append(fcf_by_year[y] / ni)
    cash_conversion_avg = (sum(cash_conv) / len(cash_conv)) if cash_conv else None

    # ---- Materiality: is capex actually a big deal for this company? ----
    # A high capex/dep ratio only matters if capex is a meaningful share of
    # operating cash flow — otherwise (e.g. an asset-light staple) the earnings
    # distortion is negligible even at a 2x ratio.
    ocf_latest = _latest(ocf)
    capex_intensity = (capex_latest / ocf_latest) if (
        capex_latest and ocf_latest and ocf_latest > 0) else None
    material = capex_intensity is not None and capex_intensity >= 0.30

    # ---- Flags / interpretation ----
    ratio = capex_to_dep or capex_to_dep_avg
    if ratio is None:
        phase = "unknown"
    elif ratio >= INTENSE_CAPEX_RATIO and material:
        phase = "intense_buildout"
    elif ratio >= HEAVY_CAPEX_RATIO and material:
        phase = "investing"
    elif ratio >= 0.8:
        phase = "steady_state"
    elif material:
        phase = "harvesting"
    else:
        phase = "steady_state"

    heavy_capex = ratio is not None and ratio >= HEAVY_CAPEX_RATIO and material

    notes: list[str] = []
    if heavy_capex:
        notes.append(
            f"Capex is running ~{ratio:.1f}× depreciation — a heavy build phase. "
            "Reported earnings are flattered (today's depreciation lags the current "
            "spend), so trailing P/E looks cheaper than the durable reality; free cash "
            "flow is simultaneously depressed. Intrinsic value is shown as a range for "
            "this reason.")
    elif phase == "harvesting" and ratio is not None:
        notes.append(
            f"Capex is only ~{ratio:.1f}× depreciation — the company is under-investing "
            "relative to its asset base, which can flatter near-term free cash flow.")
    if cash_conversion_avg is not None and cash_conversion_avg < 0.7:
        notes.append(
            f"Cash conversion is weak (~{cash_conversion_avg*100:.0f}% of net income "
            "becomes free cash flow) — a sign earnings aren't fully turning into cash.")
    if sbc_pct_ocf is not None and sbc_pct_ocf >= 0.15:
        notes.append(
            f"Stock-based comp is large (~{sbc_pct_ocf*100:.0f}% of operating cash flow) — "
            "a real dilution cost. Free cash flow here is shown net of SBC.")

    return {
        "depreciation_latest": dep_latest,
        "capex_latest": capex_latest,
        "capex_to_dep": capex_to_dep,
        "capex_to_dep_avg": capex_to_dep_avg,
        "capex_intensity": capex_intensity,
        "maintenance_capex": maintenance_capex,
        "growth_capex": growth_capex,
        "maintenance_capex_greenwald": maint_greenwald,
        "maintenance_capex_dep_proxy": maint_dep_proxy,
        "owner_earnings": owner_earnings,
        "owner_earnings_latest": owner_earnings_latest,
        "net_income_latest": ni_latest,
        "stock_based_comp": sbc_latest,
        "sbc_pct_ocf": sbc_pct_ocf,
        "cash_conversion_avg": cash_conversion_avg,
        "phase": phase,
        "heavy_capex": heavy_capex,
        "notes": notes,
        "series": {
            "capex": [{"year": y, "value": v} for y, v in capex_abs],
            "depreciation": [{"year": y, "value": v} for y, v in dep],
            "owner_earnings": [{"year": y, "value": v} for y, v in oe_series],
        },
    }
