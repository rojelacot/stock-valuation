"""Week-over-week candidate diff state, shared by the weekly job and the app.

Snapshots are keyed by scope ('core' / 'full' / 'large' / 'all' / 'custom') so
an app scan of one universe diffs against the previous scan of the *same*
universe, and the weekly 'all' job keeps its own baseline — they never clobber
each other.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import atomic_json

SNAP = Path(__file__).resolve().parent.parent / "reports" / ".candidates_snap.json"


def _load() -> dict[str, Any]:
    return atomic_json.load(SNAP)


def compute_diff(scope: str, cur_map: dict[str, int], today: str,
                 update: bool = True) -> dict[str, Any]:
    """Return {prev_date, added, dropped, prev_scores} vs the last snapshot for
    `scope`, and (if update) persist the current candidates as the new baseline."""
    if not update:
        snap = _load()
        prev = snap.get(scope) or {}
        prev_map = prev.get("candidates", {})
        return {"scope": scope, "prev_date": prev.get("date"),
                "added": sorted(set(cur_map) - set(prev_map)),
                "dropped": sorted(set(prev_map) - set(cur_map)),
                "prev_scores": prev_map}

    # Compute the diff against, and persist the new baseline from, one locked
    # read-modify-write so a concurrent scan can't clobber the snapshot.
    def _mutate(snap):
        prev = snap.get(scope) or {}
        prev_map = prev.get("candidates", {})
        snap[scope] = {"date": today, "candidates": cur_map}
        return {"scope": scope, "prev_date": prev.get("date"),
                "added": sorted(set(cur_map) - set(prev_map)),
                "dropped": sorted(set(prev_map) - set(cur_map)),
                "prev_scores": prev_map}

    return atomic_json.update(SNAP, _mutate)
