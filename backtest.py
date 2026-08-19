#!/usr/bin/env python3
"""Backtest / validation — does a high score actually predict better returns?

For each name it rebuilds the score *as of* several past start-years (fundamentals
truncated to that year, price as of then), then measures the actual forward
**total return** (price appreciation + dividends received) over the holding
horizon. Pooling several regime-diverse windows — not one lucky 5-year stretch —
and counting dividends (which a price-only test unfairly denies the high-yield
financials/REITs the model scores well) makes the check far less regime-biased.

Buckets by as-of score to see whether higher scores earned higher total returns.
Still rough (restated statements, no point-in-time data), but honest enough to
tell you whether to trust the score across regimes.

    .venv/bin/python backtest.py --scope large --edgar
    .venv/bin/python backtest.py --scope large --edgar --years 5 --windows 2016,2018,2020
"""
from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "backend"))

from data import fetch_stock            # noqa: E402  (Yahoo, bulk-safe)
from valuation import compute_metrics, resolve_assumptions  # noqa: E402
from scoring import score               # noqa: E402
import universe as _universe            # noqa: E402


def _price_in_year(price_history, year):
    """Last close on/before the end of `year`."""
    best = None
    for p in price_history or []:
        if int(p["date"][:4]) <= year:
            best = p["close"]
    return best


def _dps_during(stock, start_year, end_year):
    """Dividends per share received over (start_year, end_year] — from the
    statements' total dividends paid ÷ shares each year. This is what a
    buy-and-hold investor actually collected on top of price change."""
    div = stock["statements"].get("dividends_paid", {})
    sh = stock["statements"].get("shares", {})
    total = 0.0
    for y in range(start_year + 1, end_year + 1):
        d, s = div.get(str(y)), sh.get(str(y))
        if d is not None and s and s > 0:
            total += abs(d) / s
    return total


def _forward_total_return(stock, start_year, end_year):
    ph = stock["price_history"]
    p0, p1 = _price_in_year(ph, start_year), _price_in_year(ph, end_year)
    if not p0 or not p1 or p0 <= 0:
        return None
    return (p1 - p0 + _dps_during(stock, start_year, end_year)) / p0


def _snapshot(stock, asof_year):
    """A version of `stock` as it would have looked at end of `asof_year`."""
    st = {k: {y: v for y, v in ser.items() if int(y) <= asof_year}
          for k, ser in stock["statements"].items()}
    if not st.get("revenue"):
        return None
    ph = [p for p in stock["price_history"] if int(p["date"][:4]) <= asof_year]
    price = _price_in_year(stock["price_history"], asof_year)
    if not price:
        return None

    def _last(key):
        s = sorted((int(y), v) for y, v in st.get(key, {}).items() if v is not None)
        return s[-1][1] if s else None

    shares = _last("shares")
    info = dict(stock["info"])
    info["current_price"] = price
    info["shares_outstanding"] = shares
    info["market_cap"] = (price * shares) if (price and shares) else None
    info["total_debt"] = _last("total_debt")
    info["total_cash"] = _last("cash")
    for k in ("free_cashflow_ttm", "operating_cashflow_ttm", "ebitda_ttm",
              "enterprise_value", "ev_to_ebitda", "ev_to_revenue", "trailing_pe",
              "forward_pe", "peg_ratio", "analyst_target"):
        info[k] = None
    return {"ticker": stock["ticker"], "info": info, "statements": st,
            "price_history": ph, "data_source": "snapshot"}


BUCKETS = [("80+", 80, 200), ("70-79", 70, 80), ("50-69", 50, 70), ("<50", -1, 50)]


