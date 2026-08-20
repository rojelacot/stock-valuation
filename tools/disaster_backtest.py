#!/usr/bin/env python3
"""Disaster-catch backtest — does the app's red flags fire BEFORE a blow-up?

The score backtest asked the wrong question (survivorship-biased data can't
measure capital preservation, because the failures are deleted). This asks the
right one: take a hand-built list of known disasters — bankruptcies, big dividend
cuts, frauds/restatements — reconstruct each company from EDGAR AS IT WAS KNOWN
~1 year before the event (financial statements truncated to the last fiscal year
that would have been filed), run the red-flag checks, and see whether any fired.

Then run the same checks on a control group of healthy survivors, so the result
is a real signal (catch rate on disasters vs false-alarm rate on healthy names),
not just recall. A flag that fires on everything is worthless.

Uses ONLY EDGAR fundamentals — no price — so it works on delisted/bankrupt
filers, whose 10-K history persists at the SEC.

    .venv/bin/python tools/disaster_backtest.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

import edgar                                      # noqa: E402
import refinancing, working_capital, leverage_trend, dividend_coverage  # noqa: E402
import forensics                                  # noqa: E402

# (label, ticker, cik|None, type, event_year). cutoff = event_year - 1.
DISASTERS = [
    # Bankruptcies (delisted -> explicit CIK)
    ("Bed Bath & Beyond", "BBBY", 886158, "bankruptcy", 2023),
    ("Rite Aid", "RAD", 84129, "bankruptcy", 2023),
    ("WeWork", "WE", 1813756, "bankruptcy", 2023),
    ("J.C. Penney", "JCP", 1166126, "bankruptcy", 2020),
    ("Chesapeake Energy", "CHK", 895126, "bankruptcy", 2020),
    ("Frontier Communications", "FTR", 20520, "bankruptcy", 2020),
    ("Revlon", "REV", 887921, "bankruptcy", 2022),
    ("Yellow Corp", "YELL", 716006, "bankruptcy", 2023),
    ("Party City", "PRTY", 1592058, "bankruptcy", 2023),
    ("Sears Holdings", "SHLDQ", 1310067, "bankruptcy", 2018),
    # Big dividend cuts / suspensions (still listed)
    ("General Electric", "GE", None, "dividend_cut", 2018),
    ("Kraft Heinz", "KHC", None, "dividend_cut", 2019),
    ("Intel", "INTC", None, "dividend_cut", 2023),
    ("AT&T", "T", None, "dividend_cut", 2022),
    ("Occidental", "OXY", None, "dividend_cut", 2020),
    ("Ford", "F", None, "dividend_cut", 2020),
    ("Boeing", "BA", None, "dividend_cut", 2020),
    ("Macy's", "M", None, "dividend_cut", 2020),
    # Frauds / restatements
    ("Under Armour", "UAA", None, "fraud_restatement", 2019),
    ("Bausch (Valeant)", "BHC", None, "fraud_restatement", 2016),
]

# Healthy survivors — no blow-up over the period. Judged as of FY2019 (a clean,
# pre-COVID year). If the flags fire on these, they're too trigger-happy.
CONTROLS = ["MSFT", "AAPL", "JNJ", "PG", "KO", "PEP", "WMT", "HD", "COST", "MCD",
            "UNP", "TXN", "LOW", "CL", "ABT", "LIN", "ADP", "ROP", "NKE", "ITW"]
CONTROL_CUTOFF = 2019

# Which flags are "relevant" to each disaster type (for a fair per-type catch).
RELEVANT = {
    "bankruptcy": {"leverage", "refinancing", "dividend", "forensic", "working_capital"},
    "dividend_cut": {"dividend", "leverage", "refinancing"},
    "fraud_restatement": {"forensic", "working_capital"},
}
FLAGS = ["forensic", "refinancing", "leverage", "working_capital", "dividend"]


def _truncate(statements: dict, cutoff: int) -> dict:
    out = {}
    for k, s in (statements or {}).items():
        out[k] = {y: v for y, v in (s or {}).items()
                  if str(y).isdigit() and int(y) <= cutoff and v is not None}
    return out


def _run_checks(statements: dict) -> dict:
    """Return {flag: fired_bool} for a point-in-time statement set."""
    info = {"sector": ""}  # non-financial: run all checks
    out = {}

    fx = forensics.analyze(statements, info)
    az = (fx.get("altman") or {}) if fx.get("applicable") else {}
    bm = (fx.get("beneish") or {}) if fx.get("applicable") else {}
    out["forensic"] = bool(az.get("distress") or bm.get("manipulator"))

    rf = refinancing.assess(statements, info)
    out["refinancing"] = rf.get("level") in ("elevated", "high")

    lt = leverage_trend.assess(statements, info)
    out["leverage"] = lt.get("level") in ("deteriorating", "stressed")

    wc = working_capital.assess(statements, info)
    out["working_capital"] = wc.get("level") == "elevated"

    dc = dividend_coverage.assess(statements, info)
    out["dividend"] = dc.get("level") == "uncovered"
    return out


def _fetch(ticker, cik):
    try:
        r = edgar.fetch_statements(ticker, cik=cik)
    except Exception as e:  # noqa: BLE001
        return None, f"fetch error: {e}"
    if r.get("error"):
        return None, r["error"]
    return r.get("statements", {}), None


def main() -> None:
    print("Reconstructing each name ~1 year pre-event from EDGAR and running the red flags…\n")

    # ---- Disasters ----
    dis_rows, dis_fired = [], {f: 0 for f in FLAGS}
    caught = 0
    usable = 0
    for label, tk, cik, typ, yr in DISASTERS:
        cutoff = yr - 1
        st, err = _fetch(tk, cik)
        if st is None or len([y for y in (st.get("revenue") or {}) if int(y) <= cutoff]) < 3:
            print(f"  (skip {label:24} — {err or 'insufficient pre-event data'})")
            continue
        usable += 1
        fired = _run_checks(_truncate(st, cutoff))
        for f in FLAGS:
            if fired[f]:
                dis_fired[f] += 1
        rel = RELEVANT[typ]
        hit = any(fired[f] for f in FLAGS if f in rel)
        caught += hit
        dis_rows.append((label, typ, cutoff, fired, hit))

    # ---- Controls ----
    ctrl_rows, ctrl_fired = [], {f: 0 for f in FLAGS}
    false_alarms = 0
    ctrl_usable = 0
    for tk in CONTROLS:
        st, err = _fetch(tk, None)
        if st is None or len([y for y in (st.get("revenue") or {}) if int(y) <= CONTROL_CUTOFF]) < 3:
            print(f"  (skip control {tk} — {err or 'insufficient data'})")
            continue
        ctrl_usable += 1
        fired = _run_checks(_truncate(st, CONTROL_CUTOFF))
        for f in FLAGS:
            if fired[f]:
                ctrl_fired[f] += 1
        any_fired = any(fired.values())
        false_alarms += any_fired
        ctrl_rows.append((tk, fired, any_fired))

    # ---- Report ----
    mark = lambda b: "🚩" if b else " ·"  # noqa: E731
    W = "%-24s %-16s %-6s " + " %-4s" * len(FLAGS) + "  %s"
    print("\n" + "=" * 96)
    print("DISASTERS — did any relevant red flag fire ~1yr before the event?")
    print("=" * 96)
    print(W % ("name", "event", "as-of", *[f[:4] for f in FLAGS], "caught?"))
    for label, typ, cutoff, fired, hit in dis_rows:
        print(W % (label[:24], typ, cutoff, *[mark(fired[f]) for f in FLAGS],
                   "YES" if hit else "— missed"))

    print("\n" + "=" * 96)
    print("CONTROLS (healthy survivors, as of FY%d) — do the flags stay quiet?" % CONTROL_CUTOFF)
    print("=" * 96)
    print(W % ("name", "", "", *[f[:4] for f in FLAGS], "false alarm?"))
    for tk, fired, any_fired in ctrl_rows:
        print(W % (tk, "", "", *[mark(fired[f]) for f in FLAGS],
                   "⚠ yes" if any_fired else "clean"))

    print("\n" + "=" * 96)
    print("SCORECARD")
    print("=" * 96)
    print(f"  Disaster catch rate:   {caught}/{usable} = {caught/usable*100:.0f}%  "
          "(≥1 relevant flag fired before the event)")
    print(f"  Control false-alarm:   {false_alarms}/{ctrl_usable} = {false_alarms/ctrl_usable*100:.0f}%  "
          "(≥1 flag fired on a healthy name)")
    print("\n  Per-flag  —  fired on DISASTERS  vs  fired on CONTROLS (want high vs low):")
    for f in FLAGS:
        dr = dis_fired[f] / usable * 100 if usable else 0
        cr = ctrl_fired[f] / ctrl_usable * 100 if ctrl_usable else 0
        print(f"    {f:16} {dis_fired[f]:>2}/{usable} disasters ({dr:>3.0f}%)   "
              f"{ctrl_fired[f]:>2}/{ctrl_usable} controls ({cr:>3.0f}%)")
    print("\n  (Fundamentals-only reconstruction; no price. A flag is useful when it fires "
          "far more on\n   disasters than on healthy names — that gap, not the raw catch rate, "
          "is the signal.)")


if __name__ == "__main__":
    main()
