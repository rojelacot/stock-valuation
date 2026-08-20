"""Composite scoring -> Buy / Hold / Avoid verdict.

Philosophy baked in: we want a quality business we can hold 10-15 years, bought
with a margin of safety, whose expected return beats inflation. This is NOT a
momentum or index-tracking score. Each pillar contributes points and, crucially,
a plain-English reason so the user can see *why*.
"""
from __future__ import annotations

from typing import Any

# Verdict thresholds (single source of truth; the Guide tab reads these).
BUY_THRESHOLD = 70    # score >= this -> BUY
HOLD_THRESHOLD = 50   # score >= this -> HOLD / WATCH; below -> AVOID

# Strategy profiles — reweight the six pillars in this fixed order:
#   [Valuation, Business quality, Growth, Financial strength, Beats inflation, Margins]
# "balanced" uses the pillars' own maxes, so it reproduces the classic score
# exactly. The others tilt the emphasis. Weights are normalized, so they need not
# sum to anything in particular.
STRATEGIES = {
    "balanced":     {"label": "Balanced — quality at a fair price",
                     "weights": [30, 20, 15, 15, 10, 10]},
    "deep_value":   {"label": "Deep value — cheapness first",
                     "weights": [45, 12, 8, 15, 10, 10]},
    "quality":      {"label": "Quality compounder — returns & durability",
                     "weights": [15, 32, 20, 12, 6, 15]},
    "garp":         {"label": "Growth at a reasonable price (GARP)",
                     "weights": [22, 18, 30, 8, 10, 12]},
    "conservative": {"label": "Conservative — balance-sheet & durability",
                     "weights": [22, 18, 8, 32, 12, 8]},
}


def resolve_strategy(name) -> str:
    return name if name in STRATEGIES else "balanced"


def _pct(x):
    return None if x is None else round(x * 100, 1)


