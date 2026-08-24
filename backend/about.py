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
         "Mature wide-moat compounders (where a strict DCF extrapolates a depressed "
         "trailing growth rate into an artifact-low value) fall back to a justified "
         "P/E earnings-power model — fair P/E = (1 - g/ROE) / (r - g), on normalized "
         "earnings — used only when the DCF is clearly the artifact, with the "
         "multiple clamped so it never justifies a bubble.",
     ]},
    {"title": "Scores the business 0-100 -> Buy / Hold / Avoid",
     "items": [
         "Weighs cheapness, business quality (returns on capital), growth, "
         "balance-sheet strength, whether it beats inflation over 10-15 years, and "
         "margins — each with the reasoning shown in plain English.",
         "Selectable strategy — reweight the pillars to match your style (Balanced, "
         "Deep value, Quality compounder, GARP, or Conservative). It applies to "
         "both the single-stock score and the screener, alongside the tunable "
         "valuation assumptions (discount rate, margin of safety, etc.).",
         "Sector-relative context: shows how the name stacks up against its own "
         "sector's medians (a 13% ROIC is elite for a utility, mediocre for "
         "software). Informational — it doesn't move the score.",
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
         "DuPont ROE decomposition — splits return on equity into profitability x "
         "asset efficiency x leverage, so you can see whether a high ROE is earned "
         "or just juiced by debt.",
         "Segment breakdown — revenue and (when disclosed) operating income by "
         "reportable segment and by product, parsed from the 10-K's XBRL, so you "
         "can see where the money and the profit actually come from.",
     ]},
    {"title": "Solvency, debt & payout red-flag checks",
     "items": [
         "Refinancing risk — reads the debt maturity ladder and asks whether cash + "
         "free cash flow covers the near-term wall, and whether rolling it at +300bps "
         "would break interest coverage.",
         "Leverage trend — whether Net Debt/EBITDA is climbing and coverage "
         "compressing toward the levels where lenders set covenants (the trajectory a "
         "snapshot misses).",
         "Working-capital quality — receivables or inventory growing faster than "
         "sales, an early tell of stuffed channels or unsold product.",
         "Dividend coverage — is the dividend funded by free cash flow over ~5 years, "
         "or by debt and asset sales (a cut waiting to happen)?",
         "Data cross-check — when two independent providers disagree materially on a "
         "foreign filer's numbers, the valuation is flagged untrustworthy and the "
         "name downgraded, rather than shown as a bargain.",
         "Acquisition-accounting / impairment risk — flags a balance sheet that's "
         "mostly acquired goodwill with a negative tangible book (the debt-funded "
         "roll-up pattern), where one writedown erases the equity cushion.",
     ]},
    {"title": "AI second opinion (optional)",
     "items": [
         "Claude reads the business and writes up the moat, management, risks and "
         "the bull/bear case in words.",
     ]},
    {"title": "Beyond one stock",
     "items": [
         "Compare several stocks side by side.",
         "Weekly buy screen scans the whole investable US market (~2,000 names) in "
         "two passes — a fast scan, then a deep EDGAR + SimFin re-verify of the "
         "leaders, so a candidate's score matches its single-stock deep-dive — and "
         "surfaces only names clearing your bar, grouped by sector, with "
         "week-over-week changes. It can run on its own overnight.",
         "Track record tab: each week's shortlist stacked against the last, so the "
         "names that keep earning their place — the highest-conviction ideas — rise "
         "to the top.",
         "Watchlist + portfolio view: track names, get alerted when one hits your "
         "buy-below price, and see holdings' value, sector mix and weighted quality.",
         "Trend charts (price, revenue, cash flow, EPS, margins, returns) and "
         "tunable assumptions that instantly re-run everything.",
     ]},
]

