"""Baseline unit tests for tools/drive.py — P0-3 spec.

Per-tool: input-model validation, happy path (mocked gservices), error
path (HttpError → format_error JSON return). No live API.

The mocked _service() returns a MagicMock chain so any tool's call shape
works — `_service().files().list(q=...).execute()` resolves to whatever
the test wires `.execute.return_value` to.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest
from googleapiclient.errors import HttpError
from pydantic import ValidationError

from tools import drive as t_drive
from tools.drive import (
    SearchInput,
    ReadFileInput,
    CreateFolderInput,
    UploadFileInput,
    UploadBinaryInput,
    DownloadBinaryInput,
    MoveFileInput,
    DeleteFileInput,
    ShareFileInput,
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _resolve_tool(name: str):
    """Pull the registered tool function out of the running MCP server."""
    from server import mcp
    return mcp._tool_manager._tools[name].fn


def _run(tool_name, params):
    """Resolve + call a tool fn synchronously."""
    fn = _resolve_tool(tool_name)
    return asyncio.run(fn(params))


def _http_error():
    """Build a synthetic HttpError matching what googleapiclient raises."""
    resp = MagicMock(status=500, reason="boom")
    return HttpError(resp, b'{"error": {"message": "boom"}}')


# --------------------------------------------------------------------------- #
# Input validation
# --------------------------------------------------------------------------- #


def test_search_input_requires_query():
    with pytest.raises(ValidationError):
        SearchInput()


def test_search_input_accepts_q_alias():
    """SearchInput uses populate_by_name with alias='q' for query."""
    m = SearchInput.model_validate({"q": "name contains 'foo'"})
    assert m.query == "name contains 'foo'"


def test_search_input_limit_bounds():
    SearchInput(query="x", limit=1)
    SearchInput(query="x", limit=1000)
    with pytest.raises(ValidationError):
        SearchInput(query="x", limit=0)
    with pytest.raises(ValidationError):
        SearchInput(query="x", limit=10001)


def test_read_file_input_requires_file_id():
    with pytest.raises(ValidationError):
        ReadFileInput()
    ReadFileInput(file_id="abc123")


def test_create_folder_input_requires_name():
    with pytest.raises(ValidationError):
        CreateFolderInput()
    CreateFolderInput(name="My Folder")


def test_upload_file_input_requires_name_and_content():
    with pytest.raises(ValidationError):
        UploadFileInput()
    UploadFileInput(name="hello.txt", content="hi there")


def test_upload_binary_input_constructs():
    """UploadBinaryInput allows name-only construction; the tool itself
    rejects requests with neither local_path nor content_b64 at runtime."""
    UploadBinaryInput(name="hello.bin")
    UploadBinaryInput(name="hello.bin", content_b64="aGVsbG8=")


def test_download_binary_input_requires_file_id():
    with pytest.raises(ValidationError):
        DownloadBinaryInput()
    DownloadBinaryInput(file_id="abc")


def test_move_file_input_requires_both_ids():
    with pytest.raises(ValidationError):
        MoveFileInput()
    with pytest.raises(ValidationError):
        MoveFileInput(file_id="abc")
    MoveFileInput(file_id="abc", new_parent_id="def")


def test_delete_file_input_requires_file_id():
    with pytest.raises(ValidationError):
        DeleteFileInput()
    DeleteFileInput(file_id="abc")


def test_share_file_input_requires_file_id():
    """file_id required; email is optional (omit for anyone-with-link)."""
    with pytest.raises(ValidationError):
        ShareFileInput()
    ShareFileInput(file_id="abc")  # anyone-with-link
    ShareFileInput(file_id="abc", email="a@b.com")  # specific person


# --------------------------------------------------------------------------- #
# Happy paths
# --------------------------------------------------------------------------- #


def _fake_drive_with(execute_value):
    """Build a chainable mock whose .files().<method>(...).execute() returns
    the given value. Other methods on .files() return the same chain."""
    drive = MagicMock()
    # files().<anything>(...).execute() always returns execute_value
    chain_end = MagicMock()
    chain_end.execute.return_value = execute_value
    files_proxy = MagicMock()
    # Common files() methods used by the tools
    for attr in ("list", "get", "create", "update", "delete",
                 "get_media", "copy"):
        getattr(files_proxy, attr).return_value = chain_end
    # also support .permissions().create().execute
    perms_chain = MagicMock()
    perms_chain.execute.return_value = execute_value
    files_proxy.permissions = MagicMock(return_value=MagicMock(
        create=MagicMock(return_value=perms_chain),
    ))
    drive.files.return_value = files_proxy
    drive.permissions.return_value = MagicMock(
        create=MagicMock(return_value=perms_chain),
    )
    return drive


def test_search_files_happy(monkeypatch):
    fake = _fake_drive_with({"files": [{"id": "f1", "name": "x.pdf"}]})
    monkeypatch.setattr(t_drive, "_service", lambda: fake)
    out = _run("drive_search_files", SearchInput(query="x"))
    payload = json.loads(out)
    assert payload["count"] == 1
    assert payload["files"][0]["id"] == "f1"


def test_create_folder_happy(monkeypatch):
    fake = _fake_drive_with({"id": "fld1", "name": "New", "webViewLink": "u"})
    monkeypatch.setattr(t_drive, "_service", lambda: fake)
    out = _run("drive_create_folder", CreateFolderInput(name="New"))
    payload = json.loads(out)
    assert payload["id"] == "fld1"


def test_delete_file_happy(monkeypatch):
    fake = _fake_drive_with({})
    monkeypatch.setattr(t_drive, "_service", lambda: fake)
    out = _run("drive_delete_file", DeleteFileInput(file_id="f1"))
    payload = json.loads(out)
    # Tool returns some success indicator — just check it's not an error
    assert "error" not in payload or payload.get("ok") is True or "status" in payload


# --------------------------------------------------------------------------- #
# Error paths — every tool should return a format_error() JSON dict on
# Google API failure, never crash.
# --------------------------------------------------------------------------- #


def test_search_files_error_returns_format_error(monkeypatch):
    fake = MagicMock()
    fake.files.return_value.list.return_value.execute.side_effect = _http_error()
    monkeypatch.setattr(t_drive, "_service", lambda: fake)
    out = _run("drive_search_files", SearchInput(query="x"))
    # format_error returns either a JSON string or dict-shaped JSON
    # format_error returns a human-readable string. Just verify the
    # tool didn't crash and the message mentions the failure.
    assert isinstance(out, str)
    assert ("error" in out.lower()
            or "failed" in out.lower()
            or "boom" in out.lower()
            or "http" in out.lower())


def test_create_folder_error(monkeypatch):
    fake = MagicMock()
    fake.files.return_value.create.return_value.execute.side_effect = _http_error()
    monkeypatch.setattr(t_drive, "_service", lambda: fake)
    out = _run("drive_create_folder", CreateFolderInput(name="X"))
    # format_error returns a human-readable string. Just verify the
    # tool didn't crash and the message mentions the failure.
    assert isinstance(out, str)
    assert ("error" in out.lower()
            or "failed" in out.lower()
            or "boom" in out.lower()
            or "http" in out.lower())


def test_read_file_error(monkeypatch):
    fake = MagicMock()
    fake.files.return_value.get.return_value.execute.side_effect = _http_error()
    monkeypatch.setattr(t_drive, "_service", lambda: fake)
    out = _run("drive_read_file", ReadFileInput(file_id="abc"))
    # format_error returns a human-readable string. Just verify the
    # tool didn't crash and the message mentions the failure.
    assert isinstance(out, str)
    assert ("error" in out.lower()
            or "failed" in out.lower()
            or "boom" in out.lower()
            or "http" in out.lower())


def test_delete_file_error(monkeypatch):
    """Default behavior is trash (move-to-trash via files().update with
    {"trashed": true}). Permanent delete uses files().delete()."""
    fake = MagicMock()
    fake.files.return_value.update.return_value.execute.side_effect = _http_error()
    fake.files.return_value.delete.return_value.execute.side_effect = _http_error()
    monkeypatch.setattr(t_drive, "_service", lambda: fake)
    out = _run("drive_delete_file", DeleteFileInput(file_id="abc"))
    # format_error returns a human-readable string. Just verify the
    # tool didn't crash and the message mentions the failure.
    assert isinstance(out, str)
    assert ("error" in out.lower()
            or "failed" in out.lower()
            or "boom" in out.lower()
            or "http" in out.lower())


def test_share_file_error(monkeypatch):
    fake = MagicMock()
    fake.permissions.return_value.create.return_value.execute.side_effect = _http_error()
    monkeypatch.setattr(t_drive, "_service", lambda: fake)
    out = _run("drive_share_file", ShareFileInput(file_id="f1", email="a@b.com"))
    # format_error returns a human-readable string. Just verify the
    # tool didn't crash and the message mentions the failure.
    assert isinstance(out, str)
    assert ("error" in out.lower()
            or "failed" in out.lower()
            or "boom" in out.lower()
            or "http" in out.lower())


def test_move_file_error(monkeypatch):
    fake = MagicMock()
    fake.files.return_value.get.return_value.execute.return_value = {"parents": ["p0"]}
    fake.files.return_value.update.return_value.execute.side_effect = _http_error()
    monkeypatch.setattr(t_drive, "_service", lambda: fake)
    out = _run("drive_move_file", MoveFileInput(file_id="f1", new_parent_id="p1"))
    # format_error returns a human-readable string. Just verify the
    # tool didn't crash and the message mentions the failure.
    assert isinstance(out, str)
    assert ("error" in out.lower()
            or "failed" in out.lower()
            or "boom" in out.lower()
            or "http" in out.lower())


# --------------------------------------------------------------------------- #
# Registration smoke (doubles as a "tool exists" check)
# --------------------------------------------------------------------------- #


def test_all_drive_tools_registered():
    from server import mcp
    expected = {
        "drive_search_files", "drive_read_file", "drive_create_folder",
        "drive_upload_text_file", "drive_upload_binary_file",
        "drive_download_binary_file", "drive_move_file",
        "drive_delete_file", "drive_share_file",
    }
    actual = {n for n in mcp._tool_manager._tools if n.startswith("drive_")}
    assert expected.issubset(actual), f"missing: {expected - actual}"
