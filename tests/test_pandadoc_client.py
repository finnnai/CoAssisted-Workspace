# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Unit tests for pandadoc_client.py.

No live PandaDoc traffic. urllib is monkeypatched module-wide so
every code path can be exercised offline. These tests cover:

  - Auth precedence: api_key wins over OAuth trio.
  - Auth fallback: OAuth refresh fires when api_key is absent.
  - Auth misconfig: clean PandaDocAuthError.
  - is_configured(): three states.
  - call(): unknown operation_id raises UnknownOperationError.
  - call(): path_param substitution.
  - call(): retry on 429/5xx with exponential backoff.
  - call(): non-retryable 4xx raises PandaDocAPIError.
  - call(): poll loop on 202 + Location.
  - Multipart body building.
"""

from __future__ import annotations

import io
import json
import sys
import types
from typing import Any

import pytest

import config
import pandadoc_client


# -----------------------------------------------------------------------------
# Fakes
# -----------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, status: int, body: bytes, headers: dict | None = None):
        self.status = status
        self._body = body
        self.headers = headers or {"Content-Type": "application/json"}

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeHTTPError(Exception):
    """Stand-in for urllib.error.HTTPError that matches its interface."""
    def __init__(self, code: int, body: bytes, headers: dict | None = None):
        self.code = code
        self._body = body
        self.headers = headers or {"Content-Type": "application/json"}
        super().__init__(f"HTTP {code}")

    def read(self) -> bytes:
        return self._body


@pytest.fixture
def fake_pandadoc(monkeypatch):
    """Patch urllib.request.urlopen + the HTTPError class for this test.

    Returns a list-of-call-records the test can mutate via .returns
    (push response objects in order). Each urlopen pulls one off.
    """
    calls: list[dict] = []
    returns: list[Any] = []

    import urllib.error
    import urllib.request

    # Patch HTTPError so our raised _FakeHTTPError is recognized by
    # pandadoc_client's `except urllib.error.HTTPError` clauses.
    monkeypatch.setattr(urllib.error, "HTTPError", _FakeHTTPError)

    def _fake_urlopen(req, timeout=None):
        record = {
            "url": req.full_url,
            "method": req.get_method(),
            "headers": dict(req.headers),
            "data": req.data,
        }
        calls.append(record)
        if not returns:
            raise AssertionError(
                f"urlopen called but no canned response queued: {record!r}"
            )
        nxt = returns.pop(0)
        if isinstance(nxt, BaseException):
            raise nxt
        return nxt

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

    class Holder:
        pass

    holder = Holder()
    holder.calls = calls
    holder.returns = returns

    # Reset config cache between tests.
    config.reload()
    yield holder
    config.reload()


def _set_pandadoc_config(monkeypatch, **kw):
    """Stub config.get('pandadoc', ...) to return the supplied dict."""
    block = kw or {}
    real_get = config.get

    def _get(key, default=None):
        if key == "pandadoc":
            return block
        return real_get(key, default)

    monkeypatch.setattr(config, "get", _get)
    config.reload()


# -----------------------------------------------------------------------------
# is_configured
# -----------------------------------------------------------------------------

def test_is_configured_with_api_key(monkeypatch):
    _set_pandadoc_config(monkeypatch, api_key="abc123")
    ok, reason = pandadoc_client.is_configured()
    assert ok is True
    assert reason == ""


def test_is_configured_with_oauth_trio(monkeypatch):
    _set_pandadoc_config(
        monkeypatch,
        oauth_client_id="cid",
        oauth_client_secret="sec",
        oauth_refresh_token="rt",
    )
    ok, reason = pandadoc_client.is_configured()
    assert ok is True
    assert reason == ""


def test_is_configured_misconfig_returns_reason(monkeypatch):
    _set_pandadoc_config(monkeypatch)
    ok, reason = pandadoc_client.is_configured()
    assert ok is False
    assert "config.pandadoc.api_key" in reason


# -----------------------------------------------------------------------------
# Auth precedence
# -----------------------------------------------------------------------------

def test_auth_header_api_key_wins_when_both_set(monkeypatch):
    """api_key beats OAuth trio when both configured."""
    _set_pandadoc_config(
        monkeypatch,
        api_key="my-key",
        oauth_client_id="cid", oauth_client_secret="sec", oauth_refresh_token="rt",
    )
    h = pandadoc_client._auth_header()
    assert h == "API-Key my-key"


def test_auth_header_api_key_already_prefixed(monkeypatch):
    """If api_key starts with 'API-Key ', don't double-prefix."""
    _set_pandadoc_config(monkeypatch, api_key="API-Key existing")
    h = pandadoc_client._auth_header()
    assert h == "API-Key existing"