def _report(rows, label, years):
    """rows: list of {score, total_return}. Print bucketed medians + the edge."""
    print(f"\n{label}  (n={len(rows)})")
    print(f"  {'as-of score':12}{'obs':>6}{'MEDIAN total':>14}{'(mean)':>11}")
    meds = []
    for name, lo, hi in BUCKETS:
        grp = [r["total_return"] for r in rows if lo <= r["score"] < hi]
        if not grp:
            print(f"  {name:12}{0:>6}{'—':>14}{'—':>11}"); meds.append(None); continue
        med = statistics.median(grp)
        meds.append(med)
        print(f"  {name:12}{len(grp):>6}{med*100:>13.0f}%{sum(grp)/len(grp)*100:>10.0f}%")
    hi = [r["total_return"] for r in rows if r["score"] >= 70]
    lo = [r["total_return"] for r in rows if r["score"] < 50]
    edge = None
    if hi and lo:
        hm, lm = statistics.median(hi), statistics.median(lo)
        edge = hm - lm
        print(f"  → ≥70: {hm*100:+.0f}%  vs  <50: {lm*100:+.0f}%  = {edge*100:+.0f} pt edge over ~{years}yr")
    seq = [m for m in meds if m is not None]
    if len(seq) >= 2:
        mono = all(seq[i] >= seq[i + 1] for i in range(len(seq) - 1))
        print(f"  monotonic (higher score → higher return): {'YES' if mono else 'no'}")
    return edge


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, default=5, help="forward holding horizon (default 5)")
    ap.add_argument("--windows", default="2016,2018,2020",
                    help="comma-separated as-of start years to pool (default 2016,2018,2020)")
    ap.add_argument("--scope", choices=["core", "full", "large"], default="core")
    ap.add_argument("--simfin", action="store_true", help="use SimFin instead of Yahoo")
    ap.add_argument("--edgar", action="store_true",
                    help="use SEC EDGAR (deep history — required for multi-window)")
    args = ap.parse_args()
    if args.simfin or args.edgar:
        try:
            from dotenv import load_dotenv
            load_dotenv(ROOT / ".env")
        except ImportError:
            pass
    windows = [int(w) for w in args.windows.split(",") if w.strip()]
    a = resolve_assumptions()
    symbols = _universe.get(args.scope)
    print(f"Backtesting {len(symbols)} names · {args.years}yr total-return horizon · "
          f"windows {windows} (→ {[w + args.years for w in windows]})\n")

    rows = []            # pooled observations across all windows
    by_window = {w: [] for w in windows}
    for i, sym in enumerate(symbols, 1):
        try:
            stock = fetch_stock(sym, use_simfin=args.simfin, use_edgar=args.edgar)
            if stock.get("error"):
                continue
            rev_years = sorted(int(y) for y in stock["statements"]["revenue"])
            if len(rev_years) < 3:
                continue
            n_obs = 0
            for w in windows:
                end = w + args.years
                if len([y for y in rev_years if y <= w]) < 3:
                    continue  # need a few years of history to score at the as-of date
                tr = _forward_total_return(stock, w, end)
                # Reject impossible returns (>50x, or worse than a total loss) —
                # they're split/price-history data glitches, not real outcomes.
                if tr is None or tr > 50 or tr < -1:
                    continue
                snap = _snapshot(stock, w)
                if not snap:
                    continue
                v = score(compute_metrics(snap, a))
                obs = {"ticker": sym, "window": w, "score": v["score"], "total_return": tr}
                rows.append(obs); by_window[w].append(obs); n_obs += 1
            if n_obs:
                print(f"  [{i}/{len(symbols)}] {sym}: {n_obs} window(s)")
        except Exception:  # noqa: BLE001
            continue

    if not rows:
        print("No results."); return
    print("\n" + "=" * 66)
    print("POOLED (all windows) — the regime-diverse, dividend-inclusive result")
    _report(rows, "All windows pooled", args.years)
    print("\n" + "-" * 66)
    print("PER-WINDOW (does the edge hold across regimes?)")
    for w in windows:
        if by_window[w]:
            _report(by_window[w], f"{w} → {w + args.years}", args.years)
    print("=" * 66)
    print("\nTotal return = price change + dividends received. Pools several start "
          "years to avoid one-regime bias. Still uses restated statements (not "
          "point-in-time); directional, not an academic backtest.")


if __name__ == "__main__":
    main()
