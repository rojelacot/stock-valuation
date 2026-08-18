"""Quantitative valuation engine.

Turns normalized fundamentals into the numbers a long-term value investor
actually looks at: growth CAGRs, quality/returns metrics, balance-sheet
health, a discounted-cash-flow intrinsic value, and multiples vs the stock's
own history. Everything here is deterministic and explainable.
"""
from __future__ import annotations

from typing import Any, Optional

import earnings_quality
import duediligence

# ---- Assumptions (defaults; overridable per-request from the frontend) ----
DEFAULT_DISCOUNT_RATE = 0.10       # required return / WACC proxy
DEFAULT_TERMINAL_GROWTH = 0.025    # long-run growth ~ GDP+inflation
DEFAULT_PROJECTION_YEARS = 10
INFLATION_HURDLE = 0.03            # "beat inflation" bar
MARGIN_OF_SAFETY = 0.25           # want price <= 75% of intrinsic value

DEFAULT_ASSUMPTIONS = {
    "discount_rate": DEFAULT_DISCOUNT_RATE,
    "terminal_growth": DEFAULT_TERMINAL_GROWTH,
    "projection_years": DEFAULT_PROJECTION_YEARS,
    "inflation_hurdle": INFLATION_HURDLE,
    "margin_of_safety": MARGIN_OF_SAFETY,
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


def risk_premium(info: dict[str, Any]) -> dict[str, Any]:
    """Extra discount-rate demanded for company-specific risk (guardrail 4).

    Riskier businesses should have to clear a higher bar (bigger margin of
    safety), so we add premiums for small size, high volatility, leverage, and
    emerging-market domicile. Capped at +5%."""
    prem, reasons = 0.0, []
    mc = info.get("market_cap") or 0
    if 0 < mc < 3e9:
        prem += 0.02; reasons.append("small cap")
    elif 0 < mc < 10e9:
        prem += 0.01; reasons.append("mid cap")
    beta = info.get("beta")
    if beta is not None:
        if beta >= 1.8:
            prem += 0.02; reasons.append("high volatility")
        elif beta >= 1.3:
            prem += 0.01; reasons.append("elevated volatility")
    net_debt = (info.get("total_debt") or 0) - (info.get("total_cash") or 0)
    if mc and net_debt > 0.5 * mc:
        prem += 0.01; reasons.append("high leverage")
    country = info.get("country")
    if country and country not in DEVELOPED:
        prem += 0.02; reasons.append("emerging market")
    return {"premium": min(prem, 0.05), "reasons": reasons}


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

    gross = margin_series(_series(st["gross_profit"]))
    operating = margin_series(_series(st["operating_income"]))
    net_margin = margin_series(net_income)

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

    # ---- Balance sheet health ----
    latest_debt = _latest(st["total_debt"]) or info.get("total_debt")
    latest_equity = _latest(st["total_equity"])
    latest_cash = _latest(st["cash"]) or info.get("total_cash")
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

    # Guardrail 3: with only a short history, extrapolate growth more cautiously.
    yrs = growth.get("years_of_data") or 0
    max_g = 0.12 if yrs >= 5 else 0.10

    # Guardrail 4: risk-adjust the discount rate so riskier names clear a higher bar.
    rp = risk_premium(info)
    eff_discount = min(A["discount_rate"] + rp["premium"], 0.25)

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
    scenarios = scenario_values(val_base_cf, info, base_growth, eff_discount,
                                A["terminal_growth"], A["projection_years"],
                                A["margin_of_safety"])
    reverse = reverse_dcf(val_base_cf, info, eff_discount, A["terminal_growth"],
                          A["projection_years"])
    sensitivity = sensitivity_grid(val_base_cf, info, base_growth, eff_discount,
                                   A["terminal_growth"], A["projection_years"])

    return {
        "assumptions_used": A,
        "risk_premium": rp,
        "effective_discount_rate": eff_discount,
        "cyclical_peak": cyclical,
        "due_diligence": dd,
        "scenarios": scenarios,
        "reverse_dcf": reverse,
        "sensitivity": sensitivity,
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
        },
    }


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

    # Project and discount, linearly fading growth from stage1 -> terminal.
    pv_sum = 0.0
    projected = []
    fcf_t = base_fcf
    for t in range(1, years + 1):
        g = stage1_growth + (terminal_growth - stage1_growth) * (t - 1) / max(years - 1, 1)
        fcf_t = fcf_t * (1 + g)
        pv = fcf_t / ((1 + discount_rate) ** t)
        pv_sum += pv
        projected.append({"year": t, "fcf": fcf_t, "pv": pv, "growth": g})

    # Terminal value (Gordon) on final-year FCF, then discounted back.
    terminal_fcf = fcf_t * (1 + terminal_growth)
    terminal_value = terminal_fcf / (discount_rate - terminal_growth)
    pv_terminal = terminal_value / ((1 + discount_rate) ** years)

    enterprise_value = pv_sum + pv_terminal
    net_cash = (info.get("total_cash") or 0) - (info.get("total_debt") or 0)
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


