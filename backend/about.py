"""The Guide tab's content — capabilities & limitations, kept honest as the app
evolves.

Two layers:
  * CAPABILITIES / LIMITATIONS below are the plain-English descriptions. This is
    the ONE place to edit the wording when you add or change a feature.
  * build()'s "live" block is DERIVED from the running code — active data
    sources, universe sizes, the real scoring pillars & weights, thresholds,
    Monte-Carlo count, DCF defaults. Those update themselves; you never hand-edit
    a number that the code already knows.
"""
from __future__ import annotations

import os
from typing import Any

# ---- Plain-English descriptions (edit these when features change) ----
CAPABILITIES = [
    {"title": "Pulls the real numbers for you",
     "items": [
         "Type a ticker; it fetches 10-19 years of a US company's actual SEC 10-K "
         "filings (free) plus today's live price and market data.",
     ]},
    {"title": "Tells you what a stock is worth",
     "items": [
         "Discounted cash flow (DCF) — the core 'what's this worth' calculation — "
         "shown as a range (a strict version and a capex-friendly version), not a "
         "single false-precise number.",
         "Monte-Carlo: runs the valuation thousands of times with varied "
         "assumptions and reports the odds the stock is currently undervalued.",
         "Bear / base / bull fair values, plus a reverse-DCF that reveals the "
         "growth the current price already assumes.",
         "Banks, insurers and brokers are valued the way analysts actually value "
         "them — the justified price-to-book model (book value x through-cycle ROE) "
         "instead of a cash-flow DCF that doesn't fit them.",
         "REITs are valued on FFO (funds from operations = net income + real-estate "
         "depreciation) and P/FFO — not GAAP earnings or book value, both of which "
         "mislead for property companies.",
     ]},
    {"title": "Scores the business 0-100 -> Buy / Hold / Avoid",
     "items": [
         "Weighs cheapness, business quality (returns on capital), growth, "
         "balance-sheet strength, whether it beats inflation over 10-15 years, and "
         "margins — each with the reasoning shown in plain English.",
     ]},
    {"title": "Forensic safety checks (avoid getting burned)",
     "items": [
         "Altman Z-score flags bankruptcy/distress risk.",
         "Beneish M-score flags accounting that resembles book-cookers.",
         "A red flag here lowers the score and blocks a Buy — even if the stock "
         "looks cheap.",
     ]},
    {"title": "Deeper quality checks",
     "items": [
         "Earnings quality (does profit become real cash?), owner earnings, value "
         "creation (returns vs cost of capital), dividend safety, dilution / "
         "buybacks, insider & institutional ownership, analyst targets, and today's "
         "valuation vs the stock's own history.",
     ]},
    {"title": "AI second opinion (optional)",
     "items": [
         "Claude reads the business and writes up the moat, management, risks and "
         "the bull/bear case in words.",
     ]},
    {"title": "Beyond one stock",
     "items": [
         "Compare several stocks side by side.",
         "Weekly buy screen scans a large US universe and surfaces only names "
         "clearing your bar, grouped by sector, with week-over-week changes.",
         "Watchlist + portfolio view: track names, get alerted when one hits your "
         "buy-below price, and see holdings' value, sector mix and weighted quality.",
         "Trend charts (price, revenue, cash flow, EPS, margins, returns) and "
         "tunable assumptions that instantly re-run everything.",
     ]},
]

LIMITATIONS = [
    {"title": "Data",
     "items": [
         "Deep history is US companies only. Foreign stocks (ADRs) fall back to ~4-7 "
         "years and less detail.",
         "It handles individual stocks — not bonds, mutual funds, index funds or crypto.",
         "Free-tier data can be slightly delayed or occasionally incomplete.",
     ]},
    {"title": "The valuation is an estimate, not a fact",
     "items": [
         "A DCF is only as good as its assumptions — it projects the future from the "
         "past, so if the future looks nothing like the history, the number is off.",
         "It struggles with unprofitable / no-cash-flow companies (the DCF won't "
         "compute meaningfully).",
         "Forensic scores don't apply to banks, insurers or REITs (different "
         "financials) — the app skips them there.",
     ]},
    {"title": "The track-record test is directional, not proof",
     "items": [
         "The backtest uses restated (not point-in-time) data and ignores dividends. "
         "It's real evidence the scoring works, but a rough check — not a guarantee "
         "of future results.",
     ]},
    {"title": "Other",
     "items": [
         "The AI read is an opinion — it can be wrong or generic, and needs an API key.",
         "It's a local, single-user tool that only runs while the server is up — not "
         "a hosted website.",
         "It is not investment advice. It's a research assistant to sharpen your own "
         "judgment; the final call, and the responsibility, are yours.",
     ]},
]


