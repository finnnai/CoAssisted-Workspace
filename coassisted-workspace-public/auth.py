# © 2026 CoAssisted Workspace contributors. Licensed under MIT — see LICENSE.
"""OAuth 2.0 auth for Google Workspace APIs.

Handles the initial consent flow, caches the token to disk, refreshes it on
expiry, and hands back a credentials object that any Google API client can use.

The first time this runs, a browser window opens for user consent. After that,
it reads and refreshes the cached token silently.
"""

from __future__ import annotations

import os
from pathlib import Path
from threading import Lock

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

# Combined scopes for Gmail, Calendar, Drive, Sheets, Docs.
# Requesting them all up front means the user consents once and we can use any
# service afterwards without re-prompting.
SCOPES: list[str] = [
    # Gmail — full mailbox access (send, read, modify labels, filters, etc.)
    "https://mail.google.com/",
    "https://www.googleapis.com/auth/gmail.settings.basic",   # filters, aliases
    # Calendar — read/write events
    "https://www.googleapis.com/auth/calendar",
    # Drive — full file access (scoped to what this app creates/opens by default
    # via the API, but broader scope simplifies search/read workflows)
    "https://www.googleapis.com/auth/drive",
    # Sheets & Docs — included via Drive scope, but listed explicitly for clarity
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/documents",
    # Tasks — read/write task lists and items
    "https://www.googleapis.com/auth/tasks",
    # Contacts (People API) — full read/write + "other contacts" (auto-saved emails)
    "https://www.googleapis.com/auth/contacts",
    "https://www.googleapis.com/auth/contacts.other.readonly",
    # Google Chat — list spaces, read/write messages, list memberships
    "https://www.googleapis.com/auth/chat.spaces",
    "https://www.googleapis.com/auth/chat.messages",
    "https://www.googleapis.com/auth/chat.memberships.readonly",
    # Google Cloud (Route Optimization API). This scope is required for
    # workflow_route_optimize_advanced. Adding it here means a one-time
    # re-consent after upgrade — token.json gets invalidated and the user
    # re-runs the OAuth flow once. No effect on Workspace tools.
    "https://www.googleapis.com/auth/cloud-platform",
]

_PROJECT_DIR = Path(__file__).resolve().parent
_CREDENTIALS_PATH = _PROJECT_DIR / "credentials.json"
_TOKEN_PATH = _PROJECT_DIR / "token.json"

# Cache the creds so we don't re-read/refresh on every tool call.
_creds_cache: Credentials | None = None
_cache_lock = Lock()


class AuthError(RuntimeError):
    """Raised when OAuth setup is missing or invalid."""


def get_credentials() -> Credentials:
    """Return valid Google OAuth credentials, refreshing or re-prompting as needed.

    Raises:
        AuthError: If credentials.json is missing or the consent flow fails.
    """
    global _creds_cache

    with _cache_lock:
        creds = _creds_cache

        # Load from disk on first call.
        if creds is None and _TOKEN_PATH.exists():
            creds = Credentials.from_authorized_user_file(str(_TOKEN_PATH), SCOPES)

        # Refresh if expired but we have a refresh token.
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                raise AuthError(
                    f"Failed to refresh OAuth token: {e}. "
                    f"Delete {_TOKEN_PATH} and try again to re-authenticate."
                ) from e

        # No valid creds — run the interactive consent flow.
        if not creds or not creds.valid:
            if not _CREDENTIALS_PATH.exists():
                raise AuthError(
                    f"Missing {_CREDENTIALS_PATH}. "
                    f"See GCP_SETUP.md for how to generate it."
                )
            flow = InstalledAppFlow.from_client_secrets_file(
                str(_CREDENTIALS_PATH), SCOPES
            )
            # port=0 => pick any free port. Opens a browser window.
            creds = flow.run_local_server(port=0)

            # Persist the new token.
            _TOKEN_PATH.write_text(creds.to_json())
            # Only owner can read/write.
            os.chmod(_TOKEN_PATH, 0o600)

        _creds_cache = creds
        return creds


def clear_cached_credentials() -> None:
    """Drop the in-memory cached creds (does not delete token.json)."""
    global _creds_cache
    with _cache_lock:
        _creds_cache = None