def needs_earnings_valuation(info: dict[str, Any]) -> bool:
    """Guardrail 2: banks/insurers/REITs don't have meaningful 'free cash flow' —
    an FCF-DCF wildly misprices them. Value these on earnings power instead."""
    sec = (info.get("sector") or "").lower()
    ind = (info.get("industry") or "").lower()
    if "financial" in sec or "real estate" in sec:
        return True
    return any(w in ind for w in FINANCIAL_INDUSTRY_WORDS)


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
                discount_rate: float, terminal_growth: float, years: int) -> dict[str, Any]:
    """Solve for the stage-1 growth rate the *current price* implies, so you can
    ask 'what does the market already expect?' (checklist #15)."""
    price = info.get("current_price")
    if not base_cf or base_cf <= 0 or not price or price <= 0:
        return {"ok": False}

    def iv(g):
        d = discounted_cash_flow(base_cf, info, None, None, discount_rate,
                                 terminal_growth, years, growth_estimate=g,
                                 max_stage1_growth=1.0)
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
                    years: int, margin_of_safety: float) -> dict[str, Any]:
    """Bear / base / bull fair values by flexing growth, discount and terminal
    (checklist #14, #17)."""
    price = info.get("current_price")

    def run(gmult, ddelta, tdelta):
        d = discounted_cash_flow(
            base_cf, info, None, None,
            discount_rate=min(max(discount_rate + ddelta, 0.05), 0.25),
            terminal_growth=max(min(terminal_growth + tdelta, 0.04), 0.0),
            years=years, margin_of_safety=margin_of_safety,
            growth_estimate=max(base_growth * gmult, -0.02),
            max_stage1_growth=0.25)
        iv = d.get("intrinsic_value_per_share") if d.get("ok") else None
        return {"fair_value": iv, "upside": ((iv - price) / price) if (iv and price) else None}

    return {
        "bear": run(0.4, +0.02, -0.01),
        "base": run(1.0, 0.0, 0.0),
        "bull": run(1.6, -0.01, +0.005),
        "current_price": price,
    }


def sensitivity_grid(base_cf: Optional[float], info: dict[str, Any], base_growth: float,
                     discount_rate: float, terminal_growth: float, years: int) -> dict[str, Any]:
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
                                     growth_estimate=g, max_stage1_growth=0.30)
            iv = d.get("intrinsic_value_per_share") if d.get("ok") else None
            row.append({"iv": iv, "upside": ((iv - price) / price) if iv else None})
        cells.append(row)
    return {"ok": True, "discount_rates": dr_axis, "growth_rates": g_axis,
            "cells": cells, "current_price": price,
            "base_discount": discount_rate, "base_growth": base_growth}


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
