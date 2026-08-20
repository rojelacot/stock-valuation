#!/usr/bin/env python3
"""Point-in-time backtest: does a high score actually predict beating inflation?

The honest test of the whole methodology. For each name and each historical
cutoff year Y, we rebuild the stock *as it was knowable then* — financial
statements truncated to fiscal year <= Y, priced at the market ~4 months after
year-end (when the 10-K would have been filed, so no lookahead) — score it, then
measure the *actual* forward return from that entry to today. Bucketing the
observations by score band shows whether the score has any forward-return edge.

    .venv/bin/python tools/backtest.py --scope core --cutoffs 2017 2018 2019
    .venv/bin/python tools/backtest.py AAPL MSFT KO --cutoffs 2016 2018
    .venv/bin/python tools/backtest.py --self-test        # offline logic check

Caveats (read before trusting a number):
  * Survivorship bias — we backtest today's universe, which excludes names that
    delisted/went bankrupt. Real-world results would be worse.
  * Entry price uses a fixed ~4-month filing lag, not the actual filing date.
  * Yahoo's 10y monthly price history bounds how far back cutoffs can go.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from data import STATEMENT_KEYS, fetch_stock          # noqa: E402
from valuation import compute_metrics, resolve_assumptions  # noqa: E402
from scoring import score                              # noqa: E402

FILING_LAG_MONTHS = 4          # FY-Y 10-K is knowable ~4 months after year-end
MIN_HISTORY = 4                # need >= this many years up to Y to score
MIN_FORWARD_YEARS = 3.0        # need >= this much runway to measure a return
DEFAULT_INFLATION = 0.03       # real-return hurdle


# ---------------------------------------------------------------------------
# Point-in-time reconstruction
# ---------------------------------------------------------------------------
def truncate_stock(stock: dict, cutoff_year: int, entry_price: float) -> dict:
    """Rebuild `stock` as knowable at fiscal year <= cutoff_year, priced at
    `entry_price`. Statement series are trimmed; market fields are made
    point-in-time from the cutoff-year balance sheet."""
    st = {}
    for k in STATEMENT_KEYS:
        series = (stock.get("statements") or {}).get(k) or {}
        st[k] = {y: v for y, v in series.items() if _yr(y) is not None and _yr(y) <= cutoff_year}

    def at(key):
        s = st.get(key) or {}
        yrs = [y for y in s if _yr(y) is not None]
        return s[max(yrs, key=_yr)] if yrs else None

    shares = at("shares") or stock["info"].get("shares_outstanding")
    info = dict(stock["info"])
    info["current_price"] = entry_price
    info["shares_outstanding"] = shares
    info["market_cap"] = (entry_price * shares) if (entry_price and shares) else None
    info["total_debt"] = at("total_debt")
    info["total_cash"] = at("cash")
    # Analyst/estimate fields would be lookahead — blank them.
    for k in ("analyst_target", "recommendation", "forward_pe", "earnings_growth"):
        info[k] = None
    return {"ticker": stock["ticker"], "error": None, "info": info,
            "statements": st, "price_history": []}


def _yr(y):
    try:
        return int(str(y)[:4])
    except (ValueError, TypeError):
        return None


def entry_price(price_history: list[dict], cutoff_year: int):
    """Price at the first monthly bar on/after (cutoff_year+1)-{lag}. Returns
    (price, date_str) or (None, None) if history doesn't reach that far."""
    target = f"{cutoff_year + 1}-{FILING_LAG_MONTHS:02d}-01"
    later = [p for p in price_history if p.get("date") and p["date"] >= target and p.get("close")]
    if not later:
        return None, None
    p = min(later, key=lambda x: x["date"])
    return p["close"], p["date"]


def latest_price(price_history: list[dict]):
    pts = [p for p in price_history if p.get("date") and p.get("close")]
    if not pts:
        return None, None
    p = max(pts, key=lambda x: x["date"])
    return p["close"], p["date"]


