"""SEC EDGAR data adapter — deep (10-15yr) primary-source fundamentals, free.

Why this matters: the free tiers of Yahoo (~4yr) and SimFin (~5-7yr) don't reach
back a full economic cycle, but a buy-and-hold-15-years thesis wants to see a
company through 2008/2020. SEC's XBRL `companyfacts` API returns every line item
a US filer has reported in its 10-Ks going back to ~2009 — as-filed, no key, no
cost. The only thing EDGAR lacks is price/market data, so this module supplies
*statements only*; data.fetch_stock pairs them with Yahoo's live price + market
info (the hybrid that strictly beats either source alone for US filers).

Coverage: US domestic filers (10-K). Foreign private issuers file 20-F and won't
appear here — those transparently fall back to the Yahoo/SimFin path.

SEC fair-access rules: send a descriptive User-Agent (override via SEC_EDGAR_UA)
and stay under 10 requests/sec. We make one cached ticker->CIK lookup plus one
companyfacts call per stock, so we're well within limits.
"""
from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path
from typing import Any, Optional

from curl_cffi import requests as cr

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

_CIK_CACHE = Path(__file__).resolve().parent.parent / "reports" / ".edgar_cik.json"
_CIK_TTL = 30 * 24 * 3600  # refresh the ticker->CIK map monthly

# In-memory ticker -> CIK (int) map, loaded lazily.
_CIK_MEM: Optional[dict[str, int]] = None


def _ua() -> str:
    # SEC fair-access wants a descriptive User-Agent with a contact (their
    # documented format is "Company Name contact@email"). A URL in the UA trips
    # SEC's WAF (403), so keep it "name email". Override with your own contact
    # via SEC_EDGAR_UA in .env — the courteous thing when hitting SEC at volume.
    return os.environ.get("SEC_EDGAR_UA", "stock-valuation-tool contact@example.com")


def _session() -> cr.Session:
    s = cr.Session(impersonate="chrome")
    s.headers.update({"User-Agent": _ua()})
    return s


def _get(session: cr.Session, url: str, tries: int = 3) -> Any:
    """GET JSON with a polite backoff on 429/5xx (SEC throttles aggressively)."""
    last = None
    for i in range(tries):
        try:
            r = session.get(url, timeout=25)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 404:
                return None  # unknown CIK / no facts — not an error, just absent
            if r.status_code in (403, 429, 500, 502, 503):
                last = f"HTTP {r.status_code}"
                time.sleep(0.8 * (i + 1))
                continue
            last = f"HTTP {r.status_code}"
            break
        except Exception as e:  # noqa: BLE001
            last = str(e)
            time.sleep(0.6 * (i + 1))
    raise RuntimeError(last or "request failed")


# ---- ticker -> CIK -----------------------------------------------------------
def _load_cik_map(force: bool = False) -> dict[str, int]:
    global _CIK_MEM
    if _CIK_MEM is not None and not force:
        return _CIK_MEM
    # Disk cache first (avoids refetching a ~1MB file every process start).
    if not force:
        try:
            age = time.time() - _CIK_CACHE.stat().st_mtime
            if age < _CIK_TTL:
                _CIK_MEM = {k: int(v) for k, v in
                            json.loads(_CIK_CACHE.read_text()).items()}
                return _CIK_MEM
        except Exception:  # noqa: BLE001
            pass
    # Fetch fresh from SEC.
    try:
        payload = _get(_session(), TICKERS_URL)
    except Exception:  # noqa: BLE001
        payload = None
    mapping: dict[str, int] = {}
    if isinstance(payload, dict):
        for row in payload.values():
            t = str(row.get("ticker") or "").strip().upper()
            cik = row.get("cik_str")
            if t and cik is not None:
                mapping[t] = int(cik)
    if mapping:
        try:
            _CIK_CACHE.parent.mkdir(exist_ok=True)
            _CIK_CACHE.write_text(json.dumps(mapping))
        except Exception:  # noqa: BLE001
            pass
        _CIK_MEM = mapping
    return mapping or (_CIK_MEM or {})


