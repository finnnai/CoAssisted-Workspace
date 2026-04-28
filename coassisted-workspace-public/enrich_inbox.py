#!/usr/bin/env python3
"""Standalone script: sweep recent inbound mail and enrich saved contacts.

Parses email signatures from messages received in the last --days (default 1),
finds every sender that matches a saved contact, and updates their title,
phone (E.164), website, organization, and social URLs in place.

Designed for a daily cron:
    5 7 * * *  /path/to/.venv/bin/python /path/to/enrich_inbox.py

Prints a one-line summary to stdout and detailed logs to the usual logs dir.
Exits non-zero on hard auth/config errors so cron can detect failure.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from auth import get_credentials
from logging_util import log
from tools.enrichment import (
    _enrich_one,
    _extract_plaintext_body,  # noqa: F401 — re-exported for logging consistency
    _gmail,
    _list_all_saved_contacts_by_email,
    _parse_sender,
)


_PROJECT_DIR = Path(__file__).resolve().parent
_LOG_DIR = _PROJECT_DIR / "logs"


def _rotate_cron_log(max_mb: int = 10) -> None:
    """Same convention as refresh_stats — rotate logs/<script>.cron.log when oversize."""
    log_path = _LOG_DIR / f"{Path(__file__).stem}.cron.log"
    if not log_path.exists():
        return
    if log_path.stat().st_size > max_mb * 1024 * 1024:
        backup = log_path.with_suffix(".cron.log.old")
        try:
            if backup.exists():
                backup.unlink()
            log_path.rename(backup)
            log.info("enrich_inbox: rotated cron log (was > %d MB)", max_mb)
        except Exception as e:
            log.warning("enrich_inbox: cron log rotation failed: %s", e)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Enrich saved contacts from recent inbound mail signatures."
    )
    parser.add_argument(
        "--days",
        type=int,
        default=1,
        help="How many days back to scan the inbox. Default 1 (daily-cron friendly).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=500,
        help="Max messages to inspect. Default 500.",
    )
    parser.add_argument(
        "--no-overwrite",
        action="store_true",
        help="Only fill blanks instead of overwriting existing fields.",
    )
    parser.add_argument(
        "--aggressive-titles",
        action="store_true",
        help="Use aggressive title extraction (catches more, false-positive risk).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print the per-contact results as JSON in addition to the summary line.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview which contacts would be enriched without writing any updates.",
    )
    parser.add_argument(
        "--max-cron-log-mb",
        type=int,
        default=10,
        help="Rotate logs/<script>.cron.log when it exceeds this size (default 10).",
    )
    args = parser.parse_args(argv)

    _rotate_cron_log(args.max_cron_log_mb)

    # Confirm auth works before doing any real work.
    try:
        get_credentials()
    except Exception as e:
        print(f"auth error: {e}", file=sys.stderr)
        return 2

    gmail = _gmail()

    # 0. Preload saved contacts (one paginated sweep).
    try:
        preloaded = _list_all_saved_contacts_by_email()
    except Exception as e:
        print(f"contacts preload failed: {e}", file=sys.stderr)
        return 3
    log.info("enrich_inbox: preloaded %d saved contacts", len(preloaded))

    # 1. List inbound message IDs in the window.
    query = f"in:inbox newer_than:{args.days}d"
    message_ids: list[str] = []
    page_token = None
    while len(message_ids) < args.limit:
        kwargs: dict = {
            "userId": "me",
            "q": query,
            "maxResults": min(500, args.limit - len(message_ids)),
        }
        if page_token:
            kwargs["pageToken"] = page_token
        resp = gmail.users().messages().list(**kwargs).execute()
        batch = resp.get("messages", []) or []
        message_ids.extend(m["id"] for m in batch)
        page_token = resp.get("nextPageToken")
        if not page_token or not batch:
            break

    # 2. Fetch each message in full; keep newest per sender.
    newest_by_sender: dict[str, dict] = {}
    for mid in message_ids:
        try:
            msg = gmail.users().messages().get(userId="me", id=mid, format="full").execute()
        except Exception as e:
            log.warning("enrich_inbox: fetch %s failed: %s", mid, e)
            continue
        headers = {
            h.get("name", "").lower(): h.get("value", "")
            for h in (msg.get("payload", {}) or {}).get("headers", []) or []
        }
        sender = _parse_sender(headers.get("from", ""))
        if not sender:
            continue
        ts = int(msg.get("internalDate", "0"))
        existing = newest_by_sender.get(sender.lower())
        if not existing or int(existing.get("internalDate", "0")) < ts:
            newest_by_sender[sender.lower()] = msg

    # 3. Enrich every matching saved contact.
    updated = no_change = skipped = failed = 0
    results: list[dict] = []
    for sender_lower, msg in newest_by_sender.items():
        headers = {
            h.get("name", "").lower(): h.get("value", "")
            for h in (msg.get("payload", {}) or {}).get("headers", []) or []
        }
        sender = _parse_sender(headers.get("from", "")) or sender_lower
        try:
            r = _enrich_one(
                email=sender,
                days=args.days,
                overwrite=not args.no_overwrite,
                conservative_titles=not args.aggressive_titles,
                dry_run=args.dry_run,
                preloaded_message=msg,
                preloaded_contacts=preloaded,
            )
        except Exception as e:
            log.error("enrich_inbox: %s failed: %s", sender, e)
            r = {"email": sender, "status": "failed", "error": str(e)}
        status = r.get("status", "")
        if status == "updated":
            updated += 1
        elif status == "no_changes_needed":
            no_change += 1
        elif status == "skipped_no_saved_contact":
            skipped += 1
        elif status in ("failed",):
            failed += 1
        results.append(r)

    summary = (
        f"enrich_inbox done: scanned={len(message_ids)} senders={len(newest_by_sender)} "
        f"updated={updated} no_change={no_change} skipped={skipped} failed={failed}"
    )
    log.info(summary)
    print(summary)
    if args.verbose:
        print(json.dumps(results, indent=2))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
