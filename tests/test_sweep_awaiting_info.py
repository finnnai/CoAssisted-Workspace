"""Tests for workflow_sweep_awaiting_info — list + bulk-clear stale entries."""
from __future__ import annotations

import asyncio
import datetime as dt
import json
from pathlib import Path

import pytest

import vendor_followups as vf
from tools import project_invoices as t_pi
from tools.project_invoices import SweepAwaitingInfoInput


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    """Each test gets a fresh awaiting_info store."""
    fresh = tmp_path / "awaiting_info.json"
    vf._override_path_for_tests(fresh)
    yield vf
    vf._override_path_for_tests(
        Path(__file__).resolve().parent.parent / "awaiting_info.json"
    )


def _seed(store, content_key, *, vendor_name, project_code, age_days, channel="gmail"):
    """Register an entry and rewrite its request_sent_at to be `age_days` old."""
    store.register_request(
        content_key=content_key,
        channel=channel,
        fields_requested=["invoice_number"],
        vendor_email=f"{vendor_name.lower().replace(' ','-')}@example.com",
        vendor_name=vendor_name,
        project_code=project_code,
        thread_id="t1",
        sheet_id="s1",
        row_number=2,
    )
    # Backdate
    data = store._load()
    backdated = (
        dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=age_days)
    ).isoformat()
    data[content_key]["request_sent_at"] = backdated
    store._save(data)


def _run_sweep(**kwargs):
    """Resolve the tool fn from the registered MCP and call it."""
    from server import mcp
    fn = mcp._tool_manager._tools["workflow_sweep_awaiting_info"].fn
    params = SweepAwaitingInfoInput(**kwargs)
    return json.loads(asyncio.run(fn(params)))


def test_sweep_lists_all_open_entries(isolated_store):
    _seed(isolated_store, "k1", vendor_name="Acme", project_code="ALPHA", age_days=5)
    _seed(isolated_store, "k2", vendor_name="Beta Co", project_code="BRAVO", age_days=20)

    result = _run_sweep()
    assert result["total_open"] == 2
    assert result["by_channel"]["gmail"] == 2
    keys = {e["content_key"] for e in result["entries"]}
    assert keys == {"k1", "k2"}


def test_sweep_filters_by_project_code(isolated_store):
    _seed(isolated_store, "k1", vendor_name="A", project_code="ALPHA", age_days=5)
    _seed(isolated_store, "k2", vendor_name="B", project_code="BRAVO", age_days=5)

    result = _run_sweep(project_code="ALPHA")
    assert result["total_open"] == 1
    assert result["entries"][0]["content_key"] == "k1"


def test_sweep_filters_by_channel(isolated_store):
    _seed(isolated_store, "k1", vendor_name="A", project_code="X", age_days=1, channel="gmail")
    _seed(isolated_store, "k2", vendor_name="B", project_code="X", age_days=1, channel="chat")

    result = _run_sweep(channel="chat")
    assert result["total_open"] == 1
    assert result["entries"][0]["channel"] == "chat"


def test_sweep_dry_run_lists_what_would_clear(isolated_store):
    _seed(isolated_store, "fresh", vendor_name="A", project_code="X", age_days=2)
    _seed(isolated_store, "stale", vendor_name="B", project_code="X", age_days=30)

    # Dry-run defaults to True when older_than_days set
    result = _run_sweep(older_than_days=14)
    assert result["dry_run"] is True
    assert result["would_clear_count"] == 1
    assert "stale" in result["would_clear"]
    assert "fresh" not in result["would_clear"]
    # Nothing actually cleared
    assert isolated_store.get("stale") is not None


def test_sweep_actual_clear_with_dry_run_false(isolated_store):
    _seed(isolated_store, "fresh", vendor_name="A", project_code="X", age_days=2)
    _seed(isolated_store, "stale", vendor_name="B", project_code="X", age_days=30)

    result = _run_sweep(older_than_days=14, dry_run=False)
    assert result["dry_run"] is False
    assert result["cleared_count"] == 1
    assert "stale" in result["cleared"]
    # Fresh is still there, stale is gone
    assert isolated_store.get("fresh") is not None
    assert isolated_store.get("stale") is None


def test_sweep_age_days_calculated(isolated_store):
    _seed(isolated_store, "k1", vendor_name="A", project_code="X", age_days=10)

    result = _run_sweep()
    assert result["entries"][0]["age_days"] is not None
    # Within 0.5 day tolerance for clock drift between seed & query
    assert 9.5 <= result["entries"][0]["age_days"] <= 10.5


def test_sweep_empty_store(isolated_store):
    result = _run_sweep()
    assert result["total_open"] == 0
    assert result["entries"] == []
    assert result["by_channel"] == {}
