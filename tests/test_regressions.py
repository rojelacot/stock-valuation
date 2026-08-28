#!/usr/bin/env python3
"""Regression tests — one per valuation-integrity bug fixed, so none can silently
come back. Offline, no network; run with:

    .venv/bin/python tests/test_regressions.py     # exits non-zero on any failure

Each test names the ticker that first surfaced the bug.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import edgar                                                    # noqa: E402
import data                                                     # noqa: E402
from data import STATEMENT_KEYS                                 # noqa: E402
from valuation import compute_metrics, resolve_assumptions      # noqa: E402
from scoring import score                                       # noqa: E402

A = resolve_assumptions()
FAILS: list[str] = []


def ok(cond: bool, label: str) -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        FAILS.append(label)


def close(a, b, tol=0.02) -> bool:
    return a is not None and b is not None and b != 0 and abs(a / b - 1) <= tol


def years(start, vals):
    return {str(start + i): v for i, v in enumerate(vals)}


def make_stock(statements=None, info=None):
    st = {k: {} for k in STATEMENT_KEYS}
    st.update(statements or {})
    inf = {"name": "TEST", "currency": "USD", "financial_currency": "USD",
           "currency_converted": False, "currency_unresolved": False, "fx_rate": 1.0}
    inf.update(info or {})
    return {"ticker": "TEST", "error": None, "info": inf, "statements": st,
            "price_history": []}


def metrics(stock):
    m = compute_metrics(stock, resolve_assumptions())  # fresh assumptions (mutated in place)
    return m


# ── Fix 1 · EDGAR share-scale repair (_fix_scale) — MCD $94M/share ─────────────
adj = edgar._fix_scale({"2020": 750_100_000.0, "2021": 751.8, "2024": 721.9, "2025": 716.4})
ok(close(adj["2025"], 716_400_000), "MCD: _fix_scale rescales a millions-tagged year up 1e6x")
ok(adj["2020"] == 750_100_000.0, "MCD: _fix_scale leaves correctly-scaled years untouched")
_norm = {"2021": 100e6, "2022": 98e6, "2023": 96e6}
ok(edgar._fix_scale(_norm) == _norm, "_fix_scale leaves a normal series unchanged")

# ── Fix 8 · Split-adjusted dilution (_split_adjust) — AAPL/NVDA ────────────────
_sh = {"2019": 2.5e9, "2020": 2.5e9, "2021": 2.5e9, "2022": 2.5e9,
       "2023": 25e9, "2024": 25e9, "2025": 25e9}         # clean 10:1 split at 2023
_eq = {y: 40e9 for y in _sh}                              # equity flat → a split
_sa = edgar._split_adjust(_sh, _eq)
ok(close(_sa["2019"], 25e9), "NVDA: _split_adjust lifts pre-split years onto the post-split basis")
ok(_sa["2025"] == 25e9, "_split_adjust leaves the latest count untouched")
# same share jump but equity jumped too → real dilution, must be preserved
_sh2 = {"2019": 1e9, "2020": 1.2e9, "2021": 1.4e9, "2022": 1.6e9, "2023": 16e9}
_eq2 = {"2019": 1e9, "2020": 1.2e9, "2021": 1.4e9, "2022": 1.6e9, "2023": 16e9}
ok(edgar._split_adjust(_sh2, _eq2) == _sh2, "SNOW: _split_adjust preserves real issuance (equity grew too)")

# ── Fix 6 · ADR currency-mismatch detector (_currency_scale_mismatch) — TSM ────
ok(data._currency_scale_mismatch(5.5e11, 1.1e12) is True,
   "TSM: currency mismatch flagged when implied P/E < 2.5 (local-ccy earnings)")
ok(data._currency_scale_mismatch(5.5e11, 2.0e10) is False,
   "currency mismatch NOT flagged at a normal P/E (~27x)")
ok(data._currency_scale_mismatch(5.5e11, -1e9) is False,
   "currency mismatch NOT flagged on negative earnings")

# A healthy base company used by several pipeline tests below. Scores well and is
# deeply undervalued at the base price, so it rates BUY.
def healthy(price, extra_info=None, statements_over=None):
    st = {
        "revenue": years(2010, [100, 112, 125, 138, 152, 168, 185, 205, 226, 250, 276, 305, 336, 371, 410, 452]),
        "gross_profit": years(2010, [v * 0.62 for v in [100, 112, 125, 138, 152, 168, 185, 205, 226, 250, 276, 305, 336, 371, 410, 452]]),
        "operating_income": years(2010, [v * 0.30 for v in [100, 112, 125, 138, 152, 168, 185, 205, 226, 250, 276, 305, 336, 371, 410, 452]]),
        "net_income": years(2010, [v * 0.22 for v in [100, 112, 125, 138, 152, 168, 185, 205, 226, 250, 276, 305, 336, 371, 410, 452]]),
        "eps": years(2010, [v * 0.22 / 100 for v in [100, 112, 125, 138, 152, 168, 185, 205, 226, 250, 276, 305, 336, 371, 410, 452]]),
        "operating_cashflow": years(2010, [v * 0.26 for v in [100, 112, 125, 138, 152, 168, 185, 205, 226, 250, 276, 305, 336, 371, 410, 452]]),
        "capex": years(2010, [-v * 0.05 for v in [100, 112, 125, 138, 152, 168, 185, 205, 226, 250, 276, 305, 336, 371, 410, 452]]),
        "depreciation": years(2010, [v * 0.04 for v in [100, 112, 125, 138, 152, 168, 185, 205, 226, 250, 276, 305, 336, 371, 410, 452]]),
        "total_equity": years(2010, [v * 0.9 for v in [100, 112, 125, 138, 152, 168, 185, 205, 226, 250, 276, 305, 336, 371, 410, 452]]),
        "total_debt": years(2010, [20] * 16),
        "cash": years(2010, [30] * 16),
        "current_assets": years(2010, [80] * 16),
        "current_liabilities": years(2010, [30] * 16),
        "ebitda": years(2010, [v * 0.34 for v in [100, 112, 125, 138, 152, 168, 185, 205, 226, 250, 276, 305, 336, 371, 410, 452]]),
        "shares": years(2010, [100] * 16),
    }
    st.update(statements_over or {})
    info = {"current_price": price, "shares_outstanding": 100, "market_cap": price * 100,
            "sector": "Technology", "total_debt": 20, "total_cash": 30}
    info.update(extra_info or {})
    return make_stock(statements=st, info=info)


# ── Fix 4 · Uniform >100% suspect cap — MLI/BAH ───────────────────────────────
_cheap = metrics(healthy(price=2.0))          # fair value »2x price → >100% upside
ok(_cheap["valuation"].get("upside_mid", 0) > 1.0, "MLI: cheap company really does imply >100% upside")
ok(_cheap["valuation"].get("suspect") is True, "MLI/BAH: any model's >100% upside is flagged suspect")

# ── Fix 7 · Buy-zone rating gate — META ───────────────────────────────────────
_bb = metrics(healthy(price=8.0))["valuation"].get("buy_below")
ok(_bb is not None, "buy_below computed for the healthy company")
_below = score(metrics(healthy(price=(_bb or 8) * 0.7)))         # deep in the buy zone
_above = score(metrics(healthy(price=(_bb or 8) * 1.2)))         # above the buy-below
ok(_below["rating"] == "BUY", "healthy + deeply in the buy zone rates BUY")
ok(_above["rating"] != "BUY", "META: a high scorer ABOVE its buy-below is NOT rated BUY")

# ── Fix 3 · Share-scale guard rejects a filed count far off the implied — MCD ──
# Filed shares tag as ~716 (a scale error that reached compute_metrics); market cap
# and price imply ~700M. The guard must reject 716 and use the implied count.
_g = healthy(price=40.0, extra_info={"market_cap": 28e9, "shares_outstanding": 700e6},
             statements_over={"shares": years(2018, [716] * 8)})
metrics(_g)  # mutates _g["info"]["shares_outstanding"] in place
ok(_g["info"]["shares_outstanding"] > 1e8,
   "MCD: a filed share count >3x off the implied one is rejected (not 716)")

# ── Fix 8b · Reconciliation includes the filed count when market data is broken — PARA
# Filed 664M is correct; broken market data implies only 3.6M. Fallback must pick 664M.
_p = healthy(price=40.0, extra_info={"market_cap": 3.6e6 * 40, "shares_outstanding": 3.5e6},
             statements_over={"shares": years(2018, [664e6] * 8)})
metrics(_p)  # mutates _p["info"] in place
ok(close(_p["info"]["shares_outstanding"], 664e6, tol=0.05),
   "PARA: reconciliation keeps the correct 664M filed count over broken 3.6M market data")

# ── Fix 5 · Insurance-float DCF cap for capital-light financials — AMP ─────────
# A capital-light financial (SIC 6282) with float-inflated cash flow (OCF = 3x NI)
# must be valued no higher than one whose cash flow equals net income.
def fin(ocf_mult):
    ni = [v * 0.22 for v in [100, 112, 125, 138, 152, 168, 185, 205, 226, 250, 276, 305, 336, 371, 410, 452]]
    return healthy(price=40.0, extra_info={"sic": 6282},
                   statements_over={
                       "operating_cashflow": years(2010, [n * ocf_mult for n in ni]),
                       "capex": years(2010, [0] * 16),
                   })
_fair_ni = metrics(fin(1.0))["valuation"].get("mid")
_fair_float = metrics(fin(3.0))["valuation"].get("mid")
ok(_fair_ni and _fair_float and _fair_float <= _fair_ni * 1.15,
   "AMP: float-inflated cash flow (3x NI) is capped — fair value not inflated vs NI-based")

# ── currency_unresolved is respected downstream (kept out of BUY) ─────────────
_u = metrics(healthy(price=2.0, extra_info={"currency_unresolved": True}))
ok(score(_u)["rating"] != "BUY", "a currency-unresolved name is never rated BUY")

# ── Mortgage-REIT routing — NLY/AGNC (a bond portfolio, not a property REIT) ───
def reit(dep_over_ni):
    _ni = [v * 0.22 for v in [100, 112, 125, 138, 152, 168, 185, 205, 226, 250, 276, 305, 336, 371, 410, 452]]
    return healthy(price=40.0, extra_info={"sic": 6798},
                   statements_over={"depreciation": years(2010, [n * dep_over_ni for n in _ni])})
ok(metrics(reit(0.02))["valuation"].get("method") == "book-value",
   "NLY: a mortgage REIT (negligible real-estate depreciation) routes to book-value")
ok(metrics(reit(1.5))["valuation"].get("method") == "ffo",
   "a property REIT (heavy real-estate depreciation) stays on FFO")

# ── Hyper-growth artifact-low DCF is softened, not shown as a hard bear — PLTR ─
def hypergrowth(price, g, n=6):
    _rev = [100 * (1 + g) ** i for i in range(n)]
    st = {"revenue": years(2019, _rev), "gross_profit": years(2019, [r * 0.72 for r in _rev]),
          "operating_income": years(2019, [r * 0.08 for r in _rev]),
          "net_income": years(2019, [r * 0.05 for r in _rev]),
          "eps": years(2019, [r * 0.05 / 50 for r in _rev]),
          "operating_cashflow": years(2019, [r * 0.06 for r in _rev]),
          "capex": years(2019, [-r * 0.05 for r in _rev]),
          "depreciation": years(2019, [r * 0.03 for r in _rev]),
          "total_equity": years(2019, [r * 0.5 for r in _rev]),
          "total_debt": years(2019, [10] * n), "cash": years(2019, [20] * n),
          "ebitda": years(2019, [r * 0.11 for r in _rev])}
    return make_stock(statements=st, info={"current_price": price, "shares_outstanding": 50,
                                           "market_cap": price * 50, "sector": "Technology"})
ok(metrics(hypergrowth(80, 0.25))["valuation"].get("low_multiple_artifact") is True,
   "PLTR: a profitable hyper-grower's artifact-low DCF is flagged low-confidence")
ok(not metrics(hypergrowth(3, 0.25))["valuation"].get("low_multiple_artifact"),
   "a cheap hyper-grower (small downside) is NOT flagged")
ok(not metrics(hypergrowth(80, 0.03))["valuation"].get("low_multiple_artifact"),
   "a slow/shrinking grower with a low DCF value is left un-flagged (can deserve it)")

# ── MLP conservatism — MPLX/WES (no growth premium; D&A isn't free cash) ───────
from valuation import is_mlp                                   # noqa: E402
ok(is_mlp({"name": "Energy Transfer LP"}) and is_mlp({"name": "Enterprise Products Partners L.P."}),
   "MPLX: an 'L.P.'/'LP' name is detected as a master limited partnership")
ok(not is_mlp({"name": "ONEOK, Inc."}) and not is_mlp({"name": "Kinder Morgan, Inc."}),
   "a C-corp midstream peer (Inc.) is NOT treated as an MLP")
def _pipeline(name):
    _rev = [100 * 1.08 ** i for i in range(12)]
    st = {"revenue": years(2014, _rev), "gross_profit": years(2014, [r * 0.5 for r in _rev]),
          "operating_income": years(2014, [r * 0.15 for r in _rev]),
          "net_income": years(2014, [r * 0.08 for r in _rev]),
          "eps": years(2014, [r * 0.08 / 50 for r in _rev]),
          "operating_cashflow": years(2014, [r * 0.22 for r in _rev]),  # D&A add-back -> FCF > NI
          "capex": years(2014, [-r * 0.05 for r in _rev]),
          "depreciation": years(2014, [r * 0.12 for r in _rev]),
          "total_equity": years(2014, [r * 0.6 for r in _rev]),
          "total_debt": years(2014, [r * 0.5 for r in _rev]), "cash": years(2014, [10] * 12),
          "ebitda": years(2014, [r * 0.27 for r in _rev])}
    return make_stock(statements=st, info={"name": name, "current_price": 30,
                                           "shares_outstanding": 50, "market_cap": 1500, "sector": "Energy"})
ok(metrics(_pipeline("Midstream Partners L.P."))["valuation"]["mid"]
   < metrics(_pipeline("Midstream Holdings Inc."))["valuation"]["mid"],
   "WES: an MLP is valued more conservatively than the identical C-corp (no premium, D&A not free cash)")

# ── Preferred stock subtracted from common book value — Citigroup ──────────────
def _bank(pref):
    st = {"revenue": years(2010, [50] * 16), "net_income": years(2010, [10] * 16),
          "total_equity": years(2010, [100] * 16), "shares": years(2010, [10] * 16),
          "preferred_stock": years(2010, [pref] * 16)}
    return make_stock(statements=st, info={"sic": 6021, "current_price": 8,
                                           "shares_outstanding": 10, "market_cap": 80})
_bvps_pref = metrics(_bank(20))["valuation"].get("bvps")     # 20 of 100 equity is preferred
_bvps_none = metrics(_bank(0))["valuation"].get("bvps")
ok(_bvps_pref is not None and _bvps_none is not None and _bvps_pref < _bvps_none,
   "C: preferred stock is subtracted from equity before book value per COMMON share")
ok(abs(_bvps_none - 10.0) < 0.01 and abs(_bvps_pref - 8.0) < 0.01,
   "C: common book value drops exactly by the preferred fraction (10 -> 8 on 20% preferred)")


if FAILS:
    print(f"\n{len(FAILS)} regression test(s) FAILED:")
    print("\n".join("  - " + f for f in FAILS))
    sys.exit(1)
print("\nAll regression tests passed.")
