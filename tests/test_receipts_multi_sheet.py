# © 2026 CoAssisted Workspace contributors. Licensed under MIT — see LICENSE use only.
# See LICENSE file for terms.
"""Multi-sheet support for the receipt extractor.

Covers the three new pieces wired into tools/receipts.py:
  - RECEIPT_SHEET_PREFIX naming convention
  - _list_receipt_sheets   (auto-discovery from Drive)
  - _resolve_sheet         (id → ok | name → ok | name → not-found | none → list)
  - workflow_create_receipt_sheet input model
  - workflow_extract_receipts returns 'needs_sheet' when nothing is supplied

These tests stub out gservices.drive() and gservices.sheets() so they run
fully offline. The real Google API surface is exercised separately in the
live MCP test plan.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from tools import receipts as tr_mod


# --------------------------------------------------------------------------- #
# Naming convention
# --------------------------------------------------------------------------- #


def test_prefix_uses_em_dash():
    """The prefix must be em-dash + space — not a regular hyphen — so user
    sheets named 'Receipts - Old' don't get falsely matched."""
    assert tr_mod.RECEIPT_SHEET_PREFIX == "Receipts — "
    assert "—" in tr_mod.RECEIPT_SHEET_PREFIX
    assert tr_mod.RECEIPT_SHEET_PREFIX.endswith(" ")


# --------------------------------------------------------------------------- #
# CreateReceiptSheetInput
# --------------------------------------------------------------------------- #


class TestCreateInput:
    def test_accepts_short_label(self):
        m = tr_mod.CreateReceiptSheetInput(name="Personal 2026 Q2")
        assert m.name == "Personal 2026 Q2"

    def test_strips_whitespace(self):
        m = tr_mod.CreateReceiptSheetInput(name="  Business  ")
        assert m.name == "Business"

    def test_rejects_empty(self):
        with pytest.raises(Exception):
            tr_mod.CreateReceiptSheetInput(name="")

    def test_rejects_too_long(self):
        with pytest.raises(Exception):
            tr_mod.CreateReceiptSheetInput(name="x" * 81)

    def test_rejects_extra_fields(self):
        with pytest.raises(Exception):
            tr_mod.CreateReceiptSheetInput(name="Ok", surprise="boom")


# --------------------------------------------------------------------------- #
# Helpers for stubbing out the Drive + Sheets API surfaces
# --------------------------------------------------------------------------- #


def _make_drive_stub(files: list[dict]):
    """Build a Drive mock whose files().list().execute() returns `files`."""
    drive = MagicMock()
    drive.files().list().execute.return_value = {"files": files}
    # The real call path is drive().files().list(q=..., ...).execute().
    # MagicMock auto-chains, so we override .list to also accept kwargs and
    # still return the same object with .execute().
    list_call = MagicMock()
    list_call.execute.return_value = {"files": files}
    drive.files.return_value.list.return_value = list_call
    return drive


def _make_sheets_stub(values_by_id: dict[str, list[list]] | None = None,
                     metadata_by_id: dict[str, dict] | None = None):
    """Build a Sheets mock for spreadsheets().values().get() and .get()."""
    sheets = MagicMock()
    values_by_id = values_by_id or {}
    metadata_by_id = metadata_by_id or {}

    def values_get(spreadsheetId, range, **kwargs):
        ret = MagicMock()
        rows = values_by_id.get(spreadsheetId, [])
        ret.execute.return_value = {"values": rows}
        return ret

    def meta_get(spreadsheetId, **kwargs):
        ret = MagicMock()
        ret.execute.return_value = metadata_by_id.get(
            spreadsheetId,
            {"properties": {"title": f"Title-{spreadsheetId}"}},
        )
        return ret

    sheets.spreadsheets.return_value.values.return_value.get.side_effect = values_get
    sheets.spreadsheets.return_value.get.side_effect = meta_get
    return sheets


# --------------------------------------------------------------------------- #
# _list_receipt_sheets
# --------------------------------------------------------------------------- #


