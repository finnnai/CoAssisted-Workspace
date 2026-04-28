# © 2026 CoAssisted Workspace. Licensed for non-redistribution use only.
"""Tests for project_registry — CRUD + 5-tier resolution ladder."""

from __future__ import annotations

from pathlib import Path

import pytest

import project_registry as pr


@pytest.fixture(autouse=True)
def _isolate_registry(tmp_path: Path):
    """Each test gets a fresh registry file. Avoid bleed between tests."""
    pr._override_path_for_tests(tmp_path / "projects.json")
    yield
    pr._override_path_for_tests(
        Path(__file__).resolve().parent.parent / "projects.json",
    )


# --------------------------------------------------------------------------- #
# CRUD
# --------------------------------------------------------------------------- #


def test_register_creates_record():
    rec = pr.register(code="alpha", name="Project Alpha")
    assert rec["code"] == "ALPHA"
    assert rec["name"] == "Project Alpha"
    assert rec["active"] is True
    assert rec["invoice_count"] == 0
    assert rec["currency"] == "USD"


def test_register_normalizes_code_to_upper():
    pr.register(code="beta", name="Project Beta")
    assert pr.get("BETA") is not None
    assert pr.get("beta") is not None  # lookup is case-insensitive


def test_register_merges_lists_on_re_register():
    pr.register(
        code="GAMMA", name="Gamma",
        sender_emails=["a@x.com"],
        filename_patterns=["^INV-G-"],
    )
    pr.register(
        code="GAMMA", name="Gamma Renamed",
        sender_emails=["b@x.com"],
        filename_patterns=["(?i)gamma"],
    )
    rec = pr.get("GAMMA")
    assert "a@x.com" in rec["sender_emails"]
    assert "b@x.com" in rec["sender_emails"]
    assert "^INV-G-" in rec["filename_patterns"]
    assert "(?i)gamma" in rec["filename_patterns"]
    # name overwrites
    assert rec["name"] == "Gamma Renamed"


def test_register_dedups_lists():
    pr.register(code="DELTA", name="D", sender_emails=["x@y.com"])
    pr.register(code="DELTA", name="D", sender_emails=["x@y.com"])
    rec = pr.get("DELTA")
    assert rec["sender_emails"].count("x@y.com") == 1


def test_get_unknown_returns_none():
    assert pr.get("NONEXISTENT") is None
    assert pr.get("") is None


def test_list_all_active_only_filter():
    pr.register(code="A", name="A")
    pr.register(code="B", name="B", active=False)
    active = pr.list_all(active_only=True)
    all_ = pr.list_all(active_only=False)
    codes_active = {r["code"] for r in active}
    codes_all = {r["code"] for r in all_}
    assert codes_active == {"A"}
    assert codes_all == {"A", "B"}


def test_forget_removes_entry():
    pr.register(code="DROP", name="Drop me")
    assert pr.forget("DROP") is True
    assert pr.get("DROP") is None
    assert pr.forget("DROP") is False  # second call no-op


def test_increment_invoice_count():
    pr.register(code="INC", name="Inc")
    pr.increment_invoice_count("INC", 3)
    pr.increment_invoice_count("INC", 1)
    assert pr.get("INC")["invoice_count"] == 4


def test_increment_unknown_is_noop():
    # Should not raise.
    pr.increment_invoice_count("MISSING", 5)
    assert pr.get("MISSING") is None


def test_clear_drops_all():
    pr.register(code="A", name="A")
    pr.register(code="B", name="B")
    n = pr.clear()
    assert n == 2
    assert pr.list_all(active_only=False) == []


# --------------------------------------------------------------------------- #
# Resolution ladder
# --------------------------------------------------------------------------- #


def test_resolve_explicit_known_code():
    pr.register(code="ALPHA", name="Alpha")
    rr = pr.resolve(project_code_hint="ALPHA")
    assert rr.project_code == "ALPHA"
    assert rr.tier == "explicit"
    assert rr.confidence == pr.CONF_EXPLICIT


def test_resolve_explicit_unknown_code_still_returned():
    """Caller-passed codes win even if not in registry — but with lower conf."""
    rr = pr.resolve(project_code_hint="UNKNOWN")
    assert rr.project_code == "UNKNOWN"
    assert rr.tier == "explicit"
    assert rr.confidence < pr.CONF_EXPLICIT


