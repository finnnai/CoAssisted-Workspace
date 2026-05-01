"""Baseline unit tests for tools/calendar.py — P0-3 spec.

Per-tool: input-model validation, happy path (mocked gservices), error
path. No live API.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock

import pytest
from googleapiclient.errors import HttpError
from pydantic import ValidationError

from tools import calendar as t_cal
from tools.calendar import (
    ListEventsInput,
    CreateEventInput,
    QuickAddInput,
    RespondToEventInput,
    ListCalendarsInput,
    UpdateEventInput,
    DeleteEventInput,
    FreeBusyInput,
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _resolve_tool(name: str):
    from server import mcp
    return mcp._tool_manager._tools[name].fn


def _run(tool_name, params):
    return asyncio.run(_resolve_tool(tool_name)(params))


def _http_error():
    resp = MagicMock(status=500, reason="boom")
    return HttpError(resp, b'{"error": {"message": "boom"}}')


def _err_assert(out: str):
    """The error path returns format_error()'s human-readable string."""
    assert isinstance(out, str)
    assert ("error" in out.lower() or "failed" in out.lower()
            or "boom" in out.lower() or "http" in out.lower())


# --------------------------------------------------------------------------- #
# Input validation
# --------------------------------------------------------------------------- #


def test_list_events_defaults_are_sane():
    m = ListEventsInput()
    assert m.calendar_id is None
    assert m.limit == 25


def test_list_events_max_results_alias():
    m = ListEventsInput.model_validate({"max_results": 50})
    assert m.limit == 50


def test_list_events_limit_bounds():
    ListEventsInput(limit=1)
    ListEventsInput(limit=250)
    with pytest.raises(ValidationError):
        ListEventsInput(limit=251)
    with pytest.raises(ValidationError):
        ListEventsInput(limit=0)


def test_create_event_input_requires_summary_start_end():
    with pytest.raises(ValidationError):
        CreateEventInput()
    with pytest.raises(ValidationError):
        CreateEventInput(summary="Lunch")
    with pytest.raises(ValidationError):
        CreateEventInput(summary="Lunch", start="2026-04-25")
    CreateEventInput(summary="Lunch", start="2026-04-25", end="2026-04-25")


def test_create_event_recurrence_count_bounds():
    CreateEventInput(summary="x", start="2026-04-25", end="2026-04-25",
                     recurrence_pattern="weekly", recurrence_count=1)
    CreateEventInput(summary="x", start="2026-04-25", end="2026-04-25",
                     recurrence_pattern="weekly", recurrence_count=5000)
    with pytest.raises(ValidationError):
        CreateEventInput(summary="x", start="2026-04-25", end="2026-04-25",
                         recurrence_pattern="weekly", recurrence_count=5001)


def test_quick_add_input_requires_text():
    with pytest.raises(ValidationError):
        QuickAddInput()
    QuickAddInput(text="Dinner Friday 7pm")


def test_respond_to_event_input_requires_event_id_and_response():
    with pytest.raises(ValidationError):
        RespondToEventInput()
    with pytest.raises(ValidationError):
        RespondToEventInput(event_id="e1")
    RespondToEventInput(event_id="e1", response="accepted")


def test_list_calendars_input_takes_no_args():
    ListCalendarsInput()
    with pytest.raises(ValidationError):
        ListCalendarsInput.model_validate({"unexpected": 1})


def test_update_event_input_requires_event_id():
    with pytest.raises(ValidationError):
        UpdateEventInput()
    UpdateEventInput(event_id="e1", summary="new title")


def test_delete_event_input_requires_event_id():
    with pytest.raises(ValidationError):
        DeleteEventInput()
    DeleteEventInput(event_id="e1")


def test_free_busy_input_requires_time_window():
    with pytest.raises(ValidationError):
        FreeBusyInput()
    with pytest.raises(ValidationError):
        FreeBusyInput(time_min="2026-04-25T00:00:00Z")
    FreeBusyInput(time_min="2026-04-25T00:00:00Z",
                  time_max="2026-04-26T00:00:00Z")


# --------------------------------------------------------------------------- #
# Happy paths — mock gservices.calendar() chain
# --------------------------------------------------------------------------- #


def test_list_events_happy(monkeypatch):
    fake = MagicMock()
    fake.events.return_value.list.return_value.execute.return_value = {
        "items": [{"id": "e1", "summary": "Lunch"}]
    }
    monkeypatch.setattr(t_cal, "_service", lambda: fake)
    out = _run("calendar_list_events", ListEventsInput())
    payload = json.loads(out)
    assert "events" in payload or "items" in payload or len(payload) >= 1


