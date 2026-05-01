# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Tests for the access audit pure-logic core."""

from __future__ import annotations

import pytest

import access_audit as core
import sender_classifier


@pytest.fixture(autouse=True)
def _set_user_domain(monkeypatch):
    """Lock the user-domain to surefox.com so internal classification is deterministic."""
    monkeypatch.setattr(sender_classifier, "_user_domain", lambda: "surefox.com")
    sender_classifier._reset_for_tests()
    yield
    sender_classifier._reset_for_tests()


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _perm(**kw) -> dict:
    """Build a synthetic permission dict with sensible defaults."""
    base = {"id": "perm1", "type": "user", "role": "reader",
            "emailAddress": "user@example.com",
            "displayName": "Some User"}
    base.update(kw)
    return base


# --------------------------------------------------------------------------- #
# Relationship classification
# --------------------------------------------------------------------------- #


def test_self_relationship():
    perm = _perm(emailAddress="finn@surefox.com")
    grant = core._classify_one(perm, authed_email="finn@surefox.com")
    assert grant.relationship == "self"


def test_internal_relationship_via_user_domain():
    perm = _perm(emailAddress="alex@surefox.com")
    grant = core._classify_one(perm, authed_email="finn@surefox.com")
    assert grant.relationship == "internal"


def test_subsidiary_relationship(monkeypatch):
    monkeypatch.setattr(sender_classifier, "_config_subsidiary_domains",
                        lambda: {"staffwizard.com", "xenture.com"})
    sender_classifier._reset_for_tests()
    perm = _perm(emailAddress="amanda.miller@staffwizard.com")
    grant = core._classify_one(perm, authed_email="finn@surefox.com")
    assert grant.relationship == "subsidiary"


def test_external_relationship():
    perm = _perm(emailAddress="random@gmail.com")
    grant = core._classify_one(perm, authed_email="finn@surefox.com")
    assert grant.relationship == "external"


def test_anyone_link_relationship():
    perm = _perm(type="anyone", emailAddress=None, role="reader")
    perm.pop("emailAddress")
    grant = core._classify_one(perm, authed_email="finn@surefox.com")
    assert grant.relationship == "public"
    assert grant.target == "anyone-with-link"


def test_domain_relationship():
    perm = _perm(type="domain", domain="example.com", emailAddress=None)
    perm.pop("emailAddress")
    grant = core._classify_one(perm, authed_email="finn@surefox.com")
    assert grant.relationship == "domain-wide"
    assert grant.target == "example.com"


# --------------------------------------------------------------------------- #
# Risk flags
# --------------------------------------------------------------------------- #


def test_anyone_with_link_reader_flag():
    perm = _perm(type="anyone", role="reader")
    perm.pop("emailAddress")
    grant = core._classify_one(perm, None)
    assert "anyone_with_link" in grant.risk_flags
    assert "public_writable" not in grant.risk_flags


def test_anyone_with_link_writer_double_flag():
    perm = _perm(type="anyone", role="writer")
    perm.pop("emailAddress")
    grant = core._classify_one(perm, None)
    assert "anyone_with_link" in grant.risk_flags
    assert "public_writable" in grant.risk_flags


def test_external_writer_flag():
    perm = _perm(emailAddress="random@gmail.com", role="writer")
    grant = core._classify_one(perm, "finn@surefox.com")
    assert "external_writer" in grant.risk_flags


def test_external_owner_flag():
    perm = _perm(emailAddress="random@gmail.com", role="owner")
    grant = core._classify_one(perm, "finn@surefox.com")
    assert "external_owner" in grant.risk_flags


def test_internal_writer_no_flag():
    perm = _perm(emailAddress="alex@surefox.com", role="writer")
    grant = core._classify_one(perm, "finn@surefox.com")
    assert grant.risk_flags == []


def test_deleted_account_flag():
    perm = _perm(emailAddress="ghost@example.com", role="reader", deleted=True)
    grant = core._classify_one(perm, "finn@surefox.com")
    assert "deleted_account" in grant.risk_flags


def test_domain_writable_flag():
    perm = _perm(type="domain", domain="example.com", role="writer")
    perm.pop("emailAddress")
    grant = core._classify_one(perm, "finn@surefox.com")
    assert "domain_writable" in grant.risk_flags


# --------------------------------------------------------------------------- #
# Risk score aggregation
# --------------------------------------------------------------------------- #


def test_risk_score_sums_weights():
    perms = [
        _perm(id="p1", type="anyone", role="writer"),  # 50 + 20
        _perm(id="p2", emailAddress="random@gmail.com", role="writer"),  # 25
        _perm(id="p3", emailAddress="ghost@x.com", role="reader", deleted=True),  # 5
    ]
    for p in perms:
        if p["type"] == "anyone":
            p.pop("emailAddress", None)
    report = core.summarize_permissions(
        file_id="file1", file_name="Test", permissions=perms,
        authed_email="finn@surefox.com",
    )
    # 50 + 20 + 25 + 5 = 100
    assert report.risk_score == 100


