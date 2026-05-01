# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Unit tests for project_router.py — AP-5 routing decisions.

Tests the deterministic tiers (1-3, 7) without hitting Drive/Calendar
APIs. The calendar tiebreaker (tier 4), Geotab (tier 5), and LLM
(tier 6) are exercised in integration tests with network markers.
"""

from __future__ import annotations

import datetime as _dt

import pytest

import project_registry
import project_router


@pytest.fixture
def fresh_registry(tmp_path, monkeypatch):
    """Each test gets a clean projects.json under tmp_path."""
    fake = tmp_path / "projects.json"
    monkeypatch.setattr(project_registry, "_REGISTRY_PATH", fake)
    yield fake


def _seed_two_projects():
    """Two projects with distinct teams + aliases."""
    project_registry.register(
        "GE1",
        name="Google - Golden Eagle 1",
        client="Google, LLC",
        sender_emails=["operations@google.com"],
        name_aliases=["GE1", "Golden Eagle"],
        assigned_team_emails=[
            "sefita@surefox.com",
            "sendy@surefox.com",
        ],
        billing_origin_state="CA",
    )
    project_registry.register(
        "C12",
        name="Prometheus - Condor 12",
        client="Prometheus",
        sender_emails=["ap@prometheus.com"],
        name_aliases=["Condor 12", "C12"],
        assigned_team_emails=[
            "sefita@surefox.com",
            "meghan@surefox.com",
        ],
        billing_origin_state="CA",
    )


# -----------------------------------------------------------------------------
# Tier 1: explicit hint
# -----------------------------------------------------------------------------

def test_explicit_code_wins(fresh_registry):
    _seed_two_projects()
    result = project_router.route_project(explicit_code="GE1", subject="something")
    assert result.tier == "explicit"
    assert result.project_code == "GE1"
    assert result.confidence == 1.0


def test_explicit_unknown_code_falls_through(fresh_registry):
    _seed_two_projects()
    result = project_router.route_project(
        explicit_code="UNKNOWN",
        subject="Receipt for Golden Eagle",
    )
    # Explicit unknown → tier 2 alias still gets a shot.
    assert result.tier == "alias"
    assert result.project_code == "GE1"


# -----------------------------------------------------------------------------
# Tier 2: alias match in subject/body
# -----------------------------------------------------------------------------

def test_alias_match_in_subject(fresh_registry):
    _seed_two_projects()
    result = project_router.route_project(subject="Receipt for Condor 12 fuel")
    assert result.tier == "alias"
    assert result.project_code == "C12"


def test_alias_match_in_body(fresh_registry):
    _seed_two_projects()
    result = project_router.route_project(
        subject="receipt",
        body="Picked up parts for Golden Eagle today",
    )
    assert result.tier == "alias"
    assert result.project_code == "GE1"


def test_alias_match_case_insensitive(fresh_registry):
    _seed_two_projects()
    result = project_router.route_project(subject="GOLDEN EAGLE supplies")
    assert result.tier == "alias"
    assert result.project_code == "GE1"


# -----------------------------------------------------------------------------
# Tier 3: sender on team
# -----------------------------------------------------------------------------

def test_team_match_single_winner(fresh_registry):
    _seed_two_projects()
    # Sendy is only on GE1.
    result = project_router.route_project(sender_email="sendy@surefox.com")
    assert result.tier == "team"
    assert result.project_code == "GE1"


def test_team_match_ambiguous_no_tiebreaker_falls_to_picker(fresh_registry):
    _seed_two_projects()
    # Sefita is on both GE1 and C12; no calendar/Geotab signal.
    result = project_router.route_project(sender_email="sefita@surefox.com")
    # Without timestamp/calendar, tiebreakers can't fire.
    assert result.tier == "chat_picker"
    assert {c["code"] for c in result.candidates} == {"GE1", "C12"}


# -----------------------------------------------------------------------------
# Tier 7: unresolved
# -----------------------------------------------------------------------------

def test_no_signals_unresolved(fresh_registry):
    _seed_two_projects()
    # No alias, no team match, no LLM (use_llm=False)
    result = project_router.route_project(
        subject="random receipt",
        sender_email="external@vendor.com",
        use_llm=False,
    )
    assert result.tier == "unresolved"
    assert result.project_code is None


def test_empty_registry_unresolved(fresh_registry):
    result = project_router.route_project(
        subject="anything",
        sender_email="anyone@x.com",
        use_llm=False,
    )
    assert result.tier == "unresolved"


# -----------------------------------------------------------------------------
# Confidence action mapping
# -----------------------------------------------------------------------------

def test_confidence_action_auto_file_high_conf():
    r = project_router.RouteResult(
        project_code="GE1", confidence=1.0, tier="explicit",
        reason="", candidates=[],
    )
    assert project_router.confidence_action(r) == "auto_file"


def test_confidence_action_flag_medium_conf():
    r = project_router.RouteResult(
        project_code="GE1", confidence=0.7, tier="alias",
        reason="", candidates=[],
    )
    assert project_router.confidence_action(r) == "auto_file_flag"


def test_confidence_action_picker_low_conf():
    r = project_router.RouteResult(
        project_code="GE1", confidence=0.4, tier="alias",
        reason="", candidates=[],
    )
    assert project_router.confidence_action(r) == "chat_picker"


def test_confidence_action_chat_picker_explicit():
    r = project_router.RouteResult(
        project_code=None, confidence=0.0, tier="chat_picker",
        reason="ambiguous", candidates=[],
    )
    assert project_router.confidence_action(r) == "chat_picker"


def test_confidence_action_unresolved_routes_to_triage():
    r = project_router.RouteResult(
        project_code=None, confidence=0.0, tier="unresolved",
        reason="no signal", candidates=[],
    )
    assert project_router.confidence_action(r) == "triage"


# -----------------------------------------------------------------------------
# Resolution helpers in project_registry
# -----------------------------------------------------------------------------

def test_resolve_by_alias(fresh_registry):
    _seed_two_projects()
    rec = project_registry.resolve_by_alias("Picked up parts for Golden Eagle today")
    assert rec is not None
    assert rec["code"] == "GE1"


def test_resolve_by_alias_no_match(fresh_registry):
    _seed_two_projects()
    assert project_registry.resolve_by_alias("totally unrelated text") is None


def test_resolve_by_team_email_multiple_results(fresh_registry):
    _seed_two_projects()
    matches = project_registry.resolve_by_team_email("sefita@surefox.com")
    assert len(matches) == 2
    assert {m["code"] for m in matches} == {"GE1", "C12"}


def test_resolve_by_team_email_zero_results(fresh_registry):
    _seed_two_projects()
    assert project_registry.resolve_by_team_email("nobody@x.com") == []


def test_resolve_by_staffwizard_job(fresh_registry):
    project_registry.register(
        "GE1",
        name="Google - Golden Eagle 1",
        staffwizard_job_number="Google, LLC",
        staffwizard_job_desc="Golden Eagle 1",
    )
    rec = project_registry.resolve_by_staffwizard_job(
        "Google, LLC", "Golden Eagle 1"
    )
    assert rec is not None
    assert rec["code"] == "GE1"


def test_drive_subfolder_round_trip(fresh_registry):
    project_registry.register("GE1", name="Google - Golden Eagle 1")
    project_registry.update_drive_subfolder("GE1", "receipts", "FOLDER_ID_123")
    assert project_registry.get_drive_subfolder("GE1", "receipts") == "FOLDER_ID_123"


def test_drive_subfolder_unknown_project_returns_none(fresh_registry):
    assert project_registry.get_drive_subfolder("UNREGISTERED", "receipts") is None


def test_register_with_wave2_fields_preserves_existing(fresh_registry):
    """Re-registering a project should merge name_aliases + team_emails."""
    project_registry.register(
        "GE1",
        name="Google - Golden Eagle 1",
        name_aliases=["GE1"],
        assigned_team_emails=["sefita@surefox.com"],
        billing_origin_state="CA",
    )
    project_registry.register(
        "GE1",
        name="Google - Golden Eagle 1",
        name_aliases=["Golden Eagle"],
        assigned_team_emails=["sendy@surefox.com"],
    )
    rec = project_registry.get("GE1")
    assert set(rec["name_aliases"]) == {"GE1", "Golden Eagle"}
    assert set(rec["assigned_team_emails"]) == {
        "sefita@surefox.com", "sendy@surefox.com",
    }
    # Scalar field preserves when not re-supplied.
    assert rec["billing_origin_state"] == "CA"
