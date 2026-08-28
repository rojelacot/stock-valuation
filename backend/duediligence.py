"""Extra due-diligence metrics that round out the checklist coverage:
EV multiples, FCF yield, NOPAT-based ROIC, net-debt/EBITDA, FCF margin,
FCF-per-share, share dilution, and accruals (receivables/inventory vs revenue).
"""
from __future__ import annotations

import math
from typing import Any, Optional

DEFAULT_TAX = 0.21
RISK_FREE = 0.045          # ~10yr treasury proxy
EQUITY_RISK_PREMIUM = 0.05  # long-run equity premium


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


def _latest(pts):
    return pts[-1][1] if pts else None


def _avg(pts, n=None):
    vals = [v for _, v in pts]
    if n:
        vals = vals[-n:]
    return sum(vals) / len(vals) if vals else None


def _yoy(pts):
    """Latest year-over-year growth."""
    if len(pts) < 2 or pts[-2][1] in (0, None):
        return None
    return pts[-1][1] / pts[-2][1] - 1


def _cagr(pts):
    if len(pts) < 2:
        return None
    (y0, v0), (y1, v1) = pts[0], pts[-1]
    yrs = y1 - y0
    if yrs <= 0 or v0 <= 0 or v1 <= 0:
        return None
    return (v1 / v0) ** (1 / yrs) - 1


def compute_wacc(info: dict[str, Any], interest_expense: Optional[float],
                 tax_rate: float) -> dict[str, Any]:
    """Weighted average cost of capital — the hurdle ROIC must beat (checklist #5).

    Cost of equity via CAPM (risk-free + beta·ERP); cost of debt from the
    effective interest rate; weighted by market values of equity and debt."""
    beta = info.get("beta")
    beta = beta if beta is not None else 1.0
    cost_equity = RISK_FREE + beta * EQUITY_RISK_PREMIUM
    mc = info.get("market_cap") or 0
    debt = info.get("total_debt") or 0
    cost_debt = (abs(interest_expense) / debt) if (interest_expense and debt) else 0.05
    cost_debt = min(max(cost_debt, 0.02), 0.12)
    v = mc + debt
    if v <= 0:
        return {"wacc": None}
    we, wd = mc / v, debt / v
    wacc = we * cost_equity + wd * cost_debt * (1 - tax_rate)
    return {"wacc": wacc, "cost_of_equity": cost_equity, "cost_of_debt": cost_debt,
            "beta": beta, "equity_weight": we, "debt_weight": wd}


def _hist_multiple(per_share: list[tuple[int, float]],
                   price_by_year: dict[int, float]) -> dict[int, float]:
    out = {}
    for y, v in per_share:
        if v and v > 0 and y in price_by_year:
            out[y] = price_by_year[y] / v
    return out


def _vs_history(hist: dict[int, float], current: Optional[float]) -> Optional[dict[str, Any]]:
    vals = list(hist.values())
    if len(vals) < 2 or current is None:
        return None
    avg = sum(vals) / len(vals)
    return {"current": current, "avg": avg, "min": min(vals), "max": max(vals),
            "premium_to_avg": (current / avg - 1) if avg else None,
            "years": len(vals)}