def score(metrics: dict[str, Any]) -> dict[str, Any]:
    """Return {score 0-100, rating, pillars:[{name, points, max, note}], flags}."""
    pillars: list[dict[str, Any]] = []
    green: list[str] = []
    red: list[str] = []

    growth = metrics["growth"]
    returns = metrics["returns"]
    balance = metrics["balance"]
    margins = metrics["margins"]
    dcf = metrics["dcf"]
    val = metrics.get("valuation", {})
    eq = metrics.get("earnings_quality", {})
    exp = metrics["expected_return"]

    # ---- Pillar 1: Valuation / margin of safety (30 pts) ----
    # Score off the range MIDPOINT (blend of FCF and owner-earnings DCFs) so a
    # heavy-capex name is neither unfairly punished (depressed FCF) nor flattered
    # (lagging depreciation).
    p, note = 0, "Insufficient data for DCF."
    upside = val.get("upside_mid") if val.get("ok") else dcf.get("upside")

    if val.get("suspect"):
        # Guardrail: a suspect valuation (implausible upside, unresolved currency)
        # must NOT top the ranking. Cap the pillar and flag it, don't reward it.
        p = 8
        note = ("Valuation flagged unreliable — "
                + (val.get("suspect_reason") or "data/model doesn't fit this company.")
                + " Not treated as a bargain.")
        red.append("Valuation unreliable (" + (val.get("suspect_reason") or "data issue") + ").")
    elif upside is not None and (val.get("ok") or dcf.get("ok")):
        if upside >= 0.35:
            p, note = 30, f"Trades ~{_pct(upside)}% below fair value — strong margin of safety."
            green.append("Large discount to intrinsic value.")
        elif upside >= 0.15:
            p, note = 23, f"~{_pct(upside)}% upside to fair value — reasonable margin of safety."
            green.append("Trades below estimated intrinsic value.")
        elif upside >= 0.0:
            p, note = 15, f"Roughly fairly valued (~{_pct(upside)}% to fair value)."
        elif upside >= -0.15:
            p, note = 8, f"Slightly above fair value (~{_pct(upside)}%). Limited safety."
        else:
            p, note = 2, f"~{_pct(-upside)}% above fair value — priced for perfection."
            red.append("Trades meaningfully above intrinsic value estimate.")
        if val.get("method") == "earnings":
            note += " (Valued on earnings power — an FCF-DCF doesn't fit financials/REITs.)"
        elif val.get("spread", 0) >= 0.4:
            note += (f" Wide value range (${val['low']:.0f}–${val['high']:.0f}) "
                     "reflects heavy capex — treat with caution.")
    pillars.append({"name": "Valuation & margin of safety", "points": p, "max": 30, "note": note})

    # ---- Pillar 2: Business quality / returns on capital (20 pts) ----
    p, notes = 0, []
    roic = returns.get("roic_avg") or returns.get("roic_latest")
    roe = returns.get("roe_avg") or returns.get("roe_latest")
    if roic is not None:
        if roic >= 0.15:
            p += 12; notes.append(f"High ROIC (~{_pct(roic)}%) — capital-efficient.")
            green.append("High return on invested capital (quality compounder trait).")
        elif roic >= 0.08:
            p += 7; notes.append(f"Decent ROIC (~{_pct(roic)}%).")
        else:
            p += 2; notes.append(f"Low ROIC (~{_pct(roic)}%).")
            red.append("Weak returns on capital.")
    if roe is not None:
        if roe >= 0.15:
            p += 8; notes.append(f"Strong ROE (~{_pct(roe)}%).")
        elif roe >= 0.08:
            p += 4; notes.append(f"Moderate ROE (~{_pct(roe)}%).")
        else:
            notes.append(f"Low ROE (~{_pct(roe)}%).")
    pillars.append({"name": "Business quality (returns on capital)", "points": p, "max": 20,
                    "note": " ".join(notes) or "Returns on capital unavailable."})

    # ---- Pillar 3: Growth durability (15 pts) ----
    p, notes = 0, []
    rev_g = growth.get("revenue_cagr")
    eps_g = growth.get("eps_cagr")
    fcf_g = growth.get("fcf_cagr")
    for label, g, w in (("Revenue", rev_g, 5), ("EPS", eps_g, 5), ("FCF", fcf_g, 5)):
        if g is None:
            continue
        if g >= 0.10:
            p += w; notes.append(f"{label} CAGR ~{_pct(g)}% (strong).")
        elif g >= 0.04:
            p += w * 0.6; notes.append(f"{label} CAGR ~{_pct(g)}% (steady).")
        elif g >= 0:
            p += w * 0.3; notes.append(f"{label} CAGR ~{_pct(g)}% (slow).")
        else:
            notes.append(f"{label} shrinking (~{_pct(g)}%).")
            red.append(f"{label} declined over the period.")
    p = round(p)
    if (rev_g or 0) >= 0.08 and (eps_g or 0) >= 0.08:
        green.append("Durable multi-year revenue and earnings growth.")
    pillars.append({"name": "Growth durability", "points": p, "max": 15,
                    "note": " ".join(notes) or "Growth history unavailable."})

    # ---- Pillar 4: Financial strength / survivability (15 pts) ----
    p, notes = 0, []
    de = balance.get("debt_to_equity")
    cov = balance.get("interest_coverage")
    cr = balance.get("current_ratio")
    net_cash = balance.get("net_cash")
    if de is not None:
        if de <= 0.5:
            p += 6; notes.append(f"Low leverage (D/E ~{round(de,2)}).")
        elif de <= 1.5:
            p += 3; notes.append(f"Moderate leverage (D/E ~{round(de,2)}).")
        else:
            notes.append(f"High leverage (D/E ~{round(de,2)}).")
            red.append("Elevated debt load.")
    if net_cash is not None and net_cash > 0:
        p += 3; notes.append("Net cash on balance sheet.")
        green.append("Net cash position — balance-sheet resilience.")
    if cov is not None:
        if cov >= 8:
            p += 4; notes.append(f"Interest well covered (~{round(cov,1)}x).")
        elif cov >= 3:
            p += 2; notes.append(f"Interest coverage ~{round(cov,1)}x.")
        else:
            notes.append(f"Thin interest coverage (~{round(cov,1)}x).")
            red.append("Weak interest coverage.")
    if cr is not None and cr >= 1.5:
        p += 2; notes.append(f"Healthy current ratio (~{round(cr,1)}).")
    p = min(p, 15)
    pillars.append({"name": "Financial strength", "points": p, "max": 15,
                    "note": " ".join(notes) or "Balance-sheet data unavailable."})

    # ---- Pillar 5: Beats inflation over the hold (10 pts) ----
    p, note = 0, "Expected return unavailable."
    er = exp.get("expected_annual_return")
    if er is not None:
        real = exp.get("real_return_vs_inflation")
        if er >= 0.12:
            p, note = 10, f"~{_pct(er)}%/yr expected — comfortably beats the {_pct(exp['inflation_hurdle'])}% inflation bar."
            green.append("Expected return clears the inflation hurdle with room to spare.")
        elif er >= 0.08:
            p, note = 7, f"~{_pct(er)}%/yr expected — beats inflation (+{_pct(real)}% real)."
            green.append("Expected return beats inflation.")
        elif er > exp["inflation_hurdle"]:
            p, note = 4, f"~{_pct(er)}%/yr expected — narrowly beats inflation."
        else:
            p, note = 0, f"~{_pct(er)}%/yr expected — does NOT clear the inflation bar."
            red.append("Expected long-term return may not beat inflation.")
    pillars.append({"name": "Beats inflation (10-15yr)", "points": p, "max": 10, "note": note})

    # ---- Margins bonus/penalty (10 pts) ----
    p, notes = 0, []
    nm = margins["net"].get("latest")
    nm_trend = margins["net"].get("trend")
    gm = margins["gross"].get("latest")
    if nm is not None:
        if nm >= 0.15:
            p += 5; notes.append(f"High net margin (~{_pct(nm)}%).")
        elif nm >= 0.05:
            p += 3; notes.append(f"Moderate net margin (~{_pct(nm)}%).")
        elif nm > 0:
            p += 1; notes.append(f"Thin net margin (~{_pct(nm)}%).")
        else:
            notes.append("Unprofitable on a net basis.")
            red.append("Currently unprofitable.")
    if nm_trend is not None:
        if nm_trend > 0.01:
            p += 3; notes.append("Margins expanding over time.")
            green.append("Expanding profit margins.")
        elif nm_trend < -0.02:
            notes.append("Margins compressing over time.")
            red.append("Margins have been compressing.")
    if gm is not None and gm >= 0.4:
        p += 2; notes.append(f"High gross margin (~{_pct(gm)}%) — pricing power signal.")
    p = min(p, 10)
    pillars.append({"name": "Profitability & margins", "points": p, "max": 10,
                    "note": " ".join(notes) or "Margin data unavailable."})

    # ---- Earnings-quality flags (capex cycle / cash conversion) ----
    if eq.get("heavy_capex"):
        ratio = eq.get("capex_to_dep") or eq.get("capex_to_dep_avg")
        red.append(f"Heavy capex cycle (~{ratio:.1f}× depreciation): reported earnings "
                   "are flattered and P/E looks cheaper than reality; FCF is depressed.")
    cc = eq.get("cash_conversion_avg")
    if cc is not None:
        if cc < 0.7:
            red.append(f"Weak cash conversion (~{_pct(cc)}% of earnings become cash).")
        elif cc >= 1.0 and not eq.get("heavy_capex"):
            green.append("Strong cash conversion (earnings turn fully into cash).")

    # Value creation: ROIC vs WACC.
    dd = metrics.get("due_diligence", {})
    spread = dd.get("roic_vs_wacc_spread")
    if spread is not None:
        if spread >= 0.05:
            green.append(f"Creates economic value — ROIC exceeds WACC by ~{_pct(spread)}%.")
        elif spread < 0:
            red.append(f"ROIC is below WACC (~{_pct(spread)}%) — the company may be destroying value.")

    # Cyclical-peak warning (earnings may not be durable).
    cyc = metrics.get("cyclical_peak", {})
    if cyc.get("peak"):
        red.append("Profitability may be at a cyclical peak (" + "; ".join(cyc["reasons"]) +
                   ") — today's earnings may not be durable.")

    # Strategy-weighted normalization: each pillar's fraction (points/max) is
    # weighted by the chosen strategy profile. Balanced weights == the maxes, so
    # it reproduces the classic score.
    strategy = resolve_strategy(metrics.get("assumptions_used", {}).get("strategy"))
    weights = STRATEGIES[strategy]["weights"]
    num = sum((pl["points"] / pl["max"]) * weights[i]
              for i, pl in enumerate(pillars) if pl["max"])
    den = sum(weights[i] for i, pl in enumerate(pillars) if pl["max"])
    normalized = round(num / den * 100) if den else 0
    for i, pl in enumerate(pillars):   # expose the effective weight for the UI
        pl["weight"] = weights[i] if i < len(weights) else pl["max"]

    # ---- Forensic penalties: distress (Altman Z) & manipulation (Beneish M) ----
    # These are near-disqualifying for a decade-plus hold, so they dock the
    # NUMERIC score (not just the label) — which is what the ≥80 buy-screen gate
    # filters on. Capped so they can knock a name out of BUY without nuking it.
    fx = metrics.get("forensics", {})
    penalty = 0
    if fx.get("applicable"):
        az = fx.get("altman") or {}
        z = az.get("z")
        if z is not None:
            if az.get("distress"):
                penalty += 12
                red.append(f"Altman Z ~{z:.1f} (distress zone) — elevated bankruptcy "
                           "risk over a long hold.")
            elif az.get("zone") == "grey":
                penalty += 4
                red.append(f"Altman Z ~{z:.1f} (grey zone) — financial-distress risk "
                           "is not negligible.")
            else:
                green.append(f"Altman Z ~{z:.1f} — financially sound, low distress risk.")
        bm = fx.get("beneish") or {}
        m = bm.get("m")
        if m is not None:
            if bm.get("manipulator"):
                penalty += 12
                red.append(f"Beneish M ~{m:.2f} — accounting profile resembles earnings "
                           "manipulators; scrutinize revenue recognition & accruals.")
            elif bm.get("level") == "elevated":
                penalty += 5
                red.append(f"Beneish M ~{m:.2f} — some manipulation-risk markers; "
                           "worth a closer look at accruals.")
    # ---- Refinancing / debt-maturity risk (timing + refi-rate stress) ----
    # Sharper than the static D/E flag: when does the debt come due, can cash +
    # free cash flow cover the near-term wall, and does rolling it at +300bps
    # break interest coverage? A solvency signal, so it docks the numeric score.
    rf = metrics.get("refinancing", {})
    if rf.get("applicable"):
        lvl = rf.get("level")
        first = (rf.get("reasons") or [None])[0]
        if lvl == "high":
            penalty += 8
            red.append("Refinancing risk (high): " +
                       (first or "near-term maturities look hard to cover or refinance."))
        elif lvl == "elevated":
            penalty += 3
            red.append("Refinancing risk (elevated): " +
                       (first or "a maturity wall or thin coverage bears watching."))
        elif rf.get("positive"):
            green.append(rf["positive"])

    # ---- Working-capital quality (receivables/inventory outrunning sales) ----
    # An early accruals/demand warning — cash trapped in working capital. A
    # softer signal than distress, so only a sustained (elevated) build docks the
    # score; a moderate creep is surfaced in the section but not flagged here.
    wc = metrics.get("working_capital", {})
    if wc.get("applicable"):
        if wc.get("level") == "elevated":
            penalty += 4
            for r in (wc.get("reasons") or [])[:2]:
                red.append("Working capital: " + r)
        elif wc.get("positive"):
            green.append(wc["positive"])

    # ---- Covenant / leverage-trend deterioration ----
    # The trajectory static leverage misses: rising Net Debt/EBITDA and
    # compressing coverage toward the levels where lenders set covenants.
    lt_ = metrics.get("leverage_trend", {})
    if lt_.get("applicable"):
        lvl = lt_.get("level")
        if lvl == "stressed":
            penalty += 6
            for r in (lt_.get("reasons") or [])[:2]:
                red.append("Leverage: " + r)
        elif lvl == "deteriorating":
            penalty += 3
            first = (lt_.get("reasons") or [None])[0]
            if first:
                red.append("Leverage trend: " + first)
        elif lvl == "improving" and lt_.get("positive"):
            green.append(lt_["positive"])

    # ---- Dividend coverage from free cash flow ----
    # An uncovered dividend is a cut waiting to happen (or a growing reliance on
    # external funding) — a capital-allocation quality signal for income names.
    dc_ = metrics.get("dividend_coverage", {})
    if dc_.get("applicable"):
        if dc_.get("level") == "uncovered":
            penalty += 4
            first = (dc_.get("reasons") or [None])[0]
            if first:
                red.append("Dividend: " + first)
        elif dc_.get("level") == "comfortable" and dc_.get("positive"):
            green.append(dc_["positive"])

    # ---- Acquisition-accounting / goodwill-impairment risk ----
    # A balance sheet that is mostly acquired goodwill with a negative tangible
    # book is the roll-up/impairment configuration behind many blow-ups. 'high'
    # (bloat AND negative tangible book) docks the score; 'elevated' is a note.
    ig_ = metrics.get("intangibles", {})
    if ig_.get("applicable"):
        if ig_.get("level") == "high":
            penalty += 4
            for r in (ig_.get("reasons") or [])[:1]:
                red.append("Impairment risk: " + r)
        elif ig_.get("level") == "elevated":
            first = (ig_.get("reasons") or [None])[0]
            if first:
                red.append("Goodwill-heavy: " + first)

    penalty = min(penalty, 20)
    forensic_penalty = penalty
    normalized = max(0, normalized - penalty)

    # ---- Sector-relative context (purely informational — does NOT move the
    # score). Rigorous multi-window backtesting showed a sector nudge added no
    # forward-return signal (broad sectors are too heterogeneous — cheap pharma
    # vs pricey biotech), so this only surfaces context as green/red flags.
    sr = metrics.get("sector_relative", {})
    srm = sr.get("metrics", {})
    if sr.get("covered"):
        roic_c = srm.get("roic")
        if roic_c:
            if roic_c["verdict"] == "well_above":
                green.append(f"Sector-leading returns on capital (ROIC ~{_pct(roic_c['value'])}% "
                             f"vs a ~{_pct(roic_c['median'])}% {sr['sector']} median).")
            elif roic_c["verdict"] == "well_below":
                red.append(f"ROIC ~{_pct(roic_c['value'])}% lags the {sr['sector']} median "
                           f"(~{_pct(roic_c['median'])}%) — a laggard within its own sector.")
        upside = val.get("upside_mid") if val.get("ok") else dcf.get("upside")
        mult = [c for c in (srm.get("trailing_pe"), srm.get("price_to_fcf")) if c]
        if upside is not None and upside >= 0.15 and mult:
            if any(c["verdict"] in ("above", "well_above") for c in mult):
                green.append("Genuinely cheap vs its sector peers, not just the market.")
            else:
                red.append("Looks cheap vs the market, but trades in line with (or above) its "
                           "sector — the discount may be a sector-wide headwind, not a "
                           "stock-specific bargain.")

    # Two independent free datasets disagreeing on recent fundamentals means the
    # fair value rests on shaky ground — surface it loudly (foreign filers only;
    # US 10-K names come from authoritative EDGAR and never trip this).
    div = (metrics.get("data_confidence") or {}).get("source_divergence")
    if div and div.get("material"):
        red.append(
            f"Data sources disagree materially ({div['primary']} vs {div['peer']} differ "
            f"~{round(div['max_divergence'] * 100)}% on recent revenue/earnings) — the "
            "fair value is unreliable; reconcile the filings before trusting any number.")

    # ---- Verdict thresholds ----
    if normalized >= BUY_THRESHOLD:
        rating, stance = "BUY", (
            "Meets the quality-at-a-fair-price bar for a 10-15yr hold. Strong "
            "fundamentals with a margin of safety and an inflation-beating expected return.")
    elif normalized >= HOLD_THRESHOLD:
        rating, stance = "HOLD / WATCH", (
            "A solid business but either the price offers limited margin of safety or "
            "one pillar is weak. Worth watching for a better entry or confirmation.")
    else:
        rating, stance = "AVOID", (
            "Falls short on quality, valuation, or durability for a decade-plus hold. "
            "Doesn't clear the bar of beating inflation with a margin of safety.")

    # Hard overrides that a value investor treats as near-disqualifying.
    override_upside = val.get("upside_mid") if val.get("ok") else dcf.get("upside")
    az = (fx.get("altman") or {}) if fx.get("applicable") else {}
    bm = (fx.get("beneish") or {}) if fx.get("applicable") else {}
    if val.get("suspect") and rating == "BUY":
        rating = "HOLD / WATCH"
        stance += " (Downgraded: the valuation is flagged unreliable — verify the data before trusting it.)"
    elif (az.get("distress") or bm.get("manipulator")) and rating == "BUY":
        rating = "HOLD / WATCH"
        why = "in Altman distress zone" if az.get("distress") else "flagged by Beneish M"
        stance += (f" (Downgraded: {why} — a decade-plus holder shouldn't buy through a "
                   "distress/manipulation signal, however cheap it looks.)")
    elif override_upside is not None and override_upside < -0.30 and rating == "BUY":
        rating = "HOLD / WATCH"
        stance += " (Downgraded: trades well above intrinsic value — wait for a pullback.)"
    elif div and div.get("material") and rating == "BUY":
        rating = "HOLD / WATCH"
        stance += (f" (Downgraded: {div['primary']} and {div['peer']} disagree ~"
                   f"{round(div['max_divergence'] * 100)}% on recent fundamentals — a fair "
                   "value you can't reconcile isn't a buy, however cheap it screens.)")

    return {
        "score": normalized,
        "rating": rating,
        "stance": stance,
        "pillars": pillars,
        "green_flags": green,
        "red_flags": red,
        "forensic_penalty": forensic_penalty,
        "strategy": strategy,
        "strategy_label": STRATEGIES[strategy]["label"],
    }
