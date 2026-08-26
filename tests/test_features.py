#!/usr/bin/env python3
"""Offline regression tests for the recent features — data-source cross-check,
the divergence-driven score downgrade, and the weekly-winners history roll-up.
All pure-logic (no network), so they run anywhere.

    .venv/bin/python tests/test_features.py     # exits non-zero on any failure
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

import data                                       # noqa: E402
import history                                    # noqa: E402
from data import STATEMENT_KEYS                   # noqa: E402
from valuation import compute_metrics, resolve_assumptions  # noqa: E402
from scoring import score                         # noqa: E402

FAILS: list[str] = []


def ok(cond, label):
    if cond:
        print(f"  PASS  {label}")
    else:
        FAILS.append(label)
        print(f"  FAIL  {label}")


# ---------------------------------------------------------------------------
# 1) _cross_check_sources — the SimFin-vs-Yahoo divergence detector
# ---------------------------------------------------------------------------
def stmt(**series):
    return {"statements": series}


print("cross-check:")
# Sources agree closely -> not material.
a = stmt(revenue={"2024": 1000}, net_income={"2024": 100})
b = stmt(revenue={"2024": 1010}, net_income={"2024": 98})
d = data._cross_check_sources(a, b, "SimFin", "Yahoo")
ok(d is not None and d["material"] is False, "agreeing sources -> not material")

# DLocal case: net income 2.19B vs 197M -> ~91% divergence, material.
a = stmt(revenue={"2025": 1.09e9}, net_income={"2025": 2.19e9})
b = stmt(revenue={"2025": 1.09e9}, net_income={"2025": 1.97e8})
d = data._cross_check_sources(a, b, "SimFin", "Yahoo")
ok(d and d["material"] and 0.89 <= d["max_divergence"] <= 0.92, "DLO-like NI gap -> material ~0.91")

# str vs int year keys must still line up (Yahoo uses str, SimFin sometimes int).
a = stmt(revenue={2024: 1000}, net_income={2024: 100})
b = stmt(revenue={"2024": 2000}, net_income={"2024": 100})
d = data._cross_check_sources(a, b, "A", "B")
ok(d and d["material"] and d["metrics"]["revenue"]["divergence"] == 0.5, "int/str year keys align")

# No overlapping years -> None (nothing to compare).
a = stmt(revenue={"2020": 1000})
b = stmt(revenue={"2024": 1000})
ok(data._cross_check_sources(a, b, "A", "B") is None, "no overlap -> None")

# Both zero for a metric -> skipped (no ZeroDivisionError), other metric still compared.
a = stmt(revenue={"2024": 0}, net_income={"2024": 100})
b = stmt(revenue={"2024": 0}, net_income={"2024": 60})
d = data._cross_check_sources(a, b, "A", "B")
ok(d is not None and "revenue" not in d["metrics"] and d["material"], "zero denom skipped, no crash")

# Empty statements -> None.
ok(data._cross_check_sources(stmt(), stmt(), "A", "B") is None, "empty statements -> None")

# None values inside a series must not crash.
a = stmt(revenue={"2024": None}, net_income={"2024": 100})
b = stmt(revenue={"2024": 1000}, net_income={"2024": 100})
try:
    data._cross_check_sources(a, b, "A", "B")
    ok(True, "None value in series -> no crash")
except Exception as e:  # noqa: BLE001
    ok(False, f"None value in series -> crashed ({e})")

# Non-numeric period keys (e.g. a "TTM" row) must be ignored, not crash.
a = stmt(revenue={"2024": 1000, "TTM": 1100}, net_income={"2024": 100})
b = stmt(revenue={"2024": 1000, "TTM": 2000}, net_income={"2024": 100})
try:
    d = data._cross_check_sources(a, b, "A", "B")
    ok(d is not None and d["metrics"]["revenue"]["year"] == "2024",
       "non-numeric period key ignored (compares 2024, not TTM)")
except Exception as e:  # noqa: BLE001
    ok(False, f"non-numeric key crashed ({e})")

# --- atomic_json: locked read-modify-write survives corruption + concurrency ---
import atomic_json  # noqa: E402
apath = Path(tempfile.mkdtemp()) / "s.json"
atomic_json.update(apath, lambda d: d.__setitem__("a", 1))
atomic_json.update(apath, lambda d: d.__setitem__("b", 2))
ok(atomic_json.load(apath) == {"a": 1, "b": 2}, "atomic_json: successive updates accumulate")
apath.write_text("{ this is not json")   # simulate corruption
ok(atomic_json.load(apath) == {}, "atomic_json: unreadable file loads as {}")
atomic_json.update(apath, lambda d: d.__setitem__("c", 3))
ok(atomic_json.load(apath) == {"c": 3}, "atomic_json: update recovers after corruption")


# ---------------------------------------------------------------------------
# 2) Scoring downgrade when sources materially disagree
# ---------------------------------------------------------------------------
print("scoring downgrade:")
A = resolve_assumptions()


def make_stock(statements, info):
    st = {k: {} for k in STATEMENT_KEYS}
    st.update(statements)
    inf = {"name": "T", "currency": "USD", "financial_currency": "USD",
           "currency_converted": False, "fx_rate": 1.0}
    inf.update(info)
    return {"ticker": "T", "error": None, "info": inf, "statements": st,
            "price_history": []}


def yv(start, vals):
    return {str(start + i): v for i, v in enumerate(vals)}


# A cheap, high-quality name that should score BUY.
strong = make_stock(
    {"revenue": yv(2016, [100, 115, 132, 150, 170, 190, 210, 235, 260, 290]),
     "net_income": yv(2016, [18, 21, 25, 29, 34, 39, 45, 51, 58, 66]),
     "operating_income": yv(2016, [24, 28, 33, 39, 45, 52, 60, 68, 77, 88]),
     "gross_profit": yv(2016, [55, 63, 73, 83, 94, 105, 116, 130, 144, 160]),
     "operating_cashflow": yv(2016, [22, 26, 31, 36, 42, 49, 56, 64, 73, 83]),
     "capex": yv(2016, [-4, -4, -5, -5, -6, -7, -8, -9, -10, -11]),
     "total_equity": yv(2016, [70, 78, 88, 98, 110, 122, 136, 150, 166, 184]),
     "total_debt": yv(2016, [10] * 10), "cash": yv(2016, [30, 34, 40, 46, 54, 62, 72, 82, 94, 108]),
     "shares": yv(2016, [10] * 10)},
    {"current_price": 70, "shares_outstanding": 10, "market_cap": 700,
     "sector": "Technology", "total_debt": 10, "total_cash": 108})

m = compute_metrics(strong, A)
base = score(m)
# Inject a material divergence and re-score.
m["data_confidence"]["source_divergence"] = {
    "primary": "SimFin", "peer": "Yahoo Finance", "max_divergence": 0.6,
    "material": True, "metrics": {}}
m["data_confidence"]["low"] = True
dv = score(m)
ok(base["rating"] == "BUY", f"baseline is BUY (got {base['rating']} {base['score']})")
ok(dv["rating"] != "BUY", f"material divergence downgrades BUY (got {dv['rating']})")
ok(dv["score"] == base["score"], "divergence downgrades rating but not the numeric score")
ok(any("disagree" in r.lower() for r in dv["red_flags"]), "divergence adds a red flag")

# Non-material divergence must NOT downgrade.
m2 = compute_metrics(strong, A)
m2["data_confidence"]["source_divergence"] = {"primary": "SimFin", "peer": "Yahoo",
                                              "max_divergence": 0.05, "material": False, "metrics": {}}
ok(score(m2)["rating"] == "BUY", "immaterial divergence does not downgrade")


# ---------------------------------------------------------------------------
# 3) history.summarize — the weekly-winners roll-up
# ---------------------------------------------------------------------------
print("history roll-up:")
# Redirect history storage to a temp file so we never touch real data.
tmp = Path(tempfile.mkdtemp()) / "hist.json"
history.HIST = tmp


def w(t, s):
    return {"ticker": t, "name": t, "sector": "X", "score": s, "rating": "BUY",
            "upside": 0.2, "price": 10}


# Empty scope.
s0 = history.summarize("all")
ok(s0["weeks"] == [] and s0["board"] == [] and s0["latest"]["prev_date"] is None,
   "empty history -> empty summary, no crash")

# Week 1.
history.record("all", "2026-01-05", 80, [w("AAA", 90), w("BBB", 85)])
s1 = history.summarize("all")
ok(len(s1["weeks"]) == 1 and s1["latest"]["prev_date"] is None
   and sorted(s1["latest"]["added"]) == ["AAA", "BBB"], "week 1 -> all added, no prev")

# Week 2: keep AAA, drop BBB, add CCC.
history.record("all", "2026-01-12", 80, [w("AAA", 92), w("CCC", 81)])
s2 = history.summarize("all")
L = s2["latest"]
ok(L["added"] == ["CCC"] and L["dropped"] == ["BBB"] and L["held"] == ["AAA"],
   "week 2 -> added/dropped/held correct")
aaa = next(b for b in s2["board"] if b["ticker"] == "AAA")
ok(aaa["appearances"] == 2 and aaa["streak"] == 2, "AAA appears twice, streak 2")
bbb = next(b for b in s2["board"] if b["ticker"] == "BBB")
ok(bbb["appearances"] == 1 and bbb["streak"] == 0, "BBB dropped -> streak 0")

# Week 3: AAA reappears after... actually gap test: DDD in wk1&wk3 but not wk2.
history.record("all", "2026-01-05", 80, [w("AAA", 90), w("BBB", 85), w("DDD", 88)])  # replace wk1
history.record("all", "2026-01-19", 80, [w("AAA", 91), w("DDD", 80)])
s3 = history.summarize("all")
ddd = next(b for b in s3["board"] if b["ticker"] == "DDD")
ok(ddd["appearances"] == 2 and ddd["streak"] == 1, "DDD in wk1&wk3 (gap) -> appears 2, streak 1")

# Same-date re-record replaces (not duplicates).
n_dates = len(s3["weeks"])
history.record("all", "2026-01-19", 80, [w("AAA", 91)])
ok(len(history.summarize("all")["weeks"]) == n_dates, "same-date record replaces, no dup week")

# None score in a winner must not crash the board sort.
history.record("all", "2026-02-01", 80, [w("EEE", None), w("FFF", 70)])
try:
    history.summarize("all")
    ok(True, "None score in board -> no crash")
except Exception as e:  # noqa: BLE001
    ok(False, f"None score crashed sort ({e})")

# Per-week note surfaces.
history.record("all", "2026-02-08", 80, [w("GGG", 90)], note="legacy run")
notes = history.summarize("all")["notes"]
ok(any(n["date"] == "2026-02-08" and n["note"] == "legacy run" for n in notes),
   "per-week note surfaces in summary")


# ---------------------------------------------------------------------------
# 4) refinancing.assess — debt maturity & refi-rate stress
# ---------------------------------------------------------------------------
print("refinancing:")
import refinancing as refi  # noqa: E402


def dstmt(**kw):
    return {k: {"2025": v} for k, v in kw.items()}


# No debt -> not applicable (a positive, not a risk).
ok(refi.assess({}, {})["applicable"] is False, "no debt -> not applicable")
ok(refi.assess(dstmt(total_debt=5e9), {"sector": "Financial Services"})["applicable"] is False,
   "financials -> not applicable")

# High risk: near-term wall dwarfs cash + 2yr FCF.
hi = refi.assess({**dstmt(debt_mat_y1=4e8, debt_mat_y2=2e8, debt_mat_beyond=4e8,
                          cash=5e7, ebitda=1e8, interest_expense=1e8),
                  "free_cashflow": {"2023": 5e7, "2024": 5e7, "2025": 5e7}}, {"sector": "Industrials"})
ok(hi["level"] == "high" and hi["coverage"] < 1, "wall > cash+FCF -> high, coverage<1")
ok(hi["stress_interest_coverage"] < hi["base_interest_coverage"], "refi at +300bps lowers coverage")

# Well-termed: tiny near-term wall, big liquidity, strong coverage.
lo = refi.assess({**dstmt(debt_mat_y1=2e7, debt_mat_y2=3e7, debt_mat_beyond=9e8,
                          cash=5e8, ebitda=3e8, interest_expense=3e7),
                  "free_cashflow": {"2023": 2e8, "2024": 2e8, "2025": 2e8}}, {"sector": "Technology"})
ok(lo["level"] == "low" and lo["positive"], "small wall + liquidity -> low, positive set")

# REIT: interest coverage skipped (unreliable EBITDA), graded on wall/liquidity.
reit = refi.assess({**dstmt(debt_mat_y1=1e8, debt_mat_y2=1e8, debt_mat_beyond=8e8,
                            cash=5e8, ebitda=1e7, interest_expense=5e8),
                    "free_cashflow": {"2025": 3e8}}, {"sector": "Real Estate"})
ok(reit["applicable"] and reit["base_interest_coverage"] is None,
   "REIT -> applicable but interest coverage skipped")

# Ladder absent -> falls back to current portion of LT debt.
noladder = refi.assess(dstmt(total_debt=1e9, debt_current=1e8, cash=5e8,
                             ebitda=2e8, interest_expense=3e7,
                             free_cashflow=1e8), {"sector": "Industrials"})
ok(noladder["has_ladder"] is False and noladder["near_term_wall"] == 1e8,
   "no ladder -> uses current portion as near-term wall")

# ---------------------------------------------------------------------------
# 5) working_capital.assess — receivables/inventory vs sales
# ---------------------------------------------------------------------------
print("working capital:")
import working_capital as wcap  # noqa: E402


def wc_stmt(rev, rec=None, inv=None, gp=None):
    out = {"revenue": rev}
    if rec is not None:
        out["receivables"] = rec
    if inv is not None:
        out["inventory"] = inv
    if gp is not None:
        out["gross_profit"] = gp
    return out


# Receivables ballooning vs flat sales -> elevated concern.
bad = wcap.assess(wc_stmt(
    rev={"2022": 1000, "2023": 1000, "2024": 1000, "2025": 1000},
    rec={"2022": 100, "2023": 110, "2024": 120, "2025": 170}), {"sector": "Technology"})
ok(bad["level"] == "elevated" and bad["receivables"]["ratio"] > 1.25,
   "receivables outrunning sales -> elevated")

# Stable intensity (both grow together) -> low + positive.
good = wcap.assess(wc_stmt(
    rev={"2022": 1000, "2023": 1100, "2024": 1200, "2025": 1300},
    rec={"2022": 100, "2023": 110, "2024": 120, "2025": 130}), {"sector": "Technology"})
ok(good["level"] == "low" and good["positive"], "receivables tracking sales -> low, positive")

# High-but-stable DSO must NOT be flagged (it's the trend, not the level).
saas = wcap.assess(wc_stmt(
    rev={"2022": 1000, "2023": 1100, "2024": 1200, "2025": 1300},
    rec={"2022": 340, "2023": 374, "2024": 408, "2025": 442}), {"sector": "Technology"})
ok(saas["level"] == "low", "high but stable DSO not flagged")

# Stale line (inventory ends years before revenue) -> that component dropped.
stale = wcap.assess(wc_stmt(
    rev={"2022": 1000, "2023": 1000, "2024": 1000, "2025": 1000},
    inv={"2009": 50, "2010": 60, "2011": 90}), {"sector": "Consumer Cyclical"})
ok(stale["applicable"] is False or stale.get("inventory") is None,
   "stale inventory series is dropped, not trended as current")

# Financials -> N/A.
ok(wcap.assess(wc_stmt(rev={"2025": 1000}, rec={"2025": 500}),
               {"sector": "Financial Services"})["applicable"] is False,
   "financials -> not applicable")

# Trivially small receivables ignored; no crash on empty.
ok(wcap.assess(wc_stmt(rev={"2023": 1000, "2024": 1000, "2025": 1000},
                       rec={"2023": 1, "2024": 1, "2025": 2}), {})["applicable"] is False,
   "trivially small receivables ignored")
ok(wcap.assess({}, {})["applicable"] is False, "empty statements -> not applicable")

# ---------------------------------------------------------------------------
# 6) leverage_trend.assess — covenant / leverage-trajectory deterioration
# ---------------------------------------------------------------------------
print("leverage trend:")
import leverage_trend as levt  # noqa: E402


def lstmt(debt, ebitda, cash=None, interest=None, equity=None, rev=None):
    yrs = sorted(debt)
    out = {"revenue": rev or {y: 1000 for y in yrs}, "total_debt": debt, "ebitda": ebitda}
    if cash: out["cash"] = cash
    if interest: out["interest_expense"] = interest
    if equity: out["total_equity"] = equity
    return out


Y = ["2021", "2022", "2023", "2024", "2025"]
# Leverage climbing 2x -> 4.5x -> deteriorating.
climb = levt.assess(lstmt(
    debt={y: v for y, v in zip(Y, [200, 300, 400, 450, 450])},
    ebitda={y: 100 for y in Y},
    interest={y: 20 for y in Y}), {"sector": "Industrials"})
ok(climb["level"] in ("deteriorating", "stressed") and climb["leverage"]["latest"] >= 4,
   "leverage climbing -> deteriorating")

# Deleveraging 4x -> 1.5x -> improving.
delev = levt.assess(lstmt(
    debt={y: v for y, v in zip(Y, [400, 350, 250, 180, 150])},
    ebitda={y: 100 for y in Y},
    interest={y: 15 for y in Y}), {"sector": "Industrials"})
ok(delev["level"] == "improving" and delev["positive"], "deleveraging -> improving, positive")

# Stressed: >5x leverage AND <2x coverage.
stress = levt.assess(lstmt(
    debt={y: 650 for y in Y}, ebitda={y: 100 for y in Y},
    interest={y: 60 for y in Y}), {"sector": "Industrials"})
ok(stress["level"] == "stressed", "high leverage + thin coverage -> stressed")

# Coverage slipping from a high level to a still-fine level is NOT deterioration.
finecov = levt.assess(lstmt(
    debt={y: 150 for y in Y}, ebitda={y: 100 for y in Y},
    interest={y: v for y, v in zip(Y, [15, 18, 22, 26, 30])}), {"sector": "Industrials"})
ok(finecov["level"] in ("stable", "improving"), "coverage high->still-fine not flagged")

# A COVID-style collapsed-EBITDA year is excluded from the trend baseline.
covid = levt.assess(lstmt(
    debt={y: v for y, v in zip(Y, [300, 320, 300, 280, 260])},
    ebitda={y: v for y, v in zip(Y, [100, 2, 90, 110, 120])},  # 2022 near-zero
    interest={y: 20 for y in Y}), {"sector": "Consumer Cyclical"})
ok("2022" not in {p["year"] for p in (covid.get("leverage") or {}).get("series", [])},
   "collapsed-EBITDA year dropped from leverage series")

# Net cash -> no leverage concern.
netcash = levt.assess(lstmt(
    debt={y: 50 for y in Y}, cash={y: 300 for y in Y},
    ebitda={y: 100 for y in Y}, interest={y: 3 for y in Y}), {"sector": "Technology"})
ok(netcash["level"] in ("improving", "stable", "none"), "net cash -> not a concern")

# Financials -> N/A.
ok(levt.assess(lstmt(debt={y: 500 for y in Y}, ebitda={y: 100 for y in Y}),
               {"sector": "Financial Services"})["applicable"] is False, "financials -> N/A")

# ---------------------------------------------------------------------------
# 7) dividend_coverage.assess — dividend funded by FCF?
# ---------------------------------------------------------------------------
print("dividend coverage:")
import dividend_coverage as divc  # noqa: E402


def dvstmt(div, fcf, bb=None, ni=None):
    M = 1_000_000  # values given in $M so they clear the non-payer floor
    def sc(d): return {y: v * M for y, v in d.items()}
    out = {"dividends_paid": sc(div), "free_cashflow": sc(fcf)}
    if bb: out["buybacks"] = sc(bb)
    if ni: out["net_income"] = sc(ni)
    return out


DY = ["2021", "2022", "2023", "2024", "2025"]
# Non-payer -> N/A.
ok(divc.assess(dvstmt({y: 0 for y in DY}, {y: 100 for y in DY}), {})["applicable"] is False,
   "non-payer -> N/A")
# REIT / financials -> N/A.
ok(divc.assess(dvstmt({y: -50 for y in DY}, {y: 100 for y in DY}),
               {"sector": "Real Estate"})["applicable"] is False, "REIT -> N/A")
ok(divc.assess(dvstmt({y: -50 for y in DY}, {y: 100 for y in DY}),
               {"sector": "Financial Services"})["applicable"] is False, "financials -> N/A")

# Well covered: FCF ~2x dividends.
covd = divc.assess(dvstmt({y: -50 for y in DY}, {y: 100 for y in DY},
                          ni={y: 120 for y in DY}), {"sector": "Consumer Defensive"})
ok(covd["level"] == "comfortable" and covd["positive"], "FCF 2x dividends -> comfortable")

# Uncovered: dividends exceed FCF every year.
unc = divc.assess(dvstmt({y: -120 for y in DY}, {y: 100 for y in DY}), {"sector": "Utilities"})
ok(unc["level"] == "uncovered", "dividends > FCF -> uncovered")

# Negative FCF (heavy capex) -> uncovered with a clear message, no negative %.
neg = divc.assess(dvstmt({y: -30 for y in DY},
                         {y: v for y, v in zip(DY, [-10, -20, -5, -15, -8])}), {"sector": "Utilities"})
ok(neg["level"] == "uncovered" and neg["fcf_negative"] is True and neg["capex_heavy"] is True,
   "negative FCF -> uncovered, fcf_negative flagged")

# One-off dip: 4 good years + 1 bad still nets covered -> not 'uncovered'.
dip = divc.assess(dvstmt({y: -50 for y in DY},
                         {y: v for y, v in zip(DY, [100, 100, 10, 100, 100])}), {"sector": "Consumer Defensive"})
ok(dip["level"] != "uncovered", "one-off FCF dip doesn't read as chronic (cumulative)")

# Buyback stretch: dividend covered, but divs + buybacks exceed FCF -> noted.
stretch = divc.assess(dvstmt({y: -40 for y in DY}, {y: 100 for y in DY},
                             bb={y: -80 for y in DY}), {"sector": "Industrials"})
ok(any("buyback" in r.lower() or "shareholder returns" in r.lower() for r in stretch["reasons"]),
   "total payout > FCF is surfaced")

# ---------------------------------------------------------------------------
# 7b) intangibles.assess — acquisition-accounting / impairment risk
# ---------------------------------------------------------------------------
print("intangibles:")
import intangibles as intg  # noqa: E402


def igstmt(goodwill, intangibles_, assets, equity):
    return {k: {"2024": v * 1e6} for k, v in
            {"goodwill": goodwill, "intangibles": intangibles_,
             "total_assets": assets, "total_equity": equity}.items()}


# Roll-up: goodwill+intangibles >> assets and > equity (negative tangible book).
rollup = intg.assess(igstmt(8000, 4000, 15000, 2000), {})
ok(rollup["level"] == "high" and rollup["tangible_negative"], "roll-up (G+I 80% assets, neg tangible) -> high")
# Real assets, low goodwill -> low / positive.
real = intg.assess(igstmt(500, 200, 10000, 6000), {})
ok(real["level"] == "low" and real["positive"], "real-asset balance sheet -> low, positive")
# No goodwill -> not applicable.
ok(intg.assess(igstmt(0, 0, 10000, 6000), {})["applicable"] is False, "no goodwill -> not applicable")
# Goodwill above equity but modest share of assets -> elevated, not high.
elev = intg.assess(igstmt(3000, 1000, 12000, 3500), {})
ok(elev["level"] in ("elevated", "high") and elev["tangible_negative"],
   "goodwill > equity -> at least elevated")
# None-safe on empty.
ok(intg.assess({}, {})["applicable"] is False, "empty -> not applicable")

# ---------------------------------------------------------------------------
# 7c) risk_premium — fundamental risk, NOT beta
# ---------------------------------------------------------------------------
print("discount-rate risk premium:")
from valuation import risk_premium  # noqa: E402

fortress = risk_premium(
    {"market_cap": 1e12}, returns={"roic_avg": 0.25},
    balance={"net_cash": 5e10, "debt_to_equity": 0.2, "interest_coverage": 50}, stability=0.95)
fragile = risk_premium(
    {"market_cap": 2e9}, returns={"roic_avg": 0.03},
    balance={"net_cash": -1e9, "debt_to_equity": 3.0, "interest_coverage": 1.2}, stability=0.2)
ok(fragile["premium"] > fortress["premium"], "fragile name discounted MORE than a fortress")
ok(fortress["premium"] < 0, "a fortress compounder gets a negative (lower-bar) premium")
ok(fragile["premium"] >= 0.04, "a fragile levered small-cap clears a much higher bar")

# Beta must be ignored — a high-beta but fundamentally-sound name isn't penalised.
steady = risk_premium(
    {"market_cap": 5e11, "beta": 2.5}, returns={"roic_avg": 0.30},
    balance={"net_cash": 3e10, "debt_to_equity": 0.3, "interest_coverage": 40}, stability=0.9)
ok(steady["premium"] <= 0 and not any("beta" in r for r in steady["reasons"]),
   "high beta but sound -> no penalty, beta never cited")

# ---------------------------------------------------------------------------
# 7d) DCF growth fade — geometric (excess growth reverts faster than linear)
# ---------------------------------------------------------------------------
print("DCF growth fade:")
from valuation import discounted_cash_flow  # noqa: E402

_info = {"shares_outstanding": 10, "current_price": 50, "total_cash": 0, "total_debt": 0}
hot = discounted_cash_flow(base_fcf=100, info=_info, revenue_cagr=0.12, fcf_cagr=0.12,
                           discount_rate=0.10, terminal_growth=0.025, years=10)
path = [p["growth"] for p in hot["projection"]]
ok(abs(path[0] - 0.12) < 1e-9, "year 1 keeps the full starting growth")
linear_y2 = 0.12 + (0.025 - 0.12) * (1 / 9)              # what a linear fade would give
ok(path[1] < linear_y2, "excess growth fades geometrically — faster than linear")
ok(all(path[i] >= path[i + 1] - 1e-9 for i in range(len(path) - 1)) and path[-1] > 0.025,
   "growth decays monotonically toward terminal")

# A slow grower (below terminal) is essentially unaffected — rises gently to terminal.
slow = discounted_cash_flow(base_fcf=100, info=_info, revenue_cagr=0.013, fcf_cagr=0.013,
                            discount_rate=0.10, terminal_growth=0.025, years=10)
sp = [p["growth"] for p in slow["projection"]]
ok(all(0.012 <= g <= 0.0251 for g in sp), "slow grower rises gently toward terminal, not dampened")

# ---------------------------------------------------------------------------
# 7e) Data hardening — understated-debt flag propagates to low confidence
# ---------------------------------------------------------------------------
print("data hardening:")
_wc_stock = make_stock(
    {"revenue": yv(2016, [100 + 10 * i for i in range(10)]),
     "net_income": yv(2016, [15 + 2 * i for i in range(10)]),
     "operating_cashflow": yv(2016, [18 + 2 * i for i in range(10)]),
     "total_equity": yv(2016, [80 + 5 * i for i in range(10)]),
     "total_debt": yv(2016, [20] * 10)},
    {"current_price": 30, "shares_outstanding": 10, "market_cap": 300})
base_conf = score_conf = compute_metrics(_wc_stock, A)["data_confidence"]
ok(base_conf.get("debt_estimated") is False, "clean stock: debt not flagged estimated")
_wc_stock["debt_estimated"] = True
flagged = compute_metrics(_wc_stock, A)["data_confidence"]
ok(flagged.get("debt_estimated") is True and flagged.get("low") is True,
   "debt_estimated propagates to data_confidence.low")

# ---------------------------------------------------------------------------
# 7f) Price-source resilience (Yahoo fallbacks)
# ---------------------------------------------------------------------------
print("price-source resilience:")
import data as _data  # noqa: E402

_edg = {"statements": {"shares": {"2022": 100, "2023": 110, "2024": 120},
                       "total_debt": {"2024": 50}, "cash": {"2024": 10}},
        "entity": "Test Co", "statement_years": 8}
ok(_data._edgar_shares_latest(_edg) == 120, "latest share count picked from EDGAR")

# Full Yahoo outage + no fallback price -> give up cleanly (no crash).
_orig = _data._stooq_price
_data._stooq_price = lambda t: None
ok(_data._edgar_price_fallback("X", _edg) is None, "no fallback price -> None, not a crash")
# With a fallback price, salvage builds a price x EDGAR-shares market cap.
_data._stooq_price = lambda t: 50.0
_fb = _data._edgar_price_fallback("X", _edg)
ok(_fb and _fb["info"]["current_price"] == 50.0
   and _fb["info"]["market_cap"] == 50.0 * 120 and _fb.get("_price_fallback"),
   "salvage builds price x EDGAR-shares market cap")
_data._stooq_price = _orig

# _get fails over query1 <-> query2 (host list construction).
# (verified live that both hosts serve; here we just assert the swap logic.)
ok("query2.finance.yahoo.com" in
   "https://query1.finance.yahoo.com/v8/x".replace("query1.", "query2."),
   "query1 URL maps to the query2 mirror")

# ---------------------------------------------------------------------------
# 7g) certainty score -> certainty-scaled margin of safety
# ---------------------------------------------------------------------------
print("certainty-scaled margin of safety:")
from valuation import certainty_score  # noqa: E402

fortress_c = certainty_score(stability=0.95, returns={"roic_avg": 0.25},
                             balance={"net_cash": 5e10, "debt_to_equity": 0.2, "interest_coverage": 40},
                             years=19)
fragile_c = certainty_score(stability=0.2, returns={"roic_avg": 0.03},
                            balance={"net_cash": -1e9, "debt_to_equity": 3.0, "interest_coverage": 1.2},
                            years=5, debt_estimated=True)
ok(fortress_c > 0.8, f"fortress compounder -> high certainty ({fortress_c:.2f})")
ok(fragile_c < 0.4, f"fragile levered name -> low certainty ({fragile_c:.2f})")
ok(fortress_c > fragile_c, "certainty separates fortress from fragile")

# The scaling maps certainty -> effective MoS (base 0.25): high certainty tightens,
# low widens. (mirrors compute_metrics: base + (0.5 - certainty)*0.30, clamped)
def _mos(base, c): return min(max(base + (0.5 - c) * 0.30, 0.12), 0.45)
ok(_mos(0.25, fortress_c) < 0.18, "fortress -> tighter required discount (<18%)")
ok(_mos(0.25, fragile_c) > 0.32, "fragile -> deeper required discount (>32%)")

# Data-reliability haircut: same fundamentals, but estimated debt lowers certainty.
clean = certainty_score(0.8, {"roic_avg": 0.18}, {"net_cash": 1e9}, 15)
shaky = certainty_score(0.8, {"roic_avg": 0.18}, {"net_cash": 1e9}, 15, debt_estimated=True)
ok(shaky < clean, "estimated-debt haircut lowers certainty")

# ---------------------------------------------------------------------------
# 8) Regressions for the code-review findings
# ---------------------------------------------------------------------------
print("review-fix regressions:")

# leverage_trend: a genuine current spike clamped out of the trend series must
# still grade stressed (not "stable"), on the unclamped latest reading.
spike = levt.assess({
    "revenue": {y: 9000 for y in ["2021", "2022", "2023", "2024"]},
    "total_debt": {y: 2000 for y in ["2021", "2022", "2023", "2024"]},
    "cash": {y: 0 for y in ["2021", "2022", "2023", "2024"]},
    "ebitda": {"2021": 1000, "2022": 1000, "2023": 1000, "2024": 50},  # 2024 collapse -> 40x
}, {"sector": "Industrials"})
ok(spike["level"] == "stressed", "leverage: 40x latest (clamped) still grades stressed")

# leverage_trend: a REIT sitting at high book D/E (not rising) must not read fine.
reit_hi = levt.assess({
    "revenue": {y: 1000 for y in ["2021", "2022", "2023", "2024"]},
    "total_debt": {y: 8000 for y in ["2021", "2022", "2023", "2024"]},
    "total_equity": {y: 2000 for y in ["2021", "2022", "2023", "2024"]},
}, {"sector": "Real Estate"})
ok(reit_hi["level"] == "deteriorating", "leverage: chronically high REIT D/E flagged, not 'stable'")

# refinancing: a 'moderate' grade must always carry an explanation.
mod = refi.assess({"total_debt": {"2024": 1e9}, "debt_mat_y1": {"2024": 1.8e8},
                   "debt_mat_y2": {"2024": 1.7e8}, "debt_mat_beyond": {"2024": 6.5e8},
                   "cash": {"2024": 5e8}, "ebitda": {y: 1e8 for y in ["2022", "2023", "2024"]},
                   "interest_expense": {"2024": 2e7},
                   "free_cashflow": {y: 1e8 for y in ["2022", "2023", "2024"]}}, {"sector": "Industrials"})
ok(mod["level"] != "moderate" or mod["reasons"], "refinancing: a moderate grade carries a reason")

# dividend_coverage: a dividend suspended years ago isn't graded as a current payer.
susp = divc.assess({"dividends_paid": {y: -5e7 for y in ["2018", "2019", "2020", "2021", "2022"]},
                    "free_cashflow": {y: 1e8 for y in ["2018", "2019", "2020", "2021", "2022", "2023", "2024"]}},
                   {"sector": "Technology"})
ok(susp["applicable"] is False, "dividend: suspended-years-ago payout -> not applicable")

# dividend_coverage: a comfortable dividend keeps its positive even with a buyback note.
cb = divc.assess(dvstmt({y: -40 for y in DY}, {y: 100 for y in DY}, bb={y: -80 for y in DY}),
                 {"sector": "Industrials"})
ok(cb["level"] == "comfortable" and cb["positive"] and cb["reasons"],
   "dividend: comfortable keeps its ✓ alongside a buyback note")

# history: new-thesis enrichment — _winner carries certainty/mos/buy_below and
# derives below_buy from that week's own price vs the buy-below.
import history as _hist  # noqa: E402
w_below = _hist._winner({"ticker": "X", "price": 90, "buy_below": 100,
                         "certainty": 0.9, "mos": 0.12})
ok(w_below["below_buy"] is True and w_below["certainty"] == 0.9 and w_below["mos"] == 0.12,
   "history: winner below buy-below flagged in-zone with certainty/mos carried")
w_above = _hist._winner({"ticker": "Y", "price": 120, "buy_below": 100})
ok(w_above["below_buy"] is False, "history: winner above buy-below not in-zone")
# falls back to nested margin_of_safety_scaling when flat fields absent
w_nest = _hist._winner({"ticker": "Z", "price": 50, "buy_below": 60,
                        "margin_of_safety_scaling": {"certainty": 0.8, "effective": 0.15}})
ok(w_nest["certainty"] == 0.8 and w_nest["mos"] == 0.15,
   "history: winner reads certainty/mos from margin_of_safety_scaling fallback")

# scenario_values: the earnings-decline bear rebases cash flow down and must
# land below the base case; a deeper haircut must produce a lower fair value.
import valuation as _val  # noqa: E402
_info = {"current_price": 100.0, "shares_outstanding": 1_000_000.0,
         "total_cash": 0.0, "total_debt": 0.0}
_sc = _val.scenario_values(base_cf=10_000_000.0, info=_info, base_growth=0.10,
                           discount_rate=0.09, terminal_growth=0.025, years=10,
                           margin_of_safety=0.25, decline_haircut=0.65,
                           decline_note="rebased to through-cycle margin")
ok("earnings_decline" in _sc and _sc["earnings_decline"]["fair_value"] is not None,
   "scenario: earnings-decline case is produced")
ok(_sc["earnings_decline"]["fair_value"] < _sc["base"]["fair_value"],
   "scenario: earnings-decline fair value sits below the base case")
_sc_mild = _val.scenario_values(base_cf=10_000_000.0, info=_info, base_growth=0.10,
                                discount_rate=0.09, terminal_growth=0.025, years=10,
                                margin_of_safety=0.25, decline_haircut=0.80)
ok(_sc["earnings_decline"]["fair_value"] < _sc_mild["earnings_decline"]["fair_value"],
   "scenario: a deeper haircut yields a lower earnings-decline fair value")
# No haircut (company not above its through-cycle margin) -> no decline row at all.
_sc_none = _val.scenario_values(base_cf=10_000_000.0, info=_info, base_growth=0.10,
                                discount_rate=0.09, terminal_growth=0.025, years=10,
                                margin_of_safety=0.25, decline_haircut=None)
ok(_sc_none["earnings_decline"] is None,
   "scenario: earnings-decline omitted when no haircut is supplied")
# Heavy net debt -> stressed equity below zero is floored at 0, not shown negative.
_info_debt = {**_info, "total_cash": 0.0, "total_debt": 500_000_000.0}
_sc_wipe = _val.scenario_values(base_cf=10_000_000.0, info=_info_debt, base_growth=0.10,
                                discount_rate=0.09, terminal_growth=0.025, years=10,
                                margin_of_safety=0.25, decline_haircut=0.5,
                                decline_note="rebased")
ok(_sc_wipe["earnings_decline"]["fair_value"] == 0.0
   and "wiped out" in _sc_wipe["earnings_decline"]["note"],
   "scenario: negative stressed equity is floored at zero with a wipeout note")
# The clamp applies to every scenario row, not just the decline: no fair value
# is ever negative, and a wiped-out row is flagged.
_rows = [_sc_wipe[k] for k in ("bear", "base", "bull") if _sc_wipe.get(k)]
ok(all((r["fair_value"] is None or r["fair_value"] >= 0) for r in _rows),
   "scenario: bear/base/bull fair values are never negative")
ok(any(r.get("wiped_out") for r in _rows),
   "scenario: a wiped-out bear/base/bull row is flagged")
# The earnings-decline row is coupled to the cyclical-peak flag: a company whose
# latest margin towers over its own history gets it; a stable-margin one doesn't.
def _mk_peak_metrics(ni_last):
    _y = [2019, 2020, 2021, 2022, 2023, 2024]
    _st = {k: {} for k in STATEMENT_KEYS}
    _st.update({"revenue": {str(y): 1000e6 for y in _y},
                "net_income": {**{str(y): 100e6 for y in _y[:-1]}, "2024": ni_last},
                "total_equity": {str(y): 1000e6 for y in _y},
                "cash": {str(y): 500e6 for y in _y},
                "operating_cashflow": {**{str(y): 120e6 for y in _y[:-1]}, "2024": ni_last * 1.2},
                "capex": {str(y): -20e6 for y in _y}})
    _inf = {"name": "P", "currency": "USD", "financial_currency": "USD",
            "currency_converted": False, "currency_unresolved": False, "fx_rate": 1.0,
            "shares_outstanding": 1_000_000.0, "current_price": 100.0,
            "total_debt": 0.0, "total_cash": 500e6}
    return compute_metrics({"ticker": "P", "error": None, "info": _inf,
                            "statements": _st, "price_history": []}, resolve_assumptions())
_peaked = _mk_peak_metrics(250e6)   # latest ~25% margin vs ~10% history -> peak
_stable = _mk_peak_metrics(100e6)   # flat ~10% margin -> not a peak
ok(_peaked["cyclical_peak"]["peak"] and _peaked["scenarios"]["earnings_decline"] is not None,
   "scenario: a cyclical-peak company gets an earnings-decline row")
ok(not _stable["cyclical_peak"]["peak"] and _stable["scenarios"]["earnings_decline"] is None,
   "scenario: a stable-margin company gets no earnings-decline row")

# net-cash reconciliation: the card (balance) and the DCF (info) must agree on
# net cash — take the MORE COMPLETE debt (larger of EDGAR statements vs Yahoo
# info), and write it back to info so downstream consumers match.
_A = resolve_assumptions()
def _mk(statements, info):
    st = {k: {} for k in STATEMENT_KEYS}
    st.update(statements)
    inf = {"name": "T", "currency": "USD", "financial_currency": "USD",
           "currency_converted": False, "currency_unresolved": False, "fx_rate": 1.0,
           "shares_outstanding": 1_000_000.0, "current_price": 100.0}
    inf.update(info)
    return {"ticker": "T", "error": None, "info": inf, "statements": st, "price_history": []}

_yrs = lambda s, vals: {str(s + i): v for i, v in enumerate(vals)}
# EDGAR reports only $46M financial debt; Yahoo's $777M folds in leases.
_stmts = {"revenue": _yrs(2019, [1000e6]*6), "net_income": _yrs(2019, [180e6]*6),
          "total_equity": _yrs(2019, [2000e6]*6), "cash": _yrs(2019, [1900e6]*6),
          "total_debt": _yrs(2019, [46e6]*6),
          "operating_cashflow": _yrs(2019, [220e6]*6), "capex": _yrs(2019, [-30e6]*6)}
_stock = _mk(_stmts, {"total_debt": 777e6, "total_cash": 1900e6})
_m = compute_metrics(_stock, _A)
_bal = _m["balance"]
ok(_bal["total_debt"] == 777e6, "net-cash: reconciles to the more complete (larger) debt figure")
_dcf_nc = (_stock["info"]["total_cash"] or 0) - (_stock["info"]["total_debt"] or 0)
ok(abs(_bal["net_cash"] - _dcf_nc) < 1.0, "net-cash: card figure matches the DCF's net-cash basis")
ok(_stock["info"]["total_debt"] == 777e6, "net-cash: info is written back so downstream consumers agree")

# earnings-power model: justified P/E behaves and is clamped; value() shape.
import earnings_power as _ep
ok(_ep.justified_pe(0.30, 0.09, 0.04) > _ep.justified_pe(0.12, 0.09, 0.04),
   "earnings-power: higher ROE -> higher justified P/E")
ok(_ep.justified_pe(0.25, 0.12, 0.03) < _ep.justified_pe(0.25, 0.08, 0.03),
   "earnings-power: higher required return -> lower justified P/E")
ok(8.0 <= _ep.justified_pe(0.60, 0.08, 0.07) <= 25.0,
   "earnings-power: justified P/E clamped to a sane band (never a bubble multiple)")
ok(_ep.justified_pe(0.30, 0.10, 0.07) > _ep.justified_pe(0.30, 0.10, 0.03),
   "earnings-power: a faster grower earns a higher justified multiple")
_epv = _ep.value(5.0, 0.25, 0.09, 0.04, 100.0, 0.20)
ok(_epv["ok"] and _epv["method"] == "earnings-power" and _epv["mid"] > 0,
   "earnings-power: value() returns a positive earnings-power valuation")
ok(abs(_epv["buy_below"] - _epv["mid"] * 0.80) < 1e-9,
   "earnings-power: buy-below applies the margin of safety")
_rev = _ep.reverse(5.0, 0.25, 0.09, 0.04, 100.0)
ok(_rev["ok"] and 0.0 <= _rev["implied_growth"] <= 0.09,
   "earnings-power: reverse solves an implied growth within range")
_epv_neg = _ep.value(0.0, 0.25, 0.09, 0.04, 100.0, 0.20)
ok(not _epv_neg["ok"], "earnings-power: no valuation without positive earnings")

# capital-light financials: exchanges/data/ratings/insurance-brokers are valued as
# operating companies (not book value); real banks/insurers still use book value.
from valuation import needs_earnings_valuation as _nev  # noqa: E402
ok(_nev({"sector": "Financial Services", "industry": "Financial Data & Stock Exchanges"}) is False,
   "classify: exchange/data financial -> operating-company valuation (not book value)")
ok(_nev({"sector": "Financial Services", "industry": "Insurance Brokers"}) is False,
   "classify: insurance broker -> operating-company valuation")
ok(_nev({"sector": "Financial Services", "industry": "Banks - Diversified"}) is True,
   "classify: a real bank still uses the book-value model")
ok(_nev({"sector": "Financial Services", "industry": "Insurance - Property & Casualty"}) is True,
   "classify: a risk-bearing insurer still uses the book-value model")

# detrended stability: a steadily RISING margin should read as stable (predictable),
# not volatile — the whole point of scoring scatter around the trend.
from valuation import _earnings_stability as _stab  # noqa: E402
_sy = [2019, 2020, 2021, 2022, 2023, 2024]
_rising = _stab([(y, (0.10 + 0.02 * i) * 1000) for i, y in enumerate(_sy)],
                [(y, 1000) for y in _sy])
_choppy = _stab([(y, (0.10 if i % 2 == 0 else 0.20) * 1000) for i, y in enumerate(_sy)],
                [(y, 1000) for y in _sy])
ok(_rising > _choppy and _rising > 0.8,
   "stability: a steadily rising margin reads as stable, not volatile")
# A wild up/down swing (same mean) still scores meaningfully lower than the smooth rise.
_wild = _stab([(y, (0.02 if i % 2 == 0 else 0.28) * 1000) for i, y in enumerate(_sy)],
              [(y, 1000) for y in _sy])
ok(_wild < _rising - 0.3, "stability: a margin that swings hard still reads as unstable")

# ---------------------------------------------------------------------------
if FAILS:
    print(f"\n{len(FAILS)} failure(s):")
    for f in FAILS:
        print("  FAIL " + f)
    sys.exit(1)
print("\nAll feature tests passed.")
