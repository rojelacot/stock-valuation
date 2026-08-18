#!/usr/bin/env python3
"""Offline smoke tests — the valuation/scoring pipeline must survive degenerate
data (empty, zero, negative, missing) without crashing and keep scores in range.

    .venv/bin/python tests/smoke.py     # exits non-zero on any failure
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from data import STATEMENT_KEYS               # noqa: E402
from valuation import compute_metrics, resolve_assumptions  # noqa: E402
from scoring import score                     # noqa: E402

INFO_KEYS = [
    "name", "sector", "industry", "summary", "country", "employees", "market_cap",
    "shares_outstanding", "current_price", "currency", "trailing_pe", "forward_pe",
    "peg_ratio", "price_to_book", "dividend_yield", "beta", "profit_margin",
    "return_on_equity", "revenue_growth", "earnings_growth", "free_cashflow_ttm",
    "operating_cashflow_ttm", "total_debt", "total_cash", "analyst_target",
    "recommendation", "gross_margin", "enterprise_value", "ev_to_ebitda",
    "ev_to_revenue", "ebitda_ttm", "held_percent_insiders", "held_percent_institutions",
]


def make_stock(statements=None, info=None, prices=None):
    st = {k: {} for k in STATEMENT_KEYS}
    for k, v in (statements or {}).items():
        st[k] = v
    inf = {k: None for k in INFO_KEYS}
    inf.update({"name": "TEST", "currency": "USD", "financial_currency": "USD",
                "currency_converted": False, "currency_unresolved": False, "fx_rate": 1.0})
    inf.update(info or {})
    return {"ticker": "TEST", "error": None, "info": inf,
            "statements": st, "price_history": prices or []}


def years(start, vals):
    return {str(start + i): v for i, v in enumerate(vals)}


A = resolve_assumptions()
FAILS = []


def check(label, stock, expect_dcf=None):
    try:
        m = compute_metrics(stock, A)
        v = score(m)
    except Exception as e:  # noqa: BLE001
        FAILS.append(f"{label}: CRASH {type(e).__name__}: {e}")
        return
    s = v.get("score")
    if not (isinstance(s, (int, float)) and 0 <= s <= 100):
        FAILS.append(f"{label}: score out of range: {s}")
    if v.get("rating") not in ("BUY", "HOLD / WATCH", "AVOID"):
        FAILS.append(f"{label}: bad rating {v.get('rating')}")
    if expect_dcf is not None and bool(m["dcf"].get("ok")) != expect_dcf:
        FAILS.append(f"{label}: expected dcf.ok={expect_dcf}, got {m['dcf'].get('ok')}")
    print(f"  {label:34} score={s} {v.get('rating')}")


# A plausible healthy company.
healthy = make_stock(
    statements={
        "revenue": years(2019, [100, 110, 125, 140, 155, 170, 185]),
        "gross_profit": years(2019, [60, 66, 75, 85, 95, 105, 115]),
        "operating_income": years(2019, [25, 28, 33, 38, 43, 48, 53]),
        "net_income": years(2019, [18, 20, 24, 28, 32, 36, 40]),
        "eps": years(2019, [1.8, 2.0, 2.4, 2.8, 3.2, 3.6, 4.0]),
        "operating_cashflow": years(2019, [22, 25, 30, 35, 40, 45, 50]),
        "capex": years(2019, [-5, -6, -7, -8, -9, -10, -11]),
        "depreciation": years(2019, [4, 4, 5, 5, 6, 6, 7]),
        "total_equity": years(2019, [80, 85, 90, 95, 100, 105, 110]),
        "total_debt": years(2019, [30, 30, 28, 26, 24, 22, 20]),
        "cash": years(2019, [20, 22, 25, 28, 32, 36, 40]),
        "current_assets": years(2019, [50, 55, 60, 65, 70, 75, 80]),
        "current_liabilities": years(2019, [25, 26, 27, 28, 29, 30, 31]),
        "ebitda": years(2019, [29, 32, 38, 43, 49, 54, 60]),
        "shares": years(2019, [10, 10, 10, 10, 10, 10, 10]),
    },
    info={"current_price": 40, "shares_outstanding": 10e0, "market_cap": 400,
          "sector": "Technology", "total_debt": 20, "total_cash": 40, "beta": 1.1},
    prices=[{"date": f"{y}-12-31", "close": 20 + i * 3} for i, y in enumerate(range(2019, 2026))],
)
check("healthy company", healthy, expect_dcf=True)
check("empty statements + no price", make_stock())
check("zero revenue", make_stock(statements={"revenue": years(2019, [0, 0, 0, 0])}))
check("negative equity", make_stock(
    statements={"revenue": years(2019, [100, 110, 120, 130]),
                "net_income": years(2019, [10, 11, 12, 13]),
                "total_equity": years(2019, [-5, -8, -10, -12]),
                "total_debt": years(2019, [50, 55, 60, 65])},
    info={"current_price": 30, "shares_outstanding": 10, "market_cap": 300}))
check("missing price", make_stock(
    statements={"revenue": years(2019, [100, 120, 140]),
                "net_income": years(2019, [10, 12, 14])}))
check("single year of data", make_stock(
    statements={"revenue": {"2025": 100}, "net_income": {"2025": 10}},
    info={"current_price": 25}))

if FAILS:
    print("\n".join("FAIL " + f for f in FAILS))
    print(f"\n{len(FAILS)} failure(s).")
    sys.exit(1)
print("\nAll smoke tests passed.")
