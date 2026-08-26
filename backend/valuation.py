"""Quantitative valuation engine.

Turns normalized fundamentals into the numbers a long-term value investor
actually looks at: growth CAGRs, quality/returns metrics, balance-sheet
health, a discounted-cash-flow intrinsic value, and multiples vs the stock's
own history. Everything here is deterministic and explainable.
"""
from __future__ import annotations

import random
from typing import Any, Optional

import earnings_quality
import duediligence

# ---- Assumptions (defaults; overridable per-request from the frontend) ----
DEFAULT_DISCOUNT_RATE = 0.10       # required return / WACC proxy
DEFAULT_TERMINAL_GROWTH = 0.025    # long-run growth ~ GDP+inflation
DEFAULT_PROJECTION_YEARS = 10
# Growth-fade decay: each year the EXCESS growth (above terminal) retains this
# fraction, so abnormal growth reverts geometrically (competition erodes it) —
# a hot starter fades faster than a modest one. ~22%/yr decay, half-life ~2.8yr.
GROWTH_FADE = 0.78
INFLATION_HURDLE = 0.03            # "beat inflation" bar
MARGIN_OF_SAFETY = 0.25           # want price <= 75% of intrinsic value

DEFAULT_ASSUMPTIONS = {
    "discount_rate": DEFAULT_DISCOUNT_RATE,
    "terminal_growth": DEFAULT_TERMINAL_GROWTH,
    "projection_years": DEFAULT_PROJECTION_YEARS,
    "inflation_hurdle": INFLATION_HURDLE,
    "margin_of_safety": MARGIN_OF_SAFETY,
    "margin_normalization": 0.0,       # 0 = as-reported; 1 = revert to long-run avg margin
}


