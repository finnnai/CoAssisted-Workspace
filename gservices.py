# © 2026 CoAssisted Workspace. Licensed under MIT.
# See LICENSE file for terms. Removing or altering this header is prohibited.
"""Cached builders for Google API service objects.

Every tool used to call `build("gmail", "v1", credentials=get_credentials(), ...)`
on every invocation. That rebuilds the discovery document each time — slow, and
wasteful when the creds object is the same.

This module caches one service per (service, version) pair. The underlying
`google.oauth2.credentials.Credentials` object refreshes itself transparently,
so a cached service stays valid across token refresh.

If you ever need to force-rebuild (e.g. after `clear_cached_credentials()`),
call `reset_services()`.

Every service request is wrapped with a 429/5xx retry layer (Finnn 2026-05-03)
so a single per-minute quota burst doesn't take down the whole workflow.
"""

from __future__ import annotations

import functools
import logging
import random
import time

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import HttpRequest

import config
from auth import get_credentials

_log = logging.getLogger(__name__)


# Statuses we should retry transparently. 429 = rate limit, 5xx = server side.
_RETRYABLE_STATUSES = frozenset({408, 429, 500, 502, 503, 504})


class _RetryingHttpRequest(HttpRequest):
    """HttpRequest that retries 429 + 5xx with exponential backoff + jitter.

    Wired into every service object via `requestBuilder=` on build(). All
    `.execute()` calls in the codebase now flow through this — Sheets,
    Gmail, Drive, Calendar, Docs, Tasks, People, Chat. Existing per-callsite
    code keeps working unchanged; the retries are invisible until they fire.

    Backoff config comes from `config.retry`:
        max_attempts             — total tries including the first
        initial_backoff_seconds  — base for exponential growth
        max_backoff_seconds      — hard cap on a single sleep

    Honors the server's `Retry-After` header when present (Sheets sometimes
    sends it on 429), preferring it over the computed backoff.
    """

    def execute(self, http=None, num_retries=0):  # noqa: ANN001
        # Read retry config inside execute so live config edits take effect
        # without rebuilding cached services.
        cfg = config.retry_settings()
        max_attempts = int(cfg.get("max_attempts", 4))
        initial = float(cfg.get("initial_backoff_seconds", 1.0))
        max_backoff = float(cfg.get("max_backoff_seconds", 30.0))

        last_err: Exception | None = None
        for attempt in range(max_attempts):
            try:
                return super().execute(http=http, num_retries=num_retries)
            except HttpError as e:
                status = getattr(getattr(e, "resp", None), "status", None)
                if status not in _RETRYABLE_STATUSES:
                    raise
                if attempt + 1 >= max_attempts:
                    raise

                # Prefer server-supplied Retry-After if present.
                retry_after = None
                try:
                    retry_after = float(e.resp.get("retry-after", "")) if e.resp else None
                except (ValueError, TypeError):
                    retry_after = None

                if retry_after is not None and retry_after > 0:
                    sleep_for = min(max_backoff, retry_after)
                else:
                    # Exponential backoff with jitter.
                    base = min(max_backoff, initial * (2 ** attempt))
                    sleep_for = base * (0.5 + random.random())

                _log.warning(
                    "Google API %s returned %s; retry %d/%d in %.2fs",
                    self.uri.split("?", 1)[0] if hasattr(self, "uri") else "?",
                    status, attempt + 1, max_attempts, sleep_for,
                )
                time.sleep(sleep_for)
                last_err = e
                continue
        # Should be unreachable, but appease the type checker.
        if last_err:
            raise last_err
        return None


@functools.lru_cache(maxsize=None)
def _build(service: str, version: str):
    return build(
        service, version,
        credentials=get_credentials(),
        cache_discovery=False,
        requestBuilder=_RetryingHttpRequest,
    )


def gmail():
    return _build("gmail", "v1")


def calendar():
    return _build("calendar", "v3")


def drive():
    return _build("drive", "v3")


def sheets():
    return _build("sheets", "v4")


def docs():
    return _build("docs", "v1")


def tasks():
    return _build("tasks", "v1")


def people():
    return _build("people", "v1")


def chat():
    return _build("chat", "v1")


_maps_client = None


def maps():
    """Return a cached googlemaps.Client.

    Maps APIs use an API key, not OAuth. The key is read from:
        1. config.google_maps_api_key (preferred)
        2. GOOGLE_MAPS_API_KEY env var (fallback)

    Raises RuntimeError if no key is configured. Each tool that uses this
    should `try: maps()` and return a clear error message if it raises.
    """
    global _maps_client
    if _maps_client is not None:
        return _maps_client

    import os
    import config as _config

    key = (
        _config.get("google_maps_api_key")
        or os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()
        or None
    )
    if not key:
        raise RuntimeError(
            "Google Maps API key not configured. Set `google_maps_api_key` in "
            "config.json or export GOOGLE_MAPS_API_KEY in your env. See "
            "GCP_SETUP.md for setup steps."
        )
    try:
        import googlemaps
    except ImportError as e:
        raise RuntimeError(
            f"googlemaps SDK not installed in this venv: {e}. "
            "Run: .venv/bin/pip install googlemaps"
        )
    _maps_client = googlemaps.Client(key=key)
    return _maps_client


def reset_services() -> None:
    """Drop the cache. Use after re-authentication."""
    global _maps_client
    _build.cache_clear()
    _maps_client = None


# ---------------------------------------------------------------------------
# Sheets helpers — quota-aware multi-tab batch write.
# Use this for any "refresh N tabs in one go" pattern. Replaces the
# per-tab .clear() / .update() loop that hit the 60-writes/min/user
# quota in v0.8.5 (caught by Finnn 2026-05-03).
# ---------------------------------------------------------------------------

def sheets_batch_write(
    spreadsheet_id: str,
    payload: dict[str, list[list]],
    *,
    clear_first: bool = True,
    value_input_option: str = "USER_ENTERED",
) -> int:
    """Write a `{tab_name: rows}` payload to a Sheet in 1-2 API calls.

    - One `values().batchClear` covering every tab in the payload (when
      clear_first=True, default).
    - One `values().batchUpdate` writing each tab's rows starting at A1.

    Returns the total number of cells updated. Skips tabs whose value
    list is empty.

    Caller is responsible for ensuring the tabs exist (via
    spreadsheets().batchUpdate with addSheet requests, or
    spreadsheets().values().get / .create equivalents). This helper
    only handles the bulk write half — it doesn't create or delete tabs.

    All calls go through the auto-retrying _RetryingHttpRequest path so
    transient 429s become retries instead of failures.
    """
    svc = sheets()
    tab_names = list(payload.keys())

    if clear_first and tab_names:
        svc.spreadsheets().values().batchClear(
            spreadsheetId=spreadsheet_id,
            body={"ranges": [f"'{t}'" for t in tab_names]},
        ).execute()

    data = [
        {"range": f"'{tab}'!A1", "values": rows}
        for tab, rows in payload.items() if rows
    ]
    if not data:
        return 0

    resp = svc.spreadsheets().values().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"valueInputOption": value_input_option, "data": data},
    ).execute()
    return resp.get("totalUpdatedCells", 0)
