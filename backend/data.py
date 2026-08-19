"""Data ingestion — direct Yahoo Finance JSON endpoints via curl_cffi.

Why not yfinance? On this machine (Python 3.14) yfinance's internal session
management segfaults with curl_cffi, and its plain-requests path gets 429-ed by
Yahoo. Hitting the JSON endpoints ourselves with a browser-impersonating
curl_cffi session (a fresh one per call — sharing sessions across threads is
what crashes) is stable and dodges the rate limiting.

Note on coverage: Yahoo's *free* fundamentals only go back ~4 annual periods.
Price history goes back the full 10 years. We surface the true statement span
to the UI rather than pretending it's 10 years. Swap this module for a paid
provider (Financial Modeling Prep, Tiingo…) to get a true 10-year statement
history — nothing downstream touches Yahoo directly.
"""
from __future__ import annotations

import math
import time
from typing import Any, Optional

from curl_cffi import requests as cr

QUOTE_URL = "https://query1.finance.yahoo.com/v7/finance/quote"
CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
TS_URL = ("https://query2.finance.yahoo.com/ws/fundamentals-timeseries/v1/"
          "finance/timeseries/{sym}")
QS_URL = "https://query2.finance.yahoo.com/v10/finance/quoteSummary/{sym}"
CRUMB_URL = "https://query1.finance.yahoo.com/v1/test/getcrumb"

# timeseries type -> our normalized statement key
TS_MAP = {
    "annualTotalRevenue": "revenue",
    "annualGrossProfit": "gross_profit",
    "annualOperatingIncome": "operating_income",
    "annualNetIncome": "net_income",
    "annualDilutedEPS": "eps",
    "annualInterestExpense": "interest_expense",
    "annualDilutedAverageShares": "shares",
    "annualTotalAssets": "total_assets",
    "annualStockholdersEquity": "total_equity",
    "annualTotalDebt": "total_debt",
    "annualLongTermDebt": "long_term_debt",
    "annualCashAndCashEquivalents": "cash",
    "annualCurrentAssets": "current_assets",
    "annualCurrentLiabilities": "current_liabilities",
    "annualOperatingCashFlow": "operating_cashflow",
    "annualCapitalExpenditure": "capex",
    "annualFreeCashFlow": "free_cashflow",
    "annualStockBasedCompensation": "stock_based_comp",
    # For earnings-quality / owner-earnings analysis (capex-cycle distortion):
    "annualDepreciationAndAmortization": "depreciation",
    "annualReconciledDepreciation": "depreciation_recon",
    "annualGrossPPE": "gross_ppe",
    "annualNetPPE": "net_ppe",
    # For EV multiples, NOPAT-ROIC, accruals (due-diligence coverage):
    "annualEBITDA": "ebitda",
    "annualReceivables": "receivables",
    "annualInventory": "inventory",
    "annualPretaxIncome": "pretax_income",
    "annualTaxProvision": "tax_provision",
    # Capital returns (dividends + buybacks) for shareholder-yield analysis:
    "annualCashDividendsPaid": "dividends_paid",
    "annualRepurchaseOfCapitalStock": "buybacks",
    # For forensic scores (Altman Z distress, Beneish M manipulation):
    "annualRetainedEarnings": "retained_earnings",
    "annualSellingGeneralAndAdministration": "sga",
}
STATEMENT_KEYS = [
    "revenue", "gross_profit", "operating_income", "net_income", "eps",
    "interest_expense", "shares", "total_assets", "total_equity", "total_debt",
    "long_term_debt", "cash", "current_assets", "current_liabilities",
    "operating_cashflow", "capex", "free_cashflow", "stock_based_comp",
    "depreciation", "depreciation_recon", "gross_ppe", "net_ppe",
    "ebitda", "receivables", "inventory", "pretax_income", "tax_provision",
    "dividends_paid", "buybacks", "retained_earnings", "sga",
]


class YahooError(Exception):
    pass


