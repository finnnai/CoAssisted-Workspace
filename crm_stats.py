"""Managed CRM fields computed from Gmail activity.

Every contact with an email address carries three auto-maintained keys in its
People API `userDefined` list:

    "Last Interaction"    e.g. "Sent - 2026-04-23 - 14:32"
    "Sent, last N"        e.g. "+7"       (N = config.crm_window_days)
    "Received, last N"    e.g. "+12"

The tally labels are dynamic — if you change `crm_window_days` from 60 to 30,
the labels become "Sent, last 30" / "Received, last 30" and any stale
"Sent, last 60" / "Received, last 60" entries are removed on next refresh.

These fields are:
    - Written automatically when a contact is created or updated via the MCP.
    - Refreshable on demand via contacts_refresh_crm_stats /
      contacts_refresh_all_crm_stats.
    - Protected against manual writes by contacts_set_custom_field (which
      rejects any of the managed keys).

The format is intentionally text-only so it renders identically in the Google
Contacts UI and in any mail-merge template that references them.
"""

from __future__ import annotations

import datetime as _dt
import re
from typing import Any, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from googleapiclient.errors import HttpError

import config
import gservices
from logging_util import log
from retry import retry_call

# Fixed managed key (not dependent on window size).
LAST_INTERACTION_KEY = "Last Interaction"

# Pattern matchers for the windowed tally keys — match ANY "Sent, last <int>" so
# switching windows cleans up stale entries.
_SENT_TALLY_RE = re.compile(r"^Sent, last \d+$")
_RECEIVED_TALLY_RE = re.compile(r"^Received, last \d+$")


def _window_days() -> int:
    """Read the configured window; clamp to sane bounds."""
    raw = config.get("crm_window_days", 60)
    try:
        n = int(raw)
    except (TypeError, ValueError):
        log.warning("crm_window_days=%r is not an integer; defaulting to 60", raw)
        return 60
    if n < 1:
        log.warning("crm_window_days=%d is too small; clamping to 1", n)
        return 1
    if n > 3650:
        log.warning("crm_window_days=%d exceeds 3650; clamping to 3650", n)
        return 3650
    return n


def current_managed_keys() -> tuple[str, str, str]:
    """Return the three canonical managed keys for the *current* window."""
    n = _window_days()
    return (LAST_INTERACTION_KEY, f"Sent, last {n}", f"Received, last {n}")


def is_managed_key(key: str) -> bool:
    """True if `key` is any managed key — either Last Interaction or a Sent/Received tally
    for any window size (so stale-window keys are still recognized as managed)."""
    if key == LAST_INTERACTION_KEY:
        return True
    return bool(_SENT_TALLY_RE.match(key) or _RECEIVED_TALLY_RE.match(key))


# Backward-compatible alias so older imports keep working. Iteration-friendly:
# `for k in MANAGED_KEYS` yields the three keys for the *current* window, but
# membership tests should use `is_managed_key()`.
class _ManagedKeys(tuple):
    def __contains__(self, key) -> bool:  # type: ignore[override]
        return is_managed_key(str(key))


MANAGED_KEYS = _ManagedKeys(("Last Interaction", "Sent, last 60", "Received, last 60"))


def _gmail():
    return gservices.gmail()


def _people():
    return gservices.people()


def _tz() -> ZoneInfo | None:
    """Preferred timezone for rendering timestamps."""
    name = config.get("default_timezone")
    if not name:
        return None
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        log.warning("Unknown default_timezone %r; falling back to system local", name)
        return None


def _format_ts(epoch_ms: int) -> str:
    """Render a Gmail internalDate (ms since epoch) in 'YYYY-MM-DD - HH:MM' 24h format."""
    tz = _tz()
    if tz is not None:
        dt = _dt.datetime.fromtimestamp(epoch_ms / 1000, tz=tz)
    else:
        dt = _dt.datetime.fromtimestamp(epoch_ms / 1000).astimezone()
    return dt.strftime("%Y-%m-%d - %H:%M")


def _count_messages(query: str) -> int:
    """Count messages matching a Gmail query, paginating as needed. Retries on 429/5xx."""
    svc = _gmail()
    total = 0
    page_token: Optional[str] = None
    while True:
        kwargs: dict[str, Any] = {"userId": "me", "q": query, "maxResults": 500}
        if page_token:
            kwargs["pageToken"] = page_token
        resp = retry_call(lambda: svc.users().messages().list(**kwargs).execute())
        total += len(resp.get("messages", []) or [])
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return total


