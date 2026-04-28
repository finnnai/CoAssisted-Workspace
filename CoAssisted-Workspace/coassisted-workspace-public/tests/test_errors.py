# © 2026 CoAssisted Workspace contributors. Licensed under MIT — see LICENSE use only.
"""Tests for the shared error-formatting helper.

format_error converts any exception that bubbles out of a tool into an
actionable string. We care that the right HTTP-status branches fire and that
the AuthError type is recognized — these messages are what end users see.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from googleapiclient.errors import HttpError

import errors
from auth import AuthError


def _http_error(status: int, reason: str = "") -> HttpError:
    """Build a minimal HttpError with a controllable status + reason."""
    resp = MagicMock()
    resp.status = status
    resp.reason = reason
    err = HttpError(resp, b"")
    err.reason = reason
    return err


def test_auth_error_uses_auth_error_prefix():
    out = errors.format_error(AuthError("token expired and refresh failed"))
    assert out.startswith("Auth error:")
    assert "token expired" in out


def test_401_suggests_token_delete():
    out = errors.format_error(_http_error(401, "Invalid Credentials"))
    assert "401" in out
    assert "Delete token.json" in out


def test_403_includes_reason_and_actionable_hint():
    out = errors.format_error(_http_error(403, "insufficientPermissions"))
    assert "403" in out
    assert "insufficientPermissions" in out
    assert "API is enabled" in out or "access" in out


def test_404_includes_reason():
    out = errors.format_error(_http_error(404, "fileNotFound"))
    assert "404" in out
    assert "fileNotFound" in out


def test_429_says_rate_limit():
    out = errors.format_error(_http_error(429))
    assert "429" in out
    assert "Rate limit" in out


def test_other_http_status_falls_through():
    out = errors.format_error(_http_error(500, "internalError"))
    assert "500" in out
    # Should still include the reason for context
    assert "internalError" in out or "internal" in out.lower()


def test_unknown_exception_includes_type_name():
    """Non-HTTP, non-Auth exceptions should still produce a useful message."""
    out = errors.format_error(ValueError("bad input"))
    assert "Unexpected error" in out
    assert "ValueError" in out
    assert "bad input" in out


def test_unknown_exception_with_keyerror():
    out = errors.format_error(KeyError("missing_field"))
    assert "KeyError" in out