def piotroski_score(statements: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Piotroski F-Score (0-9) — a compact financial-strength check across
    profitability, leverage/liquidity and operating efficiency. Needs 2+ years.
    """
    def s(key):
        return dict(_series(statements.get(key)))
    ni, ocf, assets = s("net_income"), s("operating_cashflow"), s("total_assets")
    ltd, ca, cl = s("long_term_debt"), s("current_assets"), s("current_liabilities")
    sh, gp, rev = s("shares"), s("gross_profit"), s("revenue")
    yrs = sorted(set(ni) & set(assets))
    if len(yrs) < 2:
        return None
    y, p = yrs[-1], yrs[-2]

    def roa(yr):
        return (ni.get(yr) / assets[yr]) if assets.get(yr) else None

    checks = {}
    checks["ROA positive"] = (ni.get(y) or 0) > 0
    checks["Operating cash flow positive"] = (ocf.get(y) or 0) > 0
    checks["ROA improving"] = (roa(y) is not None and roa(p) is not None and roa(y) > roa(p))
    checks["Cash > earnings (low accruals)"] = (ocf.get(y) is not None and ni.get(y) is not None
                                                and ocf[y] > ni[y])
    checks["Leverage falling"] = (ltd.get(y) is not None and ltd.get(p) is not None
                                  and assets.get(y) and assets.get(p)
                                  and (ltd[y] / assets[y]) < (ltd[p] / assets[p]))
    checks["Current ratio rising"] = (ca.get(y) and cl.get(y) and ca.get(p) and cl.get(p)
                                      and (ca[y] / cl[y]) > (ca[p] / cl[p]))
    checks["No share dilution"] = (sh.get(y) is not None and sh.get(p) is not None
                                   and sh[y] <= sh[p] * 1.01)
    checks["Gross margin rising"] = (gp.get(y) and rev.get(y) and gp.get(p) and rev.get(p)
                                     and (gp[y] / rev[y]) > (gp[p] / rev[p]))
    checks["Asset turnover rising"] = (rev.get(y) and assets.get(y) and rev.get(p) and assets.get(p)
                                       and (rev[y] / assets[y]) > (rev[p] / assets[p]))
    score = sum(1 for v in checks.values() if v)
    return {"score": score, "max": 9, "checks": checks}


def dupont(statements: dict[str, Any]) -> Optional[dict[str, Any]]:
    """DuPont decomposition — ROE = net margin x asset turnover x equity multiplier.

    Splits *why* the return on equity is what it is: profitability, how hard the
    assets work, and how much leverage. A 20% ROE from margins is far higher
    quality than a 20% ROE juiced by debt. The 5-factor form further splits the
    margin into tax burden x interest burden x operating margin.
    """
    def s(k):
        return dict(_series(statements.get(k)))
    ni, rev, assets, equity = s("net_income"), s("revenue"), s("total_assets"), s("total_equity")
    pretax, ebit = s("pretax_income"), s("operating_income")
    years = sorted(set(ni) & set(rev) & set(assets) & set(equity))
    series = []
    for y in years:
        n, r, a, e = ni[y], rev[y], assets[y], equity[y]
        if not (r and a and a > 0 and e and e > 0):
            continue
        rec = {"year": y, "roe": n / e, "net_margin": n / r,
               "asset_turnover": r / a, "equity_multiplier": a / e}
        pt, eb = pretax.get(y), ebit.get(y)
        if pt and eb and eb != 0 and pt != 0:
            rec.update({"tax_burden": n / pt, "interest_burden": pt / eb,
                        "operating_margin": eb / r})
        series.append(rec)
    if not series:
        return None
    latest = series[-1]
    prior = series[0] if len(series) < 6 else series[-6]

    # Which lever moved ROE the most since `prior` (log-additive decomposition:
    # Δln ROE = Δln margin + Δln turnover + Δln leverage).
    driver = None
    if prior is not latest:
        comps = {}
        for k, label in (("net_margin", "profit margins"),
                         ("asset_turnover", "asset efficiency"),
                         ("equity_multiplier", "leverage")):
            pv, lv = prior.get(k), latest.get(k)
            if pv and lv and pv > 0 and lv > 0:
                comps[label] = math.log(lv / pv)
        if comps:
            factor = max(comps, key=lambda k: abs(comps[k]))
            driver = {"factor": factor, "direction": "higher" if comps[factor] > 0 else "lower",
                      "prior_year": prior["year"], "prior_roe": prior["roe"]}
    return {"latest": latest, "prior": prior, "driver": driver, "series": series}


DEFAULT_INC_ROIC_BAR = 0.08  # fallback cost-of-capital hurdle when WACC is unavailable


def incremental_roic(nopat_by: dict, inv_cap_by: dict,
                     roic_avg: Optional[float], wacc: Optional[float]) -> dict:
    """Return on the LAST few years of ADDED capital — the marginal economics that
    average ROIC hides. A mature business can post a high average while reinvesting
    new capital at a poor rate (empire-building M&A, forced growth); that's a value
    trap the quality filter would otherwise wave through.

    Compares average NOPAT and average invested capital across an early vs a recent
    window: incremental ROIC = ΔNOPAT ÷ ΔInvested capital. A flat or shrinking
    capital base has no 'incremental' story (it's self-funding / capital-light), so
    it's marked not-applicable rather than flagged. Endpoints are 3-year averages so
    a single outlier year (a trough, a one-off charge) doesn't drive the verdict."""
    years = sorted(set(nopat_by) & set(inv_cap_by))
    if len(years) < 6:
        return {"applicable": False, "reason": "Needs ~6yr of overlapping data."}
    k = min(3, len(years) // 2)
    early, recent = years[:k], years[-k:]
    def avg(d, ys):
        return sum(d[y] for y in ys) / len(ys)
    n0, n1 = avg(nopat_by, early), avg(nopat_by, recent)
    c0, c1 = avg(inv_cap_by, early), avg(inv_cap_by, recent)
    d_cap = c1 - c0
    if c0 <= 0 or d_cap <= 0.15 * c0:
        return {"applicable": False, "capital_light": d_cap <= 0,
                "reason": "Capital base flat or shrinking — self-funding / capital-light; "
                          "no incremental-return story to tell."}
    inc = (n1 - n0) / d_cap
    bar = wacc if (wacc and wacc > 0) else DEFAULT_INC_ROIC_BAR
    avg_pct = (roic_avg or 0) * 100
    level, flag, note = "productive", None, None
    if inc < 0:
        level = "destructive"
        flag = (f"Incremental ROIC is negative (~{inc*100:.0f}%): over this window operating "
                f"profit fell while invested capital grew — the newest capital is destroying "
                f"value, and the ~{avg_pct:.0f}% average ROIC hides it.")
    elif inc < bar:
        level = "below_cost"
        flag = (f"Incremental ROIC ~{inc*100:.0f}% is below the ~{bar*100:.0f}% cost of capital "
                f"— the newest capital earns less than it costs, even though the average ROIC "
                f"(~{avg_pct:.0f}%) still looks healthy.")
    elif roic_avg and inc < 0.5 * roic_avg:
        level = "fading"
        note = (f"Incremental ROIC ~{inc*100:.0f}% is well below the ~{avg_pct:.0f}% average — "
                f"reinvestment economics are fading, though still above the cost of capital.")
    else:
        note = (f"Incremental capital earns ~{inc*100:.0f}% — reinvestment stays productive, "
                f"at or above the legacy business.")
    return {"applicable": True, "value": inc, "avg": roic_avg, "wacc": wacc, "bar": bar,
            "window": f"{early[0]}–{early[-1]} → {recent[0]}–{recent[-1]}",
            "level": level, "flag": flag, "note": note}


def analyze(statements: dict[str, Any], info: dict[str, Any],
            fcf: list[tuple[int, float]],
            price_history: Optional[list[dict[str, Any]]] = None) -> dict[str, Any]:
    revenue = _series(statements.get("revenue"))
    op_income = _series(statements.get("operating_income"))
    ebitda_s = _series(statements.get("ebitda"))
    receivables = _series(statements.get("receivables"))
    inventory = _series(statements.get("inventory"))
    equity = _series(statements.get("total_equity"))
    debt = _series(statements.get("total_debt"))
    cash = _series(statements.get("cash"))
    shares = _series(statements.get("shares"))
    pretax = _series(statements.get("pretax_income"))
    tax = _series(statements.get("tax_provision"))

    mc = info.get("market_cap")
    price = info.get("current_price")
    rev_l = _latest(revenue)
    ebitda_l = _latest(ebitda_s) or info.get("ebitda_ttm")
    ebit_l = _latest(op_income)
    net_debt = ((info.get("total_debt") or _latest(debt) or 0)
                - (info.get("total_cash") or _latest(cash) or 0))
    fcf_l = _latest(fcf)

    ev = info.get("enterprise_value")
    if not ev and mc is not None:
        ev = mc + net_debt

    def _div(a, b):
        return (a / b) if (a is not None and b not in (None, 0)) else None

    # ---- Effective tax rate & NOPAT-based ROIC ----
    tax_rate = None
    if pretax and tax:
        pt, tp = _latest(pretax), _latest(tax)
        if pt and pt > 0 and tp is not None:
            tax_rate = min(max(tp / pt, 0.0), 0.40)
    tr = tax_rate if tax_rate is not None else DEFAULT_TAX
    eq_map, debt_map, cash_map = dict(equity), dict(debt), dict(cash)
    roic_nopat = {}
    for y, oi in op_income:
        inv_cap = (eq_map.get(y) or 0) + (debt_map.get(y) or 0) - (cash_map.get(y) or 0)
        if inv_cap and inv_cap > 0:
            roic_nopat[y] = (oi * (1 - tr)) / inv_cap
    roic_vals = [roic_nopat[y] for y in sorted(roic_nopat)]

    # ---- FCF margin / per share ----
    rev_map = dict(revenue)
    fcf_margin = {y: v / rev_map[y] for y, v in fcf if rev_map.get(y)}
    fm_vals = [fcf_margin[y] for y in sorted(fcf_margin)]
    shares_l = _latest(shares) or info.get("shares_outstanding")
    fcf_per_share = _div(fcf_l, shares_l)

    # ---- Dilution (share-count trend) ----
    dilution_cagr = _cagr(shares) if len(shares) >= 2 else None

    # ---- Accruals: receivables / inventory vs revenue ----
    rev_g = _yoy(revenue)
    ar_g = _yoy(receivables)
    inv_g = _yoy(inventory)
    accrual_flags = []
    if ar_g is not None and rev_g is not None and ar_g > rev_g + 0.10 and ar_g > 0.15:
        accrual_flags.append(
            f"Receivables up ~{ar_g*100:.0f}% vs revenue ~{rev_g*100:.0f}% — "
            "collections lagging sales (watch revenue quality).")
    if inv_g is not None and rev_g is not None and inv_g > rev_g + 0.10 and inv_g > 0.15:
        accrual_flags.append(
            f"Inventory up ~{inv_g*100:.0f}% vs revenue ~{rev_g*100:.0f}% — "
            "possible demand slowdown or overstocking.")

    # ---- ROIC vs WACC (value creation) ----
    int_exp = _latest(_series(statements.get("interest_expense")))
    wacc_d = compute_wacc(info, int_exp, tr)
    roic_avg = (sum(roic_vals) / len(roic_vals)) if roic_vals else None
    value_spread = (roic_avg - wacc_d["wacc"]) if (roic_avg is not None and wacc_d.get("wacc")) else None

    # ---- Incremental ROIC: return on the last few years of ADDED capital ----
    inv_cap_by = {}
    for y in set(eq_map) | set(debt_map):
        ic = (eq_map.get(y) or 0) + (debt_map.get(y) or 0) - (cash_map.get(y) or 0)
        if ic > 0:
            inv_cap_by[y] = ic
    nopat_by = {y: oi * (1 - tr) for y, oi in op_income}
    inc_roic = incremental_roic(nopat_by, inv_cap_by, roic_avg, wacc_d.get("wacc"))

    # ---- Valuation vs its own history (P/E, P/FCF over the available years) ----
    eps_s = _series(statements.get("eps"))
    shares_s = _series(statements.get("shares"))
    price_by_year: dict[int, float] = {}
    for p in (price_history or []):
        try:
            price_by_year[int(p["date"][:4])] = p["close"]   # last month of year wins
        except Exception:  # noqa: BLE001
            continue
    shares_map = dict(shares_s)
    fcf_ps = [(y, v / shares_map[y]) for y, v in fcf if shares_map.get(y)]
    pe_hist = _hist_multiple(eps_s, price_by_year)
    pfcf_hist = _hist_multiple(fcf_ps, price_by_year)
    cur_pfcf = _div(mc, fcf_l)
    valuation_vs_history = {
        "pe": _vs_history(pe_hist, info.get("trailing_pe")),
        "pfcf": _vs_history(pfcf_hist, cur_pfcf),
    }

    # ---- Capital returns (dividends + buybacks) & other free metrics ----
    div_paid = abs(_latest(_series(statements.get("dividends_paid"))) or 0)
    buybacks = abs(_latest(_series(statements.get("buybacks"))) or 0)
    total_returns = div_paid + buybacks
    ni_l = _latest(net_income) if (net_income := _series(statements.get("net_income"))) else None
    assets_l = _latest(_series(statements.get("total_assets")))
    capital_returns = {
        "dividends_paid": div_paid or None,
        "buybacks": buybacks or None,
        "total": total_returns or None,
        "shareholder_yield": _div(total_returns, mc) if total_returns else None,
        "payout_ratio": _div(div_paid, ni_l) if (div_paid and ni_l and ni_l > 0) else None,
        "buyback_yield": _div(buybacks, mc) if buybacks else None,
    }

    # ---- Dividend safety ----
    div_s = _series(statements.get("dividends_paid"))
    shares_s2 = dict(_series(statements.get("shares")))
    dps = [(y, abs(v) / shares_s2[y]) for y, v in div_s if shares_s2.get(y) and shares_s2[y] > 0]
    div_latest = abs(_latest(div_s) or 0)
    dividend_safety = {
        "pays_dividend": div_latest > 0,
        "dps_latest": dps[-1][1] if dps else None,
        "dividend_growth_cagr": _cagr(dps) if len(dps) >= 2 else None,
        "fcf_coverage": _div(fcf_l, div_latest) if div_latest else None,
        "payout_ratio": _div(div_paid, ni_l) if (div_paid and ni_l and ni_l > 0) else None,
        "years": len(dps),
    }

    return {
        "wacc": wacc_d.get("wacc"),
        "wacc_detail": wacc_d,
        "roic_vs_wacc_spread": value_spread,
        "creates_value": (value_spread is not None and value_spread > 0),
        "incremental_roic": inc_roic,
        "valuation_vs_history": valuation_vs_history,
        "price_to_sales": _div(mc, rev_l),
        "return_on_assets": _div(ni_l, assets_l),
        "capital_returns": capital_returns,
        "piotroski": piotroski_score(statements),
        "dupont": dupont(statements),
        "dividend_safety": dividend_safety,
        "enterprise_value": ev,
        "ev_to_ebitda": info.get("ev_to_ebitda") or _div(ev, ebitda_l),
        "ev_to_ebit": _div(ev, ebit_l),
        "ev_to_revenue": info.get("ev_to_revenue") or _div(ev, rev_l),
        "fcf_yield": _div(fcf_l, mc),
        "net_debt": net_debt,
        "net_debt_to_ebitda": _div(net_debt, ebitda_l),
        "roic_nopat_latest": roic_vals[-1] if roic_vals else None,
        "roic_nopat_avg": (sum(roic_vals) / len(roic_vals)) if roic_vals else None,
        "effective_tax_rate": tax_rate,
        "fcf_margin_latest": fm_vals[-1] if fm_vals else None,
        "fcf_margin_avg": (sum(fm_vals) / len(fm_vals)) if fm_vals else None,
        "fcf_per_share": fcf_per_share,
        "dilution_cagr": dilution_cagr,   # +ve = issuing shares, -ve = buybacks
        "held_percent_insiders": info.get("held_percent_insiders"),
        "held_percent_institutions": info.get("held_percent_institutions"),
        "accrual_flags": accrual_flags,
        "series": {
            "shares": [{"year": y, "value": v} for y, v in shares],
            "fcf_margin": [{"year": y, "value": fcf_margin[y]} for y in sorted(fcf_margin)],
        },
    }