def _latest_interaction(email: str) -> tuple[str, int] | None:
    """Return (direction, epoch_ms) of the most recent message to/from `email`, or None."""
    svc = _gmail()
    resp = retry_call(
        lambda: svc.users()
        .messages()
        .list(userId="me", q=f"(from:{email} OR to:{email})", maxResults=1)
        .execute()
    )
    ids = [m["id"] for m in resp.get("messages", []) or []]
    if not ids:
        return None
    m = retry_call(
        lambda: svc.users()
        .messages()
        .get(userId="me", id=ids[0], format="metadata", metadataHeaders=["Date"])
        .execute()
    )
    direction = "Sent" if "SENT" in (m.get("labelIds") or []) else "Received"
    return direction, int(m["internalDate"])


def compute_stats(email: str) -> dict[str, str]:
    """Compute the three managed field values for one email address.

    Always returns all three keys, labeled for the *current* crm_window_days.
    """
    last_key, sent_key, received_key = current_managed_keys()
    window = _window_days()

    email = email.strip()
    if not email:
        return {
            last_key: "None",
            sent_key: "+0",
            received_key: "+0",
        }

    # Counts.
    try:
        sent = _count_messages(f"in:sent to:{email} newer_than:{window}d")
    except HttpError as e:
        log.warning("sent count failed for %s: %s", email, e)
        sent = 0
    try:
        received = _count_messages(f"from:{email} newer_than:{window}d")
    except HttpError as e:
        log.warning("received count failed for %s: %s", email, e)
        received = 0

    # Last interaction.
    try:
        latest = _latest_interaction(email)
    except HttpError as e:
        log.warning("last interaction lookup failed for %s: %s", email, e)
        latest = None
    if latest is None:
        last_str = "None"
    else:
        direction, epoch_ms = latest
        last_str = f"{direction} - {_format_ts(epoch_ms)}"

    return {
        last_key: last_str,
        sent_key: f"+{sent}",
        received_key: f"+{received}",
    }


def merge_managed_into_userdefined(
    existing: list[dict], stats: dict[str, str]
) -> list[dict]:
    """Return a new userDefined list: existing minus ALL managed keys (incl. stale window
    sizes), then current managed keys re-added.

    Non-managed entries are preserved. Order: non-managed first, then managed in
    the canonical order (Last Interaction, Sent, Received).
    """
    preserved = [u for u in (existing or []) if not is_managed_key(u.get("key", ""))]
    ordered_keys = current_managed_keys()
    managed = [{"key": k, "value": stats[k]} for k in ordered_keys if k in stats]
    return preserved + managed


def compute_stats_batch(emails: list[str]) -> dict[str, dict[str, str]]:
    """Compute stats for many emails with Gmail's batch HTTP API.

    Issues 3 queries per email (sent count, received count, latest message) in
    a single batched round-trip of up to 100 sub-requests. Returns a dict keyed
    by email with the same shape as compute_stats for each.

    Sub-request failures produce safe defaults (+0, "None") for that email rather
    than failing the whole batch.
    """
    if not emails:
        return {}

    svc = _gmail()
    window = _window_days()
    last_key, sent_key, received_key = current_managed_keys()

    # Per-email accumulators.
    results: dict[str, dict] = {
        e: {"sent": 0, "received": 0, "latest_id": None, "latest_internal_date": 0,
            "latest_label_ids": None}
        for e in emails
    }

    # Batch 1: counts (sent + received), in chunks of ≤100 sub-requests (Gmail's limit).
    CHUNK = 50  # 2 queries per email, so 50 emails = 100 sub-requests
    for i in range(0, len(emails), CHUNK):
        chunk_emails = emails[i : i + CHUNK]
        batch = svc.new_batch_http_request()

        def make_sent_cb(email_addr: str):
            def cb(_id, response, exception):
                if exception:
                    log.warning("sent count sub-req failed for %s: %s", email_addr, exception)
                    return
                # Count only the messages we see in this page — we don't paginate
                # inside a batch. For 60d windows this is almost never >500.
                results[email_addr]["sent"] = len((response or {}).get("messages", []) or [])
            return cb

        def make_received_cb(email_addr: str):
            def cb(_id, response, exception):
                if exception:
                    log.warning("received count sub-req failed for %s: %s", email_addr, exception)
                    return
                results[email_addr]["received"] = len((response or {}).get("messages", []) or [])
            return cb

        for e in chunk_emails:
            batch.add(
                svc.users().messages().list(
                    userId="me", q=f"in:sent to:{e} newer_than:{window}d", maxResults=500
                ),
                callback=make_sent_cb(e),
            )
            batch.add(
                svc.users().messages().list(
                    userId="me", q=f"from:{e} newer_than:{window}d", maxResults=500
                ),
                callback=make_received_cb(e),
            )

        try:
            retry_call(lambda: batch.execute())
        except Exception as e:
            log.warning("batch counts failed: %s — falling back per-email", e)
            for em in chunk_emails:
                try:
                    results[em]["sent"] = _count_messages(f"in:sent to:{em} newer_than:{window}d")
                    results[em]["received"] = _count_messages(f"from:{em} newer_than:{window}d")
                except Exception:
                    pass

    # Batch 2: latest message lookup per email. Two phases: list (returns IDs) then get.
    # Simpler to just do sequential retry_call for this — one call per email, cheap.
    for e in emails:
        try:
            latest = _latest_interaction(e)
            if latest:
                direction, epoch_ms = latest
                results[e]["latest_direction"] = direction
                results[e]["latest_internal_date"] = epoch_ms
            else:
                results[e]["latest_direction"] = None
        except Exception as inner:
            log.warning("latest lookup failed for %s: %s", e, inner)
            results[e]["latest_direction"] = None

    # Assemble response.
    out: dict[str, dict[str, str]] = {}
    for e in emails:
        r = results[e]
        if r.get("latest_direction"):
            last_str = f"{r['latest_direction']} - {_format_ts(r['latest_internal_date'])}"
        else:
            last_str = "None"
        out[e] = {
            last_key: last_str,
            sent_key: f"+{r['sent']}",
            received_key: f"+{r['received']}",
        }
    return out