def _universes() -> dict[str, Any]:
    out = {}
    try:
        import universe
        for scope in ("core", "full", "large"):
            try:
                out[scope] = len(universe.get(scope))
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        pass
    try:
        import all_us_symbols
        out["all_us_listed"] = len(all_us_symbols.ALL_US)
    except Exception:  # noqa: BLE001
        pass
    return out


def _pillars() -> Any:
    """Real scoring pillars + weights, read by running the live scorer on a
    synthetic healthy company. Auto-reflects any change to scoring.py."""
    try:
        from data import STATEMENT_KEYS
        import valuation
        import scoring
        yrs = [str(y) for y in range(2016, 2026)]

        def ser(vals):
            return {y: float(v) for y, v in zip(yrs, vals)}

        st = {k: {} for k in STATEMENT_KEYS}
        st["revenue"] = ser([100 + 12 * i for i in range(10)])
        st["gross_profit"] = ser([60 + 7 * i for i in range(10)])
        st["operating_income"] = ser([25 + 3 * i for i in range(10)])
        st["net_income"] = ser([18 + 2.4 * i for i in range(10)])
        st["eps"] = ser([1.8 + 0.24 * i for i in range(10)])
        st["operating_cashflow"] = ser([22 + 3 * i for i in range(10)])
        st["capex"] = ser([-(5 + i) for i in range(10)])
        st["depreciation"] = ser([4 + 0.3 * i for i in range(10)])
        st["total_equity"] = ser([80 + 5 * i for i in range(10)])
        st["total_debt"] = ser([30 - i for i in range(10)])
        st["cash"] = ser([20 + 2 * i for i in range(10)])
        st["total_assets"] = ser([150 + 10 * i for i in range(10)])
        st["current_assets"] = ser([50 + 5 * i for i in range(10)])
        st["current_liabilities"] = ser([25 + i for i in range(10)])
        st["retained_earnings"] = ser([40 + 6 * i for i in range(10)])
        info = {"current_price": 40, "shares_outstanding": 10, "market_cap": 400,
                "sector": "Technology", "total_debt": 20, "total_cash": 40, "beta": 1.1,
                "currency": "USD", "financial_currency": "USD",
                "currency_converted": False, "currency_unresolved": False, "fx_rate": 1.0}
        stock = {"ticker": "SYNTH", "error": None, "info": info,
                 "statements": st, "price_history": []}
        v = scoring.score(valuation.compute_metrics(stock))
        return [{"name": p["name"], "max": p["max"]} for p in v["pillars"]]
    except Exception:  # noqa: BLE001
        return None


def build() -> dict[str, Any]:
    """Capabilities & limitations + a live snapshot of the current configuration."""
    import valuation
    import scoring

    edgar = True  # EDGAR is the default single-stock source (no key required)
    simfin = bool(os.environ.get("SIMFIN_API_KEY"))
    ai = bool(os.environ.get("ANTHROPIC_API_KEY"))

    sources = ["SEC EDGAR — 10-19yr as-filed 10-K fundamentals (US filers)",
               "Yahoo Finance — live price, market data & analyst sentiment"]
    if simfin:
        sources.append("SimFin — fallback statements for names EDGAR can't cover")
    else:
        sources.append("SimFin — not configured (optional fallback; set SIMFIN_API_KEY)")
    sources.append("Claude AI qualitative read — "
                   + ("ON" if ai else "OFF (set ANTHROPIC_API_KEY to enable)"))

    A = valuation.DEFAULT_ASSUMPTIONS
    return {
        "capabilities": CAPABILITIES,
        "limitations": LIMITATIONS,
        "live": {
            "note": "This snapshot is read live from the running code, so it stays "
                    "accurate as the app is revised.",
            "data_sources": sources,
            "universe_sizes": _universes(),
            "scoring": {
                "pillars": _pillars(),
                "buy_bar": scoring.BUY_THRESHOLD,
                "hold_bar": scoring.HOLD_THRESHOLD,
                "forensic_max_penalty": 20,
            },
            "valuation_defaults": {
                "discount_rate": A["discount_rate"],
                "terminal_growth": A["terminal_growth"],
                "projection_years": A["projection_years"],
                "inflation_hurdle": A["inflation_hurdle"],
                "margin_of_safety": A["margin_of_safety"],
                "monte_carlo_runs": valuation.MC_ITERATIONS,
            },
            "forensic_thresholds": {
                "altman_safe": "> 2.99", "altman_distress": "< 1.81",
                "beneish_manipulation_flag": "> -1.78",
            },
        },
    }