def _clean(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, dict):  # Yahoo wraps numbers as {"raw":..,"fmt":..}
        value = value.get("raw")
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _new_session() -> cr.Session:
    """Fresh browser-impersonating session with cookies + crumb bootstrapped."""
    s = cr.Session(impersonate="chrome")
    try:
        s.get("https://fc.yahoo.com", timeout=10)
    except Exception:
        pass  # cookie priming; failure here is non-fatal
    return s


def _get(session: cr.Session, url: str, params: dict, tries: int = 3) -> Any:
    """GET JSON with a small backoff on 429/5xx."""
    last = None
    for i in range(tries):
        try:
            r = session.get(url, params=params, timeout=20)
            if r.status_code == 200:
                return r.json()
            if r.status_code in (429, 500, 502, 503):
                last = f"HTTP {r.status_code}"
                time.sleep(0.8 * (i + 1))
                continue
            last = f"HTTP {r.status_code}"
            break
        except Exception as e:  # noqa: BLE001
            last = str(e)
            time.sleep(0.6 * (i + 1))
    raise YahooError(last or "request failed")


def _crumb(session: cr.Session) -> str:
    try:
        c = session.get(CRUMB_URL, timeout=10).text.strip()
        return c if c and "<html" not in c.lower() else ""
    except Exception:
        return ""


def _fx_rate(session: cr.Session, frm: str, to: str) -> Optional[float]:
    """Latest FX rate to convert 1 unit of `frm` into `to` (e.g. TWD->USD)."""
    if not frm or not to or frm == to:
        return 1.0
    try:
        ch = _get(session, CHART_URL.format(sym=f"{frm}{to}=X"),
                  {"range": "5d", "interval": "1d"})
        result = (ch.get("chart", {}).get("result") or [None])[0]
        if not result:
            return None
        rate = _clean((result.get("meta") or {}).get("regularMarketPrice"))
        if rate:
            return rate
        closes = [c for c in (result.get("indicators", {}).get("quote") or [{}])[0].get("close") or []
                  if c is not None]
        return _clean(closes[-1]) if closes else None
    except Exception:
        return None


def _parse_timeseries(payload: Any) -> dict[str, dict[str, Optional[float]]]:
    """Turn Yahoo's timeseries payload into {statement_key: {year: value}}."""
    out: dict[str, dict[str, Optional[float]]] = {k: {} for k in STATEMENT_KEYS}
    try:
        results = payload["timeseries"]["result"]
    except (KeyError, TypeError):
        return out
    for item in results:
        ts_type = (item.get("meta", {}).get("type") or [None])[0]
        key = TS_MAP.get(ts_type)
        if not key:
            continue
        for point in item.get(ts_type, []) or []:
            if not point:
                continue
            date = point.get("asOfDate")
            if not date:
                continue
            year = date[:4]
            out[key][year] = _clean(point.get("reportedValue"))
    # keep years sorted
    return {k: dict(sorted(v.items())) for k, v in out.items()}