def _years_between(d0: str, d1: str) -> float:
    y0, m0, dd0 = (int(x) for x in d0[:10].split("-"))
    y1, m1, dd1 = (int(x) for x in d1[:10].split("-"))
    return (date(y1, m1, dd1) - date(y0, m0, dd0)).days / 365.25


# ---------------------------------------------------------------------------
# Per-ticker backtest
# ---------------------------------------------------------------------------
def backtest_ticker(ticker: str, cutoffs: list[int], assumptions: dict) -> list[dict]:
    try:
        stock = fetch_stock(ticker, use_simfin=False, use_edgar=True)
    except Exception as e:  # noqa: BLE001
        print(f"  {ticker}: fetch failed ({e})")
        return []
    if stock.get("error"):
        print(f"  {ticker}: {stock['error']}")
        return []
    ph = stock.get("price_history") or []
    cur_price, cur_date = latest_price(ph)
    if not cur_price:
        print(f"  {ticker}: no price history")
        return []

    obs = []
    for Y in cutoffs:
        # enough history up to Y to score?
        rev_years = [_yr(y) for y in (stock["statements"].get("revenue") or {}) if _yr(y) is not None]
        if sum(1 for y in rev_years if y <= Y) < MIN_HISTORY:
            continue
        ep, ed = entry_price(ph, Y)
        if not ep:
            continue
        fwd_years = _years_between(ed, cur_date)
        if fwd_years < MIN_FORWARD_YEARS:
            continue
        pit = truncate_stock(stock, Y, ep)
        try:
            v = score(compute_metrics(pit, assumptions))
        except Exception:  # noqa: BLE001
            continue
        total_return = cur_price / ep - 1.0
        annualized = (cur_price / ep) ** (1.0 / fwd_years) - 1.0
        obs.append({
            "ticker": ticker, "cutoff": Y, "entry_date": ed, "entry_price": ep,
            "score": v.get("score"), "rating": v.get("rating"),
            "fwd_years": round(fwd_years, 1),
            "total_return": total_return, "annualized": annualized,
        })
    return obs


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------
BANDS = [(0, 50, "<50 (avoid)"), (50, 65, "50-64"),
         (65, 80, "65-79"), (80, 101, "80+ (buy bar)")]


