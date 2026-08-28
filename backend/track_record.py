"""Forward track record — the survivorship-free, out-of-sample validator.

The weekly screen logs every BUY candidate with its entry price and date. This
scores what actually happened next: each past pick's forward price return since it
was first flagged, benchmarked against the S&P 500 (SPY) over the same span. Unlike
the backtest — retrospective, survivorship-biased, three windows in one bull decade
— this is the real, forward, out-of-sample record accumulating in real time. It is
the honest test of whether the screen adds value; it just needs months to mature.

Return is price-only (dividends not yet counted) and each ticker is measured from
its FIRST appearance on the buy list (buy-and-hold from when the screen first
flagged it). The S&P benchmark is dated to the last monthly close on/before the
flag date — approximate for very recent picks, fine for the multi-month horizons
this is built to measure.
"""
from __future__ import annotations

import json
import statistics
import time
from datetime import date
from pathlib import Path
from typing import Any

import data

ROOT = Path(__file__).resolve().parent.parent
HISTORY_FILE = ROOT / "reports" / "screen_history.json"
_CACHE: dict[str, tuple[float, dict]] = {}   # scope -> (timestamp, result)
CACHE_TTL = 900   # 15 min — live prices, but no need to re-fetch on every tab click


def _load(scope: str) -> list:
    try:
        return json.loads(HISTORY_FILE.read_text()).get(scope, [])
    except (OSError, ValueError):
        return []


def _spy_close_at(spy_hist: list, date_str: str):
    """Last monthly SPY close on/before `date_str` (spy_hist sorted ascending)."""
    best = None
    for d, c in spy_hist:
        if d <= date_str:
            best = c
        else:
            break
    return best


def forward_performance(scope: str = "large") -> dict[str, Any]:
    now = time.time()
    cached = _CACHE.get(scope)
    if cached and now - cached[0] < CACHE_TTL:
        return cached[1]

    runs = sorted(_load(scope), key=lambda r: r.get("date", ""))
    first: dict[str, dict] = {}   # ticker -> first appearance (buy-and-hold basis)
    for r in runs:
        for w in (r.get("winners") or []):
            t = w.get("ticker")
            if t and t not in first and w.get("price"):
                first[t] = {"date": r["date"], "entry": w["price"],
                            "name": w.get("name"), "sector": w.get("sector")}
    if not first:
        return {"available": False, "reason": "No screen history yet."}

    quotes = data.bulk_quote(list(first))
    try:
        spy = data.fetch_stock("SPY", use_simfin=False, use_edgar=False)
        spy_hist = sorted((p["date"], p["close"]) for p in spy.get("price_history", []) if p.get("close"))
        spy_now = spy["info"].get("current_price") or (spy_hist[-1][1] if spy_hist else None)
    except Exception:  # noqa: BLE001
        spy_hist, spy_now = [], None

    today = date.today()
    picks = []
    for t, f in first.items():
        cur = (quotes.get(t) or {}).get("price")
        entry = f["entry"]
        if not cur or not entry or entry <= 0:
            continue
        ret = cur / entry - 1
        spy0 = _spy_close_at(spy_hist, f["date"]) if spy_hist else None
        spret = (spy_now / spy0 - 1) if (spy0 and spy_now) else None
        alpha = (ret - spret) if spret is not None else None
        days = (today - date.fromisoformat(f["date"])).days
        picks.append({"ticker": t, "name": f["name"], "sector": f["sector"],
                      "flagged": f["date"], "days": days, "entry": entry, "current": cur,
                      "return": ret, "sp_return": spret, "alpha": alpha})
    if not picks:
        return {"available": False, "reason": "Could not fetch current prices."}

    picks.sort(key=lambda p: p["return"], reverse=True)
    rets = [p["return"] for p in picks]
    alphas = [p["alpha"] for p in picks if p["alpha"] is not None]
    summary = {
        "n": len(picks),
        "median_return": statistics.median(rets),
        "mean_return": sum(rets) / len(rets),
        "median_alpha": statistics.median(alphas) if alphas else None,
        "mean_alpha": (sum(alphas) / len(alphas)) if alphas else None,
        "hit_rate": (sum(1 for a in alphas if a > 0) / len(alphas)) if alphas else None,
        "avg_days": round(sum(p["days"] for p in picks) / len(picks)),
        "since": min(p["flagged"] for p in picks),
    }
    result = {"available": True, "picks": picks, "summary": summary}
    _CACHE[scope] = (now, result)
    return result
