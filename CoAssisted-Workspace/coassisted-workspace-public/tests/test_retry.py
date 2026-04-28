# © 2026 CoAssisted Workspace contributors. Licensed under MIT — see LICENSE use only.
"""Tests for retry/backoff logic.

retry.py decides what's retryable (429, 5xx, network) vs fail-fast (4xx
other than 429, AuthError). It also implements exponential backoff with
jitter. Subtle bugs here get exponentially expensive — over-retrying on
401s would burn quota fast and never succeed.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from googleapiclient.errors import HttpError

import retry


def _http_error(status: int) -> HttpError:
    resp = MagicMock()
    resp.status = status
    err = HttpError(resp, b"")
    return err


# --------------------------------------------------------------------------- #
# _should_retry classification
# --------------------------------------------------------------------------- #


class TestShouldRetry:
    def test_429_is_retryable(self):
        assert retry._should_retry(_http_error(429)) is True

    def test_500_is_retryable(self):
        assert retry._should_retry(_http_error(500)) is True

    def test_503_is_retryable(self):
        assert retry._should_retry(_http_error(503)) is True

    def test_400_is_not_retryable(self):
        """Bad request — your input is wrong, retrying won't help."""
        assert retry._should_retry(_http_error(400)) is False

    def test_401_is_not_retryable(self):
        """Auth — fail fast so the user can rotate the token."""
        assert retry._should_retry(_http_error(401)) is False

    def test_403_is_not_retryable(self):
        assert retry._should_retry(_http_error(403)) is False

    def test_404_is_not_retryable(self):
        assert retry._should_retry(_http_error(404)) is False

    def test_timeout_is_retryable(self):
        assert retry._should_retry(TimeoutError()) is True

    def test_connection_error_is_retryable(self):
        assert retry._should_retry(ConnectionError()) is True

    def test_random_exception_not_retryable(self):
        """ValueError, TypeError, etc. — code bugs, retrying doesn't fix."""
        assert retry._should_retry(ValueError("bad")) is False
        assert retry._should_retry(KeyError("x")) is False


# --------------------------------------------------------------------------- #
# retry_call (sync) — backoff + max_attempts
# --------------------------------------------------------------------------- #


def _retry_settings(max_attempts=4, initial=0.001, cap=0.01):
    """Tiny delays so tests run fast."""
    return {
        "max_attempts": max_attempts,
        "initial_backoff_seconds": initial,
        "max_backoff_seconds": cap,
    }


def test_retry_call_returns_immediately_on_success():
    fn = MagicMock(return_value="ok")
    with patch("config.retry_settings", return_value=_retry_settings()):
        result = retry.retry_call(fn)
    assert result == "ok"
    assert fn.call_count == 1


def test_retry_call_retries_on_429_then_succeeds():
    """First call 429s, second call returns. retry_call should swallow the
    first error and return the second result."""
    fn = MagicMock(side_effect=[_http_error(429), "ok"])
    with patch("config.retry_settings", return_value=_retry_settings()), \
         patch("time.sleep"):  # don't actually wait
        result = retry.retry_call(fn)
    assert result == "ok"
    assert fn.call_count == 2


def test_retry_call_gives_up_after_max_attempts():
    """All 4 attempts hit 429 — should raise the final HttpError, not a generic."""
    final_err = _http_error(429)
    fn = MagicMock(side_effect=[
        _http_error(429), _http_error(429), _http_error(429), final_err,
    ])
    with patch("config.retry_settings", return_value=_retry_settings(max_attempts=4)), \
         patch("time.sleep"):
        with pytest.raises(HttpError):
            retry.retry_call(fn)
    assert fn.call_count == 4


def test_retry_call_does_not_retry_on_400():
    """Non-retryable errors should fail immediately, not after backoff."""
    fn = MagicMock(side_effect=_http_error(400))
    with patch("config.retry_settings", return_value=_retry_settings()), \
         patch("time.sleep") as sleep_mock:
        with pytest.raises(HttpError):
            retry.retry_call(fn)
    assert fn.call_count == 1
    sleep_mock.assert_not_called()


def test_retry_call_passes_args_and_kwargs():
    """Make sure args/kwargs flow through to the wrapped function."""
    fn = MagicMock(return_value="ok")
    with patch("config.retry_settings", return_value=_retry_settings()):
        retry.retry_call(fn, "a", "b", x=1)
    fn.assert_called_once_with("a", "b", x=1)
