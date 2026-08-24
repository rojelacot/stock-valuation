"""Earnings-power valuation for mature, wide-moat compounders.

Why a separate model: a strict free-cash-flow DCF misprices a mature, high-return
compounder (Coca-Cola, J&J, Walmart) badly on the LOW side. It extrapolates a
depressed trailing FCF growth rate through a full equity discount rate and a
Gordon terminal, and spits out a fair value at 3-10x earnings — a multiple these
businesses have never traded at and never will. That's a data/extrapolation
artifact, not a real "80% overvalued".

The right tool for a business whose earnings are steady and whose reinvestment
earns a high return is the **justified P/E**, the Gordon model re-expressed on
earnings and adjusted for how efficiently growth is funded:

    justified P/E = (1 - g/ROE) / (r - g)
    fair value    = justified P/E x normalized earnings per share

The (1 - g/ROE) term is the payout-equivalent: the share of earnings NOT retained
to fund growth. Growth is funded by retained EQUITY earning ROE, so a high-ROE
business needs to retain little to grow and pays a higher multiple — this is the
same residual-income logic behind the financials' justified-P/B, expressed on
earnings. We use THROUGH-CYCLE average ROE and a conservative, capped growth
rate, and clamp the multiple to a sane band — the point is to replace an artifact
with a credible number, never to justify a bubble multiple. (Eligibility is gated
on ROIC too, so a merely leverage-inflated ROE doesn't earn the premium.)

Used as a FLOOR: this only replaces the DCF when the DCF is producing an
implausibly low value for a genuinely high-quality, stable business. Ordinary
names keep the DCF.
"""
from __future__ import annotations

import random
from typing import Any, Optional

PE_FLOOR = 8.0     # even a wonderful business is worth at least ~8x earnings
PE_CAP = 25.0      # never justify a bubble multiple, however high the ROE/growth
G_CAP = 0.075      # durable-growth ceiling; a decade-plus of >7.5% is rare
MIN_SPREAD = 0.02  # floor on (r - g) to avoid the Gordon blow-up


def justified_pe(roe: Optional[float], r: float, g: float) -> Optional[float]:
    """Fair P/E from through-cycle ROE, required return r, growth g.

    The Gordon form scales the multiple with growth on its own — a faster grower
    earns a higher fair multiple — so growth is allowed up to G_CAP (not pinned at
    a low mature rate), letting a genuine compounder justify more than a no-growth
    staple. PE_CAP is the anti-bubble backstop; MIN_SPREAD stops the blow-up as g
    approaches r."""
    if roe is None or roe <= 0 or r is None:
        return None
    spread = max(r - g, MIN_SPREAD)
    reinvest = min(g / roe, 0.9) if roe > 0 else 0.9     # cap implied reinvestment
    pe = (1 - reinvest) / spread
    return max(PE_FLOOR, min(pe, PE_CAP))


def _upside(iv: Optional[float], price: Optional[float]) -> Optional[float]:
    return ((iv - price) / price) if (iv and price and price > 0) else None


def _norm(g: Optional[float]) -> float:
    """Growth for the justified multiple: capped at the durable ceiling."""
    return min(max(g if g is not None else 0.03, 0.0), G_CAP)


def value(earnings_ps: Optional[float], roe: Optional[float], r: float,
          g: Optional[float], price: Optional[float],
          margin_of_safety: float) -> dict[str, Any]:
    """Headline justified-P/E valuation, in the same shape build_valuation_range
    returns so the rest of the app renders it unchanged (method='earnings-power')."""
    g = _norm(g)
    if not earnings_ps or earnings_ps <= 0:
        return {"ok": False, "method": "earnings-power", "is_financial": False,
                "suspect": True, "suspect_reason": "No positive normalized earnings to value on."}
    pe = justified_pe(roe, r, g)
    if pe is None:
        return {"ok": False, "method": "earnings-power", "is_financial": False,
                "suspect": True, "suspect_reason": "Return on equity unavailable."}
    iv = earnings_ps * pe
    current_pe = (price / earnings_ps) if (price and earnings_ps > 0) else None
    up = _upside(iv, price)
    suspect, reason = False, None
    if up is not None and up > 1.0:
        suspect, reason = True, (f"Implied upside ~{up*100:.0f}% is implausibly high — "
                                 "treat the earnings-power inputs with caution.")
    return {
        "ok": True, "method": "earnings-power", "is_financial": False,
        "low": iv, "high": iv, "mid": iv, "spread": 0.0,
        "conservative_iv": iv, "adjusted_iv": iv,
        "current_price": price, "upside_low": up, "upside_high": up, "upside_mid": up,
        "buy_below": iv * (1 - margin_of_safety), "margin_of_safety": margin_of_safety,
        "suspect": suspect, "suspect_reason": reason,
        # earnings-power detail block (for the UI):
        "justified_pe": pe, "current_pe": current_pe, "eps_used": earnings_ps,
        "roe_used": roe, "cost_of_equity": r, "growth": g,
    }


