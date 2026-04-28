"""Tests for the system_check_* helper functions in tools/system.py.

These tests exercise the helpers directly (not via MCP) and use mocks for
network-dependent paths.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import tools.system as system


def test_result_helper_pass():
    r = system._result("foo", "pass", "all good")
    assert r["status"] == "pass"
    assert r["name"] == "foo"
    assert r["details"] == "all good"
    assert "fix" not in r


def test_result_helper_fail_with_fix():
    r = system._result("bar", "fail", "broken", fix="run baz")
    assert r["status"] == "fail"
    assert r["fix"] == "run baz"


def test_result_helper_with_extra():
    r = system._result("baz", "pass", "ok", extra={"count": 5, "items": [1, 2]})
    assert r["count"] == 5
    assert r["items"] == [1, 2]


def test_check_config_missing_returns_warn(tmp_path, monkeypatch):
    """When config.json doesn't exist, returns warn (not fail) since defaults work."""
    fake_config = MagicMock()
    fake_config.__file__ = str(tmp_path / "config.py")
    fake_config._DEFAULTS = {"key1": None, "key2": "default"}
    with patch.dict("sys.modules", {"config": fake_config}):
        r = system._check_config()
    assert r["status"] == "warn"
    assert "not found" in r["details"].lower()
    assert "fix" in r


def test_check_dependencies_python_version():
    """Smoke: just exercise _check_dependencies — it should pass on any 3.10+."""
    r = system._check_dependencies()
    assert r["name"] == "Dependencies"
    assert r["status"] in ("pass", "warn", "fail")
    # Python version check should never put it at fail in a working env
    assert r["status"] != "fail" or "Python" not in r["details"]


def test_check_tools_counts():
    """Tool registration check should produce a count."""
    r = system._check_tools()
    assert r["name"] == "Tool registration"
    if r["status"] == "pass":
        assert "count" in r
        # We expect at least 100 tools registered in any reasonable build.
        assert r["count"] >= 100


def test_check_clock_with_mocked_skew():
    """Clock check returns fail when skew exceeds 5 minutes."""
    import datetime as _dt
    import email.utils as _eu
    fake_resp = MagicMock()
    # Pretend Google's server is 10 minutes behind us.
    server_time = (_dt.datetime.now(_dt.timezone.utc)
                   - _dt.timedelta(minutes=10))
    fake_resp.headers = {"Date": _eu.format_datetime(server_time)}
    with patch("requests.head", return_value=fake_resp):
        r = system._check_clock()
    assert r["status"] == "fail"
    assert "off by" in r["details"].lower() or "skew" in r["details"].lower()


def test_check_clock_within_tolerance():
    """Clock check passes when skew is small."""
    import datetime as _dt
    import email.utils as _eu
    fake_resp = MagicMock()
    server_time = _dt.datetime.now(_dt.timezone.utc)
    fake_resp.headers = {"Date": _eu.format_datetime(server_time)}
    with patch("requests.head", return_value=fake_resp):
        r = system._check_clock()
    assert r["status"] == "pass"


def test_check_unit_tests_returns_pass_count(monkeypatch):
    """The pytest-as-diagnostic check should produce a structured result
    with passed count and elapsed time. We don't call the real pytest from
    inside pytest (would recurse forever); instead mock subprocess.run to
    return the canonical 'X passed in Y' summary line."""
    fake_proc = MagicMock()
    fake_proc.returncode = 0
    fake_proc.stdout = "............................\n123 passed in 0.50s\n"
    fake_proc.stderr = ""
    with patch("subprocess.run", return_value=fake_proc):
        r = system._check_unit_tests()
    assert r["name"] == "Unit tests"
    assert r["status"] == "pass"
    assert r["passed"] == 123
    assert r["elapsed_seconds"] >= 0


def test_check_unit_tests_reports_failures(monkeypatch):
    """Failure path: subprocess returns non-zero, parses failed/passed count."""
    fake_proc = MagicMock()
    fake_proc.returncode = 1
    fake_proc.stdout = (
        "FAILED tests/test_foo.py::test_bar\n"
        "FAILED tests/test_baz.py::test_qux\n"
        "2 failed, 380 passed in 0.95s\n"
    )
    fake_proc.stderr = ""
    with patch("subprocess.run", return_value=fake_proc):
        r = system._check_unit_tests()
    assert r["status"] == "fail"
    assert r["failed"] == 2
    assert r["passed"] == 380
    assert "first_failures" in r
    assert any("test_bar" in f for f in r["first_failures"])


def test_every_check_returns_required_keys():
    """Type guard — every system check must return a dict with name, status,
    details. The aggregator (system_doctor) and JSON serializer count on
    these being present without optional handling."""
    checks_to_call = [
        system._check_oauth,
        system._check_filesystem,
        system._check_dependencies,
        system._check_tools,
        system._check_license,
    ]
    for check in checks_to_call:
        try:
            r = check()
        except Exception as e:
            pytest.fail(f"{check.__name__} raised: {e}")
        assert "name" in r, f"{check.__name__} missing 'name'"
        assert "status" in r, f"{check.__name__} missing 'status'"
        assert "details" in r, f"{check.__name__} missing 'details'"
        assert r["status"] in ("pass", "warn", "fail"), (
            f"{check.__name__} returned unrecognized status {r['status']!r}"
        )


def test_check_unit_tests_handles_missing_tests_dir(monkeypatch, tmp_path):
    """If tests/ disappears (e.g. handoff archive without tests included),
    return warn rather than crash."""
    # Point gservices to a fake project root with no tests/ dir
    import gservices
    fake_module = MagicMock()
    fake_module.__file__ = str(tmp_path / "gservices.py")
    monkeypatch.setattr(system, "gservices", fake_module)
    r = system._check_unit_tests()
    assert r["status"] == "warn"
    assert "tests" in r["details"].lower()
