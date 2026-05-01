"""Baseline unit tests for tools/sheets.py — P0-3 spec."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock

import pytest
from googleapiclient.errors import HttpError
from pydantic import ValidationError

from tools import sheets as t_sheets
from tools.sheets import (
    CreateSheetInput, ReadRangeInput, WriteRangeInput, AppendRowsInput,
    AddSheetInput, DeleteSheetInput, ListSheetsInput,
)


def _resolve(name):
    from server import mcp
    return mcp._tool_manager._tools[name].fn


def _run(name, params):
    return asyncio.run(_resolve(name)(params))


def _http_error():
    return HttpError(MagicMock(status=500, reason="boom"),
                     b'{"error": {"message": "boom"}}')


def _err_assert(out):
    assert isinstance(out, str)
    assert ("error" in out.lower() or "failed" in out.lower()
            or "boom" in out.lower() or "http" in out.lower())


# Input validation
def test_create_sheet_requires_title():
    with pytest.raises(ValidationError):
        CreateSheetInput()
    CreateSheetInput(title="Budget 2026")


def test_read_range_requires_id_and_range():
    with pytest.raises(ValidationError):
        ReadRangeInput()
    with pytest.raises(ValidationError):
        ReadRangeInput(spreadsheet_id="ss1")
    ReadRangeInput(spreadsheet_id="ss1", range="A1:B10")


def test_write_range_requires_id_range_values():
    with pytest.raises(ValidationError):
        WriteRangeInput()
    with pytest.raises(ValidationError):
        WriteRangeInput(spreadsheet_id="ss1", range="A1")
    WriteRangeInput(spreadsheet_id="ss1", range="A1", values=[["x"]])


def test_append_rows_requires_id_range_values():
    with pytest.raises(ValidationError):
        AppendRowsInput()
    AppendRowsInput(spreadsheet_id="ss1", range="A1", values=[["x"]])


def test_add_sheet_requires_id_and_title():
    with pytest.raises(ValidationError):
        AddSheetInput()
    with pytest.raises(ValidationError):
        AddSheetInput(spreadsheet_id="ss1")
    AddSheetInput(spreadsheet_id="ss1", title="New Tab")


def test_delete_sheet_requires_spreadsheet_id():
    with pytest.raises(ValidationError):
        DeleteSheetInput()
    # Either sheet_id or title is required at runtime, but Pydantic accepts
    # either omitted (the tool returns an error if neither is given).
    DeleteSheetInput(spreadsheet_id="ss1", sheet_id=0)


def test_list_sheets_requires_spreadsheet_id():
    with pytest.raises(ValidationError):
        ListSheetsInput()
    ListSheetsInput(spreadsheet_id="ss1")


# Error paths
def test_create_sheet_error(monkeypatch):
    fake = MagicMock()
    fake.spreadsheets.return_value.create.return_value.execute.side_effect = _http_error()
    monkeypatch.setattr(t_sheets, "_service", lambda: fake)
    _err_assert(_run("sheets_create_spreadsheet", CreateSheetInput(title="x")))


def test_read_range_error(monkeypatch):
    fake = MagicMock()
    fake.spreadsheets.return_value.values.return_value.get.return_value.execute.side_effect = _http_error()
    monkeypatch.setattr(t_sheets, "_service", lambda: fake)
    _err_assert(_run("sheets_read_range",
                     ReadRangeInput(spreadsheet_id="ss1", range="A1:B")))


def test_write_range_error(monkeypatch):
    fake = MagicMock()
    fake.spreadsheets.return_value.values.return_value.update.return_value.execute.side_effect = _http_error()
    monkeypatch.setattr(t_sheets, "_service", lambda: fake)
    _err_assert(_run("sheets_write_range",
                     WriteRangeInput(spreadsheet_id="ss1", range="A1", values=[["x"]])))


def test_append_rows_error(monkeypatch):
    fake = MagicMock()
    fake.spreadsheets.return_value.values.return_value.append.return_value.execute.side_effect = _http_error()
    monkeypatch.setattr(t_sheets, "_service", lambda: fake)
    _err_assert(_run("sheets_append_rows",
                     AppendRowsInput(spreadsheet_id="ss1", range="A1", values=[["x"]])))


def test_list_sheets_error(monkeypatch):
    fake = MagicMock()
    fake.spreadsheets.return_value.get.return_value.execute.side_effect = _http_error()
    monkeypatch.setattr(t_sheets, "_service", lambda: fake)
    _err_assert(_run("sheets_list_sheets", ListSheetsInput(spreadsheet_id="ss1")))


def test_all_sheets_tools_registered():
    from server import mcp
    expected = {"sheets_create_spreadsheet", "sheets_read_range",
                "sheets_write_range", "sheets_append_rows",
                "sheets_list_sheets", "sheets_add_sheet", "sheets_delete_sheet"}
    assert expected.issubset(set(mcp._tool_manager._tools))