def fetch_market_supplement(ticker: str) -> dict[str, Any]:
    """Free Yahoo market data to enrich any analysis (esp. SimFin-sourced, which
    lacks these): analyst forward estimates + price targets, analyst coverage,
    short interest, and net insider buying/selling. One quoteSummary call."""
    ticker = ticker.strip().upper()
    session = _new_session()
    crumb = _crumb(session)
    if not crumb:
        return {}
    mods = "financialData,defaultKeyStatistics,summaryDetail,netSharePurchaseActivity"
    try:
        payload = _get(session, QS_URL.format(sym=ticker),
                       {"modules": mods, "crumb": crumb, "formatted": "false"})
        qres = (payload.get("quoteSummary", {}).get("result") or [None])[0]
    except YahooError:
        return {}
    if not qres:
        return {}
    fin = qres.get("financialData", {}) or {}
    stats = qres.get("defaultKeyStatistics", {}) or {}
    summ = qres.get("summaryDetail", {}) or {}
    nspa = qres.get("netSharePurchaseActivity", {}) or {}
    return {
        "forward_pe": _clean(summ.get("forwardPE")) or _clean(stats.get("forwardPE")),
        "peg_ratio": _clean(stats.get("pegRatio")) or _clean(stats.get("trailingPegRatio")),
        "analyst_target": _clean(fin.get("targetMeanPrice")),
        "target_high": _clean(fin.get("targetHighPrice")),
        "target_low": _clean(fin.get("targetLowPrice")),
        "num_analysts": _clean(fin.get("numberOfAnalystOpinions")),
        "recommendation": fin.get("recommendationKey"),
        "recommendation_mean": _clean(fin.get("recommendationMean")),
        "short_pct_float": _clean(stats.get("shortPercentOfFloat")),
        "short_ratio": _clean(stats.get("shortRatio")),
        "held_percent_insiders": _clean(stats.get("heldPercentInsiders")),
        "held_percent_institutions": _clean(stats.get("heldPercentInstitutions")),
        "insider_net_shares": _clean(nspa.get("netInfoShares")),
        "insider_buy_shares": _clean(nspa.get("buyInfoShares")),
        "insider_sell_shares": _clean(nspa.get("sellInfoShares")),
        "insider_period": nspa.get("period"),
    }


def bulk_quote(symbols: list[str], batch: int = 100,
               progress=None) -> dict[str, dict[str, Optional[float]]]:
    """Cheap batched market-cap + price lookup (one request per ~100 symbols).

    Used to pre-filter a large universe before doing the expensive per-stock
    analysis. Bad/unknown tickers are simply absent from the result.
    """
    session = _new_session()
    crumb = _crumb(session)
    out: dict[str, dict[str, Optional[float]]] = {}
    for i in range(0, len(symbols), batch):
        chunk = symbols[i:i + batch]
        try:
            payload = _get(session, QUOTE_URL,
                           {"symbols": ",".join(chunk), "crumb": crumb})
        except YahooError:
            continue
        for q in payload.get("quoteResponse", {}).get("result", []) or []:
            sym = q.get("symbol")
            if sym:
                out[sym] = {"market_cap": _clean(q.get("marketCap")),
                            "price": _clean(q.get("regularMarketPrice"))}
        if progress:
            progress(min(i + batch, len(symbols)), len(symbols))
    return out


def prefilter_by_size(symbols: list[str], min_cap: float = 2e9,
                      min_price: float = 5.0, progress=None) -> list[str]:
    """Keep only symbols with market cap >= min_cap and price >= min_price.

    This is the 'quality floor' that turns ~5,000 US-listed names into the
    couple-thousand that are large/liquid enough for a 10-15yr value thesis
    (and that Yahoo actually has usable fundamentals for)."""
    quotes = bulk_quote(symbols, progress=progress)
    survivors = []
    for sym in symbols:
        q = quotes.get(sym)
        if not q:
            continue
        if (q["market_cap"] or 0) >= min_cap and (q["price"] or 0) >= min_price:
            survivors.append(sym)
    return survivors


