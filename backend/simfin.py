"""SimFin data adapter (optional, opt-in via SIMFIN_API_KEY).

Returns the SAME normalized shape as data.fetch_stock so it's a drop-in source
for the single-stock Analyze view. On any failure it returns {"error": ...} and
the caller falls back to Yahoo. The bulk screener never calls this (it would
burn the free tier's monthly credits).

Free tier: 5,000 US stocks, ~5yr history, 500 credits/month. Paid ($15/mo+)
extends history to 10yr+ with no code change here.

NOTE: SimFin's v3 field names are confirmed against a live key via `probe()`
and mapped in FIELD_MAP below.
"""
from __future__ import annotations

import math
import os
import time
from typing import Any, Optional

from curl_cffi import requests as cr

BASE = "https://backend.simfin.com/api/v3"

# SimFin standardized line-item name -> our normalized statement key. Matching is
# case-insensitive and tolerant of minor punctuation differences.
FIELD_MAP = {
    # Income statement (PL)
    "revenue": "revenue",
    "gross profit": "gross_profit",
    "operating income (loss)": "operating_income",
    "net income": "net_income",
    "pretax income (loss)": "pretax_income",
    "income tax (expense) benefit, net": "tax_provision",
    # Balance sheet (BS)
    "total assets": "total_assets",
    "total equity": "total_equity",
    "long term debt": "long_term_debt",
    "cash, cash equivalents & short term investments": "cash",
    "total current assets": "current_assets",
    "total current liabilities": "current_liabilities",
    "accounts & notes receivable": "receivables",
    "inventories": "inventory",
    "property, plant & equipment, net": "net_ppe",
    # Cash flow (CF)
    "depreciation & amortization": "depreciation",
    "cash from operating activities": "operating_cashflow",
    "change in fixed assets & intangibles": "capex",
    # Capital returns (CF)
    "dividends paid": "dividends_paid",
    "cash from (repurchase of) equity": "buybacks",
    # DERIVED block
    "ebitda": "ebitda",
    "free cash flow": "free_cashflow",
    "earnings per share, diluted": "eps",
    "total debt": "total_debt",
}
# Not available from SimFin's statement feed (left None; app degrades gracefully):
# interest expense, stock-based comp, gross PP&E. Diluted share count is derived
# from net income ÷ diluted EPS.


def _key() -> Optional[str]:
    return os.environ.get("SIMFIN_API_KEY")


