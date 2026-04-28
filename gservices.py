# © 2026 CoAssisted Workspace. Licensed for non-redistribution use only.
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
"""

from __future__ import annotations

import functools

from googleapiclient.discovery import build

from auth import get_credentials


@functools.lru_cache(maxsize=None)
def _build(service: str, version: str):
    return build(service, version, credentials=get_credentials(), cache_discovery=False)


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