def reverse(earnings_ps: Optional[float], roe: Optional[float], r: float,
            g: Optional[float], price: Optional[float]) -> dict[str, Any]:
    """What long-term growth today's P/E implies, given the justified-P/E model —
    the earnings-power analog of a reverse DCF. Returned as `implied_growth` so the
    UI's standard reverse panel renders it unchanged."""
    if not earnings_ps or earnings_ps <= 0 or not price or price <= 0 or not roe or roe <= 0:
        return {"ok": False, "method": "earnings-power"}
    current_pe = price / earnings_ps
    lo, hi = 0.0, max(r - MIN_SPREAD, 0.001)
    # justified_pe rises monotonically in g -> bisection.
    if justified_pe(roe, r, lo) >= current_pe:
        return {"ok": True, "method": "earnings-power", "implied_growth": 0.0,
                "current_pe": current_pe}
    if justified_pe(roe, r, hi) <= current_pe:
        return {"ok": True, "method": "earnings-power", "implied_growth": hi,
                "current_pe": current_pe, "note": ">= cost of capital (very demanding)"}
    for _ in range(40):
        mid = (lo + hi) / 2
        if justified_pe(roe, r, mid) < current_pe:
            lo = mid
        else:
            hi = mid
    return {"ok": True, "method": "earnings-power", "implied_growth": (lo + hi) / 2,
            "current_pe": current_pe}


def scenarios(earnings_ps: float, roe: Optional[float], r: float, g: Optional[float],
              price: Optional[float]) -> dict[str, Any]:
    """Bear/base/bull by flexing ROE (reinvestment efficiency), growth and the
    required return around the base case."""
    g = _norm(g)
    if roe is None or not earnings_ps:
        return {}

    def run(roe_mult, g_delta, r_delta):
        pe = justified_pe(roe * roe_mult, min(max(r + r_delta, 0.05), 0.20),
                          min(max(g + g_delta, 0.0), G_CAP))
        iv = (pe * earnings_ps) if pe is not None else None
        wiped = iv is not None and iv < 0
        if wiped:
            iv = 0.0
        return {"fair_value": iv, "upside": _upside(iv, price), "wiped_out": wiped}

    return {"bear": run(0.85, -0.015, +0.015), "base": run(1.0, 0.0, 0.0),
            "bull": run(1.10, +0.010, -0.010), "current_price": price}


def sensitivity(earnings_ps: float, roe: Optional[float], r: float, g: Optional[float],
                price: Optional[float]) -> dict[str, Any]:
    """Fair value across a discount-rate x growth grid, mirroring the DCF grid's
    shape so the UI renders it identically."""
    g = _norm(g)
    if roe is None or not earnings_ps or not price:
        return {"ok": False}
    dr_axis = [min(max(round(r + d, 4), 0.05), 0.20) for d in (-0.02, -0.01, 0, 0.01, 0.02)]
    g_axis = [round(max(min(g + d, G_CAP), 0.0), 4) for d in (-0.02, -0.01, 0, 0.01, 0.02)]
    cells = []
    for dr in dr_axis:
        row = []
        for gg in g_axis:
            pe = justified_pe(roe, dr, gg)
            iv = (pe * earnings_ps) if pe is not None else None
            wiped = iv is not None and iv < 0
            if wiped:
                iv = 0.0
            row.append({"iv": iv, "wiped_out": wiped,
                        "upside": ((iv - price) / price) if (iv is not None) else None})
        cells.append(row)
    return {"ok": True, "discount_rates": dr_axis, "growth_rates": g_axis,
            "cells": cells, "current_price": price, "base_discount": r, "base_growth": g}


def monte_carlo(earnings_ps: float, roe: Optional[float], r: float, g: Optional[float],
                price: Optional[float], iterations: int = 2000) -> dict[str, Any]:
    """Distribution of fair value sampling ROIC, required return and growth —
    same output shape as the DCF Monte-Carlo so the UI renders it identically."""
    g = _norm(g)
    if roe is None or not earnings_ps or not price or price <= 0:
        return {"ok": False}
    rng = random.Random(1_234_567)
    ivs: list[float] = []
    for _ in range(iterations):
        roe_s = max(rng.gauss(roe, abs(roe) * 0.20 + 0.01), 0.01)
        r_s = min(max(rng.gauss(r, 0.015), 0.05), 0.20)
        g_s = min(max(rng.gauss(g, 0.005), 0.0), G_CAP)
        pe = justified_pe(roe_s, r_s, g_s)
        if pe is not None and pe > 0:
            ivs.append(pe * earnings_ps)
    if len(ivs) < iterations * 0.5:
        return {"ok": False}
    ivs.sort()

    def pct(q):
        return ivs[min(int(q * len(ivs)), len(ivs) - 1)]

    p50 = pct(0.50)
    return {"ok": True, "iterations": len(ivs),
            "p10": pct(0.10), "p25": pct(0.25), "p50": p50, "p75": pct(0.75),
            "p90": pct(0.90), "prob_undervalued": sum(1 for v in ivs if v > price) / len(ivs),
            "median_upside": _upside(p50, price), "current_price": price}
