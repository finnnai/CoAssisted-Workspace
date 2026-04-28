"""Tests for the recent_actions JSONL audit log."""

import json
import tempfile
from pathlib import Path

import pytest

import recent_actions as ra


@pytest.fixture
def tmp_log(monkeypatch):
    """Redirect the log file to a tempfile per-test."""
    path = Path(tempfile.mktemp(suffix=".jsonl"))
    monkeypatch.setattr(ra, "_LOG_PATH", path)
    yield path
    if path.exists():
        path.unlink()


def test_record_and_list_recent(tmp_log):
    rid1 = ra.record(
        tool="t1", action="create", target_kind="x", target_id="1",
        summary="first",
    )
    rid2 = ra.record(
        tool="t2", action="update", target_kind="y", target_id="2",
        summary="second",
    )
    assert rid1 != rid2
    items = ra.list_recent(limit=10)
    assert len(items) == 2
    # Newest-first
    assert items[0]["id"] == rid2
    assert items[1]["id"] == rid1


def test_filters(tmp_log):
    ra.record(tool="alpha", action="x", target_kind="cat", target_id="1", summary="a")
    ra.record(tool="beta", action="x", target_kind="dog", target_id="2", summary="b")
    ra.record(tool="alpha", action="x", target_kind="cat", target_id="3", summary="c")

    cats = ra.list_recent(target_kind_filter="cat")
    assert len(cats) == 2
    assert all(r["target_kind"] == "cat" for r in cats)

    alphas = ra.list_recent(tool_filter="alpha")
    assert len(alphas) == 2
    assert all(r["tool"] == "alpha" for r in alphas)


def test_get_action_by_id(tmp_log):
    rid = ra.record(
        tool="t", action="x", target_kind="k", target_id="1",
        summary="s",
    )
    found = ra.get_action(rid)
    assert found is not None
    assert found["id"] == rid
    assert ra.get_action("does-not-exist") is None


def test_mark_reverted_excludes_from_revertable(tmp_log):
    rid = ra.record(tool="t", action="x", target_kind="k",
                    target_id="1", summary="s")
    assert ra.mark_reverted(rid, "revert-uuid") is True

    # The original should now be marked reverted.
    assert ra.get_action(rid)["reverted"] is True

    # And only_revertable should exclude it.
    items = ra.list_recent(only_revertable=True)
    assert len(items) == 0


def test_only_revertable_excludes_revert_records(tmp_log):
    # Original
    orig = ra.record(tool="t", action="x", target_kind="k",
                     target_id="1", summary="orig")
    # A revert pointing at it
    ra.record(tool="t", action="x", target_kind="k", target_id="1",
              summary="revert-of-orig", revert_target_action_id=orig)

    items_all = ra.list_recent(limit=10)
    assert len(items_all) == 2

    items_rev = ra.list_recent(only_revertable=True)
    # The revert record itself is excluded; the original is still present
    # (it hasn't been mark_reverted yet).
    assert len(items_rev) == 1
    assert items_rev[0]["id"] == orig


def test_jsonl_corruption_tolerated(tmp_log):
    """If the log file has a bad line, list_recent skips it instead of crashing."""
    ra.record(tool="t", action="x", target_kind="k", target_id="1", summary="ok")
    # Append garbage
    with tmp_log.open("a") as f:
        f.write("{not valid json\n")
    # Append another good record
    ra.record(tool="t", action="x", target_kind="k", target_id="2", summary="ok2")

    items = ra.list_recent(limit=10)
    # 2 valid, 1 garbage skipped
    assert len(items) == 2
    assert {r["target_id"] for r in items} == {"1", "2"}