def fetch_stock(ticker: str, use_simfin: bool = True,
                use_edgar: bool = True) -> dict[str, Any]:
    """Fetch one ticker in the normalized shape.

    Source priority for the single-stock (Analyze) path:
      1. SEC EDGAR for statements (10-15yr, as-filed) paired with Yahoo for
         live price/market/analyst data — the best free combo for US filers.
      2. SimFin (if a key is set) then Yahoo, for names EDGAR doesn't cover
         (foreign private issuers file 20-F, not 10-K).

    Bulk callers (the screener) pass use_edgar=False and use_simfin=False so a
    thousand-name scan stays fast on Yahoo's cheap batched endpoints.
    """
    import os
    # 1) EDGAR statements + Yahoo market data (US 10-K filers).
    if use_edgar:
        try:
            import edgar as _ed
            edg = _ed.fetch_statements(ticker)
        except Exception:  # noqa: BLE001
            edg = {"error": "edgar unavailable"}
        if not edg.get("error") and edg.get("years", 0) >= 4:
            base = _fetch_yahoo(ticker)
            if base.get("error"):
                return base  # need Yahoo for price; nothing to pair EDGAR with
            _overlay_edgar(base, edg)
            return base

    # 2) SimFin then Yahoo (EDGAR didn't cover this name).
    if use_simfin and os.environ.get("SIMFIN_API_KEY"):
        try:
            import simfin as _sf
            res = _sf.fetch_stock(ticker)
            if res and not res.get("error") and res.get("statements", {}).get("revenue"):
                # Cross-check SimFin's fundamentals against Yahoo's. Foreign filers
                # (no EDGAR 10-K) are exactly where the two free datasets diverge;
                # a wide gap means neither fair value can be trusted (e.g. DLocal,
                # where SimFin and Yahoo implied fair values differed ~3x).
                try:
                    div = _cross_check_sources(res, _fetch_yahoo(ticker),
                                               "SimFin", "Yahoo Finance")
                    if div:
                        res["source_divergence"] = div
                except Exception:  # noqa: BLE001
                    pass  # cross-check is best-effort; never block the analysis
                return res
        except Exception:  # noqa: BLE001
            pass  # fall through to Yahoo
    return _fetch_yahoo(ticker)


def _cross_check_sources(primary: dict[str, Any], peer: dict[str, Any],
                         name_primary: str, name_peer: str) -> Optional[dict[str, Any]]:
    """Compare two independent fundamental datasets for the same ticker.

    Returns a divergence report when the most recent overlapping revenue or
    net-income figures disagree, or None if there's nothing to compare. This is
    only meaningful off the EDGAR path — for US 10-K filers EDGAR is
    authoritative, so no cross-check is needed. `material` (>=20% on the worst
    metric) is the signal that a fair value built on this data is unreliable."""
    p_st = primary.get("statements") or {}
    q_st = peer.get("statements") or {}
    detail: dict[str, Any] = {}
    worst = 0.0
    for key in ("revenue", "net_income"):
        pv = p_st.get(key) or {}
        qv = q_st.get(key) or {}
        # Values may be keyed by str or int years; normalize to str for overlap.
        p_by = {str(y): v for y, v in pv.items() if v is not None}
        q_by = {str(y): v for y, v in qv.items() if v is not None}
        common = set(p_by) & set(q_by)
        if not common:
            continue
        yr = max(common, key=int)
        a, b = p_by[yr], q_by[yr]
        denom = max(abs(a), abs(b))
        if denom <= 0:
            continue
        d = abs(a - b) / denom
        detail[key] = {"year": yr, name_primary: a, name_peer: b,
                       "divergence": round(d, 3)}
        worst = max(worst, d)
    if not detail:
        return None
    return {
        "primary": name_primary,
        "peer": name_peer,
        "metrics": detail,
        "max_divergence": round(worst, 3),
        "material": worst >= 0.20,
    }


def _overlay_edgar(base: dict[str, Any], edg: dict[str, Any]) -> None:
    """Replace Yahoo's short statement history with EDGAR's deep one in-place.

    EDGAR wins wholesale when it has at least as many revenue-years as Yahoo (it
    almost always does — 15-19yr vs ~4). For the handful of line items EDGAR
    doesn't populate for a given filer (e.g. a holding company's EPS), we keep
    Yahoo's series rather than blanking it. Yahoo's price/market/info is left
    untouched. Both are USD for a US 10-K filer, so no currency reconciliation is
    needed (foreign issuers never reach here)."""
    e_st = edg.get("statements") or {}
    y_st = base.get("statements") or {}
    e_years = len(e_st.get("revenue") or {})
    if e_years < len(y_st.get("revenue") or {}):
        return  # Yahoo somehow deeper — leave it alone
    base["statements"] = {k: (e_st.get(k) or y_st.get(k) or {}) for k in STATEMENT_KEYS}
    base["statement_years"] = e_years
    base["data_source"] = (f"SEC EDGAR ({e_years}yr as-filed 10-K) "
                           "+ Yahoo Finance (price/market)")


