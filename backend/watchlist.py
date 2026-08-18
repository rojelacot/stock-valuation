"""Persistent watchlist + thesis journal (one JSON file).

Each entry is a ticker you're watching or own, with your thesis notes, the price
you'd pay / paid, and thesis-breakers (optionally seeded from the AI read). The
live price-vs-buy-below check is added by the API at read time.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

STORE = Path(__file__).resolve().parent.parent / "reports" / "watchlist.json"

FIELDS = ("notes", "buy_price", "thesis", "thesis_breakers", "owned", "added")


def load() -> dict[str, Any]:
    try:
        return json.loads(STORE.read_text())
    except Exception:  # noqa: BLE001
        return {}


def save(data: dict[str, Any]) -> None:
    STORE.parent.mkdir(exist_ok=True)
    STORE.write_text(json.dumps(data, indent=2))


def upsert(ticker: str, fields: dict[str, Any], today: str) -> dict[str, Any]:
    data = load()
    ticker = ticker.strip().upper()
    entry = data.get(ticker, {"added": today})
    for k in ("notes", "buy_price", "thesis", "owned"):
        if k in fields:
            entry[k] = fields[k]
    if "thesis_breakers" in fields:
        tb = fields["thesis_breakers"]
        entry["thesis_breakers"] = tb if isinstance(tb, list) else [tb] if tb else []
    entry.setdefault("added", today)
    data[ticker] = entry
    save(data)
    return entry


def remove(ticker: str) -> bool:
    data = load()
    ticker = ticker.strip().upper()
    if ticker in data:
        del data[ticker]
        save(data)
        return True
    return False
