#!/usr/bin/env python3
"""Standalone script: refresh managed CRM fields on every saved contact.

Designed for a daily cron:
    0 7 * * *  /path/to/.venv/bin/python /path/to/refresh_stats.py

Prints a one-line summary to stdout and detailed logs to the usual logs dir.
Exits non-zero on hard auth/config errors so cron can detect failure.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import crm_stats
from auth import get_credentials
from googleapiclient.discovery import build
from logging_util import log


_PROJECT_DIR = Path(__file__).resolve().parent
_LOG_DIR = _PROJECT_DIR / "logs"


def _rotate_cron_log(max_mb: int = 10) -> None:
    """Trim our shell-redirected cron log file once it exceeds max_mb.

    Convention: cron line in HANDOFF.md redirects to logs/<script>.cron.log.
    We keep one backup as .old; the previous backup is overwritten on rotation.
    """
    log_path = _LOG_DIR / f"{Path(__file__).stem}.cron.log"
    if not log_path.exists():
        return
    if log_path.stat().st_size > max_mb * 1024 * 1024:
        backup = log_path.with_suffix(".cron.log.old")
        try:
            if backup.exists():
                backup.unlink()
            log_path.rename(backup)
            log.info("refresh_stats: rotated cron log (was > %d MB)", max_mb)
        except Exception as e:
            log.warning("refresh_stats: cron log rotation failed: %s", e)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Refresh managed CRM fields on every saved contact."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List contacts that would be refreshed; skip actual updates.",
    )
    parser.add_argument(
        "--max-cron-log-mb",
        type=int,
        default=10,
        help="Rotate logs/<script>.cron.log when it exceeds this size (default 10).",
    )
    args = parser.parse_args(argv)

    _rotate_cron_log(args.max_cron_log_mb)

    try:
        get_credentials()
    except Exception as e:
        print(f"auth error: {e}", file=sys.stderr)
        return 2

    people = build("people", "v1", credentials=get_credentials(), cache_discovery=False)

    refreshed = failed = skipped = would = 0
    page_token = None
    total = 0

    while True:
        kwargs = {
            "resourceName": "people/me",
            "personFields": "emailAddresses,metadata",
            "pageSize": 1000,
        }
        if page_token:
            kwargs["pageToken"] = page_token
        resp = people.people().connections().list(**kwargs).execute()
        batch = resp.get("connections", []) or []
        total += len(batch)

        for p in batch:
            resource_name = p.get("resourceName")
            addrs = p.get("emailAddresses") or []
            email = addrs[0].get("value") if addrs else None
            if not email:
                skipped += 1
                continue
            if args.dry_run:
                would += 1
                continue
            try:
                crm_stats.apply_stats_to_contact(resource_name, email)
                refreshed += 1
            except Exception as e:
                log.error("refresh_stats: %s failed: %s", resource_name, e)
                failed += 1

        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    if args.dry_run:
        msg = (
            f"refresh_stats DRY-RUN: total={total} would_refresh={would} "
            f"skipped_no_email={skipped}"
        )
    else:
        msg = (
            f"refresh_stats done: total={total} refreshed={refreshed} "
            f"failed={failed} skipped_no_email={skipped}"
        )
    log.info(msg)
    print(msg)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
