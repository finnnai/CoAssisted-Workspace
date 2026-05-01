# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Tests for the background scanner core (registry + state + run_due)."""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

import pytest

import scanner


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path):
    scanner._override_state_path_for_tests(tmp_path / "scan_state.json")
    scanner._reset_registry_for_tests()
    yield
    scanner._reset_registry_for_tests()
    scanner._override_state_path_for_tests(
        Path(__file__).resolve().parent.parent / "scan_state.json",
    )


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #


def test_register_check_appears_in_list():
    scanner.register_check("c1", cadence_hours=1, fn=lambda: [], description="t")
    checks = scanner.list_checks()
    assert len(checks) == 1
    assert checks[0]["name"] == "c1"
    assert checks[0]["cadence_hours"] == 1


def test_register_overwrites_same_name():
    scanner.register_check("c1", cadence_hours=1, fn=lambda: [], description="v1")
    scanner.register_check("c1", cadence_hours=2, fn=lambda: [], description="v2")
    checks = scanner.list_checks()
    assert len(checks) == 1
    assert checks[0]["cadence_hours"] == 2
    assert checks[0]["description"] == "v2"


def test_register_rejects_zero_cadence():
    with pytest.raises(ValueError):
        scanner.register_check("c1", cadence_hours=0, fn=lambda: [])


def test_register_rejects_empty_name():
    with pytest.raises(ValueError):
        scanner.register_check("", cadence_hours=1, fn=lambda: [])


def test_unregister_returns_true_when_present():
    scanner.register_check("c1", cadence_hours=1, fn=lambda: [])
    assert scanner.unregister_check("c1") is True
    assert scanner.unregister_check("c1") is False


# --------------------------------------------------------------------------- #
# run_one
# --------------------------------------------------------------------------- #


def test_run_one_executes_callable_and_persists_state():
    scanner.register_check(
        "c1", cadence_hours=1,
        fn=lambda: [{"kind": "x", "title": "hello"}],
    )
    result = scanner.run_one("c1")
    assert result.alert_count == 1
    assert result.error is None
    # State should now have a last_run timestamp
    state = scanner._load_state()
    assert "c1" in state
    assert state["c1"]["last_run"]


def test_run_one_unknown_check_returns_error():
    result = scanner.run_one("missing")
    assert result.alert_count == 0
    assert "unknown check" in result.error.lower()


def test_run_one_handles_exception_in_check_fn():
    def explode():
        raise RuntimeError("boom")
    scanner.register_check("c1", cadence_hours=1, fn=explode)
    result = scanner.run_one("c1")
    assert result.alert_count == 0
    assert result.error == "boom"
    # State still recorded the run.
    state = scanner._load_state()
    assert "c1" in state


# --------------------------------------------------------------------------- #
# run_due — cadence + skipping logic
# --------------------------------------------------------------------------- #


def test_first_run_always_due():
    scanner.register_check("c1", cadence_hours=24,
                           fn=lambda: [{"k": 1}])
    summary = scanner.run_due()
    assert len(summary["ran"]) == 1
    assert summary["skipped"] == []
    assert summary["total_alerts"] == 1


def test_recently_run_check_is_skipped():
    scanner.register_check("c1", cadence_hours=24, fn=lambda: [])
    scanner.run_one("c1")
    summary = scanner.run_due()
    assert summary["ran"] == []
    assert len(summary["skipped"]) == 1
    assert summary["skipped"][0]["name"] == "c1"


def test_check_runs_again_after_cadence_elapses(monkeypatch):
    scanner.register_check("c1", cadence_hours=1, fn=lambda: [])
    # Manually write a stale last_run.
    stale = (_dt.datetime.now().astimezone()
             - _dt.timedelta(hours=2)).isoformat()
    scanner._save_state({"c1": {"last_run": stale, "last_alert_count": 0}})
    summary = scanner.run_due()
    assert len(summary["ran"]) == 1


def test_run_due_handles_multiple_checks():
    scanner.register_check("c1", cadence_hours=1, fn=lambda: [{"k": "a1"}])
    scanner.register_check("c2", cadence_hours=1,
                           fn=lambda: [{"k": "a2"}, {"k": "a3"}])
    summary = scanner.run_due()
    assert len(summary["ran"]) == 2
    assert summary["total_alerts"] == 3


# --------------------------------------------------------------------------- #
# State persistence + atomic write
# --------------------------------------------------------------------------- #


def test_atomic_write_no_leftover_tmp_files(tmp_path):
    scanner.register_check("c1", cadence_hours=1, fn=lambda: [])
    scanner.run_one("c1")
    leftover = list(tmp_path.glob("*.tmp"))
    assert leftover == []


def test_state_survives_reload(tmp_path):
    scanner.register_check("c1", cadence_hours=1, fn=lambda: [{"k": 1}])
    scanner.run_one("c1")
    # Re-read state from disk
    state = scanner._load_state()
    assert state["c1"]["last_alert_count"] == 1


def test_list_checks_includes_next_due():
    scanner.register_check("c1", cadence_hours=2, fn=lambda: [])
    scanner.run_one("c1")
    checks = scanner.list_checks()
    assert checks[0]["next_due"] is not None
    assert checks[0]["last_alert_count"] == 0