def test_zero_risk_score_for_clean_share():
    perms = [
        _perm(id="p1", emailAddress="finn@surefox.com", role="owner"),
        _perm(id="p2", emailAddress="alex@surefox.com", role="writer"),
        _perm(id="p3", emailAddress="sam@surefox.com", role="reader"),
    ]
    report = core.summarize_permissions(
        file_id="file1", file_name="Internal Doc",
        permissions=perms, authed_email="finn@surefox.com",
    )
    assert report.risk_score == 0


# --------------------------------------------------------------------------- #
# Sorting
# --------------------------------------------------------------------------- #


def test_grants_sorted_owner_first_external_first():
    perms = [
        _perm(id="p1", emailAddress="alex@surefox.com", role="reader"),
        _perm(id="p2", emailAddress="random@gmail.com", role="writer"),
        _perm(id="p3", emailAddress="finn@surefox.com", role="owner"),
    ]
    report = core.summarize_permissions(
        file_id="x", file_name="x", permissions=perms,
        authed_email="finn@surefox.com",
    )
    # Sort key: role rank (owner=0 first), then external/public/etc relationship.
    # So order should be: finn (owner), random (external writer), alex (internal reader)
    targets = [g.target for g in report.grants]
    assert targets[0] == "finn@surefox.com"
    assert targets[1] == "random@gmail.com"
    assert targets[2] == "alex@surefox.com"


# --------------------------------------------------------------------------- #
# Summary counts
# --------------------------------------------------------------------------- #


def test_summary_counts_by_relationship_and_role():
    perms = [
        _perm(id="p1", emailAddress="finn@surefox.com", role="owner"),
        _perm(id="p2", emailAddress="alex@surefox.com", role="writer"),
        _perm(id="p3", emailAddress="sam@surefox.com", role="reader"),
        _perm(id="p4", emailAddress="random@gmail.com", role="reader"),
        _perm(id="p5", type="anyone", role="reader"),
    ]
    perms[-1].pop("emailAddress")
    report = core.summarize_permissions(
        file_id="x", file_name=None, permissions=perms,
        authed_email="finn@surefox.com",
    )
    rel = report.summary["by_relationship"]
    assert rel.get("self") == 1
    assert rel.get("internal") == 2
    assert rel.get("external") == 1
    assert rel.get("public") == 1
    role = report.summary["by_role"]
    assert role.get("owner") == 1
    assert role.get("writer") == 1
    assert role.get("reader") == 3
    assert "anyone_with_link" in report.summary["risk_flags"]


# --------------------------------------------------------------------------- #
# Diff
# --------------------------------------------------------------------------- #


def test_diff_added_and_removed():
    before_perms = [_perm(id="p1", emailAddress="alex@surefox.com", role="reader")]
    after_perms = [_perm(id="p2", emailAddress="brian@xenture.com", role="reader")]
    before = core.summarize_permissions("x", "x", before_perms, "finn@surefox.com")
    after = core.summarize_permissions("x", "x", after_perms, "finn@surefox.com")
    diff = core.diff_reports(before, after)
    assert len(diff.added) == 1
    assert diff.added[0].target == "brian@xenture.com"
    assert len(diff.removed) == 1
    assert diff.removed[0].target == "alex@surefox.com"
    assert diff.changed_role == []


def test_diff_role_change():
    before_perms = [_perm(id="p1", emailAddress="alex@surefox.com", role="reader")]
    after_perms = [_perm(id="p1", emailAddress="alex@surefox.com", role="writer")]
    before = core.summarize_permissions("x", "x", before_perms, "finn@surefox.com")
    after = core.summarize_permissions("x", "x", after_perms, "finn@surefox.com")
    diff = core.diff_reports(before, after)
    assert diff.added == []
    assert diff.removed == []
    assert len(diff.changed_role) == 1
    b, a = diff.changed_role[0]
    assert b.role == "reader" and a.role == "writer"


def test_diff_no_changes_signal():
    perms = [_perm(id="p1", emailAddress="alex@surefox.com", role="reader")]
    before = core.summarize_permissions("x", "x", perms, "finn@surefox.com")
    after = core.summarize_permissions("x", "x", perms, "finn@surefox.com")
    diff = core.diff_reports(before, after)
    assert not diff.to_dict()["any_changes"]


# --------------------------------------------------------------------------- #
# Output shape
# --------------------------------------------------------------------------- #


def test_to_dict_serializes_full_report():
    perms = [_perm(id="p1", emailAddress="finn@surefox.com", role="owner")]
    report = core.summarize_permissions("file1", "Test", perms, "finn@surefox.com")
    d = report.to_dict()
    assert d["file_id"] == "file1"
    assert d["file_name"] == "Test"
    assert d["grant_count"] == 1
    assert "summary" in d
    assert "grants" in d
    assert d["grants"][0]["relationship"] == "self"
