"""Cross-process-safe JSON read-modify-write.

The weekly launchd job (`weekly_screen.py`) and the in-app background scan
(`main._run_scan_job`) are separate processes that both mutate shared state
files (`screen_history.json`, `.candidates_snap.json`). A plain
read → mutate → write races: the later writer clobbers the earlier one, and an
interleaved partial write can leave malformed JSON that the next reader silently
resets to `{}`, dropping the whole multi-week series.

`update()` fixes both: an exclusive file lock serializes writers across
processes, and a temp-file + `os.replace` makes each write atomic, so a reader
ever only sees a complete old-or-new file — never a partial one.

Locking uses POSIX `fcntl.flock` (this app targets darwin). On a platform
without `fcntl` the cross-process lock degrades to a no-op — the atomic-replace
still prevents a *corrupt* file, but a concurrent lost update becomes possible.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Callable

try:
    import fcntl  # POSIX (macOS/Linux); this app targets darwin
    _HAVE_FCNTL = True
except ImportError:  # pragma: no cover - non-POSIX fallback
    _HAVE_FCNTL = False


def load(path: Path) -> dict:
    """Read a JSON object, or {} if absent/unreadable. Because writes are
    atomic, 'unreadable' means a genuinely missing/first-run file, not a
    half-written one."""
    try:
        return json.loads(Path(path).read_text())
    except Exception:  # noqa: BLE001
        return {}


def _atomic_write(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, path)  # atomic on POSIX
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def update(path: Path, mutate: Callable[[dict], Any]) -> Any:
    """Under an exclusive lock: load the file, call `mutate(data)` (which edits
    `data` in place and may return a value), then atomically persist it. Returns
    whatever `mutate` returned."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_suffix(path.suffix + ".lock")
    lf = open(lock, "w")
    try:
        if _HAVE_FCNTL:
            fcntl.flock(lf, fcntl.LOCK_EX)
        data = load(path)
        result = mutate(data)
        # Pretty-print: screen_history.json is versioned in git, so each weekly
        # run should produce a readable line-level diff, not one giant line.
        _atomic_write(path, json.dumps(data, indent=2) + "\n")
        return result
    finally:
        try:
            if _HAVE_FCNTL:
                fcntl.flock(lf, fcntl.LOCK_UN)
        finally:
            lf.close()
