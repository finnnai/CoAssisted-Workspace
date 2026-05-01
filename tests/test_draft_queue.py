# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Tests for the draft queue core."""

from __future__ import annotations

from pathlib import Path

import pytest

import draft_queue as dq


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path):
    dq._override_path_for_tests(tmp_path / "draft_queue.json")
    yield
    dq._override_path_for_tests(
        Path(__file__).resolve().parent.parent / "draft_queue.json",
    )


# --------------------------------------------------------------------------- #
# enqueue + get
# --------------------------------------------------------------------------- #


def test_enqueue_returns_id_and_persists():
    eid = dq.enqueue(
        kind="reply", subject="Re: hi", body_plain="Hello",
        target="x@y.com",
    )
    assert eid
    rec = dq.get(eid)
    assert rec is not None
    assert rec["status"] == dq.STATUS_PENDING
    assert rec["subject"] == "Re: hi"
    assert rec["to"] == ["x@y.com"]


def test_enqueue_with_list_target():
    eid = dq.enqueue(
        kind="meeting", subject="Sync", body_plain="see thread",
        target=["a@x.com", "b@x.com"],
    )
    rec = dq.get(eid)
    assert rec["to"] == ["a@x.com", "b@x.com"]


def test_enqueue_auto_html_from_plain():
    eid = dq.enqueue(
        kind="reply", subject="x", body_plain="line1\nline2", target="x@y.com",
    )
    rec = dq.get(eid)
    assert "<br>" in rec["body_html"]


def test_enqueue_requires_kind():
    with pytest.raises(ValueError):
        dq.enqueue(kind="", subject="x", body_plain="y", target="z@x.com")


def test_enqueue_requires_subject_or_body():
    with pytest.raises(ValueError):
        dq.enqueue(kind="x", subject="", body_plain="", target="z@x.com")


# --------------------------------------------------------------------------- #
# list
# --------------------------------------------------------------------------- #


def test_list_pending_only_returns_pending():
    eid1 = dq.enqueue(kind="a", subject="s1", body_plain="b1", target="x@y.com")
    eid2 = dq.enqueue(kind="a", subject="s2", body_plain="b2", target="x@y.com")
    dq.approve(eid1)
    pending = dq.list_pending()
    assert len(pending) == 1
    assert pending[0]["id"] == eid2


def test_list_pending_filters_by_kind():
    dq.enqueue(kind="reply", subject="r", body_plain="b", target="x@y.com")
    dq.enqueue(kind="agenda", subject="a", body_plain="b", target="x@y.com")
    out = dq.list_pending(kind="agenda")
    assert len(out) == 1
    assert out[0]["kind"] == "agenda"


# --------------------------------------------------------------------------- #
# approve / discard / mark_sent
# --------------------------------------------------------------------------- #


def test_approve_changes_status():
    eid = dq.enqueue(kind="x", subject="s", body_plain="b", target="x@y.com")
    rec = dq.approve(eid)
    assert rec["status"] == dq.STATUS_APPROVED
    assert rec["decided_at"]


def test_approve_returns_none_for_unknown():
    assert dq.approve("missing") is None


def test_approve_idempotent_returns_none_after_first():
    eid = dq.enqueue(kind="x", subject="s", body_plain="b", target="x@y.com")
    dq.approve(eid)
    # Re-approving is a no-op
    assert dq.approve(eid) is None


def test_discard_changes_status():
    eid = dq.enqueue(kind="x", subject="s", body_plain="b", target="x@y.com")
    assert dq.discard(eid) is True
    rec = dq.get(eid)
    assert rec["status"] == dq.STATUS_DISCARDED


def test_discard_unknown_returns_false():
    assert dq.discard("missing") is False


def test_mark_sent_only_after_approve():
    eid = dq.enqueue(kind="x", subject="s", body_plain="b", target="x@y.com")
    # Before approve: mark_sent is a no-op
    assert dq.mark_sent(eid) is None
    dq.approve(eid)
    rec = dq.mark_sent(eid)
    assert rec["status"] == dq.STATUS_SENT
    assert rec["sent_at"]


# --------------------------------------------------------------------------- #
# update_body
# --------------------------------------------------------------------------- #


def test_update_subject_and_body():
    eid = dq.enqueue(
        kind="x", subject="old", body_plain="old body", target="x@y.com",
    )
    updated = dq.update_body(
        eid, subject="new", body_plain="new\nmultiline body",
    )
    assert updated["subject"] == "new"
    assert updated["body_plain"] == "new\nmultiline body"
    assert "<br>" in updated["body_html"]  # auto-regen html on newlines


def test_update_only_after_approve_blocked():
    eid = dq.enqueue(kind="x", subject="s", body_plain="b", target="x@y.com")
    dq.approve(eid)
    assert dq.update_body(eid, subject="changed") is None


# --------------------------------------------------------------------------- #
# clear
# --------------------------------------------------------------------------- #


def test_clear_drops_everything():
    dq.enqueue(kind="x", subject="s1", body_plain="b1", target="x@y.com")
    dq.enqueue(kind="x", subject="s2", body_plain="b2", target="x@y.com")
    n = dq.clear()
    assert n == 2
    assert dq.list_all() == []


def test_clear_by_status():
    e1 = dq.enqueue(kind="x", subject="s1", body_plain="b1", target="x@y.com")
    e2 = dq.enqueue(kind="x", subject="s2", body_plain="b2", target="x@y.com")
    dq.discard(e1)
    n = dq.clear_by_status(dq.STATUS_DISCARDED)
    assert n == 1
    assert len(dq.list_all()) == 1
    assert dq.list_all()[0]["id"] == e2


# --------------------------------------------------------------------------- #
# Atomic write
# --------------------------------------------------------------------------- #


def test_atomic_write_no_leftover_tmp(tmp_path):
    dq.enqueue(kind="x", subject="s", body_plain="b", target="x@y.com")
    leftover = list(tmp_path.glob("*.tmp"))
    assert leftover == []
