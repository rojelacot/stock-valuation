#!/usr/bin/env python3
"""Cross-platform weekly-screen scheduler — the container replacement for the
macOS launchd job. Runs `weekly_screen.py --scope <SCREEN_SCOPE>` every week at
SCHEDULE_HOUR on SCHEDULE_WEEKDAY (local time, honouring the TZ env var).

Enabled via the compose 'scheduler' profile; not needed if you run the screen
by hand (`docker compose exec app python weekly_screen.py --scope large`).
"""
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta

SCOPE = os.environ.get("SCREEN_SCOPE", "large")
HOUR = int(os.environ.get("SCHEDULE_HOUR", "8"))
WEEKDAY = int(os.environ.get("SCHEDULE_WEEKDAY", "0"))  # 0 = Monday


def _next_run(now: datetime) -> datetime:
    days = (WEEKDAY - now.weekday()) % 7
    cand = (now + timedelta(days=days)).replace(hour=HOUR, minute=0, second=0, microsecond=0)
    if cand <= now:
        cand += timedelta(days=7)
    return cand


def main() -> None:
    print(f"[scheduler] scope={SCOPE} weekday={WEEKDAY} hour={HOUR} "
          f"tz={os.environ.get('TZ', 'UTC')}", flush=True)
    while True:
        now = datetime.now()
        nxt = _next_run(now)
        wait = max(1.0, (nxt - now).total_seconds())
        print(f"[scheduler] next run {nxt.isoformat()} (in {wait/3600:.1f}h)", flush=True)
        time.sleep(wait)
        print(f"[scheduler] running weekly screen (--scope {SCOPE})", flush=True)
        try:
            subprocess.run([sys.executable, "weekly_screen.py", "--scope", SCOPE, "--no-ai"],
                           cwd="/app", check=False)
        except Exception as e:  # noqa: BLE001
            print(f"[scheduler] run failed: {e}", flush=True)
        time.sleep(60)  # avoid a tight re-fire if the run was near-instant


if __name__ == "__main__":
    main()