def _fetch_yahoo(ticker: str) -> dict[str, Any]:
    ticker = ticker.strip().upper()
    session = _new_session()

    # ---- Price history + basic meta (chart endpoint; no crumb needed) ----
    price_history: list[dict[str, Any]] = []
    meta: dict[str, Any] = {}
    try:
        chart = _get(session, CHART_URL.format(sym=ticker),
                     {"range": "10y", "interval": "1mo"})
        result = (chart.get("chart", {}).get("result") or [None])[0]
        if not result:
            err = chart.get("chart", {}).get("error")
            msg = (err or {}).get("description") if err else None
            return {"ticker": ticker,
                    "error": msg or f"No data found for '{ticker}'. Check the symbol."}
        meta = result.get("meta", {})
        stamps = result.get("timestamp") or []
        closes = (result.get("indicators", {}).get("quote") or [{}])[0].get("close") or []
        for t, c in zip(stamps, closes):
            cc = _clean(c)
            if cc is not None:
                price_history.append(
                    {"date": time.strftime("%Y-%m-%d", time.gmtime(t)), "close": cc})
    except YahooError as e:
        return {"ticker": ticker, "error": f"Could not load '{ticker}' from Yahoo ({e})."}

    # ---- Fundamentals timeseries (annual; ~4yr on the free tier) ----
    types = ",".join(TS_MAP.keys())
    now = int(time.time())
    p1 = now - 13 * 366 * 24 * 3600  # ask wide; Yahoo returns what it has
    try:
        ts_payload = _get(session, TS_URL.format(sym=ticker),
                          {"symbol": ticker, "type": types,
                           "period1": p1, "period2": now, "merge": "false"})
        statements = _parse_timeseries(ts_payload)
    except YahooError:
        statements = {k: {} for k in STATEMENT_KEYS}

    # ---- Key stats via quoteSummary (needs crumb) ----
    crumb = _crumb(session)
    qs: dict[str, Any] = {}
    if crumb:
        mods = ("summaryDetail,defaultKeyStatistics,financialData,"
                "assetProfile,price,quoteType")
        try:
            qpayload = _get(session, QS_URL.format(sym=ticker),
                            {"modules": mods, "crumb": crumb, "formatted": "false"})
            qres = (qpayload.get("quoteSummary", {}).get("result") or [None])[0]
            qs = qres or {}
        except YahooError:
            qs = {}

    summary = qs.get("summaryDetail", {}) or {}
    stats = qs.get("defaultKeyStatistics", {}) or {}
    fin = qs.get("financialData", {}) or {}
    profile = qs.get("assetProfile", {}) or {}
    price_mod = qs.get("price", {}) or {}
    qtype = qs.get("quoteType", {}) or {}

    name = (price_mod.get("longName") or price_mod.get("shortName")
            or qtype.get("longName") or qtype.get("shortName")
            or meta.get("longName") or ticker)

    current_price = (_clean(fin.get("currentPrice"))
                     or _clean(price_mod.get("regularMarketPrice"))
                     or _clean(meta.get("regularMarketPrice"))
                     or (price_history[-1]["close"] if price_history else None))

    div_yield = _clean(summary.get("dividendYield"))
    if div_yield is not None and div_yield > 1:  # some payloads give percent
        div_yield /= 100.0

    info = {
        "name": name,
        "sector": profile.get("sector"),
        "industry": profile.get("industry"),
        "summary": profile.get("longBusinessSummary"),
        "country": profile.get("country"),
        "website": profile.get("website"),
        "employees": profile.get("fullTimeEmployees"),
        "market_cap": _clean(summary.get("marketCap")) or _clean(price_mod.get("marketCap")),
        "shares_outstanding": _clean(stats.get("sharesOutstanding")),
        "current_price": current_price,
        "currency": meta.get("currency") or price_mod.get("currency") or "USD",
        "trailing_pe": _clean(summary.get("trailingPE")),
        "forward_pe": _clean(summary.get("forwardPE")) or _clean(stats.get("forwardPE")),
        "peg_ratio": _clean(stats.get("pegRatio")) or _clean(stats.get("trailingPegRatio")),
        "price_to_book": _clean(stats.get("priceToBook")),
        "dividend_yield": div_yield,
        "beta": _clean(summary.get("beta")) or _clean(stats.get("beta")),
        "profit_margin": _clean(fin.get("profitMargins")) or _clean(stats.get("profitMargins")),
        "return_on_equity": _clean(fin.get("returnOnEquity")),
        "revenue_growth": _clean(fin.get("revenueGrowth")),
        "earnings_growth": _clean(fin.get("earningsGrowth")),
        "free_cashflow_ttm": _clean(fin.get("freeCashflow")),
        "operating_cashflow_ttm": _clean(fin.get("operatingCashflow")),
        "total_debt": _clean(fin.get("totalDebt")),
        "total_cash": _clean(fin.get("totalCash")),
        "analyst_target": _clean(fin.get("targetMeanPrice")),
        "recommendation": fin.get("recommendationKey"),
        "gross_margin": _clean(fin.get("grossMargins")),
        "enterprise_value": _clean(stats.get("enterpriseValue")),
        "ev_to_ebitda": _clean(stats.get("enterpriseToEbitda")),
        "ev_to_revenue": _clean(stats.get("enterpriseToRevenue")),
        "ebitda_ttm": _clean(fin.get("ebitda")),
        "held_percent_insiders": _clean(stats.get("heldPercentInsiders")),
        "held_percent_institutions": _clean(stats.get("heldPercentInstitutions")),
        "financial_currency": fin.get("financialCurrency"),
        "currency_converted": False,
        "currency_unresolved": False,
        "fx_rate": 1.0,
    }

    # ---- Guardrail 1: currency. Statements & financialData come in the company's
    # reporting currency, but price/market cap are in the trading currency. For
    # ADRs / foreign listings these differ (e.g. TSM reports TWD, trades in USD),
    # which would otherwise blow up the DCF. Convert monetary figures to the
    # trading currency; if we can't get an FX rate, flag it so scoring distrusts
    # the valuation rather than showing a bogus one.
    trading_ccy = info["currency"]
    fin_ccy = info["financial_currency"]
    if fin_ccy and trading_ccy and fin_ccy != trading_ccy:
        fx = _fx_rate(session, fin_ccy, trading_ccy)
        if fx and fx > 0:
            for series in statements.values():
                for yr, val in list(series.items()):
                    if val is not None:
                        series[yr] = val * fx
            for k in ("free_cashflow_ttm", "operating_cashflow_ttm",
                      "total_debt", "total_cash", "ebitda_ttm"):
                if info.get(k) is not None:
                    info[k] = info[k] * fx
            info["currency_converted"] = True
            info["fx_rate"] = fx
        else:
            info["currency_unresolved"] = True

    # Guard: an unknown/typo ticker (e.g. "APPL") can return a non-error but
    # empty payload — no price and no statements. Treat that as not-found so the
    # app shows a clear error instead of a bogus all-blank verdict.
    if info["current_price"] is None and not statements.get("revenue"):
        return {"ticker": ticker,
                "error": f"No data found for '{ticker}'. Check the symbol "
                         "(e.g. AAPL, not APPL)."}

    return {
        "ticker": ticker,
        "error": None,
        "info": info,
        "statements": statements,
        "price_history": price_history,
        "data_source": "Yahoo Finance (free tier)",
    }