def _spearman(pairs):
    """Rank correlation between score and annualized return (no numpy)."""
    pairs = [(a, b) for a, b in pairs if a is not None and b is not None]
    n = len(pairs)
    if n < 3:
        return None

    def ranks(vals):
        order = sorted(range(n), key=lambda i: vals[i])
        r = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and vals[order[j + 1]] == vals[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r

    xs = ranks([p[0] for p in pairs])
    ys = ranks([p[1] for p in pairs])
    mx, my = sum(xs) / n, sum(ys) / n
    num = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
    den = (sum((xs[i] - mx) ** 2 for i in range(n)) * sum((ys[i] - my) ** 2 for i in range(n))) ** 0.5
    return num / den if den else None


def aggregate(obs: list[dict], inflation: float) -> None:
    obs = [o for o in obs if o.get("score") is not None and o.get("annualized") is not None]
    if not obs:
        print("\nNo usable observations (need enough history + forward runway).")
        return
    print(f"\n{'='*68}\nBACKTEST — {len(obs)} point-in-time observations "
          f"(inflation hurdle {inflation*100:.0f}%/yr)\n{'='*68}")
    print(f"{'Score band':16} {'n':>4} {'median ann.':>12} {'mean ann.':>10} "
          f"{'beat infl.':>11} {'beat by 5%':>11}")
    for lo, hi, label in BANDS:
        b = [o for o in obs if lo <= (o["score"] or 0) < hi]
        if not b:
            print(f"{label:16} {0:>4}")
            continue
        anns = sorted(o["annualized"] for o in b)
        med = anns[len(anns) // 2]
        mean = sum(anns) / len(anns)
        beat = sum(1 for a in anns if a > inflation) / len(anns)
        beat5 = sum(1 for a in anns if a > inflation + 0.05) / len(anns)
        print(f"{label:16} {len(b):>4} {med*100:>11.1f}% {mean*100:>9.1f}% "
              f"{beat*100:>10.0f}% {beat5*100:>10.0f}%")

    rho = _spearman([(o["score"], o["annualized"]) for o in obs])
    print(f"\nSpearman rank corr (score vs forward annualized return): "
          f"{rho:+.3f}" if rho is not None else "\nSpearman: n/a")
    buys = [o for o in obs if o.get("rating") == "BUY"]
    if buys:
        anns = sorted(o["annualized"] for o in buys)
        beat = sum(1 for a in anns if a > inflation) / len(anns)
        print(f"BUY-rated calls: {len(buys)} · median {anns[len(anns)//2]*100:+.1f}%/yr · "
              f"{beat*100:.0f}% beat inflation")
    print("\n(Survivorship-biased — today's universe excludes past failures — so "
          "real edge is weaker than shown.)")


# ---------------------------------------------------------------------------
def _self_test() -> int:
    """Offline validation of the point-in-time reconstruction (no network)."""
    fails = []
    stock = {"ticker": "T", "info": {"shares_outstanding": 10, "current_price": 99},
             "statements": {k: {} for k in STATEMENT_KEYS}}
    stock["statements"]["revenue"] = {"2018": 100, "2019": 110, "2020": 120, "2021": 130}
    stock["statements"]["cash"] = {"2018": 5, "2019": 6, "2020": 7, "2021": 8}
    stock["statements"]["shares"] = {"2018": 10, "2019": 10, "2020": 9, "2021": 9}
    pit = truncate_stock(stock, 2019, entry_price=50)
    if set(pit["statements"]["revenue"]) != {"2018", "2019"}:
        fails.append("truncate: revenue not cut to <=2019")
    if pit["info"]["current_price"] != 50:
        fails.append("truncate: entry price not set")
    if pit["info"]["total_cash"] != 6:
        fails.append("truncate: cash not taken from cutoff year")
    if pit["info"]["market_cap"] != 50 * 10:
        fails.append("truncate: market cap not point-in-time")

    ph = [{"date": f"{y}-{m:02d}-01", "close": 10.0 + i}
          for i, (y, m) in enumerate([(2019, 6), (2020, 1), (2020, 5), (2021, 4), (2024, 4)])]
    ep, ed = entry_price(ph, 2019)      # first bar >= 2020-04
    if ed != "2020-05-01":
        fails.append(f"entry_price: expected 2020-05, got {ed}")
    if entry_price(ph, 2030) != (None, None):
        fails.append("entry_price: should be None past history")
    if abs(_years_between("2020-05-01", "2024-05-01") - 4.0) > 0.02:
        fails.append("years_between: wrong")
    if _spearman([(1, 1), (2, 2), (3, 3), (4, 4)]) < 0.99:
        fails.append("spearman: perfect monotonic should be ~1")

    for f in fails:
        print("  FAIL " + f)
    if not fails:
        print("  self-test: all reconstruction checks passed.")
    return 1 if fails else 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("tickers", nargs="*", help="tickers (default: --scope universe)")
    ap.add_argument("--scope", default="core", help="universe scope if no tickers given")
    ap.add_argument("--cutoffs", type=int, nargs="+", default=[2017, 2018, 2019],
                    help="fiscal-year cutoffs to backtest from")
    ap.add_argument("--inflation", type=float, default=DEFAULT_INFLATION)
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        sys.exit(_self_test())

    if args.tickers:
        symbols = [t.upper() for t in args.tickers]
    else:
        import universe as _u
        symbols = _u.get(args.scope)
    assumptions = resolve_assumptions()
    print(f"Backtesting {len(symbols)} names from cutoffs {args.cutoffs}…")
    all_obs = []
    for i, sym in enumerate(symbols, 1):
        print(f" [{i}/{len(symbols)}] {sym}")
        all_obs.extend(backtest_ticker(sym, args.cutoffs, assumptions))
    aggregate(all_obs, args.inflation)


if __name__ == "__main__":
    main()
