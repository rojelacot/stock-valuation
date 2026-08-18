#!/usr/bin/env python3
"""Backtest / validation — does a high score actually predict better returns?

For each name in a universe it rebuilds the score *as of ~N years ago* (fundamentals
truncated to that year, price as of then), then measures the actual forward price
return to today. Buckets by as-of score to see whether higher scores earned higher
returns. This is a rough, data-limited check (free ~4-7yr statements, no
point-in-time restatement history), not an academic backtest — but it tells you
whether to trust the score.

    .venv/bin/python backtest.py                 # core universe, 2yr lookback
    .venv/bin/python backtest.py --years 3 --scope full
"""
from __future__ import annotations

import argparse
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
    # Blank out "today" market fields to avoid look-ahead leakage.
    for k in ("free_cashflow_ttm", "operating_cashflow_ttm", "ebitda_ttm",
              "enterprise_value", "ev_to_ebitda", "ev_to_revenue", "trailing_pe",
              "forward_pe", "peg_ratio", "analyst_target"):
        info[k] = None
    return {"ticker": stock["ticker"], "info": info, "statements": st,
            "price_history": ph, "data_source": "snapshot"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, default=2, help="lookback in years (default 2)")
    ap.add_argument("--scope", choices=["core", "full", "large"], default="core")
    ap.add_argument("--simfin", action="store_true",
                    help="use SimFin (7yr) instead of Yahoo — needs SIMFIN_API_KEY, spends credits")
    args = ap.parse_args()
    if args.simfin:
        try:
            from dotenv import load_dotenv
            load_dotenv(ROOT / ".env")
        except ImportError:
            pass
    a = resolve_assumptions()
    symbols = _universe.get(args.scope)
    print(f"Backtesting {len(symbols)} names, {args.years}yr lookback…\n")

    results = []
    for i, sym in enumerate(symbols, 1):
        try:
            stock = fetch_stock(sym, use_simfin=args.simfin)
            if stock.get("error"):
                continue
            yrs = sorted(int(y) for y in stock["statements"]["revenue"])
            if len(yrs) < 2:
                continue
            asof = yrs[-1] - args.years
            if asof < yrs[0]:
                continue
            snap = _snapshot(stock, asof)
            if not snap:
                continue
            m = compute_metrics(snap, a)
            v = score(m)
            price_then = snap["info"]["current_price"]
            price_now = stock["info"]["current_price"]
            if not price_then or not price_now:
                continue
            fwd = price_now / price_then - 1
            results.append({"ticker": sym, "score": v["score"], "rating": v["rating"],
                            "fwd_return": fwd})
            print(f"  [{i}/{len(symbols)}] {sym}: as-of score {v['score']} → {fwd*100:+.0f}%")
        except Exception:  # noqa: BLE001
            continue

    if not results:
        print("No results."); return
    import statistics
    print("\n" + "=" * 64)
    # MEDIAN is the headline (outlier-robust); mean shown only as context, since a
    # few bull-market moonshots in the low-score bucket distort the average.
    buckets = [("80+", 80, 200), ("70-79", 70, 80), ("50-69", 50, 70), ("<50", -1, 50)]
    print(f"{'As-of score':12}{'names':>7}{'MEDIAN fwd':>14}{'(mean)':>12}")
    med_by_bucket = []
    for label, lo, hi in buckets:
        grp = [r["fwd_return"] for r in results if lo <= r["score"] < hi]
        if not grp:
            print(f"{label:12}{0:>7}{'—':>14}{'—':>12}"); med_by_bucket.append(None); continue
        med = statistics.median(grp)
        med_by_bucket.append(med)
        print(f"{label:12}{len(grp):>7}{med*100:>13.0f}%{sum(grp)/len(grp)*100:>11.0f}%")

    # Signal verdict — MEDIAN-based (the metric that actually holds up at scale).
    hi = [r["fwd_return"] for r in results if r["score"] >= 70]
    lo = [r["fwd_return"] for r in results if r["score"] < 50]
    if hi and lo:
        hm, lm = statistics.median(hi), statistics.median(lo)
        print(f"\nMedian: high scorers (≥70) {hm*100:+.0f}% vs low scorers (<50) {lm*100:+.0f}% "
              f"over ~{args.years}yr\n→ score {'ADDED signal' if hm > lm else 'did NOT add signal'} "
              "(median-based, outlier-robust).")
    # Monotonicity check on medians (best → worst bucket).
    seq = [m for m in med_by_bucket if m is not None]
    if len(seq) >= 2:
        mono = all(seq[i] >= seq[i + 1] for i in range(len(seq) - 1))
        print(f"Median monotonic across score buckets (higher score → higher return): "
              f"{'YES' if mono else 'no (some wobble)'}.")
    print("=" * 64)
    print("\nCaveat: uses today's (possibly restated) statements truncated by year, "
          "not true point-in-time data, and ignores dividends. Directional only.")


if __name__ == "__main__":
    main()