def cik_for(ticker: str) -> Optional[int]:
    # EDGAR uses '-' for class shares (BRK-B); Yahoo/user may pass 'BRK.B'.
    t = ticker.strip().upper().replace(".", "-")
    m = _load_cik_map()
    return m.get(t)


# ---- XBRL fact extraction ----------------------------------------------------
# Each concept maps to an ordered list of candidate us-gaap tags. Tags are merged
# across the whole list (highest-priority tag wins per fiscal year) so a history
# that spans a tag change — e.g. Revenues -> RevenueFromContractWithCustomer at
# ASC 606 (2018) — is stitched into one continuous series instead of truncated.
# (tags, unit, negate, instant)
_CONCEPTS: dict[str, tuple[list[str], str, bool, bool]] = {
    "revenue": (["RevenueFromContractWithCustomerExcludingAssessedTax",
                 "Revenues", "RevenueFromContractWithCustomerIncludingAssessedTax",
                 "SalesRevenueNet", "SalesRevenueGoodsNet"], "USD", False, False),
    "gross_profit": (["GrossProfit"], "USD", False, False),
    "operating_income": (["OperatingIncomeLoss"], "USD", False, False),
    "net_income": (["NetIncomeLoss", "ProfitLoss"], "USD", False, False),
    "pretax_income": (["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
                       "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"],
                      "USD", False, False),
    "tax_provision": (["IncomeTaxExpenseBenefit"], "USD", False, False),
    "interest_expense": (["InterestExpense", "InterestExpenseNonoperating",
                          "InterestAndDebtExpense"], "USD", False, False),
    "eps": (["EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted"],
            "USD/shares", False, False),
    "shares": (["WeightedAverageNumberOfDilutedSharesOutstanding",
                "WeightedAverageNumberOfSharesOutstandingBasic"], "shares", False, False),
    "operating_cashflow": (["NetCashProvidedByUsedInOperatingActivities",
                            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
                           "USD", False, False),
    "capex": (["PaymentsToAcquirePropertyPlantAndEquipment",
               "PaymentsToAcquireProductiveAssets"], "USD", True, False),  # outflow -> negative
    "depreciation": (["DepreciationDepletionAndAmortization",
                      "DepreciationAmortizationAndAccretionNet",
                      "DepreciationAndAmortization", "Depreciation"], "USD", False, False),
    "stock_based_comp": (["ShareBasedCompensation",
                          "ShareBasedCompensationExpenseIncludingDiscontinuedOperations"],
                         "USD", False, False),
    "dividends_paid": (["PaymentsOfDividendsCommonStock", "PaymentsOfDividends"],
                       "USD", True, False),
    "buybacks": (["PaymentsForRepurchaseOfCommonStock",
                  "PaymentsForRepurchaseOfEquity"], "USD", True, False),
    # Balance sheet (instant) --------------------------------------------------
    "total_assets": (["Assets"], "USD", False, True),
    "total_equity": (["StockholdersEquity",
                      "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
                     "USD", False, True),
    "long_term_debt": (["LongTermDebtNoncurrent", "LongTermDebt"], "USD", False, True),
    "cash": (["CashAndCashEquivalentsAtCarryingValue",
              "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"],
             "USD", False, True),
    "current_assets": (["AssetsCurrent"], "USD", False, True),
    "current_liabilities": (["LiabilitiesCurrent"], "USD", False, True),
    "receivables": (["AccountsReceivableNetCurrent", "ReceivablesNetCurrent"],
                    "USD", False, True),
    "inventory": (["InventoryNet"], "USD", False, True),
    "net_ppe": (["PropertyPlantAndEquipmentNet"], "USD", False, True),
    "gross_ppe": (["PropertyPlantAndEquipmentGross"], "USD", False, True),
    "retained_earnings": (["RetainedEarningsAccumulatedDeficit"], "USD", False, True),
    # SG&A (duration) — for Beneish SGAI; neutralized if absent.
    "sga": (["SellingGeneralAndAdministrativeExpense",
             "GeneralAndAdministrativeExpense"], "USD", False, False),
    # Debt maturity ladder (instant) — for the refinancing-risk check. The 10-K
    # contractual-maturities footnote. Absent for many filers; the check degrades
    # gracefully to the current-portion + interest-coverage stress when so.
    "debt_current": (["LongTermDebtCurrent"], "USD", False, True),
    "debt_mat_y1": (["LongTermDebtMaturitiesRepaymentsOfPrincipalInNextTwelveMonths"], "USD", False, True),
    "debt_mat_y2": (["LongTermDebtMaturitiesRepaymentsOfPrincipalInYearTwo"], "USD", False, True),
    "debt_mat_y3": (["LongTermDebtMaturitiesRepaymentsOfPrincipalInYearThree"], "USD", False, True),
    "debt_mat_y4": (["LongTermDebtMaturitiesRepaymentsOfPrincipalInYearFour"], "USD", False, True),
    "debt_mat_y5": (["LongTermDebtMaturitiesRepaymentsOfPrincipalInYearFive"], "USD", False, True),
    "debt_mat_beyond": (["LongTermDebtMaturitiesRepaymentsOfPrincipalAfterYearFive"], "USD", False, True),
}
# Current portion of debt — summed into total_debt, not surfaced on its own.
_DEBT_CURRENT_TAGS = ["LongTermDebtCurrent", "DebtCurrent", "ShortTermBorrowings"]


def _clean(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if (math.isnan(f) or math.isinf(f)) else f


def _daydiff(start: Optional[str], end: Optional[str]) -> Optional[int]:
    if not start or not end:
        return None
    try:
        s = time.strptime(start, "%Y-%m-%d")
        e = time.strptime(end, "%Y-%m-%d")
        return int((time.mktime(e) - time.mktime(s)) / 86400)
    except (ValueError, OverflowError):
        return None


def _tag_series(node: Any, unit: str, instant: bool) -> dict[str, tuple[str, float]]:
    """One us-gaap tag -> {fiscal_year: (filed_date, value)} from 10-K filings.

    Annual only: duration facts must span ~a year (300-400d); instant (balance
    sheet) facts are taken at fiscal-year end. Keyed by the year of the period
    end so income/balance items for the same fiscal year align.
    """
    units = (node or {}).get("units") or {}
    arr = units.get(unit) or next(iter(units.values()), None)
    out: dict[str, tuple[str, float]] = {}
    for e in arr or []:
        form = e.get("form") or ""
        if not form.startswith("10-K"):
            continue
        end = e.get("end")
        if not end:
            continue
        if not instant:
            d = _daydiff(e.get("start"), end)
            if d is None or d < 300 or d > 400:
                continue
        val = _clean(e.get("val"))
        if val is None:
            continue
        yr = end[:4]
        filed = e.get("filed") or ""
        prev = out.get(yr)
        if prev is None or filed >= prev[0]:  # keep the most-recently-filed value
            out[yr] = (filed, val)
    return out


def _concept(facts: dict[str, Any], tags: list[str], unit: str,
             negate: bool, instant: bool) -> dict[str, float]:
    """Merge candidate tags into one {year: value} series.

    A concept's history often spans a tag change (e.g. Revenues -> ASC 606
    RevenueFromContractWithCustomer), so we stitch tags together. But some filers
    report BOTH a total line and a much smaller partial line under different tags
    (e.g. an insurer's total 'Revenues' vs a small 'RevenueFromContractWithCustomer'
    fee subset) — naively letting the higher-priority tag win per year mixes the
    two and creates a fake order-of-magnitude cliff. So: take the tag with the
    LONGEST annual history as the primary series (ties broken by priority order),
    then back-fill missing years from other tags ONLY when the value is within an
    order of magnitude of the primary's scale.
    """
    per_tag: dict[str, dict[str, float]] = {}
    for tag in tags:
        node = facts.get(tag)
        if not node:
            continue
        s = {yr: val for yr, (_, val) in _tag_series(node, unit, instant).items()}
        if s:
            per_tag[tag] = s
    if not per_tag:
        return {}
    primary = max(per_tag, key=lambda t: (len(per_tag[t]), -tags.index(t)))
    merged: dict[str, float] = dict(per_tag[primary])
    scale = sorted(abs(v) for v in merged.values() if v)
    ref = scale[len(scale) // 2] if scale else 0.0  # median magnitude of primary
    for tag in tags:
        s = per_tag.get(tag)
        if not s or tag == primary:
            continue
        for yr, val in s.items():
            if yr in merged:
                continue
            if ref and val and not (ref / 10 <= abs(val) <= ref * 10):
                continue  # scale-inconsistent (partial vs total line) — don't stitch
            merged[yr] = val
    return {yr: (-v if negate else v) for yr, v in merged.items()}


def fetch_statements(ticker: str) -> dict[str, Any]:
    """Return {'statements': {key: {year: value}}, 'years': N, 'entity': name}
    from SEC EDGAR, or {'error': ...} if this filer isn't covered."""
    from data import STATEMENT_KEYS

    cik = cik_for(ticker)
    if cik is None:
        return {"error": f"{ticker}: no CIK on file (not a US 10-K filer)"}
    try:
        payload = _get(_session(), FACTS_URL.format(cik=cik))
    except Exception as e:  # noqa: BLE001
        return {"error": f"EDGAR fetch failed: {e}"}
    if not payload:
        return {"error": f"{ticker}: no company facts"}

    facts = (payload.get("facts") or {}).get("us-gaap") or {}
    if not facts:
        return {"error": f"{ticker}: no us-gaap facts"}

    statements: dict[str, dict[str, float]] = {k: {} for k in STATEMENT_KEYS}
    for key, (tags, unit, negate, instant) in _CONCEPTS.items():
        statements[key] = _concept(facts, tags, unit, negate, instant)

    if not statements.get("revenue"):
        return {"error": f"{ticker}: no revenue history in XBRL facts"}

    # ---- Derived series ----
    # total_debt = long-term + current portion (best-effort across debt tags).
    debt_cur: dict[str, float] = {}
    for tag in _DEBT_CURRENT_TAGS:
        node = facts.get(tag)
        if node:
            for yr, (_, val) in _tag_series(node, "USD", True).items():
                debt_cur.setdefault(yr, val)
    lt = statements["long_term_debt"]
    total_debt: dict[str, float] = {}
    for yr in set(lt) | set(debt_cur):
        total_debt[yr] = (lt.get(yr) or 0.0) + (debt_cur.get(yr) or 0.0)
    statements["total_debt"] = total_debt

    # free_cash_flow = operating cash flow + capex (capex already negative).
    ocf, capex = statements["operating_cashflow"], statements["capex"]
    fcf = {yr: ocf[yr] + (capex.get(yr) or 0.0) for yr in ocf}
    statements["free_cashflow"] = fcf

    # EBITDA = operating income + D&A (no direct XBRL tag).
    oi, dep = statements["operating_income"], statements["depreciation"]
    statements["ebitda"] = {yr: oi[yr] + dep[yr] for yr in oi if yr in dep}

    # Sort every series by year for stable downstream iteration.
    statements = {k: dict(sorted(v.items())) for k, v in statements.items()}
    years = len(statements["revenue"])
    return {
        "statements": statements,
        "years": years,
        "entity": payload.get("entityName"),
        "cik": cik,
    }
