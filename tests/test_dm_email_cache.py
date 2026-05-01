# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Tests for dm_email_cache — DM space ↔ email sidecar."""

from __future__ import annotations

from pathlib import Path

import pytest

import dm_email_cache as dmc


@pytest.fixture(autouse=True)
def _isolate_store(tmp_path: Path):
    """Every test gets its own JSON file. Don't pollute the user's real cache."""
    dmc._override_path_for_tests(tmp_path / "dm_emails.json")
    yield
    dmc._override_path_for_tests(
        Path(__file__).resolve().parent.parent / "dm_emails.json",
    )


# --------------------------------------------------------------------------- #
# Basic record + lookup
# --------------------------------------------------------------------------- #


def test_record_then_lookup_returns_email():
    dmc.record("spaces/AAA111", "amanda.miller@staffwizard.com")
    assert dmc.lookup_by_space("spaces/AAA111") == "amanda.miller@staffwizard.com"


def test_lookup_unknown_space_returns_none():
    assert dmc.lookup_by_space("spaces/never-recorded") is None


def test_lookup_with_no_space_arg_returns_none():
    assert dmc.lookup_by_space("") is None
    assert dmc.lookup_by_space(None) is None


def test_record_normalizes_email_lowercase():
    dmc.record("spaces/AAA111", "Amanda.Miller@StaffWizard.com")
    assert dmc.lookup_by_space("spaces/AAA111") == "amanda.miller@staffwizard.com"


# --------------------------------------------------------------------------- #
# Idempotent re-record + send_count
# --------------------------------------------------------------------------- #


def test_re_record_same_pair_increments_count():
    dmc.record("spaces/AAA111", "x@y.com")
    dmc.record("spaces/AAA111", "x@y.com")
    dmc.record("spaces/AAA111", "x@y.com")
    rec = dmc.all_known_dms()["spaces/AAA111"]
    assert rec["send_count"] == 3
    assert rec["email"] == "x@y.com"


def test_re_record_with_different_email_overwrites():
    """A DM space is 1:1 with the other party. If the recorded email
    changes for the same space (rare but possible after a recipient
    switches addresses), the new one wins."""
    dmc.record("spaces/AAA111", "old@example.com")
    dmc.record("spaces/AAA111", "new@example.com")
    rec = dmc.all_known_dms()["spaces/AAA111"]
    assert rec["email"] == "new@example.com"
    # Counter resets because this is a fresh mapping.
    assert rec["send_count"] == 1


# --------------------------------------------------------------------------- #
# Reverse lookup + admin
# --------------------------------------------------------------------------- #


def test_lookup_space_by_email_reverse():
    dmc.record("spaces/AAA111", "amanda@example.com")
    dmc.record("spaces/BBB222", "brian@example.com")
    assert dmc.lookup_space_by_email("amanda@example.com") == "spaces/AAA111"
    assert dmc.lookup_space_by_email("brian@example.com") == "spaces/BBB222"
    assert dmc.lookup_space_by_email("unknown@example.com") is None


def test_clear_drops_everything():
    dmc.record("spaces/AAA111", "a@x.com")
    dmc.record("spaces/BBB222", "b@x.com")
    dropped = dmc.clear()
    assert dropped == 2
    assert dmc.all_known_dms() == {}


def test_record_with_empty_inputs_is_noop():
    dmc.record("", "x@y.com")
    dmc.record("spaces/AAA111", "")
    assert dmc.all_known_dms() == {}


# --------------------------------------------------------------------------- #
# Atomic write — file is well-formed even after crash mid-write
# --------------------------------------------------------------------------- #


def test_atomic_write_no_partial_files_left_behind(tmp_path):
    """Writing should not leave .tmp files lying around."""
    dmc.record("spaces/AAA111", "x@y.com")
    dmc.record("spaces/BBB222", "y@z.com")
    leftover = list(tmp_path.glob("*.tmp"))
    assert leftover == [], f"leftover temp files: {leftover}"