def resolve_assumptions(a: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Merge user overrides onto defaults and clamp to sane, self-consistent
    ranges (so e.g. terminal growth can never exceed the discount rate, which
    would blow up the Gordon terminal value)."""
    out = dict(DEFAULT_ASSUMPTIONS)
    for k in out:
        v = (a or {}).get(k)
        if v is not None:
            try:
                out[k] = float(v)
            except (TypeError, ValueError):
                pass
    out["discount_rate"] = min(max(out["discount_rate"], 0.03), 0.25)
    out["terminal_growth"] = min(max(out["terminal_growth"], 0.0), 0.04)
    if out["terminal_growth"] >= out["discount_rate"]:
        out["terminal_growth"] = round(out["discount_rate"] - 0.01, 4)
    out["projection_years"] = int(min(max(out["projection_years"], 3), 20))
    out["inflation_hurdle"] = min(max(out["inflation_hurdle"], 0.0), 0.15)
    out["margin_of_safety"] = min(max(out["margin_of_safety"], 0.0), 0.60)
    out["margin_normalization"] = min(max(out["margin_normalization"], 0.0), 1.0)
    # Strategy is a scoring-emphasis profile (a string), not a numeric assumption;
    # it rides along in the assumptions dict so it reaches scoring everywhere.
    import scoring
    out["strategy"] = scoring.resolve_strategy((a or {}).get("strategy"))
    return out


def _series(d: dict[str, Optional[float]]) -> list[tuple[int, float]]:
    """Turn a {year: value} dict into a sorted list of (year, value) pairs,
    dropping missing values."""
    out = []
    for k, v in d.items():
        if v is None:
            continue
        try:
            out.append((int(k), float(v)))
        except (TypeError, ValueError):
            continue
    return sorted(out)


def cagr(series: list[tuple[int, float]]) -> Optional[float]:
    """Compound annual growth rate from first to last point.

    Returns None when it can't be computed meaningfully (need positive
    endpoints and a real time span). Sign-flips (loss->profit) aren't valid
    CAGRs, so we guard for that.
    """
    pts = [(y, v) for y, v in series if v is not None]
    if len(pts) < 2:
        return None
    (y0, v0), (y1, v1) = pts[0], pts[-1]
    years = y1 - y0
    if years <= 0 or v0 <= 0 or v1 <= 0:
        return None
    return (v1 / v0) ** (1 / years) - 1


def _latest(d: dict[str, Optional[float]]) -> Optional[float]:
    s = _series(d)
    return s[-1][1] if s else None


def _avg(values: list[Optional[float]]) -> Optional[float]:
    nums = [v for v in values if v is not None]
    return sum(nums) / len(nums) if nums else None


def _normalized_base_fcf(fcf: list[tuple[int, float]],
                         ttm: Optional[float]) -> Optional[float]:
    """A stable base FCF for the DCF.

    Yahoo's `financialData.freeCashflow` (the TTM field) is notoriously wrong —
    it can be several-fold off the real figure. So we trust the cash-flow
    *statements* (OCF + capex) and normalize by averaging the last up-to-3
    positive years to smooth out one-off capex spikes. We only fall back to the
    TTM field when we have no statement data at all.
    """
    positives = [v for _, v in fcf if v is not None and v > 0]
    if positives:
        recent = positives[-3:]
        return sum(recent) / len(recent)
    if ttm and ttm > 0:
        return ttm
    return None


DEVELOPED = {"United States", "Canada", "United Kingdom", "Germany", "France",
             "Switzerland", "Netherlands", "Japan", "Australia", "Sweden",
             "Denmark", "Norway", "Finland", "Ireland", "Belgium", "Austria",
             "Singapore", "Hong Kong", "New Zealand", "Israel", "Italy", "Spain"}




def _earnings_stability(net_income: list, revenue: list) -> Optional[float]:
    """0 (erratic / loss-ridden) .. 1 (rock-steady) from the net-MARGIN history.

    Measured as scatter around the TREND, not raw dispersion: a business whose
    margins rise steadily (a strengthening compounder — LLY, NVDA) is predictable,
    not volatile, so it should read as stable. Fitting a line and scoring the
    residuals means a smooth up- (or down-) trend no longer reads as instability,
    while a margin that genuinely swings up and down still does. Loss years dock it."""
    ni, rev = dict(net_income), dict(revenue)
    years = sorted(y for y in rev if y in ni and rev[y])
    margins = [ni[y] / rev[y] for y in years]
    if len(margins) < 4:
        return None
    losses = sum(1 for m in margins if m < 0)
    mean = sum(margins) / len(margins)
    if mean <= 0:
        return 0.1
    import statistics
    n = len(margins)
    xs = list(range(n))
    mx = sum(xs) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    slope = (sum((xs[i] - mx) * (margins[i] - mean) for i in range(n)) / sxx) if sxx else 0.0
    intercept = mean - slope * mx
    resid = [margins[i] - (slope * xs[i] + intercept) for i in range(n)]
    # Detrended coefficient of variation: dispersion AROUND the trend line.
    cv = statistics.pstdev(resid) / mean
    score = (1.0 - min(cv, 1.5) / 1.5) - 0.15 * losses
    return max(0.0, min(1.0, score))


def certainty_score(stability: Optional[float], returns: Optional[dict],
                    balance: Optional[dict], years: Optional[int],
                    data_low: bool = False, debt_estimated: bool = False) -> float:
    """0 (unpredictable) .. 1 (fortress-certain): how knowable a business's future
    is, from earnings steadiness, returns on capital, balance-sheet strength, and
    length of record. Drives the certainty-scaled margin of safety (thesis
    principle 4) — the more certain, the less discount we need to demand."""
    returns, balance = returns or {}, balance or {}
    parts: list[float] = []
    if stability is not None:
        parts.append(stability)
    roic = returns.get("roic_avg") or returns.get("roic_latest")
    if roic is not None:
        parts.append(min(max((roic - 0.04) / 0.16, 0.0), 1.0))          # 4%→0, 20%→1
    de, nc, cov = balance.get("debt_to_equity"), balance.get("net_cash"), balance.get("interest_coverage")
    if nc is not None and nc > 0:
        bs = 0.9
    elif de is not None:
        bs = min(max(1.0 - de / 3.0, 0.0), 1.0)                          # D/E 0→1, 3→0
    else:
        bs = 0.5
    if cov is not None and cov < 3:
        bs = min(bs, 0.4)
    parts.append(bs)
    if years is not None:
        parts.append(min(max((years - 4) / 8.0, 0.0), 1.0))             # 4yr→0, 12yr→1
    c = sum(parts) / len(parts) if parts else 0.5
    if data_low or debt_estimated:
        c *= 0.7                                                         # can't be certain on shaky data
    return min(max(c, 0.0), 1.0)


def risk_premium(info: dict[str, Any], returns: Optional[dict] = None,
                 balance: Optional[dict] = None,
                 stability: Optional[float] = None) -> dict[str, Any]:
    """Signed adjustment to the discount rate for company-specific *fundamental*
    risk — the things that threaten a permanent loss of capital, which is what a
    value investor actually means by risk.

    Deliberately NOT beta. Price volatility ≠ business risk: a steady,
    fortress-balance-sheet compounder is *lower* risk than a volatile-but-sound
    one, and CAPM would say the opposite. Instead we adjust for balance-sheet
    fragility (leverage, thin interest coverage, offset by net cash), earnings
    instability, weak returns on capital, small size (durability), and
    emerging-market domicile (governance / rule of law). Bounded to [−3%, +5%]."""
    returns, balance = returns or {}, balance or {}
    prem, reasons = 0.0, []

    # --- Balance-sheet fragility (the #1 cause of permanent loss) ---
    # Leverage first; the net-cash credit requires GENUINELY low leverage, so an
    # understated debt tag (finance-arm debt at SO/F) can't spuriously earn a
    # "fortress" credit for a heavily-levered company.
    de = balance.get("debt_to_equity")
    net_cash = balance.get("net_cash")
    cov = balance.get("interest_coverage")
    if de is not None and de > 2.0:
        prem += 0.02; reasons.append(f"high leverage (D/E {de:.1f})")
    elif de is not None and de > 1.0:
        prem += 0.01; reasons.append(f"elevated leverage (D/E {de:.1f})")
    elif (net_cash is not None and net_cash > 0 and (de is None or de < 0.5)
          and (cov is None or cov > 8)):
        # A genuine fortress: net cash, low D/E, AND barely reliant on debt.
        # The coverage test blocks a spurious credit when a finance-arm debt tag
        # understates leverage (SO, F) but interest expense reveals the truth.
        prem -= 0.005; reasons.append("net cash (fortress balance sheet)")
    if cov is not None and 0 < cov < 3:
        prem += 0.015; reasons.append(f"thin interest coverage ({cov:.1f}×)")

    # --- Earnings instability = business risk ---
    if stability is not None:
        if stability < 0.4:
            prem += 0.015; reasons.append("erratic earnings")
        elif stability > 0.85:
            prem -= 0.005; reasons.append("very steady earnings")

    # --- Returns on capital = quality/durability of the economics ---
    roic = returns.get("roic_avg") or returns.get("roic_latest")
    if roic is not None:
        if roic < 0.06:
            prem += 0.01; reasons.append("weak returns on capital")
        elif roic > 0.20:
            prem -= 0.005; reasons.append("high returns on capital")

    # --- Size & jurisdiction (fundamental durability, not price) ---
    mc = info.get("market_cap") or 0
    if 0 < mc < 3e9:
        prem += 0.02; reasons.append("small cap")
    elif 0 < mc < 10e9:
        prem += 0.01; reasons.append("mid cap")
    country = info.get("country")
    if country and country not in DEVELOPED:
        prem += 0.02; reasons.append("emerging market")

    return {"premium": min(max(prem, -0.03), 0.05), "reasons": reasons}


def cyclical_peak_check(margins: dict[str, Any], returns: dict[str, Any]) -> dict[str, Any]:
    """Flag when current profitability is well above the company's own recent
    average — a sign earnings may be at a cyclical peak, not a durable level."""
    reasons = []
    nm_l, nm_a = margins["net"].get("latest"), margins["net"].get("avg")
    if nm_l and nm_a and nm_a > 0 and nm_l > nm_a * 1.3:
        reasons.append(f"net margin {nm_l*100:.0f}% vs {nm_a*100:.0f}% avg")
    roe_l, roe_a = returns.get("roe_latest"), returns.get("roe_avg")
    if roe_l and roe_a and roe_a > 0 and roe_l > roe_a * 1.35:
        reasons.append(f"ROE {roe_l*100:.0f}% vs {roe_a*100:.0f}% avg")
    return {"peak": bool(reasons), "reasons": reasons}


def compute_metrics(stock: dict[str, Any],
                    assumptions: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Compute the full quantitative picture under the given assumptions."""
    A = resolve_assumptions(assumptions)
    st = stock["statements"]
    info = stock["info"]

    revenue = _series(st["revenue"])
    net_income = _series(st["net_income"])
    eps = _series(st["eps"])
    ocf = _series(st["operating_cashflow"])
    capex = _series(st["capex"])
    sbc = _series(st.get("stock_based_comp", {}))

    # ---- Free cash flow series (OCF + capex - stock-based comp) ----
    # Operating cash flow adds SBC back as "non-cash", but SBC is a real cost to
    # owners (dilution). Subtracting it (guardrail) stops SBC-heavy names — many
    # tech companies — from looking cheaper than they are.
    fcf_by_year: dict[int, float] = {}
    ocf_map = dict(ocf)
    capex_map = dict(capex)
    sbc_map = dict(sbc)
    for year in set(ocf_map) | set(capex_map):
        o = ocf_map.get(year)
        c = capex_map.get(year)
        if o is not None:
            fcf_by_year[year] = o + (c or 0.0) - (sbc_map.get(year) or 0.0)
    fcf = sorted(fcf_by_year.items())
    # Fall back to yfinance's own FCF line if we couldn't derive it.
    if not fcf:
        fcf = _series(st["free_cashflow"])

    # ---- Growth ----
    growth = {
        "revenue_cagr": cagr(revenue),
        "net_income_cagr": cagr(net_income),
        "eps_cagr": cagr(eps),
        "fcf_cagr": cagr(fcf),
        "years_of_data": (revenue[-1][0] - revenue[0][0] + 1) if len(revenue) >= 2 else len(revenue),
    }

    # ---- Profitability / margins (latest + trend) ----
    def margin_series(numer: list[tuple[int, float]]) -> dict[str, Optional[float]]:
        rev_map = dict(revenue)
        m = {}
        for y, v in numer:
            r = rev_map.get(y)
            if r and r != 0:
                m[y] = v / r
        vals = [m[y] for y in sorted(m)]
        return {
            "latest": vals[-1] if vals else None,
            "avg": _avg(vals),
            "trend": (vals[-1] - vals[0]) if len(vals) >= 2 else None,
        }

    gross_profit_s = _series(st["gross_profit"])
    operating_income_s = _series(st["operating_income"])
    gross = margin_series(gross_profit_s)
    operating = margin_series(operating_income_s)
    net_margin = margin_series(net_income)

    # ---- Margin-normalization stress lever ----
    # Scale the earnings base that feeds the DCF toward the company's own long-run
    # average net margin: 0 = as-reported (no change), 1 = full reversion to the
    # average. Lets you ask "what's it worth if today's peak (or trough) margins
    # normalize?" — the DCF, scenarios, Monte-Carlo, expected return and score all
    # follow. Skipped when margins are non-positive or unavailable.
    mn = A.get("margin_normalization", 0.0)
    nm_latest, nm_avg = net_margin.get("latest"), net_margin.get("avg")
    margin_ratio, target_margin = 1.0, nm_latest
    if mn > 0 and nm_latest and nm_latest > 0 and nm_avg and nm_avg > 0:
        target_margin = nm_latest + (nm_avg - nm_latest) * mn
        margin_ratio = max(target_margin / nm_latest, 0.0)

    # ---- Data-sanity guard ----
    # A broken data feed (e.g. revenue picking up a small partial line) can produce
    # physically impossible fundamentals and a confident-looking but garbage
    # valuation. A sustained net margin above 100%, or non-positive revenue, is not
    # real — flag it so the valuation is treated as unreliable rather than a bargain.
    _rev_latest = revenue[-1][1] if revenue else None
    data_bad_reason = None
    if nm_latest is not None and abs(nm_latest) > 1.0:
        data_bad_reason = (f"implied net margin {nm_latest*100:.0f}% isn't physically "
                           "possible — the revenue figure looks wrong (bad data feed).")
    elif _rev_latest is not None and _rev_latest <= 0:
        data_bad_reason = "non-positive revenue — the data looks broken."

    # ---- Returns on capital (ROE, ROIC-ish) ----
    equity = dict(_series(st["total_equity"]))
    debt = dict(_series(st["total_debt"]))
    ni_map = dict(net_income)
    roe_by_year = {}
    roic_by_year = {}
    for y, ni in net_income:
        e = equity.get(y)
        if e and e > 0:
            roe_by_year[y] = ni / e
        invested = (e or 0) + (debt.get(y) or 0)
        if invested and invested > 0:
            roic_by_year[y] = ni / invested
    roe_vals = [roe_by_year[y] for y in sorted(roe_by_year)]
    roic_vals = [roic_by_year[y] for y in sorted(roic_by_year)]

    returns = {
        "roe_latest": roe_vals[-1] if roe_vals else info.get("return_on_equity"),
        "roe_avg": _avg(roe_vals),
        "roic_latest": roic_vals[-1] if roic_vals else None,
        "roic_avg": _avg(roic_vals),
    }

    # ---- Reconcile debt & cash to ONE figure everything uses ----
    # The card, the DCF (net cash added to equity value), due-diligence and
    # refinancing all need "how much debt / cash". They used to read different
    # sources — the card took EDGAR's total_debt (often financial debt only),
    # the DCF took Yahoo's info.total_debt (which folds in operating-lease
    # liabilities) — so a lease-heavy name (e.g. DECK) showed net cash on the
    # card that the DCF didn't credit. Take the MORE COMPLETE debt so we never
    # understate leverage (Yahoo catches leases EDGAR reports separately; EDGAR's
    # hardening catches finance-arm debt Yahoo misses), then write it back to
    # info so every downstream consumer agrees.
    _edgar_debt, _yahoo_debt = _latest(st["total_debt"]), info.get("total_debt")
    _debts = [d for d in (_edgar_debt, _yahoo_debt) if d is not None]
    recon_debt = max(_debts) if _debts else None
    recon_cash = _latest(st["cash"])
    if recon_cash is None:
        recon_cash = info.get("total_cash")
    info["total_debt"] = recon_debt
    info["total_cash"] = recon_cash

    # ---- Reconcile the share count ----
    # Yahoo's `shares_outstanding` field is often stale; when it disagrees with the
    # live market_cap / price, that mismatch silently inflates (or deflates) EVERY
    # per-share figure — EPS, the DCF, the earnings-power value. A stale-LOW count is
    # the dangerous case (it inflates per-share value into false upside — e.g. AOS
    # showed 110M shares vs 136M implied, a fake +94%). Take the LARGER of the two:
    # market_cap/price is derived from two live fields, and the bigger count is the
    # more conservative per-share value. Write it back so every consumer agrees.
    _rep_sh = info.get("shares_outstanding")
    _mc, _px = info.get("market_cap"), info.get("current_price")
    _impl_sh = (_mc / _px) if (_mc and _px and _px > 0) else None
    _shs = [s for s in (_rep_sh, _impl_sh) if s and s > 0]
    if _shs:
        info["shares_outstanding"] = max(_shs)

    # ---- Balance sheet health ----
    latest_debt = recon_debt
    latest_equity = _latest(st["total_equity"])
    latest_cash = recon_cash
    latest_op_income = _latest(st["operating_income"])
    latest_interest = _latest(st["interest_expense"])
    cur_assets = _latest(st["current_assets"])
    cur_liab = _latest(st["current_liabilities"])

    balance = {
        "debt_to_equity": (latest_debt / latest_equity)
        if (latest_debt is not None and latest_equity and latest_equity > 0) else None,
        "current_ratio": (cur_assets / cur_liab)
        if (cur_assets and cur_liab and cur_liab > 0) else None,
        "interest_coverage": (latest_op_income / abs(latest_interest))
        if (latest_op_income and latest_interest and latest_interest != 0) else None,
        "net_cash": (latest_cash - latest_debt)
        if (latest_cash is not None and latest_debt is not None) else None,
        "cash": latest_cash,
        "total_debt": latest_debt,
    }

    # ---- Earnings quality & capex-cycle analysis ----
    eq = earnings_quality.analyze(st, info)

    # ---- Normalized base FCF (shared by DCF + expected return) ----
    base_fcf = _normalized_base_fcf(fcf, info.get("free_cashflow_ttm"))
    if base_fcf and margin_ratio != 1.0:
        base_fcf *= margin_ratio

    # Guardrail 3: with only a short history, extrapolate growth more cautiously.
    yrs = growth.get("years_of_data") or 0
    max_g = 0.12 if yrs >= 5 else 0.10

    # Guardrail 4: risk-adjust the discount rate by *fundamental* risk (balance
    # sheet, earnings stability, returns on capital) — not beta — so a fragile
    # business clears a higher bar and a fortress compounder a lower one.
    stability = _earnings_stability(net_income, revenue)
    rp = risk_premium(info, returns=returns, balance=balance, stability=stability)
    # Risk-adjusted discount rate, floored at 6% (a defensive name still can't be
    # discounted below a sane minimum) and capped at 25%.
    eff_discount = min(max(A["discount_rate"] + rp["premium"], 0.06), 0.25)

    # Guardrail 4b: margin of safety scales with certainty (thesis principle 4).
    # A fortress-certain compounder can be bought closer to fair value; a
    # cyclical / levered / short-record name must be genuinely cheap. The user's
    # setting anchors it; certainty tightens or widens the required discount.
    base_mos = A["margin_of_safety"]
    data_low = bool((stock.get("source_divergence") or {}).get("material"))
    debt_est = bool(stock.get("debt_estimated"))
    certainty = certainty_score(stability, returns, balance,
                                growth.get("years_of_data"), data_low, debt_est)
    effective_mos = min(max(base_mos + (0.5 - certainty) * 0.30, 0.12), 0.45)
    A["margin_of_safety"] = effective_mos                 # all downstream calcs use it
    mos_reasons: list[str] = []
    roic_c = returns.get("roic_avg") or returns.get("roic_latest")
    if effective_mos < base_mos - 0.005:                 # tightened — high certainty
        if stability is not None and stability > 0.7: mos_reasons.append("steady earnings")
        if balance.get("net_cash") and balance["net_cash"] > 0: mos_reasons.append("net cash")
        if roic_c is not None and roic_c > 0.15: mos_reasons.append("high returns on capital")
        if (growth.get("years_of_data") or 0) >= 12: mos_reasons.append("long track record")
    elif effective_mos > base_mos + 0.005:               # widened — low certainty
        if stability is not None and stability < 0.4: mos_reasons.append("erratic earnings")
        de_c = balance.get("debt_to_equity")
        if de_c is not None and de_c > 1.5: mos_reasons.append("elevated leverage")
        cov_c = balance.get("interest_coverage")
        if cov_c is not None and cov_c < 3: mos_reasons.append("thin interest coverage")
        if (growth.get("years_of_data") or 0) < 6: mos_reasons.append("short record")
        if debt_est: mos_reasons.append("debt figures estimated")

    # ---- DCF #1: conservative, on free cash flow (penalizes all capex) ----
    dcf = discounted_cash_flow(
        base_fcf=base_fcf,
        info=info,
        revenue_cagr=growth["revenue_cagr"],
        fcf_cagr=growth["fcf_cagr"],
        discount_rate=eff_discount,
        terminal_growth=A["terminal_growth"],
        years=A["projection_years"],
        margin_of_safety=A["margin_of_safety"],
        max_stage1_growth=max_g,
    )

    # ---- DCF #2: adjusted, on owner earnings (credits growth capex) ----
    # Grow owner earnings at an earnings-based rate (net income / EPS), which is
    # the right driver for an earnings-power measure.
    oe_growth_candidates = [g for g in (growth["net_income_cagr"],
                                        growth["eps_cagr"],
                                        growth["revenue_cagr"]) if g is not None]
    oe_growth = (sum(oe_growth_candidates) / len(oe_growth_candidates)
                 ) if oe_growth_candidates else None
    # Base: owner earnings normally; but for financials/REITs (guardrail 2) their
    # capex/D&A are meaningless, so value on normalized net income directly.
    base_ni = _normalized_base_fcf(net_income, None)
    is_fin = needs_earnings_valuation(info)
    earnings_base = base_ni if is_fin else (eq.get("owner_earnings") or base_ni)
    if earnings_base and margin_ratio != 1.0:
        earnings_base *= margin_ratio
    dcf_owner = discounted_cash_flow(
        base_fcf=earnings_base,
        info=info,
        revenue_cagr=growth["revenue_cagr"],
        fcf_cagr=growth["net_income_cagr"],
        discount_rate=eff_discount,
        terminal_growth=A["terminal_growth"],
        years=A["projection_years"],
        margin_of_safety=A["margin_of_safety"],
        growth_estimate=oe_growth,
        max_stage1_growth=max_g,
    )

    # ---- Valuation range: the width IS the capex distortion ----
    valuation = build_valuation_range(dcf, dcf_owner, info, A["margin_of_safety"])

    # For financials (banks/insurers/brokers), replace the meaningless FCF/earnings
    # DCF with the justified price-to-book model (book value x sustainable ROE) —
    # the method that actually fits them. Through-cycle average ROE keeps a
    # cyclical peak from inflating the value.
    fin_valuation = None
    reit_valuation = None
    price = info.get("current_price")
    is_reit = needs_ffo_valuation(info)

    # REITs: value on FFO (net income + real-estate depreciation), discounted as
    # an equity-level stream (no net-cash add-back — FFO is already post-interest).
    if is_reit:
        dep_map = {p["year"]: p["value"] for p in eq.get("series", {}).get("depreciation", [])}
        ffo = sorted((y, ni + dep_map[y]) for y, ni in net_income if dep_map.get(y) is not None)
        base_ffo = _normalized_base_fcf(ffo, None)
        reit_shares = (info.get("shares_outstanding")
                       or ((info["market_cap"] / price) if (info.get("market_cap") and price) else None))
        if base_ffo and base_ffo > 0 and reit_shares:
            ffo_g = cagr(ffo)
            reit_growth = max(min(ffo_g if ffo_g is not None else 0.03, 0.08), 0.0)
            rdcf = discounted_cash_flow(base_ffo, info, ffo_g, ffo_g, eff_discount,
                                        A["terminal_growth"], A["projection_years"],
                                        A["margin_of_safety"], growth_estimate=reit_growth,
                                        max_stage1_growth=0.08, add_net_cash=False)
            iv = rdcf.get("intrinsic_value_per_share") if rdcf.get("ok") else None
            if iv and iv > 0:
                ffo_ps = base_ffo / reit_shares
                up = ((iv - price) / price) if price else None
                suspect = up is not None and up > 1.0
                reit_valuation = {
                    "ok": True, "method": "ffo", "is_financial": True,
                    "low": iv, "high": iv, "mid": iv, "spread": 0.0,
                    "conservative_iv": iv, "adjusted_iv": iv, "current_price": price,
                    "upside_low": up, "upside_high": up, "upside_mid": up,
                    "buy_below": iv * (1 - A["margin_of_safety"]),
                    "margin_of_safety": A["margin_of_safety"], "suspect": bool(suspect),
                    "suspect_reason": (f"Implied upside ~{up*100:.0f}% is implausibly high — verify the FFO inputs." if suspect else None),
                    "ffo_per_share": ffo_ps, "ffo_total": base_ffo,
                    "current_pffo": (price / ffo_ps) if (price and ffo_ps) else None,
                    "fair_pffo": (iv / ffo_ps) if ffo_ps else None,
                    "ffo_growth": rdcf.get("assumptions", {}).get("stage1_growth"),
                }
                valuation = reit_valuation

    if is_fin and not is_reit:
        import financials
        fin_shares = (info.get("shares_outstanding")
                      or ((info["market_cap"] / price) if (info.get("market_cap") and price) else None))
        # Sustainable ROE = median of the last ~7 years: current-regime (not
        # dragged by a 2008 that may never recur) yet smoothed against a single
        # peak/trough. Falls back to the full-history average.
        _roe_recent = [roe_by_year[y] for y in sorted(roe_by_year)][-7:]
        if _roe_recent:
            _rs = sorted(_roe_recent); _n = len(_rs)
            fin_roe = _rs[_n // 2] if _n % 2 else (_rs[_n // 2 - 1] + _rs[_n // 2]) / 2
        else:
            fin_roe = returns.get("roe_avg") or returns.get("roe_latest")
        fv = financials.value(latest_equity, fin_shares, fin_roe, eff_discount,
                              A["terminal_growth"], price, A["margin_of_safety"])
        # The P/B model only fits balance-sheet financials, where book value ≈
        # economic capital. Capital-light "financials" — payment networks (V, MA),
        # exchanges, capital-light fintechs — earn enormous ROE on tiny equity, so
        # book value is meaningless (Mastercard: 156% ROE, 64x book). Detect them
        # (very high ROE AND very high price/book) and keep the earnings valuation.
        capital_light = (fin_roe is not None and fin_roe > 0.35
                         and fv.get("current_pb") is not None and fv["current_pb"] > 8)
        if fv.get("ok") and not capital_light:
            fv["roe_basis"] = "7yr median"
            valuation = fv
            fin_valuation = fv

    if data_bad_reason:  # data-sanity guard overrides any apparent bargain
        valuation["suspect"] = True
        valuation["suspect_reason"] = "Data quality: " + data_bad_reason
        valuation["data_quality_bad"] = True

    # ---- Earnings-power floor for mature, wide-moat compounders ----
    # A strict DCF misprices these on the LOW side (a depressed trailing growth
    # rate x a full equity discount x a Gordon terminal -> an artifact fair value
    # at 3-10x earnings). When the business is genuinely high-return and stable AND
    # the DCF sits well below its justified-P/E earnings-power value, the DCF is the
    # artifact, not a real bear case — value it on earnings power instead. Operating
    # companies only; financials/REITs keep their own models.
    earnings_power_val = None
    if (not fin_valuation and not reit_valuation
            and valuation.get("ok") and not valuation.get("suspect")):
        import earnings_power
        # The multiple keys off through-cycle return on equity, but capped at 2x ROIC
        # so a leverage- or goodwill-inflated ROE can't buy a premium multiple: the
        # capped return credits genuine economics while pulling a heavily-levered name
        # back toward its true return on capital. This lets capital-intensive wide-
        # moats (e.g. Waste Management: 22% ROE, 8% ROIC) be valued conservatively
        # rather than left showing a -95% DCF artifact.
        _epv_roe = returns.get("roe_avg")
        _epv_roic = returns.get("roic_avg")
        _epv_return = (min(_epv_roe, 2 * _epv_roic)
                       if (_epv_roe is not None and _epv_roic is not None) else None)
        _epv_shares = (info.get("shares_outstanding")
                       or ((info["market_cap"] / price) if (info.get("market_cap") and price) else None))
        # P/E model -> value NORMALIZED net income per share. Normally the 3-yr
        # average (margin-adjusted), so a name in a cyclical earnings TROUGH (TXN,
        # SBUX) isn't valued on a depressed year — that would understate the floor
        # exactly when it's needed most.
        _epv_ni = base_ni * margin_ratio if (base_ni and margin_ratio) else base_ni
        # BUT a name earning BELOW its 3-yr average with HEALTHY (not depressed)
        # margins is fading from a demand boom, not sitting in a trough — the
        # average is inflated by boom years it can't repeat (POOL: COVID pool-
        # building), so value it on the latest actual instead. A margin trough
        # (latest margin below the through-cycle average) keeps the average, since
        # depressed margins mean-revert UP.
        _latest_ni = net_income[-1][1] if net_income else None
        _nm_l, _nm_a = net_margin.get("latest"), net_margin.get("avg")
        if (_epv_ni and _latest_ni and _epv_ni > _latest_ni > 0
                and _nm_l is not None and _nm_a is not None and _nm_l >= _nm_a):
            _epv_ni = _latest_ni
        _epv_eps = (_epv_ni / _epv_shares) if (_epv_ni and _epv_shares and _epv_ni > 0) else None
        # A proven high-ROE, stable, mature compounder is a low-business-risk,
        # bond-like stream; its required return belongs in ~7-10%, so don't let a
        # leverage-driven discount (which crushes the justified multiple) exceed 10%.
        _epv_disc = min(max(eff_discount, 0.07), 0.10)
        _epv_mature = (growth.get("years_of_data") or 0) >= 7
        _epv_stable = stability is not None and stability >= 0.5
        # Eligible when the business earns a real return on capital (ROIC >= 7%) and
        # a decent leverage-capped return (>= 12%), and is stable and mature.
        if (_epv_eps and _epv_eps > 0 and _epv_stable and _epv_mature
                and _epv_roic and _epv_roic >= 0.07
                and _epv_return and _epv_return >= 0.12):
            _epv = earnings_power.value(_epv_eps, _epv_return, _epv_disc, oe_growth,
                                        price, A["margin_of_safety"])
            # Floor: step in only when the DCF is materially BELOW the earnings-power
            # value (>20%) — that gap is the artifact. A DCF near or above it stands.
            if (_epv.get("ok") and valuation.get("mid")
                    and valuation["mid"] < _epv["mid"] * 0.80):
                earnings_power_val = _epv
                valuation = _epv

        # Low-multiple artifact guard (the strict-gate complement): a name the
        # earnings-power floor doesn't cover — a capital-intensive wide-moat below
        # the ROIC gate (WM), or a hyper-growth name whose margin regime-change reads
        # as unstable (LLY) — can still get an artifact-low DCF. A profitable, mature
        # business that is stable OR growing almost never has a real fair value below
        # ~7x normalized earnings, so when the DCF says that, flag it low-confidence
        # rather than present it as a real bear case. (A genuinely SHRINKING business
        # can deserve a low multiple, so decline is the one case left un-flagged.)
        # This does NOT hand out a premium multiple; it just stops a -95% artifact
        # being trusted (and gives a neutral valuation score, not a false 'priced for
        # perfection').
        if (earnings_power_val is None and valuation.get("ok")
                and valuation.get("method") == "dcf-range" and not valuation.get("suspect")
                and _epv_eps and _epv_eps > 0 and _epv_mature
                and (_epv_stable or (oe_growth and oe_growth > 0.05))
                and valuation.get("mid") and valuation["mid"] / _epv_eps < 7.0):
            valuation["suspect"] = True
            valuation["low_multiple_artifact"] = True
            valuation["suspect_reason"] = (
                f"DCF fair value implies only ~{valuation['mid'] / _epv_eps:.0f}x normalized "
                "earnings — implausibly low for a profitable, mature business, so it's "
                "likely a growth-extrapolation artifact, not a real bear case. Treated as "
                "low-confidence (the earnings-power model doesn't cover this business type).")

    # ---- Multiples vs the stock's own recent history ----
    multiples = compute_multiples(stock, eps, fcf)
    # Trailing-PEG proxy = trailing P/E ÷ historical EPS growth (%). A backward-
    # looking stand-in for PEG when forward analyst estimates aren't available.
    _pe, _epsg = multiples.get("trailing_pe"), growth.get("eps_cagr")
    multiples["peg_trailing"] = (_pe / (_epsg * 100)) if (_pe and _epsg and _epsg > 0) else None

    # ---- Expected long-term return vs inflation hurdle ----
    # Use the range-midpoint intrinsic value so heavy-capex names are neither
    # unfairly punished (depressed FCF) nor flattered (lagging depreciation).
    expected_return = expected_annual_return(stock, dcf, growth, base_fcf,
                                             inflation_hurdle=A["inflation_hurdle"],
                                             intrinsic_override=valuation.get("mid"))

    # ---- Cyclical-peak check (guardrail: durable vs peak earnings) ----
    cyclical = cyclical_peak_check({"gross": gross, "operating": operating, "net": net_margin},
                                   returns)

    # ---- Extended due-diligence metrics + scenarios + reverse DCF ----
    dd = duediligence.analyze(st, info, fcf, stock.get("price_history"))
    val_base_cf = earnings_base if valuation.get("is_financial") else base_fcf
    base_growth = (dcf_owner if valuation.get("is_financial") else dcf) \
        .get("assumptions", {}).get("stage1_growth", 0.05)
    # Earnings-decline bear: only when the tool has flagged a possible cyclical
    # peak (elevated margin or ROE vs the company's own history) AND current margins
    # actually sit above the through-cycle average — so the row appears exactly when
    # the PEAK badge does, and never on a stable name whose margin merely wobbled a
    # point (e.g. WMT). The haircut rebases proportionally to the average margin: a
    # mild premium cuts little, a steep one cuts more (capped at 50%).
    _nm_l, _nm_a = net_margin.get("latest"), net_margin.get("avg")
    if cyclical.get("peak") and _nm_l and _nm_a and _nm_a > 0 and _nm_l > _nm_a:
        _haircut = max(_nm_a / _nm_l, 0.50)
        _dec_note = (f"earnings rebased to the {_nm_a*100:.0f}% through-cycle net "
                     f"margin (now {_nm_l*100:.0f}%)")
    else:
        _haircut, _dec_note = None, None
    scenarios = scenario_values(val_base_cf, info, base_growth, eff_discount,
                                A["terminal_growth"], A["projection_years"],
                                A["margin_of_safety"],
                                decline_haircut=_haircut, decline_note=_dec_note)
    reverse = reverse_dcf(val_base_cf, info, eff_discount, A["terminal_growth"],
                          A["projection_years"])
    sensitivity = sensitivity_grid(val_base_cf, info, base_growth, eff_discount,
                                   A["terminal_growth"], A["projection_years"])
    monte_carlo = monte_carlo_dcf(val_base_cf, info, base_growth, eff_discount,
                                  A["terminal_growth"], A["projection_years"])

    # REITs: run scenarios / Monte-Carlo / reverse on FFO, equity-level (no net cash).
    if reit_valuation:
        _ffo, _g = reit_valuation["ffo_total"], (reit_valuation["ffo_growth"] or 0.04)
        scenarios = scenario_values(_ffo, info, _g, eff_discount, A["terminal_growth"],
                                    A["projection_years"], A["margin_of_safety"], add_net_cash=False,
                                    decline_haircut=0.80, decline_note="a 20% FFO stress (occupancy/rent downturn)")
        reverse = reverse_dcf(_ffo, info, eff_discount, A["terminal_growth"],
                              A["projection_years"], add_net_cash=False)
        sensitivity = sensitivity_grid(_ffo, info, _g, eff_discount, A["terminal_growth"],
                                       A["projection_years"], add_net_cash=False)
        monte_carlo = monte_carlo_dcf(_ffo, info, _g, eff_discount, A["terminal_growth"],
                                      A["projection_years"], add_net_cash=False)

    # Financials: flex ROE & required return (not cash-flow growth) for scenarios
    # and Monte-Carlo, and report the ROE the price implies instead of a growth rate.
    if fin_valuation:
        import financials
        _bvps, _roe = fin_valuation["bvps"], fin_valuation["roe_used"]
        scenarios = financials.scenarios(_bvps, _roe, eff_discount, A["terminal_growth"], price)
        monte_carlo = financials.monte_carlo(_bvps, _roe, eff_discount, A["terminal_growth"], price)
        sensitivity = {"ok": False}  # the DCF discount×growth grid doesn't apply
        reverse = {"ok": True, "method": "book-value",
                   "implied_roe": fin_valuation.get("implied_roe")}

    # Earnings-power names: run scenarios / Monte-Carlo / sensitivity / reverse on
    # the justified-P/E model so the whole panel matches the headline valuation
    # (otherwise it would show the artifact-low DCF numbers under an EPV headline).
    if earnings_power_val:
        import earnings_power
        _e, _re = earnings_power_val["eps_used"], earnings_power_val["return_used"]
        _rd = earnings_power_val["cost_of_equity"]   # the capped EPV discount rate
        scenarios = earnings_power.scenarios(_e, _re, _rd, oe_growth, price)
        monte_carlo = earnings_power.monte_carlo(_e, _re, _rd, oe_growth, price)
        sensitivity = earnings_power.sensitivity(_e, _re, _rd, oe_growth, price)
        reverse = earnings_power.reverse(_e, _re, _rd, oe_growth, price)

    # ---- Forensic scores: Altman Z (distress) + Beneish M (manipulation) ----
    import forensics  # lazy: forensics imports this module (needs_earnings_valuation)
    forensic_scores = forensics.analyze(st, info)

    # ---- Debt maturities & refinancing risk ----
    import refinancing
    refin = refinancing.assess(st, info)

    # ---- Working-capital quality (receivables/inventory vs sales) ----
    import working_capital
    workcap = working_capital.assess(st, info)

    # ---- Covenant / leverage-trend deterioration ----
    import leverage_trend
    levtrend = leverage_trend.assess(st, info)

    # ---- Dividend coverage from free cash flow ----
    import dividend_coverage
    divcov = dividend_coverage.assess(st, info)

    # ---- Acquisition-accounting / goodwill-impairment risk ----
    import intangibles
    intang = intangibles.assess(st, info)

    # ---- Sector-relative context (is this good *for this sector*?) ----
    import sector_benchmarks as _sb
    _sector = info.get("sector")
    _sr = {}
    for _k, _v in (("roic", returns.get("roic_avg") or returns.get("roic_latest")),
                   ("net_margin", net_margin.get("avg")),
                   ("revenue_cagr", growth.get("revenue_cagr")),
                   ("trailing_pe", multiples.get("trailing_pe")),
                   ("price_to_fcf", multiples.get("price_to_fcf"))):
        _c = _sb.compare(_sector, _k, _v)
        if _c:
            _sr[_k] = _c
    sector_relative = {"sector": _sector, "metrics": _sr,
                       "covered": bool(_sb.sector_median(_sector, "roic"))}

    return {
        "assumptions_used": A,
        "risk_premium": rp,
        "effective_discount_rate": eff_discount,
        "cyclical_peak": cyclical,
        "due_diligence": dd,
        "scenarios": scenarios,
        "reverse_dcf": reverse,
        "sensitivity": sensitivity,
        "monte_carlo": monte_carlo,
        "forensics": forensic_scores,
        "refinancing": refin,
        "working_capital": workcap,
        "leverage_trend": levtrend,
        "dividend_coverage": divcov,
        "intangibles": intang,
        "sector_relative": sector_relative,
        # Confidence gate: CAGRs, medians and the DCF need a few years to be
        # stable. Thin history (foreign filers on Yahoo, recent IPOs) is flagged
        # so a sparse-data score isn't over-trusted.
        "margin_of_safety_scaling": {
            "base": base_mos, "effective": effective_mos,
            "certainty": round(certainty, 2), "reasons": mos_reasons,
        },
        "data_confidence": {
            "years": growth.get("years_of_data"),
            "low": ((growth.get("years_of_data") or 0) < 6
                    or bool((stock.get("source_divergence") or {}).get("material"))
                    or bool(stock.get("debt_estimated"))),
            # Set when the two free datasets (SimFin vs Yahoo) disagree on recent
            # fundamentals — a foreign-filer signal that fair value is shaky.
            "source_divergence": stock.get("source_divergence"),
            # Set when the filer's debt tags were understated (finance-arm debt)
            # and total debt had to be estimated from interest expense — leverage
            # / net-cash figures are approximate.
            "debt_estimated": bool(stock.get("debt_estimated")),
        },
        "margin_normalization": {
            "factor": mn, "ratio": margin_ratio, "applied": margin_ratio != 1.0,
            "latest_margin": nm_latest, "avg_margin": nm_avg,
            "target_margin": target_margin,
        },
        "earnings_quality": eq,
        "dcf_owner": dcf_owner,
        "valuation": valuation,
        "growth": growth,
        "margins": {"gross": gross, "operating": operating, "net": net_margin},
        "returns": returns,
        "balance": balance,
        "dcf": dcf,
        "multiples": multiples,
        "expected_return": expected_return,
        "series": {  # for charts on the frontend
            "revenue": [{"year": y, "value": v} for y, v in revenue],
            "net_income": [{"year": y, "value": v} for y, v in net_income],
            "eps": [{"year": y, "value": v} for y, v in eps],
            "fcf": [{"year": y, "value": v} for y, v in fcf],
            "roe": [{"year": y, "value": roe_by_year[y]} for y in sorted(roe_by_year)],
            "roic": [{"year": y, "value": roic_by_year[y]} for y in sorted(roic_by_year)],
            "gross_margin": _margin_by_year(gross_profit_s, revenue),
            "operating_margin": _margin_by_year(operating_income_s, revenue),
            "net_margin": _margin_by_year(net_income, revenue),
        },
    }


def _margin_by_year(numer: list, revenue: list) -> list:
    """[{year, value}] of numer ÷ revenue per year — for margin-trend charts."""
    rev = dict(revenue)
    return [{"year": y, "value": v / rev[y]} for y, v in numer if rev.get(y)]


def discounted_cash_flow(
    base_fcf: Optional[float],
    info: dict[str, Any],
    revenue_cagr: Optional[float],
    fcf_cagr: Optional[float],
    discount_rate: float = DEFAULT_DISCOUNT_RATE,
    terminal_growth: float = DEFAULT_TERMINAL_GROWTH,
    years: int = DEFAULT_PROJECTION_YEARS,
    margin_of_safety: float = MARGIN_OF_SAFETY,
    growth_estimate: Optional[float] = None,
    max_stage1_growth: float = 0.12,
    add_net_cash: bool = True,
) -> dict[str, Any]:
    """A two-stage cash-flow-to-equity DCF.

    Growth is estimated from history but clamped to sane bounds so a stock
    with a freak 60% CAGR doesn't get an absurd valuation. Terminal value uses
    the Gordon growth model. Net cash is added back to get equity value.
    `base_fcf` is the normalized starting cash flow (free cash flow, or owner
    earnings for the adjusted run). Pass `growth_estimate` to override the
    history-blended growth (e.g. use earnings growth for an owner-earnings DCF).
    """
    if not base_fcf or base_fcf <= 0:
        return {
            "ok": False,
            "reason": "No positive cash flow to project — DCF not meaningful "
                      "for this company (common for pre-profit or heavy-capex firms).",
            "intrinsic_value_per_share": None,
        }

    # Blend historical growth, then clamp (unless an explicit estimate is given).
    if growth_estimate is not None:
        est_growth = growth_estimate
    else:
        growth_candidates = [g for g in (fcf_cagr, revenue_cagr) if g is not None]
        est_growth = (sum(growth_candidates) / len(growth_candidates)) if growth_candidates else 0.05
    # Fade high growth toward terminal; never negative in stage 1, cap tightly
    # (guardrail 3) so a short, hot history doesn't extrapolate into fantasy.
    stage1_growth = max(min(est_growth, max_stage1_growth), 0.0)

    # Project and discount, fading growth from stage1 -> terminal. The EXCESS
    # growth (above terminal) decays GEOMETRICALLY, not linearly: abnormally high
    # growth reverts fast, so a hot starter fades quicker than a modest one —
    # which reins in the classic DCF over-valuation of high-growth names. Year 1
    # keeps the full starting growth; the fade begins in year 2.
    excess = stage1_growth - terminal_growth
    pv_sum = 0.0
    projected = []
    fcf_t = base_fcf
    for t in range(1, years + 1):
        g = terminal_growth + excess * (GROWTH_FADE ** (t - 1))
        fcf_t = fcf_t * (1 + g)
        pv = fcf_t / ((1 + discount_rate) ** t)
        pv_sum += pv
        projected.append({"year": t, "fcf": fcf_t, "pv": pv, "growth": g})

    # Terminal value (Gordon) on final-year FCF, then discounted back.
    terminal_fcf = fcf_t * (1 + terminal_growth)
    terminal_value = terminal_fcf / (discount_rate - terminal_growth)
    pv_terminal = terminal_value / ((1 + discount_rate) ** years)

    enterprise_value = pv_sum + pv_terminal
    # FFO/owner-earnings that are already equity-level (post-interest) must NOT
    # get the debt subtracted again — REITs pass add_net_cash=False.
    net_cash = ((info.get("total_cash") or 0) - (info.get("total_debt") or 0)) if add_net_cash else 0.0
    equity_value = enterprise_value + net_cash

    shares = info.get("shares_outstanding")
    if not shares or shares <= 0:
        mc = info.get("market_cap")
        price = info.get("current_price")
        shares = (mc / price) if (mc and price) else None

    iv_per_share = (equity_value / shares) if shares else None
    price = info.get("current_price")

    upside = None
    if iv_per_share and price:
        upside = (iv_per_share - price) / price

    return {
        "ok": True,
        "assumptions": {
            "base_fcf": base_fcf,
            "stage1_growth": stage1_growth,
            "terminal_growth": terminal_growth,
            "discount_rate": discount_rate,
            "years": years,
            "margin_of_safety": margin_of_safety,
        },
        "pv_of_cashflows": pv_sum,
        "pv_of_terminal": pv_terminal,
        # How much of the value rests on the (most assumption-sensitive) terminal
        # value vs the explicitly-projected years.
        "terminal_pct": (pv_terminal / enterprise_value) if enterprise_value else None,
        "enterprise_value": enterprise_value,
        "equity_value": equity_value,
        "intrinsic_value_per_share": iv_per_share,
        "current_price": price,
        "upside": upside,
        "margin_of_safety_price": (iv_per_share * (1 - margin_of_safety)) if iv_per_share else None,
        "projection": projected,
    }


FINANCIAL_INDUSTRY_WORDS = ("bank", "insurance", "insurer", "capital markets",
                            "asset management", "reit", "mortgage", "financial")


# Capital-light "financials" that carry trivial balance sheets relative to their
# earnings — exchanges, index/data/ratings shops, insurance brokers. Book value is
# meaningless for them (a justified-P/B model badly under-prices SPGI, ICE, CME),
# so value them as ordinary operating companies (DCF -> earnings-power) instead.
# NB: "credit services" is deliberately NOT here — it mixes capital-light networks
# (V, MA) with balance-sheet lenders (SYF, COF); the ROE/P-B capital-light escape
# in compute_metrics separates those.
CAPITAL_LIGHT_FIN_INDUSTRIES = ("financial data & stock exchanges", "insurance brokers")


def needs_earnings_valuation(info: dict[str, Any]) -> bool:
    """Guardrail 2: banks/insurers/REITs don't have meaningful 'free cash flow' —
    an FCF-DCF wildly misprices them. Value these on earnings power instead."""
    sec = (info.get("sector") or "").lower()
    ind = (info.get("industry") or "").lower()
    if any(w in ind for w in CAPITAL_LIGHT_FIN_INDUSTRIES):
        return False  # operating-company path, not book value
    if "financial" in sec or "real estate" in sec:
        return True
    return any(w in ind for w in FINANCIAL_INDUSTRY_WORDS)


def needs_ffo_valuation(info: dict[str, Any]) -> bool:
    """REITs: GAAP earnings are crushed by real-estate depreciation and book value
    understates the property, so both a cash-flow DCF and a price-to-book model
    mislead. The right measure is FFO (funds from operations = net income + real-
    estate D&A) and a P/FFO / FFO-discount valuation."""
    sec = (info.get("sector") or "").lower()
    ind = (info.get("industry") or "").lower()
    return "real estate" in sec or "reit" in ind


# Above this implied upside, a DCF is almost always a data/extrapolation artifact
# rather than a real bargain (a liquid large-cap rarely trades at half its
# conservative fair value) — flag it so scoring distrusts it.
SUSPECT_UPSIDE = 1.0


def build_valuation_range(dcf: dict[str, Any], dcf_owner: dict[str, Any],
                          info: dict[str, Any], margin_of_safety: float) -> dict[str, Any]:
    """Combine the conservative (FCF) and adjusted (owner-earnings) DCFs into a
    range. The midpoint drives scoring; the width signals capex sensitivity.

    Guardrails: financials are valued on earnings only (FCF-DCF invalid); a
    valuation is marked `suspect` when the implied upside is implausibly large
    or the currency couldn't be resolved."""
    iv_fcf = dcf.get("intrinsic_value_per_share") if dcf.get("ok") else None
    iv_owner = dcf_owner.get("intrinsic_value_per_share") if dcf_owner.get("ok") else None
    price = info.get("current_price")
    is_financial = needs_earnings_valuation(info)

    # For financials/REITs, ignore the FCF end entirely — use earnings power.
    if is_financial:
        ivs = [v for v in (iv_owner,) if v is not None and v > 0]
        method = "earnings"
    else:
        ivs = [v for v in (iv_fcf, iv_owner) if v is not None and v > 0]
        method = "dcf-range"

    def _upside(iv):
        return ((iv - price) / price) if (iv and price) else None

    if not ivs:
        return {"ok": False, "low": None, "high": None, "mid": None,
                "conservative_iv": iv_fcf, "adjusted_iv": iv_owner, "method": method,
                "is_financial": is_financial, "current_price": price, "upside_mid": None,
                "suspect": True, "suspect_reason": "No usable cash-flow/earnings to value."}

    low, high = min(ivs), max(ivs)
    mid = sum(ivs) / len(ivs)
    spread = (high - low) / mid if mid else 0
    upside_mid = _upside(mid)

    # ---- Suspect flags (guardrails) ----
    suspect, reason = False, None
    if info.get("currency_unresolved"):
        suspect, reason = True, "Reports in a foreign currency we couldn't convert."
    elif upside_mid is not None and upside_mid > SUSPECT_UPSIDE:
        suspect, reason = True, (f"Implied upside ~{upside_mid*100:.0f}% is implausibly "
                                 "high — likely a data or growth-extrapolation artifact.")

    return {
        "ok": True,
        "conservative_iv": iv_fcf,
        "adjusted_iv": iv_owner,
        "method": method,
        "is_financial": is_financial,
        "low": low,
        "high": high,
        "mid": mid,
        "spread": spread,
        "current_price": price,
        "upside_low": _upside(low),
        "upside_high": _upside(high),
        "upside_mid": upside_mid,
        "buy_below": mid * (1 - margin_of_safety),
        "margin_of_safety": margin_of_safety,
        "suspect": suspect,
        "suspect_reason": reason,
    }


def reverse_dcf(base_cf: Optional[float], info: dict[str, Any],
                discount_rate: float, terminal_growth: float, years: int,
                add_net_cash: bool = True) -> dict[str, Any]:
    """Solve for the stage-1 growth rate the *current price* implies, so you can
    ask 'what does the market already expect?' (checklist #15)."""
    price = info.get("current_price")
    if not base_cf or base_cf <= 0 or not price or price <= 0:
        return {"ok": False}

    def iv(g):
        d = discounted_cash_flow(base_cf, info, None, None, discount_rate,
                                 terminal_growth, years, growth_estimate=g,
                                 max_stage1_growth=1.0, add_net_cash=add_net_cash)
        return d.get("intrinsic_value_per_share") if d.get("ok") else None

    lo, hi = 0.0, 0.40
    v_lo, v_hi = iv(lo), iv(hi)
    if v_lo is None or v_hi is None:
        return {"ok": False}
    if price <= v_lo:
        return {"ok": True, "implied_growth": 0.0, "note": "flat-to-declining"}
    if price >= v_hi:
        return {"ok": True, "implied_growth": 0.40, "note": ">40% (very demanding)"}
    for _ in range(40):
        mid = (lo + hi) / 2
        if iv(mid) < price:
            lo = mid
        else:
            hi = mid
    return {"ok": True, "implied_growth": (lo + hi) / 2, "note": None}


def scenario_values(base_cf: Optional[float], info: dict[str, Any],
                    base_growth: float, discount_rate: float, terminal_growth: float,
                    years: int, margin_of_safety: float,
                    add_net_cash: bool = True,
                    decline_haircut: Optional[float] = None,
                    decline_note: Optional[str] = None) -> dict[str, Any]:
    """Bear / base / bull fair values by flexing growth, discount and terminal
    (checklist #14, #17), plus an optional *earnings-decline* bear.

    The bear/base/bull cases only flex the growth RATE — they can't let earnings
    actually fall, because the DCF floors stage-1 growth at zero. That understates
    downside for a company earning ABOVE its through-cycle norm (a cyclical peak),
    where the real risk is "these margins don't last." The earnings-decline case
    rebases cash flow DOWN by `decline_haircut` (to normalized margins), lets it
    grow only modestly off that lower base, and adds +1% to the discount rate.

    It is only meaningful when earnings are above normal, so the CALLER decides:
    pass a `decline_haircut` (< 1.0) to include it, or leave it None to omit the
    row entirely (a company at or below its through-cycle margin has no peak to
    revert, and forcing a decline there is misleading)."""
    price = info.get("current_price")

    def _pack(iv, extra=None):
        """Floor a fair value at zero: a negative DCF per-share value means net
        debt swamps the (stressed) enterprise value — that's an equity wipeout,
        which reads far more clearly as $0 / -100% than as a negative price."""
        wiped = iv is not None and iv < 0
        if wiped:
            iv = 0.0
        out = {"fair_value": iv,
               "upside": ((iv - price) / price) if (iv is not None and price) else None,
               "wiped_out": wiped}
        if extra:
            out.update(extra)
        return out

    def run(gmult, ddelta, tdelta):
        d = discounted_cash_flow(
            base_cf, info, None, None,
            discount_rate=min(max(discount_rate + ddelta, 0.05), 0.25),
            terminal_growth=max(min(terminal_growth + tdelta, 0.04), 0.0),
            years=years, margin_of_safety=margin_of_safety,
            growth_estimate=max(base_growth * gmult, -0.02),
            max_stage1_growth=0.25, add_net_cash=add_net_cash)
        return _pack(d.get("intrinsic_value_per_share") if d.get("ok") else None)

    def run_decline(haircut):
        if not haircut or not base_cf or base_cf <= 0:
            return None
        d = discounted_cash_flow(
            base_cf * haircut, info, None, None,
            discount_rate=min(max(discount_rate + 0.01, 0.05), 0.25),
            terminal_growth=max(min(terminal_growth - 0.005, 0.04), 0.0),
            years=years, margin_of_safety=margin_of_safety,
            # After the reset, earnings grow only modestly off the lower base.
            growth_estimate=max(terminal_growth, 0.02),
            max_stage1_growth=0.25, add_net_cash=add_net_cash)
        iv = d.get("intrinsic_value_per_share") if d.get("ok") else None
        note = decline_note
        if iv is not None and iv < 0:
            note = (decline_note or "") + " — equity value is wiped out (net debt exceeds the stressed enterprise value)"
        return _pack(iv, {"haircut": haircut, "note": note})

    return {
        "bear": run(0.4, +0.02, -0.01),
        "base": run(1.0, 0.0, 0.0),
        "bull": run(1.6, -0.01, +0.005),
        "earnings_decline": run_decline(decline_haircut),
        "current_price": price,
    }


def sensitivity_grid(base_cf: Optional[float], info: dict[str, Any], base_growth: float,
                     discount_rate: float, terminal_growth: float, years: int,
                     add_net_cash: bool = True) -> dict[str, Any]:
    """Fair value across a discount-rate × stage-1-growth matrix, so the DCF's
    assumption-sensitivity is visible at a glance (checklist #14)."""
    price = info.get("current_price")
    if not base_cf or base_cf <= 0 or not price:
        return {"ok": False}
    dr_axis = [round(discount_rate + d, 4) for d in (-0.02, -0.01, 0, 0.01, 0.02)]
    dr_axis = [min(max(d, 0.05), 0.25) for d in dr_axis]
    g_axis = [round(max(base_growth + d, 0.0), 4) for d in (-0.04, -0.02, 0, 0.02, 0.04)]
    cells = []
    for dr in dr_axis:
        row = []
        for g in g_axis:
            d = discounted_cash_flow(base_cf, info, None, None, discount_rate=dr,
                                     terminal_growth=terminal_growth, years=years,
                                     growth_estimate=g, max_stage1_growth=0.30,
                                     add_net_cash=add_net_cash)
            iv = d.get("intrinsic_value_per_share") if d.get("ok") else None
            # Same clamp as the scenario rows: a negative DCF value is an equity
            # wipeout — show $0 / -100%, not a negative price.
            wiped = iv is not None and iv < 0
            if wiped:
                iv = 0.0
            row.append({"iv": iv, "wiped_out": wiped,
                        "upside": ((iv - price) / price) if (iv is not None) else None})
        cells.append(row)
    return {"ok": True, "discount_rates": dr_axis, "growth_rates": g_axis,
            "cells": cells, "current_price": price,
            "base_discount": discount_rate, "base_growth": base_growth}


MC_ITERATIONS = 2000  # Monte-Carlo simulation count (Guide tab reads this)


def monte_carlo_dcf(base_cf: Optional[float], info: dict[str, Any], base_growth: float,
                    discount_rate: float, terminal_growth: float, years: int,
                    iterations: int = MC_ITERATIONS, add_net_cash: bool = True) -> dict[str, Any]:
    """Monte-Carlo intrinsic value.

    A single-point DCF is false precision — its answer is only as good as four
    guessed inputs. Instead we sample growth, discount rate, terminal growth and
    the starting cash flow from distributions centered on the base case, run the
    DCF thousands of times, and report the *spread* of fair values plus the
    probability the current price sits below fair value. That converts "worth
    $X" into "P10-P90 is $A-$B, ~N% chance it's undervalued" — an honest read of
    how assumption-sensitive the call is.

    Seeded (fixed) so identical inputs always yield the same distribution; no
    flickering verdicts between requests."""
    price = info.get("current_price")
    if not base_cf or base_cf <= 0 or not price or price <= 0:
        return {"ok": False}
    rng = random.Random(1_234_567)
    ivs: list[float] = []
    for _ in range(iterations):
        g = min(max(rng.gauss(base_growth, 0.03), -0.02), 0.25)
        dr = min(max(rng.gauss(discount_rate, 0.015), 0.06), 0.20)
        tg = min(max(rng.gauss(terminal_growth, 0.005), 0.0), 0.04)
        if tg >= dr:
            tg = dr - 0.01
        cf = base_cf * (1 + rng.gauss(0, 0.10))  # ±10% on the starting cash flow
        if cf <= 0:
            continue
        d = discounted_cash_flow(cf, info, None, None, discount_rate=dr,
                                 terminal_growth=tg, years=years,
                                 growth_estimate=g, max_stage1_growth=0.25,
                                 add_net_cash=add_net_cash)
        iv = d.get("intrinsic_value_per_share") if d.get("ok") else None
        if iv is not None and iv > 0:
            ivs.append(iv)
    if len(ivs) < iterations * 0.5:  # too many degenerate draws — don't pretend
        return {"ok": False}
    ivs.sort()

    def pct(q):
        return ivs[min(int(q * len(ivs)), len(ivs) - 1)]

    p50 = pct(0.50)
    return {
        "ok": True,
        "iterations": len(ivs),
        "p10": pct(0.10), "p25": pct(0.25), "p50": p50,
        "p75": pct(0.75), "p90": pct(0.90),
        "prob_undervalued": sum(1 for v in ivs if v > price) / len(ivs),
        "median_upside": (p50 - price) / price,
        "current_price": price,
    }


def compute_multiples(stock: dict[str, Any], eps: list, fcf: list) -> dict[str, Any]:
    """Current multiples plus a sense of whether they're high/low for this name."""
    info = stock["info"]
    price = info.get("current_price")
    market_cap = info.get("market_cap")

    pe = info.get("trailing_pe")
    if pe is None and price and eps and eps[-1][1] and eps[-1][1] > 0:
        pe = price / eps[-1][1]

    latest_fcf = fcf[-1][1] if fcf else info.get("free_cashflow_ttm")
    p_fcf = (market_cap / latest_fcf) if (market_cap and latest_fcf and latest_fcf > 0) else None

    return {
        "trailing_pe": pe,
        "forward_pe": info.get("forward_pe"),
        "peg_ratio": info.get("peg_ratio"),
        "price_to_book": info.get("price_to_book"),
        "price_to_fcf": p_fcf,
        "dividend_yield": info.get("dividend_yield"),
    }


def expected_annual_return(
    stock: dict[str, Any],
    dcf: dict[str, Any],
    growth: dict[str, Any],
    base_fcf: Optional[float] = None,
    inflation_hurdle: float = INFLATION_HURDLE,
    intrinsic_override: Optional[float] = None,
) -> dict[str, Any]:
    """Rough expected annualized return over a 10-15yr hold, vs inflation.

    Two lenses:
      1) If DCF gives intrinsic value, the mispricing closes over the horizon
         and is earned on top of underlying business growth + dividend.
      2) A simple "owner earnings growth + starting FCF yield + dividend" proxy.
    We report the more conservative of the two as the headline.
    """
    info = stock["info"]
    price = info.get("current_price")
    horizon = 12  # midpoint of 10-15yr

    div_yield = info.get("dividend_yield") or 0.0
    if div_yield > 1:  # some sources return percent, not fraction
        div_yield = div_yield / 100.0

    # Underlying growth proxy: prefer FCF CAGR, then EPS, then revenue.
    underlying = next((g for g in (growth.get("fcf_cagr"),
                                   growth.get("eps_cagr"),
                                   growth.get("revenue_cagr")) if g is not None), None)
    if underlying is not None:
        underlying = max(min(underlying, 0.15), -0.05)  # clamp

    # Starting FCF yield — use the normalized base FCF, not Yahoo's TTM field.
    mc = info.get("market_cap")
    fcf_for_yield = base_fcf if (base_fcf and base_fcf > 0) else info.get("free_cashflow_ttm")
    fcf_yield = (fcf_for_yield / mc) if (mc and fcf_for_yield and mc > 0) else None

    # Lens 1: mispricing reversion. Prefer the range-midpoint intrinsic value
    # (capex-adjusted) when available; fall back to the FCF DCF.
    intrinsic = intrinsic_override or dcf.get("intrinsic_value_per_share")
    reversion_return = None
    if intrinsic and price and price > 0:
        total_ratio = intrinsic / price
        if total_ratio > 0:
            reversion_cagr = total_ratio ** (1 / horizon) - 1
            reversion_return = reversion_cagr + (underlying or 0.0) + div_yield

    # Lens 2: growth + income proxy.
    proxy_return = None
    if underlying is not None:
        proxy_return = underlying + (fcf_yield or 0.0) * 0.0 + div_yield
        # Use FCF yield as a floor contribution when growth is the driver.
        if fcf_yield:
            proxy_return = underlying + div_yield + max(fcf_yield - 0.04, 0) * 0.5

    candidates = [r for r in (reversion_return, proxy_return) if r is not None]
    headline = min(candidates) if candidates else None

    return {
        "horizon_years": horizon,
        "inflation_hurdle": inflation_hurdle,
        "dividend_yield": div_yield,
        "underlying_growth": underlying,
        "fcf_yield": fcf_yield,
        "reversion_return": reversion_return,
        "proxy_return": proxy_return,
        "expected_annual_return": headline,
        "real_return_vs_inflation": (headline - inflation_hurdle) if headline is not None else None,
        "beats_inflation": (headline > inflation_hurdle) if headline is not None else None,
    }