THESIS = [
    {"title": "Don't lose money permanently",
     "items": ["Avoiding a permanent loss of capital is the first job — a 50% loss "
               "needs a 100% gain to recover, so the disasters you dodge matter more "
               "than the winners you catch. Distress, cooked books, a refinancing "
               "wall, deteriorating leverage, an uncovered dividend, or a goodwill-"
               "impairment setup disqualify a name however cheap it looks."]},
    {"title": "Stay in your circle of competence",
     "items": ["Only own businesses you understand well enough to name what would "
               "break the thesis. If you can't state the two or three things that "
               "would prove you wrong, you don't understand it well enough to own it."]},
    {"title": "Own quality — including how it's run",
     "items": ["High returns on capital, a durable moat, and a strong balance sheet "
               "— plus management that allocates capital sensibly (buys back stock "
               "cheaply, avoids empire-building M&A). Great economics run by poor "
               "allocators still destroy value."]},
    {"title": "A margin of safety that scales with certainty",
     "items": ["Price is what you pay; value is what you get. Demand a deep discount "
               "(pay ≤ ~75% of intrinsic value) for cyclical, levered or uncertain "
               "names; accept closer to fair value only for the most predictable, "
               "fortress-quality compounders — for them the certainty itself is the "
               "safety. The required discount is set per stock accordingly."]},
    {"title": "Beat inflation at minimum — aim to beat the index",
     "items": ["Beating inflation (~3%) is the floor a real return must clear; the "
               "goal is to outperform a low-cost index fund — the alternative you "
               "could hold for free. If you can't reasonably expect to beat the "
               "index, own the index. Run a concentrated, hand-picked book only "
               "where you have real reason to clear that higher bar, and size each "
               "position to conviction × certainty."]},
    {"title": "Hold while the thesis holds; sell when it breaks",
     "items": ["Buy to own for a decade, so durability outweighs this quarter — but "
               "'hold' is conditional, not a vow. Monitor the thesis-breakers, act "
               "when the moat erodes or the facts change, and remember that holding "
               "cash and waiting for a fat pitch is a legitimate position."]},
    {"title": "Judge decisions by process, not outcome",
     "items": ["Track your calls, actively seek the evidence that would make you "
               "wrong, and update when it appears. A good outcome from a bad process "
               "is luck; the aim is a sound process, honestly measured — including "
               "admitting when a check or the score has no real edge."]},
]