def test_auth_header_misconfig_raises(monkeypatch):
    _set_pandadoc_config(monkeypatch)
    with pytest.raises(pandadoc_client.PandaDocAuthError):
        pandadoc_client._auth_header()


def test_auth_header_oauth_fallback_refreshes_token(monkeypatch, fake_pandadoc):
    _set_pandadoc_config(
        monkeypatch,
        oauth_client_id="cid", oauth_client_secret="sec", oauth_refresh_token="rt",
    )
    fake_pandadoc.returns.append(
        _FakeResponse(200, json.dumps({"access_token": "fresh-token"}).encode("utf-8"))
    )
    h = pandadoc_client._auth_header()
    assert h == "Bearer fresh-token"
    # The OAuth call hit the access_token endpoint with refresh-token grant.
    assert fake_pandadoc.calls[0]["url"].endswith("/oauth2/access_token")
    assert b"grant_type=refresh_token" in fake_pandadoc.calls[0]["data"]


def test_auth_header_oauth_no_access_token_raises(monkeypatch, fake_pandadoc):
    _set_pandadoc_config(
        monkeypatch,
        oauth_client_id="cid", oauth_client_secret="sec", oauth_refresh_token="rt",
    )
    fake_pandadoc.returns.append(
        _FakeResponse(200, json.dumps({"foo": "bar"}).encode("utf-8"))
    )
    with pytest.raises(pandadoc_client.PandaDocAuthError):
        pandadoc_client._auth_header()


# -----------------------------------------------------------------------------
# call() — operation lookup + path substitution
# -----------------------------------------------------------------------------

def test_call_unknown_operation_id_raises():
    with pytest.raises(pandadoc_client.UnknownOperationError):
        pandadoc_client.call("does_not_exist")


def test_call_substitutes_path_params(monkeypatch, fake_pandadoc):
    _set_pandadoc_config(monkeypatch, api_key="k")
    # Use the real OPERATION_TABLE — detailsDocument has {id} in path.
    fake_pandadoc.returns.append(
        _FakeResponse(200, json.dumps({"id": "doc-99"}).encode("utf-8"))
    )
    result = pandadoc_client.call(
        "detailsDocument",
        path_params={"id": "doc-99"},
    )
    assert result == {"id": "doc-99"}
    assert "/public/v1/documents/doc-99/details" in fake_pandadoc.calls[0]["url"]


# -----------------------------------------------------------------------------
# Retry behavior
# -----------------------------------------------------------------------------

def test_call_retries_on_429(monkeypatch, fake_pandadoc):
    _set_pandadoc_config(monkeypatch, api_key="k")
    # Override retry config for fast test.
    monkeypatch.setattr(
        config, "retry_settings",
        lambda: {"max_attempts": 3, "initial_backoff_seconds": 0.01,
                 "max_backoff_seconds": 0.1},
    )
    # Suppress sleep so the test runs instantly.
    monkeypatch.setattr(pandadoc_client.time, "sleep", lambda s: None)

    fake_pandadoc.returns.extend([
        _FakeHTTPError(429, b'{"error":"slow down"}'),
        _FakeHTTPError(429, b'{"error":"slow down"}'),
        _FakeResponse(200, json.dumps({"ok": True}).encode("utf-8")),
    ])
    result = pandadoc_client.call("listDocuments")
    assert result == {"ok": True}
    assert len(fake_pandadoc.calls) == 3


