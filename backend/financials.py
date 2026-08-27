"""Valuation for financial companies (banks, insurers, brokers, asset managers).

Why a separate model: a free-cash-flow or earnings DCF misprices financials
badly. Their "cash flow" is meaningless (deposits, policy reserves, and float
dominate the balance sheet), and adding "net cash" to an equity DCF double-counts
capital that's already working. That's what produced Arch Capital's absurd +100%.

The right tool is the one bank/insurance analysts actually use — the **justified
price-to-book** model, the closed form of the residual-income / Gordon model:

    justified P/B = (ROE - g) / (r - g)
    fair value    = justified P/B x book value per share

A financial is worth a premium to book only to the extent its return on equity
(ROE) exceeds the return investors require (r); the faster it can grow that
excess (g), the bigger the premium. We use **through-cycle average ROE** as the
sustainable figure, which also stops a cyclical peak (a hard insurance market, a
low-rate lending boom) from inflating the value.
"""
from __future__ import annotations

import random
from typing import Any, Optional

PB_CAP = 6.0   # a financial rarely justifies more than ~6x book; clamp runaways
STAGE1_YEARS = 10   # high-growth horizon for the two-stage model


def justified_pb(roe: Optional[float], r: float, g: float) -> Optional[float]:
    """Fair price-to-book from sustainable ROE, required return r, growth g
    (single-stage Gordon: fine for a mature financial growing at a rate below r)."""
    if roe is None or r is None or r <= g:
        return None
    return max(0.0, min((roe - g) / (r - g), PB_CAP))


def justified_pb_two_stage(roe: Optional[float], r: float, g1: Optional[float],
                           g_terminal: float, n_years: int = STAGE1_YEARS
                           ) -> Optional[float]:
    """Two-stage justified P/B via the residual-income (excess-return) model:

        value / book = 1 + sum_t (ROE - r)*book_{t-1} / (1+r)^t  +  terminal

    Book compounds at the near-term rate g1 for n_years (which may exceed r — that's
    exactly what the single-stage Gordon form can't represent, and why it under-
    prices a fast-compounding financial like Kinsale or Progressive), then residual
    income grows at g_terminal (< r) in perpetuity. Reduces to ~the single-stage
    value when g1 == g_terminal. Bounded by PB_CAP so a durable high-ROE grower
    can't run away."""
    if roe is None or r is None or r <= g_terminal:
        return None
    if g1 is None:
        g1 = g_terminal
    excess = roe - r
    book = 1.0            # normalise book to 1; result is a multiple of book
    pv_ri = 0.0
    for t in range(1, n_years + 1):
        pv_ri += (excess * book) / (1 + r) ** t   # RI_t = (ROE-r) * book at start of t
        book *= (1 + g1)                           # grow book to end of year t
    # Terminal: residual income continues on the stage-1-end book, growing at
    # g_terminal forever — a Gordon perpetuity, discounted back to today.
    terminal_ri = excess * book                    # RI in the first terminal year
    pv_terminal = (terminal_ri / (r - g_terminal)) / (1 + r) ** n_years
    pb = 1.0 + pv_ri + pv_terminal
    return max(0.0, min(pb, PB_CAP))


def _upside(iv: Optional[float], price: Optional[float]) -> Optional[float]:
    return ((iv - price) / price) if (iv and price and price > 0) else None