def test_resolve_filename_pattern():
    pr.register(
        code="ALPHA", name="Alpha",
        filename_patterns=[r"^INV-ALPHA-"],
    )
    rr = pr.resolve(filename="INV-ALPHA-1234.pdf")
    assert rr.project_code == "ALPHA"
    assert rr.tier == "filename"
    assert rr.confidence == pr.CONF_FILENAME


def test_resolve_sender_email_match():
    pr.register(
        code="BETA", name="Beta",
        sender_emails=["pm@subcontractor.com"],
    )
    rr = pr.resolve(sender_email="PM@Subcontractor.COM")  # case-insensitive
    assert rr.project_code == "BETA"
    assert rr.tier == "sender"


def test_resolve_sender_with_rfc_format():
    pr.register(
        code="GAMMA", name="Gamma",
        sender_emails=["billing@vendor.io"],
    )
    rr = pr.resolve(sender_email="Billing Dept <billing@vendor.io>")
    assert rr.project_code == "GAMMA"


def test_resolve_chat_space():
    pr.register(
        code="DELTA", name="Delta",
        chat_space_ids=["spaces/AAQA1234"],
    )
    rr = pr.resolve(chat_space_id="spaces/AAQA1234")
    assert rr.project_code == "DELTA"
    assert rr.tier == "chat_space"


def test_resolve_filename_priority_over_sender():
    """When both rules match different projects, filename wins."""
    pr.register(
        code="ALPHA", name="Alpha",
        filename_patterns=[r"^INV-ALPHA-"],
    )
    pr.register(
        code="BETA", name="Beta",
        sender_emails=["pm@x.com"],
    )
    rr = pr.resolve(
        filename="INV-ALPHA-99.pdf",
        sender_email="pm@x.com",
    )
    assert rr.project_code == "ALPHA"
    assert rr.tier == "filename"


def test_resolve_no_rules_no_llm():
    pr.register(code="A", name="A")
    rr = pr.resolve(use_llm=False)
    assert rr.project_code is None
    assert rr.tier == "unresolved"


def test_resolve_empty_registry():
    rr = pr.resolve(filename="INV-X-1.pdf", sender_email="a@b.com")
    assert rr.project_code is None
    assert rr.tier == "unresolved"
    assert "no_projects_registered" in rr.reason


def test_resolve_bad_regex_skipped_silently():
    """A malformed regex in the registry shouldn't crash resolve."""
    pr.register(
        code="BAD", name="Bad",
        filename_patterns=["[unclosed"],
    )
    pr.register(
        code="OK", name="Ok",
        filename_patterns=[r"^INV-OK-"],
    )
    rr = pr.resolve(filename="INV-OK-1.pdf")
    assert rr.project_code == "OK"


def test_resolve_threshold_default():
    """RESOLVE_THRESHOLD is the gate above which we honor a resolution."""
    assert 0 < pr.RESOLVE_THRESHOLD <= 1
    # Filename + sender + chat tiers all sit above the threshold.
    assert pr.CONF_FILENAME >= pr.RESOLVE_THRESHOLD
    assert pr.CONF_SENDER >= pr.RESOLVE_THRESHOLD
    assert pr.CONF_CHAT >= pr.RESOLVE_THRESHOLD


def test_resolve_result_as_dict():
    rr = pr.ResolveResult(
        project_code="X", confidence=0.876, tier="filename",
        reason="match",
    )
    d = rr.as_dict()
    assert d["project_code"] == "X"
    assert d["confidence"] == 0.88  # rounded


def test_register_default_billable_and_markup():
    rec = pr.register(
        code="MK", name="Markup test",
        default_billable=False, default_markup_pct=15.5,
    )
    assert rec["default_billable"] is False
    assert rec["default_markup_pct"] == 15.5


def test_register_persists_sheet_id():
    pr.register(code="S", name="Sheet test", sheet_id="abc123",
                sheet_name="Project Invoices — S — Sheet test")
    rec = pr.get("S")
    assert rec["sheet_id"] == "abc123"
    assert rec["sheet_name"].endswith("Sheet test")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
