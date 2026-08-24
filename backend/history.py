"""Append-only history of weekly screen winners, for the Track-record tab.

`diffstate` keeps only the single previous snapshot (enough for a one-week
added/dropped diff). This keeps the *full* multi-week series so the app can show
each week's winners side by side and surface the names that keep showing up —
the highest-conviction ideas. Keyed by scope, like diffstate, so the 'all'
weekly job and an ad-hoc 'core' scan don't clobber each other.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import atomic_json

HIST = Path(__file__).resolve().parent.parent / "reports" / "screen_history.json"
MAX_WEEKS = 104  # keep ~2yr of weekly snapshots per scope


def _load() -> dict[str, Any]:
    return atomic_json.load(HIST)


def _winner(row: dict[str, Any]) -> dict[str, Any]:
    """Trim a candidate row to the fields the track-record view needs. Handles
    both the app's _summary_row shape and weekly_screen's row shape."""
    up = row.get("upside")
    if up is None:
        up = row.get("upside_mid")
    scaling = row.get("margin_of_safety_scaling") or {}
    certainty = row.get("certainty", scaling.get("certainty"))
    mos = row.get("mos", scaling.get("effective"))
    buy_below = row.get("buy_below")
    price = row.get("price")
    below_buy = row.get("below_buy")
    if below_buy is None and price is not None and buy_below is not None:
        below_buy = price <= buy_below
    return {
        "ticker": row.get("ticker"),
        "name": row.get("name"),
        "sector": row.get("sector"),
        "score": row.get("score"),
        "rating": row.get("rating"),
        "upside": up,
        "price": price,
        # New-thesis fields: certainty-scaled margin of safety (principle 4).
        "certainty": certainty,
        "mos": mos,
        "buy_below": buy_below,
        "below_buy": below_buy,
    }


def record(scope: str, today: str, min_score: int,
           candidates: list[dict[str, Any]], note: str | None = None) -> None:
    """Append (or replace, if same date) this run's winners for `scope`.

    `note` flags a week that isn't fully comparable to the others (e.g. a
    backfilled run made on an older methodology) — surfaced as a caveat in the
    Track-record tab so the week-over-week deltas are read correctly."""
    if not today:
        return
    winners = [_winner(c) for c in candidates if c.get("ticker")]
    entry = {"date": today, "min_score": min_score,
             "count": len(winners), "winners": winners}
    if note:
        entry["note"] = note

    def _mutate(hist):
        runs = [r for r in hist.get(scope, []) if r.get("date") != today]
        runs.append(entry)
        runs.sort(key=lambda r: r.get("date") or "")
        hist[scope] = runs[-MAX_WEEKS:]

    # Locked + atomic: the launchd job and an in-app scan can write concurrently.
    atomic_json.update(HIST, _mutate)


def summarize(scope: str) -> dict[str, Any]:
    """Roll the raw series up into what the Track-record tab renders:
      weeks  — chronological [{date, count, tickers:[...]}]
      board  — per-ticker roll-up sorted by conviction (appearances, then streak)
      latest — added / dropped / held vs the prior week
    """
    runs = _load().get(scope, [])
    weeks = [{"date": r["date"], "count": r.get("count", len(r.get("winners", []))),
              "tickers": [w["ticker"] for w in r.get("winners", [])],
              "note": r.get("note")}
             for r in runs]
    notes = [{"date": r["date"], "note": r["note"]} for r in runs if r.get("note")]

    # Per-ticker roll-up across every recorded week.
    dates = [r["date"] for r in runs]
    agg: dict[str, dict[str, Any]] = {}
    for r in runs:
        for w in r.get("winners", []):
            t = w["ticker"]
            a = agg.setdefault(t, {"ticker": t, "name": w.get("name"),
                                   "sector": w.get("sector"), "scores": {},
                                   "buyzone": {},
                                   "first_seen": r["date"], "last_seen": r["date"]})
            a["name"] = w.get("name") or a["name"]
            a["sector"] = w.get("sector") or a["sector"]
            a["scores"][r["date"]] = w.get("score")
            a["buyzone"][r["date"]] = w.get("below_buy")
            # Certainty-scaled MoS is a current intrinsic estimate, not per-week;
            # keep the most recent non-null across the series.
            for k in ("certainty", "mos", "buy_below"):
                if w.get(k) is not None:
                    a[k] = w.get(k)
            a["first_seen"] = min(a["first_seen"], r["date"])
            a["last_seen"] = max(a["last_seen"], r["date"])

    def _streak(scores: dict[str, Any]) -> int:
        # consecutive most-recent weeks the name appears in
        s = 0
        for d in reversed(dates):
            if d in scores:
                s += 1
            else:
                break
        return s

    board = []
    latest_date = dates[-1] if dates else None
    for a in agg.values():
        appearances = len(a["scores"])
        latest_score = a["scores"].get(latest_date) if latest_date else None
        board.append({
            "ticker": a["ticker"], "name": a["name"], "sector": a["sector"],
            "appearances": appearances, "weeks_total": len(dates),
            "streak": _streak(a["scores"]),
            "first_seen": a["first_seen"], "last_seen": a["last_seen"],
            "latest_score": latest_score,
            "present_latest": latest_date in a["scores"] if latest_date else False,
            "scores": a["scores"],
            # New-thesis: certainty-scaled margin of safety and buy-below.
            "certainty": a.get("certainty"),
            "mos": a.get("mos"),
            "buy_below": a.get("buy_below"),
            "buyzone": a.get("buyzone", {}),
        })
    # Highest conviction first: most appearances, then longest active streak, then score.
    board.sort(key=lambda x: (-x["appearances"], -x["streak"],
                              -(x["latest_score"] or 0)))

    latest = {"date": None, "prev_date": None, "added": [], "dropped": [], "held": []}
    if len(runs) >= 1:
        latest["date"] = dates[-1]
        cur = set(weeks[-1]["tickers"])
        if len(runs) >= 2:
            latest["prev_date"] = dates[-2]
            prev = set(weeks[-2]["tickers"])
            latest["added"] = sorted(cur - prev)
            latest["dropped"] = sorted(prev - cur)
            latest["held"] = sorted(cur & prev)
        else:
            latest["added"] = sorted(cur)

    return {"scope": scope, "weeks": weeks, "board": board,
            "latest": latest, "notes": notes}