def test_call_non_retryable_4xx_raises(monkeypatch, fake_pandadoc):
    _set_pandadoc_config(monkeypatch, api_key="k")
    monkeypatch.setattr(
        config, "retry_settings",
        lambda: {"max_attempts": 3, "initial_backoff_seconds": 0.01,
                 "max_backoff_seconds": 0.1},
    )
    monkeypatch.setattr(pandadoc_client.time, "sleep", lambda s: None)
    fake_pandadoc.returns.append(
        _FakeHTTPError(404, b'{"error":"not found"}')
    )
    with pytest.raises(pandadoc_client.PandaDocAPIError) as ei:
        pandadoc_client.call(
            "detailsDocument", path_params={"id": "x"},
        )
    assert ei.value.status_code == 404


# -----------------------------------------------------------------------------
# 202-poll
# -----------------------------------------------------------------------------

def test_call_polls_202_until_200(monkeypatch, fake_pandadoc):
    _set_pandadoc_config(monkeypatch, api_key="k", poll_interval_seconds=0.0)
    monkeypatch.setattr(pandadoc_client.time, "sleep", lambda s: None)

    # First request returns 202 + Location, polls return 202, then 200.
    fake_pandadoc.returns.extend([
        _FakeResponse(
            202, b'{"status":"working"}',
            headers={"Content-Type": "application/json",
                     "Location": "/public/v1/documents/doc-1/summary"},
        ),
        _FakeResponse(202, b'{"status":"working"}'),
        _FakeResponse(200, json.dumps({"summary": "all good"}).encode("utf-8")),
    ])
    result = pandadoc_client.call(
        "getDocumentSummary",
        path_params={"id": "doc-1"},
        poll=True,
    )
    assert result == {"summary": "all good"}
    # 1 initial + 2 poll calls = 3 total.
    assert len(fake_pandadoc.calls) == 3


def test_call_poll_timeout_raises(monkeypatch, fake_pandadoc):
    _set_pandadoc_config(
        monkeypatch, api_key="k",
        poll_max_seconds=0,        # forces immediate timeout
        poll_interval_seconds=0.0,
    )
    monkeypatch.setattr(pandadoc_client.time, "sleep", lambda s: None)

    fake_pandadoc.returns.append(
        _FakeResponse(
            202, b'{}',
            headers={"Content-Type": "application/json",
                     "Location": "/public/v1/x"},
        )
    )
    with pytest.raises(pandadoc_client.PandaDocPollTimeout):
        pandadoc_client.call(
            "getDocumentSummary", path_params={"id": "x"}, poll=True,
        )


# -----------------------------------------------------------------------------
# Multipart body
# -----------------------------------------------------------------------------

def test_build_multipart_body_includes_file_part():
    body = pandadoc_client._build_multipart_body(
        [("file", ("hello.txt", b"hello world", "text/plain"))],
        boundary="BOUNDARY",
    )
    assert b"--BOUNDARY\r\n" in body
    assert b'Content-Disposition: form-data; name="file"; filename="hello.txt"\r\n' in body
    assert b"Content-Type: text/plain\r\n" in body
    assert b"hello world\r\n" in body
    assert b"--BOUNDARY--\r\n" in body


def test_build_multipart_body_string_field():
    """Non-file fields use a name-only Content-Disposition."""
    body = pandadoc_client._build_multipart_body(
        [("project", (None, b"GE1", "text/plain"))],
        boundary="B",
    )
    assert b'Content-Disposition: form-data; name="project"\r\n\r\n' in body
    assert b"GE1\r\n" in body


# -----------------------------------------------------------------------------
# OPERATION_TABLE smoke
# -----------------------------------------------------------------------------

def test_operation_table_has_122_entries():
    """The generator should have produced exactly 122 operations."""
    from pandadoc_operations import OPERATION_TABLE
    assert len(OPERATION_TABLE) == 122


def test_operation_table_has_polling_flag_set():
    from pandadoc_operations import OPERATION_TABLE
    assert OPERATION_TABLE["getDocumentSummary"]["needs_polling"] is True
    assert OPERATION_TABLE["getDocumentContent"]["needs_polling"] is True
    # Spot check: a normal endpoint shouldn't poll.
    assert OPERATION_TABLE["createDocument"]["needs_polling"] is False


def test_operation_table_module_split():
    """All 6 expected modules should be represented."""
    from pandadoc_operations import OPERATION_TABLE
    modules = {info["module"] for info in OPERATION_TABLE.values()}
    assert modules == {
        "documents", "templates", "workspace", "content", "webhooks", "misc",
    }