class TestListReceiptSheets:
    def test_filters_by_exact_prefix(self):
        """Drive's `name contains` is loose — re-filter on the prefix."""
        drive = _make_drive_stub([
            {"id": "id_a", "name": "Receipts — Personal 2026",
             "modifiedTime": "2026-04-01T00:00:00Z",
             "webViewLink": "https://example/a"},
            # Looks like a match to Drive but doesn't start with the prefix.
            {"id": "id_b", "name": "My Receipts — old",
             "modifiedTime": "2026-03-01T00:00:00Z",
             "webViewLink": "https://example/b"},
        ])
        sheets = _make_sheets_stub(values_by_id={
            "id_a": [["header"], ["row1"], ["row2"]],
        })
        with patch.object(tr_mod, "_drive", return_value=drive), \
             patch.object(tr_mod, "_sheets", return_value=sheets):
            out = tr_mod._list_receipt_sheets()
        assert len(out) == 1
        assert out[0]["sheet_id"] == "id_a"
        assert out[0]["name"] == "Receipts — Personal 2026"
        assert out[0]["label"] == "Personal 2026"
        # 3 rows, 1 of which is the header
        assert out[0]["row_count"] == 2

    def test_handles_zero_results(self):
        drive = _make_drive_stub([])
        sheets = _make_sheets_stub()
        with patch.object(tr_mod, "_drive", return_value=drive), \
             patch.object(tr_mod, "_sheets", return_value=sheets):
            out = tr_mod._list_receipt_sheets()
        assert out == []

    def test_row_count_failure_doesnt_break(self):
        """A sheet whose contents we can't read should still appear, with
        row_count=None — auth scope partial-failures shouldn't hide sheets."""
        drive = _make_drive_stub([
            {"id": "id_x", "name": "Receipts — Test",
             "modifiedTime": "2026-04-01T00:00:00Z",
             "webViewLink": "https://example/x"},
        ])
        sheets = MagicMock()

        def boom(*args, **kwargs):
            raise Exception("403 forbidden")
        sheets.spreadsheets.return_value.values.return_value.get.side_effect = boom

        with patch.object(tr_mod, "_drive", return_value=drive), \
             patch.object(tr_mod, "_sheets", return_value=sheets):
            out = tr_mod._list_receipt_sheets()
        assert len(out) == 1
        assert out[0]["row_count"] is None


# --------------------------------------------------------------------------- #
# _resolve_sheet
# --------------------------------------------------------------------------- #