def apply_stats_batch(contacts: list[tuple[str, str]]) -> dict[str, dict]:
    """Apply stats to many contacts efficiently. `contacts` is list of (resource_name, email) tuples.

    Uses compute_stats_batch for the Gmail side, then updates each contact one at
    a time (People API doesn't offer a bulk updateContact). Returns {resource_name: stats}.
    """
    if not contacts:
        return {}
    emails = [c[1] for c in contacts if c[1]]
    stats_by_email = compute_stats_batch(emails)

    out: dict[str, dict] = {}
    svc = _people()
    for resource_name, email in contacts:
        stats = stats_by_email.get(email) if email else {
            LAST_INTERACTION_KEY: "None",
            **{k: "+0" for k in current_managed_keys()[1:]},
        }
        if stats is None:
            stats = compute_stats(email or "")
        try:
            existing = retry_call(
                lambda: svc.people()
                .get(resourceName=resource_name, personFields="userDefined,metadata")
                .execute()
            )
            merged = merge_managed_into_userdefined(existing.get("userDefined", []) or [], stats)
            body = {"etag": existing["etag"], "userDefined": merged}
            retry_call(
                lambda: svc.people()
                .updateContact(
                    resourceName=resource_name,
                    updatePersonFields="userDefined",
                    body=body,
                )
                .execute()
            )
            out[resource_name] = {"status": "refreshed", "email": email, **stats}
        except Exception as inner:
            log.error("apply_stats_batch: %s failed: %s", resource_name, inner)
            out[resource_name] = {"status": "failed", "email": email, "error": str(inner)}
    return out


def apply_stats_to_contact(resource_name: str, email: Optional[str]) -> dict:
    """Compute stats for `email` and write them into the contact's userDefined.

    If `email` is falsy, writes the empty/default stat values (so the three keys
    are always present on every contact we touch).

    Returns the updated contact's flattened form.
    """
    stats = compute_stats(email or "")
    svc = _people()
    existing = retry_call(
        lambda: svc.people()
        .get(resourceName=resource_name, personFields="userDefined,metadata")
        .execute()
    )
    merged = merge_managed_into_userdefined(existing.get("userDefined", []) or [], stats)
    body = {"etag": existing["etag"], "userDefined": merged}
    updated = retry_call(
        lambda: svc.people()
        .updateContact(
            resourceName=resource_name,
            updatePersonFields="userDefined",
            body=body,
        )
        .execute()
    )
    last_key, sent_key, received_key = current_managed_keys()
    log.info(
        "crm_stats applied to %s (email=%s): last=%s sent=%s received=%s",
        resource_name,
        email,
        stats.get(last_key),
        stats.get(sent_key),
        stats.get(received_key),
    )
    return updated


def strip_managed_from_userdefined(userdefined: list[dict] | None) -> list[dict]:
    """Remove any managed keys (including stale-window variants) from a user-supplied list.

    Use in tools that accept custom_fields from the caller — we never let the
    caller write managed keys directly.
    """
    return [u for u in (userdefined or []) if not is_managed_key(u.get("key", ""))]