def _clean(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if (math.isnan(f) or math.isinf(f)) else f


def _get(session: cr.Session, path: str, params: dict) -> Any:
    r = session.get(f"{BASE}{path}", params=params,
                    headers={"Authorization": _key(), "Accept": "application/json"},
                    timeout=20)
    if r.status_code != 200:
        raise RuntimeError(f"SimFin HTTP {r.status_code}: {r.text[:160]}")
    return r.json()


def probe(ticker: str) -> dict[str, Any]:
    """Return raw v3 responses for one ticker — used to confirm field names."""
    s = cr.Session(impersonate="chrome")
    out = {}
    for label, path, params in [
        ("general", "/companies/general/verbose", {"ticker": ticker}),
        ("statements", "/companies/statements/verbose",
         {"ticker": ticker, "statements": "PL,BS,CF,DERIVED", "period": "FY"}),
        ("prices", "/companies/prices/verbose", {"ticker": ticker}),
    ]:
        try:
            out[label] = _get(s, path, params)
        except Exception as e:  # noqa: BLE001
            out[label] = {"error": str(e)}
    return out


# ---- Parser (finalized against the live v3 response) ----
def _map_statements(payload: Any) -> dict[str, dict[str, Optional[float]]]:
    """Turn the statements payload into {our_key: {fyear: value}}.

    Defensive: walks the response looking for (line-item name, fiscal year,
    value) triples regardless of the exact nesting, and maps names via FIELD_MAP.
    """
    from data import STATEMENT_KEYS  # normalized key list
    out: dict[str, dict[str, Optional[float]]] = {k: {} for k in STATEMENT_KEYS}

    def norm(name: str) -> Optional[str]:
        return FIELD_MAP.get((name or "").strip().lower())

    # v3 'verbose' returns a list of company objects, each with a 'statements'
    # list; each statement has 'data' rows keyed by fiscal period + named cells.
    companies = payload if isinstance(payload, list) else payload.get("data", payload)
    if isinstance(companies, dict):
        companies = [companies]
    for comp in companies or []:
        statements = comp.get("statements") if isinstance(comp, dict) else None
        if not statements:
            continue
        for stmt in statements:
            for row in (stmt.get("data") or []):
                fy = row.get("Fiscal Year") or row.get("fiscalYear") or row.get("fyear")
                if fy is None:
                    continue
                for name, value in row.items():
                    key = norm(name)
                    if not key:
                        continue
                    v = _clean(value)
                    # Don't let a null column in another statement block (e.g. CF
                    # carries a null "Net Income") clobber a real value.
                    if v is not None:
                        out[key][str(int(fy))] = v
    return {k: dict(sorted(v.items())) for k, v in out.items()}


def fetch_stock(ticker: str) -> dict[str, Any]:
    """Fetch one ticker from SimFin in the normalized shape. Returns {'error':..}
    on any problem so the caller falls back to Yahoo."""
    if not _key():
        return {"error": "no SIMFIN_API_KEY"}
    ticker = ticker.strip().upper()
    s = cr.Session(impersonate="chrome")
    try:
        gen = _get(s, "/companies/general/verbose", {"ticker": ticker})
        stmt = _get(s, "/companies/statements/verbose",
                    {"ticker": ticker, "statements": "PL,BS,CF,DERIVED", "period": "FY"})
        prices = _get(s, "/companies/prices/verbose", {"ticker": ticker})
    except Exception as e:  # noqa: BLE001
        return {"error": f"SimFin fetch failed: {e}"}

    statements = _map_statements(stmt)
    if not statements.get("revenue"):
        return {"error": "SimFin returned no usable statements"}

    # Derive diluted share count per year (not in the statement feed): NI ÷ EPS.
    ni, eps = statements["net_income"], statements["eps"]
    for yr, e in eps.items():
        n = ni.get(yr)
        if e and n is not None and e != 0:
            statements["shares"][yr] = abs(n / e)
    statements["shares"] = dict(sorted(statements["shares"].items()))

    g = (gen[0] if isinstance(gen, list) and gen else gen) or {}
    price_history, current_price, shares_out = _map_prices(prices)

    def _last(d):
        s = [(int(k), v) for k, v in d.items() if v is not None]
        return sorted(s)[-1][1] if s else None

    total_debt = _last(statements["total_debt"])
    total_cash = _last(statements["cash"])
    total_equity = _last(statements["total_equity"])
    if not shares_out:
        shares_out = _last(statements["shares"])
    currency = g.get("currency") or g.get("Currency") or "USD"

    market_cap = (current_price * shares_out) if (current_price and shares_out) else None
    # Price/Book from market cap ÷ equity; dividend yield from SimFin's DPS.
    price_to_book = (market_cap / total_equity) if (market_cap and total_equity and total_equity > 0) else None
    dps = _extract_latest(stmt, "Dividends Per Share")
    dividend_yield = (abs(dps) / current_price) if (dps and current_price) else None

    info = {
        "name": g.get("name") or ticker,
        # SimFin: industryName is the broad sector, sectorName the sub-industry.
        "sector": g.get("industryName") or g.get("sectorName"),
        "industry": g.get("sectorName"),
        "summary": g.get("companyDescription"),
        "country": g.get("market"),
        "employees": g.get("numEmployees"),
        "currency": currency,
        "current_price": current_price,
        "shares_outstanding": shares_out,
        "market_cap": market_cap,
        "total_debt": total_debt,
        "total_cash": total_cash,
        "financial_currency": currency,
        "currency_converted": False, "currency_unresolved": False, "fx_rate": 1.0,
        # Computed from what SimFin provides:
        "price_to_book": price_to_book,
        "dividend_yield": dividend_yield,
        # Genuinely unavailable from SimFin's statement feed (analyst estimates /
        # market data): forward P/E, PEG, beta.
        "trailing_pe": None, "forward_pe": None, "peg_ratio": None, "beta": None,
        "profit_margin": None, "return_on_equity": None, "revenue_growth": None,
        "earnings_growth": None, "free_cashflow_ttm": None, "operating_cashflow_ttm": None,
        "analyst_target": None, "recommendation": None, "gross_margin": None,
        "enterprise_value": None, "ev_to_ebitda": None, "ev_to_revenue": None,
        "ebitda_ttm": None, "held_percent_insiders": None, "held_percent_institutions": None,
    }
    return {
        "ticker": ticker, "error": None, "info": info,
        "statements": statements, "price_history": price_history,
        "data_source": "SimFin",
    }


def _extract_latest(stmt_payload: Any, field: str) -> Optional[float]:
    """Latest non-null value of a raw SimFin field across all statement blocks."""
    comp = (stmt_payload[0] if isinstance(stmt_payload, list) and stmt_payload else {}) or {}
    best_year, best_val = None, None
    for st in comp.get("statements", []) or []:
        for row in st.get("data", []) or []:
            fy, v = row.get("Fiscal Year"), row.get(field)
            if fy is not None and v is not None and (best_year is None or fy > best_year):
                best_year, best_val = fy, v
    return _clean(best_val)


def _map_prices(payload: Any):
    """Return (monthly price_history, latest close, latest shares outstanding)."""
    rows = []
    if isinstance(payload, list) and payload and isinstance(payload[0], dict):
        rows = payload[0].get("data") or []
    elif isinstance(payload, dict):
        rows = payload.get("data") or []
    clean = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        date = str(r.get("Date") or "")[:10]
        close = _clean(r.get("Last Closing Price") or r.get("Adjusted Closing Price"))
        sh = _clean(r.get("Common Shares Outstanding"))
        if date and close is not None:
            clean.append((date, close, sh))
    clean.sort()
    if not clean:
        return [], None, None
    # Downsample to one point per month (last trading day) for the chart.
    monthly, seen = [], set()
    for date, close, _ in reversed(clean):
        ym = date[:7]
        if ym not in seen:
            seen.add(ym)
            monthly.append({"date": date, "close": close})
    monthly.reverse()
    return monthly, clean[-1][1], clean[-1][2]