GUARDRAILS = [
    {"title": "The right model for each business",
     "items": ["Operating companies: free-cash-flow / owner-earnings DCF. Banks, "
               "insurers, brokers: justified price-to-book on through-cycle ROE. "
               "REITs: FFO (funds from operations). Mature wide-moat compounders "
               "whose DCF comes out artifact-low: a justified-P/E earnings-power "
               "model. A one-size DCF misprices these badly, so the fitting model "
               "is used instead."]},
    {"title": "Forensic gate (avoid blow-ups & frauds)",
     "items": ["Altman Z-score (bankruptcy risk) and Beneish M-score (earnings-"
               "manipulation profile) dock the score and block a Buy, however "
               "cheap the stock looks."]},
    {"title": "Don't trust a too-good number",
     "items": ["An implausible implied upside (>100%), an unresolved foreign "
               "currency, or physically impossible fundamentals (e.g. a broken "
               "revenue line giving a >100% net margin) are flagged and excluded "
               "from buy candidates rather than shown as a bargain."]},
    {"title": "Peak-earnings & capex-cycle checks",
     "items": ["Flags when margins/ROE are well above the company's own average "
               "(cyclical peak). Heavy-capex names get an owner-earnings value "
               "range, not a single flattered-or-depressed number — and you can "
               "stress margins back to normal with the normalization slider."]},
    {"title": "Honest cash flow & risk",
     "items": ["Stock-based comp is subtracted from cash flow (no flattered FCF). "
               "The discount rate is risk-adjusted per stock by *fundamental* risk — "
               "leverage, thin interest coverage, erratic earnings, weak returns on "
               "capital, small size, emerging-market domicile — NOT by beta, because "
               "price volatility isn't business risk (a steady fortress compounder is "
               "safer than a volatile-but-sound one). "
               "ADR reporting currencies are converted to the trading currency."]},
    {"title": "Solvency & payout downgrades",
     "items": ["Stressed leverage, a near-term refinancing wall it can't cover, a "
               "materially deteriorating debt trend, or a dividend that free cash "
               "flow doesn't fund each dock the score and can knock a name out of "
               "Buy — a cheap price doesn't offset a balance sheet that may not "
               "survive the hold."]},
    {"title": "A Buy must actually be a Buy",
     "items": ["Screen candidates must be rated Buy, not merely score above the bar. "
               "A high score that a guardrail downgraded — overvalued, distressed, "
               "flagged by the data cross-check — is not surfaced as a candidate."]},
    {"title": "Know when to distrust the score",
     "items": ["Thin history (<6 years) is flagged as low-confidence. The reverse-"
               "DCF shows the growth the current price already assumes. Sector "
               "context shows whether a metric is actually good for its sector. And "
               "when two data sources disagree on a foreign filer, the fair value "
               "is marked unreliable rather than trusted."]},
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
    {"title": "The score is a filter, not a proven signal",
     "items": [
         "A point-in-time backtest (score the past on data knowable then, measure "
         "the actual forward return) found the composite 0-100 score has NO clear "
         "ability to predict returns — no free single-number score realistically "
         "would, and the test is survivorship-biased and dividend-blind on top.",
         "So treat the score as a filter and a checklist, never a buy signal. Trust "
         "the specific red flags — forensics, refinancing, leverage trend, "
         "working-capital, dividend coverage, the data cross-check — each of which "
         "rests on something you can check, far more than the overall grade.",
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
        "thesis": THESIS,
        "guardrails": GUARDRAILS,
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
            "decision_thresholds": _decision_thresholds(scoring, valuation, A),
        },
    }


def _decision_thresholds(scoring, valuation, A) -> list[dict[str, str]]:
    """The concrete parameter values that turn analysis into a verdict — read live
    from the code so this list can't drift from what the app actually does."""
    import leverage_trend as lt, refinancing as rf, working_capital as wc
    import dividend_coverage as dc, intangibles as ig

    def pct(x): return f"{x*100:.0f}%"
    rows = [
        ("Rating: BUY", f"score ≥ {scoring.BUY_THRESHOLD}"),
        ("Rating: HOLD / WATCH", f"score {scoring.HOLD_THRESHOLD}–{scoring.BUY_THRESHOLD - 1}"),
        ("Rating: AVOID", f"score < {scoring.HOLD_THRESHOLD}"),
        ("Overvaluation override", "a would-be BUY trading > 30% above intrinsic value is cut to HOLD"),
        ("Margin of safety (base)", f"pay ≤ {pct(1 - A['margin_of_safety'])} of intrinsic value "
                                    f"({pct(A['margin_of_safety'])} discount)"),
        ("Margin of safety (scaled)", "12% for fortress-certain names → 45% for the least certain — "
                                      "set per stock by a certainty score"),
        ("DCF discount rate", f"{pct(A['discount_rate'])} base, risk-adjusted 6–25% by fundamental risk"),
        ("DCF terminal growth", pct(A['terminal_growth']) + " (≈ GDP + inflation)"),
        ("DCF growth fade", f"excess growth decays ×{valuation.GROWTH_FADE}/yr toward terminal"),
        ("Inflation hurdle", pct(A['inflation_hurdle']) + " minimum real return"),
        ("Forensics", "Altman Z: safe > 2.99, distress < 1.81 · Beneish M: flag > −1.78"),
        ("Leverage trend", f"Net Debt/EBITDA elevated ≥ {lt.LEV_ELEVATED:.0f}×, stressed ≥ "
                           f"{lt.LEV_STRESS:.0f}×; coverage floor {lt.COV_FLOOR:.1f}×"),
        ("Refinancing stress", f"near-term = due ≤ {rf.NEAR_TERM_YEARS}yr; re-tested at "
                              f"+{rf.STRESS_BPS*10000:.0f}bps"),
        ("Working-capital build", f"receivables/inventory intensity ≥ {wc.RISE_ELEVATED:.2f}× "
                                 "its recent norm = elevated"),
        ("Dividend coverage", f"free cash flow ≥ {dc.COMFORTABLE:.1f}× dividends = comfortable, "
                             f"< 1.0× = uncovered"),
        ("Impairment / roll-up", f"goodwill+intangibles ≥ {ig.CONCENTRATED*100:.0f}% of assets AND "
                                "negative tangible book = high"),
    ]
    return [{"name": n, "value": v} for n, v in rows]
