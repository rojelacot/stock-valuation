"""Week-over-week candidate diff state, shared by the weekly job and the app.

Snapshots are keyed by scope ('core' / 'full' / 'large' / 'all' / 'custom') so
an app scan of one universe diffs against the previous scan of the *same*
universe, and the weekly 'all' job keeps its own baseline — they never clobber
each other.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SNAP = Path(__file__).resolve().parent.parent / "reports" / ".candidates_snap.json"


def _load() -> dict[str, Any]:
    try:
        return json.loads(SNAP.read_text())
    except Exception:  # noqa: BLE001
        return {}


def compute_diff(scope: str, cur_map: dict[str, int], today: str,
                 update: bool = True) -> dict[str, Any]:
    """Return {prev_date, added, dropped, prev_scores} vs the last snapshot for
    `scope`, and (if update) persist the current candidates as the new baseline."""
    snap = _load()
    prev = snap.get(scope) or {}
    prev_map = prev.get("candidates", {})
    added = sorted(set(cur_map) - set(prev_map))
    dropped = sorted(set(prev_map) - set(cur_map))
    diff = {"scope": scope, "prev_date": prev.get("date"),
            "added": added, "dropped": dropped, "prev_scores": prev_map}
    if update:
        snap[scope] = {"date": today, "candidates": cur_map}
        SNAP.parent.mkdir(exist_ok=True)
        SNAP.write_text(json.dumps(snap))
    return diff