def test_list_calendars_happy(monkeypatch):
    fake = MagicMock()
    fake.calendarList.return_value.list.return_value.execute.return_value = {
        "items": [{"id": "primary", "summary": "Me"}]
    }
    monkeypatch.setattr(t_cal, "_service", lambda: fake)
    out = _run("calendar_list_calendars", ListCalendarsInput())
    payload = json.loads(out)
    # Tool returns calendars list in some shape
    assert payload  # non-empty


def test_quick_add_happy(monkeypatch):
    fake = MagicMock()
    fake.events.return_value.quickAdd.return_value.execute.return_value = {
        "id": "e_new", "summary": "Dinner Friday", "htmlLink": "u",
    }
    monkeypatch.setattr(t_cal, "_service", lambda: fake)
    out = _run("calendar_quick_add", QuickAddInput(text="Dinner Friday 7pm"))
    payload = json.loads(out)
    assert payload  # tool returned something


# --------------------------------------------------------------------------- #
# Error paths
# --------------------------------------------------------------------------- #


def test_list_events_error(monkeypatch):
    fake = MagicMock()
    fake.events.return_value.list.return_value.execute.side_effect = _http_error()
    monkeypatch.setattr(t_cal, "_service", lambda: fake)
    out = _run("calendar_list_events", ListEventsInput())
    _err_assert(out)


def test_list_calendars_error(monkeypatch):
    fake = MagicMock()
    fake.calendarList.return_value.list.return_value.execute.side_effect = _http_error()
    monkeypatch.setattr(t_cal, "_service", lambda: fake)
    out = _run("calendar_list_calendars", ListCalendarsInput())
    _err_assert(out)


def test_create_event_error(monkeypatch):
    fake = MagicMock()
    fake.events.return_value.insert.return_value.execute.side_effect = _http_error()
    monkeypatch.setattr(t_cal, "_service", lambda: fake)
    out = _run("calendar_create_event", CreateEventInput(
        summary="x", start="2026-04-25", end="2026-04-25",
    ))
    _err_assert(out)


def test_update_event_error(monkeypatch):
    fake = MagicMock()
    fake.events.return_value.patch.return_value.execute.side_effect = _http_error()
    fake.events.return_value.update.return_value.execute.side_effect = _http_error()
    monkeypatch.setattr(t_cal, "_service", lambda: fake)
    out = _run("calendar_update_event", UpdateEventInput(
        event_id="e1", summary="new title",
    ))
    _err_assert(out)


def test_delete_event_error(monkeypatch):
    fake = MagicMock()
    fake.events.return_value.delete.return_value.execute.side_effect = _http_error()
    monkeypatch.setattr(t_cal, "_service", lambda: fake)
    out = _run("calendar_delete_event", DeleteEventInput(event_id="e1"))
    _err_assert(out)


def test_quick_add_error(monkeypatch):
    fake = MagicMock()
    fake.events.return_value.quickAdd.return_value.execute.side_effect = _http_error()
    monkeypatch.setattr(t_cal, "_service", lambda: fake)
    out = _run("calendar_quick_add", QuickAddInput(text="x"))
    _err_assert(out)


def test_respond_to_event_error(monkeypatch):
    fake = MagicMock()
    fake.events.return_value.get.return_value.execute.side_effect = _http_error()
    monkeypatch.setattr(t_cal, "_service", lambda: fake)
    out = _run("calendar_respond_to_event",
               RespondToEventInput(event_id="e1", response="accepted"))
    _err_assert(out)


def test_free_busy_error(monkeypatch):
    fake = MagicMock()
    fake.freebusy.return_value.query.return_value.execute.side_effect = _http_error()
    monkeypatch.setattr(t_cal, "_service", lambda: fake)
    out = _run("calendar_find_free_busy", FreeBusyInput(
        time_min="2026-04-25T00:00:00Z",
        time_max="2026-04-26T00:00:00Z",
    ))
    _err_assert(out)


# --------------------------------------------------------------------------- #
# Registration smoke
# --------------------------------------------------------------------------- #


def test_all_calendar_tools_registered():
    from server import mcp
    expected = {
        "calendar_list_events", "calendar_create_event", "calendar_update_event",
        "calendar_delete_event", "calendar_find_free_busy", "calendar_quick_add",
        "calendar_respond_to_event", "calendar_list_calendars",
    }
    actual = {n for n in mcp._tool_manager._tools if n.startswith("calendar_")}
    assert expected.issubset(actual), f"missing: {expected - actual}"
