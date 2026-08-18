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


def justified_pb(roe: Optional[float], r: float, g: float) -> Optional[float]:
    """Fair price-to-book from sustainable ROE, required return r, growth g."""
    if roe is None or r is None or r <= g:
        return None
    return max(0.0, min((roe - g) / (r - g), PB_CAP))


def _upside(iv: Optional[float], price: Optional[float]) -> Optional[float]:
    return ((iv - price) / price) if (iv and price and price > 0) else None


def value(book_value: Optional[float], shares: Optional[float], roe: Optional[float],
          r: float, g: float, price: Optional[float],
          margin_of_safety: float) -> dict[str, Any]:
    """Headline justified-P/B valuation, in the same shape build_valuation_range
    returns so the rest of the app renders it unchanged (method='book-value')."""
    if not book_value or book_value <= 0 or not shares or shares <= 0:
        return {"ok": False, "method": "book-value", "is_financial": True,
                "suspect": True, "suspect_reason": "No usable book value to value on."}
    bvps = book_value / shares
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
        "book_value": book_value,
    }


def scenarios(bvps: float, roe: Optional[float], r: float, g: float,
              price: Optional[float]) -> dict[str, Any]:
    """Bear/base/bull by flexing sustainable ROE and the required return."""
    if roe is None or not bvps:
        return {}

    def run(roe_mult, r_delta):
        pb = justified_pb(roe * roe_mult, min(max(r + r_delta, 0.05), 0.20), g)
        iv = (pb * bvps) if pb is not None else None
        return {"fair_value": iv, "upside": _upside(iv, price)}

    return {"bear": run(0.75, +0.015), "base": run(1.0, 0.0),
            "bull": run(1.25, -0.010), "current_price": price}


def monte_carlo(bvps: float, roe: Optional[float], r: float, g: float,
                price: Optional[float], iterations: int = 2000) -> dict[str, Any]:
    """Distribution of fair value sampling ROE, required return and growth —
    same output shape as the DCF Monte-Carlo so the UI renders it identically."""
    if roe is None or not bvps or not price or price <= 0:
        return {"ok": False}
    rng = random.Random(1_234_567)
    ivs: list[float] = []
    for _ in range(iterations):
        roe_s = max(rng.gauss(roe, abs(roe) * 0.20 + 0.01), 0.0)
        r_s = min(max(rng.gauss(r, 0.015), 0.05), 0.20)
        g_s = min(max(rng.gauss(g, 0.005), 0.0), 0.04)
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