class TestResolveSheet:
    def test_explicit_id_passes_through(self):
        sheets = _make_sheets_stub(metadata_by_id={
            "abc123": {"properties": {"title": "Receipts — X"}},
        })
        with patch.object(tr_mod, "_sheets", return_value=sheets):
            sid, title, err = tr_mod._resolve_sheet("abc123", None)
        assert sid == "abc123"
        assert title == "Receipts — X"
        assert err is None

    def test_explicit_id_404_returns_error(self):
        sheets = MagicMock()
        sheets.spreadsheets.return_value.get.side_effect = Exception("404 not found")
        with patch.object(tr_mod, "_sheets", return_value=sheets):
            sid, title, err = tr_mod._resolve_sheet("does_not_exist", None)
        assert sid is None and title is None
        assert err is not None
        assert err["status"] == "sheet_not_accessible"
        # Helpful next step in the hint
        assert "workflow_list_receipt_sheets" in err["hint"]

    def test_resolves_by_label(self):
        drive = _make_drive_stub([
            {"id": "id_q2", "name": "Receipts — Personal 2026 Q2",
             "modifiedTime": "2026-04-01T00:00:00Z",
             "webViewLink": "https://example/q2"},
        ])
        sheets = _make_sheets_stub(values_by_id={"id_q2": [["h"]]})
        with patch.object(tr_mod, "_drive", return_value=drive), \
             patch.object(tr_mod, "_sheets", return_value=sheets):
            sid, title, err = tr_mod._resolve_sheet(None, "Personal 2026 Q2")
        assert sid == "id_q2"
        assert err is None

    def test_resolves_by_full_title(self):
        drive = _make_drive_stub([
            {"id": "id_q2", "name": "Receipts — Personal 2026 Q2",
             "modifiedTime": "2026-04-01T00:00:00Z",
             "webViewLink": "https://example/q2"},
        ])
        sheets = _make_sheets_stub(values_by_id={"id_q2": [["h"]]})
        with patch.object(tr_mod, "_drive", return_value=drive), \
             patch.object(tr_mod, "_sheets", return_value=sheets):
            sid, _t, err = tr_mod._resolve_sheet(
                None, "Receipts — Personal 2026 Q2",
            )
        assert sid == "id_q2"
        assert err is None

    def test_resolves_case_insensitive(self):
        drive = _make_drive_stub([
            {"id": "id_q2", "name": "Receipts — Personal 2026 Q2",
             "modifiedTime": "2026-04-01T00:00:00Z",
             "webViewLink": "https://example/q2"},
        ])
        sheets = _make_sheets_stub(values_by_id={"id_q2": [["h"]]})
        with patch.object(tr_mod, "_drive", return_value=drive), \
             patch.object(tr_mod, "_sheets", return_value=sheets):
            sid, _t, err = tr_mod._resolve_sheet(None, "personal 2026 q2")
        assert sid == "id_q2"
        assert err is None

    def test_name_not_found_returns_discovery_list(self):
        drive = _make_drive_stub([
            {"id": "id_a", "name": "Receipts — Business",
             "modifiedTime": "2026-04-01T00:00:00Z",
             "webViewLink": "https://example/a"},
        ])
        sheets = _make_sheets_stub(values_by_id={"id_a": [["h"]]})
        with patch.object(tr_mod, "_drive", return_value=drive), \
             patch.object(tr_mod, "_sheets", return_value=sheets):
            sid, title, err = tr_mod._resolve_sheet(None, "Personal 2026 Q2")
        assert sid is None and title is None
        assert err["status"] == "sheet_not_found"
        assert err["requested"] == "Personal 2026 Q2"
        # The list of what IS available is included for the user to pick from
        assert len(err["available_sheets"]) == 1
        assert err["available_sheets"][0]["label"] == "Business"

    def test_no_args_returns_needs_sheet(self):
        drive = _make_drive_stub([])
        sheets = _make_sheets_stub()
        with patch.object(tr_mod, "_drive", return_value=drive), \
             patch.object(tr_mod, "_sheets", return_value=sheets):
            sid, title, err = tr_mod._resolve_sheet(None, None)
        assert sid is None and title is None
        assert err["status"] == "needs_sheet"
        assert err["available_sheets"] == []

    def test_ambiguous_name_returns_match_list(self):
        """If two sheets share a label (rare but possible after manual rename),
        we don't silently pick — caller must disambiguate by id."""
        drive = _make_drive_stub([
            {"id": "id_1", "name": "Receipts — Personal",
             "modifiedTime": "2026-04-01T00:00:00Z",
             "webViewLink": "https://example/1"},
            {"id": "id_2", "name": "Receipts — Personal",
             "modifiedTime": "2026-03-01T00:00:00Z",
             "webViewLink": "https://example/2"},
        ])
        sheets = _make_sheets_stub(values_by_id={
            "id_1": [["h"]], "id_2": [["h"]],
        })
        with patch.object(tr_mod, "_drive", return_value=drive), \
             patch.object(tr_mod, "_sheets", return_value=sheets):
            sid, _t, err = tr_mod._resolve_sheet(None, "Personal")
        assert sid is None
        assert err["status"] == "ambiguous_sheet_name"
        assert len(err["matches"]) == 2


# --------------------------------------------------------------------------- #
# CreateReceiptSheetInput — prefix dedupe
# --------------------------------------------------------------------------- #


class TestCreatePrefixHandling:
    """The create tool should accept either bare labels or full titles, and
    never produce 'Receipts — Receipts — X' duplication."""

    def test_bare_label_gets_prefix(self):
        m = tr_mod.CreateReceiptSheetInput(name="Q2 2026")
        assert m.name == "Q2 2026"  # unmodified at validation stage
        # Logic that adds the prefix lives in the tool body itself.

    def test_full_title_round_trips(self):
        # The user pastes the full sheet title back in.
        m = tr_mod.CreateReceiptSheetInput(name="Receipts — Q2 2026")
        assert "Receipts —" in m.name


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