def value(book_value: Optional[float], shares: Optional[float], roe: Optional[float],
          r: float, g: float, price: Optional[float],
          margin_of_safety: float,
          growth_stage1: Optional[float] = None) -> dict[str, Any]:
    """Headline justified-P/B valuation, in the same shape build_valuation_range
    returns so the rest of the app renders it unchanged (method='book-value').

    When `growth_stage1` (a near-term book/earnings growth rate) is supplied and
    exceeds the terminal rate, value on the two-stage residual-income model so a
    fast-compounding financial isn't under-priced by a single terminal growth; a
    mature financial (g1 <= g) keeps the single-stage form."""
    if not book_value or book_value <= 0 or not shares or shares <= 0:
        return {"ok": False, "method": "book-value", "is_financial": True,
                "suspect": True, "suspect_reason": "No usable book value to value on."}
    bvps = book_value / shares
    if growth_stage1 is not None and growth_stage1 > g:
        pb = justified_pb_two_stage(roe, r, growth_stage1, g)
    else:
        pb = justified_pb(roe, r, g)
    if pb is None or roe is None:
        return {"ok": False, "method": "book-value", "is_financial": True,
                "suspect": True, "suspect_reason": "Return on equity unavailable."}
    iv = pb * bvps
    current_pb = (price / bvps) if (price and bvps > 0) else None
    up = _upside(iv, price)
    # Implied ROE the current price bakes in (the P/B analog of a reverse DCF).
    implied_roe = (current_pb * (r - g) + g) if current_pb is not None else None
    suspect, reason = False, None
    if up is not None and up > 1.0:
        suspect, reason = True, (f"Implied upside ~{up*100:.0f}% is implausibly high — "
                                 "treat the book-value model's inputs with caution.")
    return {
        "ok": True, "method": "book-value", "is_financial": True,
        "low": iv, "high": iv, "mid": iv, "spread": 0.0,
        "conservative_iv": iv, "adjusted_iv": iv,
        "current_price": price, "upside_low": up, "upside_high": up, "upside_mid": up,
        "buy_below": iv * (1 - margin_of_safety), "margin_of_safety": margin_of_safety,
        "suspect": suspect, "suspect_reason": reason,
        # book-value detail block (for the UI):
        "justified_pb": pb, "current_pb": current_pb, "bvps": bvps,
        "roe_used": roe, "cost_of_equity": r, "growth": g, "implied_roe": implied_roe,
        "growth_stage1": (growth_stage1 if (growth_stage1 is not None and growth_stage1 > g)
                          else None),
        "book_value": book_value,
    }


def scenarios(bvps: float, roe: Optional[float], r: float, g: float,
              price: Optional[float], growth_stage1: Optional[float] = None
              ) -> dict[str, Any]:
    """Bear/base/bull by flexing sustainable ROE and the required return. Uses the
    two-stage model (matching the headline) when a near-term growth is supplied."""
    if roe is None or not bvps:
        return {}
    two_stage = growth_stage1 is not None and growth_stage1 > g

    def run(roe_mult, r_delta):
        r_s = min(max(r + r_delta, 0.05), 0.20)
        pb = (justified_pb_two_stage(roe * roe_mult, r_s, growth_stage1, g)
              if two_stage else justified_pb(roe * roe_mult, r_s, g))
        iv = (pb * bvps) if pb is not None else None
        # Floor at zero: a negative justified value (ROE below growth, or negative
        # book value) is an equity wipeout — show $0 / -100%, not a negative price.
        wiped = iv is not None and iv < 0
        if wiped:
            iv = 0.0
        return {"fair_value": iv, "upside": _upside(iv, price), "wiped_out": wiped}

    return {"bear": run(0.75, +0.015), "base": run(1.0, 0.0),
            "bull": run(1.25, -0.010), "current_price": price}


def monte_carlo(bvps: float, roe: Optional[float], r: float, g: float,
                price: Optional[float], iterations: int = 2000,
                growth_stage1: Optional[float] = None) -> dict[str, Any]:
    """Distribution of fair value sampling ROE, required return and growth —
    same output shape as the DCF Monte-Carlo so the UI renders it identically.
    Uses the two-stage model (matching the headline) when g1 is supplied."""
    if roe is None or not bvps or not price or price <= 0:
        return {"ok": False}
    two_stage = growth_stage1 is not None and growth_stage1 > g
    rng = random.Random(1_234_567)
    ivs: list[float] = []
    for _ in range(iterations):
        roe_s = max(rng.gauss(roe, abs(roe) * 0.20 + 0.01), 0.0)
        r_s = min(max(rng.gauss(r, 0.015), 0.05), 0.20)
        g_s = min(max(rng.gauss(g, 0.005), 0.0), 0.04)
        if two_stage:
            g1_s = min(max(rng.gauss(growth_stage1, 0.02), 0.0), 0.15)
            pb = justified_pb_two_stage(roe_s, r_s, g1_s, g_s)
        else:
            pb = justified_pb(roe_s, r_s, g_s)
        if pb is not None and pb > 0:
            ivs.append(pb * bvps)
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
