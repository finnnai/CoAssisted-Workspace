# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Unit tests for ap_sweep.py — AP-4 routing decisions.

The pure decision logic (`decide_disposition`) is fully testable
without OAuth/Drive plumbing. The full `run_sweep_cycle` end-to-end
runs in integration tests with network markers.
"""

from __future__ import annotations

import datetime as _dt

import pytest

import ap_sweep
import project_registry


@pytest.fixture
def fresh_registry(tmp_path, monkeypatch):
    fake = tmp_path / "projects.json"
    monkeypatch.setattr(project_registry, "_REGISTRY_PATH", fake)
    project_registry.register(
        "GE1",
        name="Google - Golden Eagle 1",
        name_aliases=["Golden Eagle"],
        assigned_team_emails=["sefita@surefox.com"],
        billing_origin_state="CA",
    )
    yield fake


# -----------------------------------------------------------------------------
# decide_disposition — auto_file path
# -----------------------------------------------------------------------------

def test_auto_file_when_alias_clearly_matches(fresh_registry):
    """Subject mentions a registered alias → high confidence → auto_file."""
    result, action = ap_sweep.decide_disposition(
        sender_email=None,
        subject="Receipt for Golden Eagle",
        body=None,
        timestamp=None,
        use_llm=False,
    )
    assert result.tier == "alias"
    assert action == "auto_file"
    assert result.project_code == "GE1"


def test_auto_file_when_team_match_unambiguous(fresh_registry):
    """Sender on exactly one project's team → tier=team → auto_file."""
    result, action = ap_sweep.decide_disposition(
        sender_email="sefita@surefox.com",
        subject="random",
        body=None,
        timestamp=None,
        use_llm=False,
    )
    assert result.tier == "team"
    assert action == "auto_file"
    assert result.project_code == "GE1"


# -----------------------------------------------------------------------------
# decide_disposition — chat_picker path
# -----------------------------------------------------------------------------

def test_chat_picker_when_team_ambiguous(fresh_registry):
    """Multiple team matches with no tiebreaker → chat_picker."""
    project_registry.register(
        "C12",
        name="Prometheus - Condor 12",
        assigned_team_emails=["sefita@surefox.com"],
    )
    result, action = ap_sweep.decide_disposition(
        sender_email="sefita@surefox.com",
        subject="random",
        body=None,
        timestamp=None,
        use_llm=False,
    )
    assert result.tier == "chat_picker"
    assert action == "chat_picker"
    assert {c["code"] for c in result.candidates} == {"GE1", "C12"}


# -----------------------------------------------------------------------------
# decide_disposition — triage path
# -----------------------------------------------------------------------------

def test_triage_when_no_signals(fresh_registry):
    """No alias, no team, no LLM → unresolved → triage."""
    result, action = ap_sweep.decide_disposition(
        sender_email="external@vendor.com",
        subject="random receipt",
        body=None,
        timestamp=None,
        use_llm=False,
    )
    assert result.tier == "unresolved"
    assert action == "triage"


# -----------------------------------------------------------------------------
# target_subfolder_for_action — only fires for filing actions
# -----------------------------------------------------------------------------

def test_target_subfolder_returns_none_for_chat_picker(fresh_registry):
    """chat_picker doesn't need a Drive target folder."""
    assert ap_sweep.target_subfolder_for_action(
        "GE1", "chat_picker"
    ) is None


def test_target_subfolder_returns_none_for_triage(fresh_registry):
    """triage routes to Triage/, not project subfolders."""
    assert ap_sweep.target_subfolder_for_action(
        "GE1", "triage"
    ) is None


def test_target_subfolder_returns_none_when_no_project_code():
    """Empty project_code means no target."""
    assert ap_sweep.target_subfolder_for_action(
        "", "auto_file"
    ) is None


# -----------------------------------------------------------------------------
# SweepResult summary
# -----------------------------------------------------------------------------

def test_sweep_result_summary_line():
    r = ap_sweep.SweepResult(
        counts={
            "auto_file": 12,
            "auto_file_flag": 3,
            "chat_picker": 1,
            "triage": 2,
        },
    )
    line = r.summary_line()
    assert "12 auto-filed" in line
    assert "3 flagged" in line
    assert "1 awaiting picker" in line
    assert "2 in Triage" in line


def test_sweep_result_summary_zero_counts():
    """Empty result reports zero across the board, not crashes."""
    r = ap_sweep.SweepResult()
    line = r.summary_line()
    assert "0 auto-filed" in line


# -----------------------------------------------------------------------------
# SweepItem dataclass roundtrip
# -----------------------------------------------------------------------------

def test_sweep_item_minimum_fields():
    """Every required field has a sensible default or is supplied."""
    item = ap_sweep.SweepItem(
        source="email",
        source_id="msg-id-123",
        sender="josh@surefox.com",
        subject="Receipt for X",
        timestamp=_dt.datetime(2026, 5, 1, 12, 0, tzinfo=_dt.timezone.utc),
        project_code=None,
        confidence=0.0,
        tier="",
        action="",
        target_folder_id=None,
    )
    assert item.note == ""
    assert item.source == "email"
