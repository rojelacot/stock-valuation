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
if FAILS:
    print(f"\n{len(FAILS)} failure(s):")
    for f in FAILS:
        print("  FAIL " + f)
    sys.exit(1)
print("\nAll feature tests passed.")
