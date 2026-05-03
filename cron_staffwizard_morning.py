#!/usr/bin/env python3
# © 2026 CoAssisted Workspace. Licensed under MIT.
"""AP-7 StaffWizard morning cron — runs the full pipeline daily at 6:30am.

Wraps `staffwizard_pipeline.refresh_all` with retry-on-late-email logic:
the StaffWizard nightly Overall Report sometimes lands at 6:35am instead
of 6:00am. If the first run gets `NoReportFoundError`, we retry every 30
minutes until 9:00am, then alert.

Logs to $HOME/logs/cron_staffwizard.log via the parent crontab redirect.
Exits 0 on success, 1 on hard failure (with a one-line summary on stderr
for the cron mail body).

Usage (from cron):
    30 6 * * *  $VENV_PYTHON $HOME/cron_staffwizard_morning.py

Env vars:
    COASSISTED_STAFFWIZARD_RETRY_UNTIL  HH:MM cutoff (default 09:00)
    COASSISTED_STAFFWIZARD_RETRY_EVERY  retry interval seconds (default 1800)
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import sys
import time
import traceback


def _retry_cutoff() -> _dt.time:
    raw = os.environ.get("COASSISTED_STAFFWIZARD_RETRY_UNTIL", "09:00")
    try:
        hh, mm = raw.split(":")
        return _dt.time(int(hh), int(mm))
    except ValueError:
        return _dt.time(9, 0)


def _retry_every() -> int:
    raw = os.environ.get("COASSISTED_STAFFWIZARD_RETRY_EVERY", "1800")
    try:
        return max(60, int(raw))
    except ValueError:
        return 1800


def main() -> int:
    # Ensure the project root is importable. This script is installed at
    # $HOME by install_crontab.sh; the import path is added there.
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        import staffwizard_pipeline as _pipe
    except Exception:  # pragma: no cover — import failure is fatal
        print("FATAL: cannot import staffwizard_pipeline", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return 1

    cutoff = _retry_cutoff()
    interval = _retry_every()

    started_at = _dt.datetime.now().astimezone()
    print(f"[{started_at.isoformat(timespec='seconds')}] starting refresh_all")
    while True:
        try:
            result = _pipe.refresh_all(fetch_latest=True)
            print(json.dumps(result, indent=2, default=str))
            return 0
        except _pipe.NoReportFoundError as e:
            now = _dt.datetime.now().astimezone()
            if now.time() >= cutoff:
                print(
                    f"[{now.isoformat(timespec='seconds')}] still no report "
                    f"after retry cutoff {cutoff} — alerting",
                    file=sys.stderr,
                )
                print(f"NoReportFoundError: {e}", file=sys.stderr)
                return 1
            print(
                f"[{now.isoformat(timespec='seconds')}] no report yet, "
                f"retrying in {interval}s",
            )
            time.sleep(interval)
        except Exception as e:  # noqa: BLE001 — top-level entry point
            print(f"FATAL: {type(e).__name__}: {e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
