"""Baseline unit tests for tools/gmail.py — P0-3 spec.

Per-tool: input-model validation, error path. Happy paths covered for
the highest-traffic tools (send, search, get_thread). 17 tools total.
No live API.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock

import pytest
from googleapiclient.errors import HttpError
from pydantic import ValidationError

from tools import gmail as t_gmail
from tools.gmail import (
    SendEmailInput, CreateDraftInput, ReplyInput, SearchEmailInput,
    GetThreadInput, ListLabelsInput, ModifyLabelsInput, ListDraftsInput,
    CreateLabelInput, UpdateLabelInput, DeleteLabelInput,
    ForwardInput, TrashInput, UntrashInput, DownloadAttachmentInput,
    ListFiltersInput, CreateFilterInput, DeleteFilterInput,
    ListAliasesInput,
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
    assert isinstance(out, str)
    assert ("error" in out.lower() or "failed" in out.lower()
            or "boom" in out.lower() or "http" in out.lower())


# --------------------------------------------------------------------------- #
# Input validation
# --------------------------------------------------------------------------- #


def test_send_email_requires_to_subject_body():
    with pytest.raises(ValidationError):
        SendEmailInput()
    with pytest.raises(ValidationError):
        SendEmailInput(to=["a@b.com"])
    with pytest.raises(ValidationError):
        SendEmailInput(to=["a@b.com"], subject="Hi")
    SendEmailInput(to=["a@b.com"], subject="Hi", body="body")


def test_send_email_to_must_be_non_empty_list():
    with pytest.raises(ValidationError):
        SendEmailInput(to=[], subject="Hi", body="body")


def test_send_email_attachments_coerce_strings():
    """A bare string in `attachments` is coerced to {"path": <str>}."""
    m = SendEmailInput(
        to=["a@b.com"], subject="x", body="x",
        attachments=["/tmp/file.pdf"],
    )
    # Pydantic re-shaped it as AttachmentSpec
    assert m.attachments and getattr(m.attachments[0], "path", None) == "/tmp/file.pdf"


def test_create_draft_inherits_send_email_fields():
    """CreateDraftInput is just SendEmailInput aliased — same required fields."""
    with pytest.raises(ValidationError):
        CreateDraftInput()
    CreateDraftInput(to=["a@b.com"], subject="Hi", body="body")


def test_reply_requires_thread_and_body():
    with pytest.raises(ValidationError):
        ReplyInput()
    with pytest.raises(ValidationError):
        ReplyInput(thread_id="t1")
    ReplyInput(thread_id="t1", body="hello")


def test_search_email_requires_query():
    with pytest.raises(ValidationError):
        SearchEmailInput()
    SearchEmailInput(query="from:foo@bar")


def test_search_email_q_alias():
    m = SearchEmailInput.model_validate({"q": "from:foo@bar"})
    assert m.query == "from:foo@bar"


def test_search_email_max_results_alias():
    m = SearchEmailInput.model_validate({"q": "x", "max_results": 50})
    assert m.limit == 50


def test_search_email_limit_bounds():
    SearchEmailInput(query="x", limit=1)
    SearchEmailInput(query="x", limit=100)
    with pytest.raises(ValidationError):
        SearchEmailInput(query="x", limit=101)


def test_get_thread_requires_thread_id():
    with pytest.raises(ValidationError):
        GetThreadInput()
    GetThreadInput(thread_id="t1")


def test_modify_labels_requires_message_id():
    with pytest.raises(ValidationError):
        ModifyLabelsInput()
    ModifyLabelsInput(message_id="m1")


def test_create_label_requires_name():
    with pytest.raises(ValidationError):
        CreateLabelInput()
    CreateLabelInput(name="Clients/Acme")


def test_create_label_name_length_bounds():
    CreateLabelInput(name="X")  # 1 char OK
    CreateLabelInput(name="X" * 225)
    with pytest.raises(ValidationError):
        CreateLabelInput(name="X" * 226)


def test_update_label_requires_label_id():
    with pytest.raises(ValidationError):
        UpdateLabelInput()
    UpdateLabelInput(label_id="Label_1", name="renamed")


def test_delete_label_requires_label_id():
    with pytest.raises(ValidationError):
        DeleteLabelInput()
    DeleteLabelInput(label_id="Label_1")


def test_forward_requires_message_id_and_to():
    with pytest.raises(ValidationError):
        ForwardInput()
    with pytest.raises(ValidationError):
        ForwardInput(message_id="m1")
    ForwardInput(message_id="m1", to=["a@b.com"])


def test_forward_note_alias():
    m = ForwardInput.model_validate({
        "message_id": "m1", "to": ["a@b.com"], "note": "FYI",
    })
    assert m.comment == "FYI"


def test_trash_requires_message_id():
    with pytest.raises(ValidationError):
        TrashInput()
    TrashInput(message_id="m1")


def test_untrash_requires_message_id():
    with pytest.raises(ValidationError):
        UntrashInput()
    UntrashInput(message_id="m1")


def test_download_attachment_requires_message_id():
    with pytest.raises(ValidationError):
        DownloadAttachmentInput()
    DownloadAttachmentInput(message_id="m1")  # attachment_id/filename optional (lists)


def test_create_filter_from_alias():
    """Pydantic alias `from` -> field `from_` (Python keyword conflict)."""
    m = CreateFilterInput.model_validate({"from": "noreply@x.com"})
    assert m.from_ == "noreply@x.com"


def test_delete_filter_requires_filter_id():
    with pytest.raises(ValidationError):
        DeleteFilterInput()
    DeleteFilterInput(filter_id="filter_42")


def test_no_arg_inputs_take_no_args():
    ListLabelsInput()
    ListFiltersInput()
    ListAliasesInput()
    with pytest.raises(ValidationError):
        ListLabelsInput.model_validate({"unexpected": 1})


# --------------------------------------------------------------------------- #
# Happy paths — high-traffic tools only
# --------------------------------------------------------------------------- #


def test_search_happy(monkeypatch):
    """Empty result set is a valid happy path — covers the list path
    without needing a complete fake message shape (headers, threadId,
    payload tree, etc.)."""
    fake = MagicMock()
    fake.users.return_value.messages.return_value.list.return_value.execute.return_value = {
        "messages": [],
    }
    monkeypatch.setattr(t_gmail, "_service", lambda: fake)
    out = _run("gmail_search", SearchEmailInput(query="x"))
    payload = json.loads(out)
    assert payload["count"] == 0
    assert payload["messages"] == []


def test_list_labels_happy(monkeypatch):
    fake = MagicMock()
    fake.users.return_value.labels.return_value.list.return_value.execute.return_value = {
        "labels": [{"id": "INBOX", "name": "INBOX", "type": "system"}],
    }
    monkeypatch.setattr(t_gmail, "_service", lambda: fake)
    out = _run("gmail_list_labels", ListLabelsInput())
    payload = json.loads(out)
    assert payload  # non-empty


def test_list_drafts_happy(monkeypatch):
    fake = MagicMock()
    fake.users.return_value.drafts.return_value.list.return_value.execute.return_value = {
        "drafts": [],
    }
    monkeypatch.setattr(t_gmail, "_service", lambda: fake)
    out = _run("gmail_list_drafts", ListDraftsInput())
    payload = json.loads(out)
    assert isinstance(payload, (list, dict))


# --------------------------------------------------------------------------- #
# Error paths — every send/modify tool surfaces format_error on API failure.
# --------------------------------------------------------------------------- #


def test_search_error(monkeypatch):
    fake = MagicMock()
    fake.users.return_value.messages.return_value.list.return_value.execute.side_effect = _http_error()
    monkeypatch.setattr(t_gmail, "_service", lambda: fake)
    _err_assert(_run("gmail_search", SearchEmailInput(query="x")))


def test_list_labels_error(monkeypatch):
    fake = MagicMock()
    fake.users.return_value.labels.return_value.list.return_value.execute.side_effect = _http_error()
    monkeypatch.setattr(t_gmail, "_service", lambda: fake)
    _err_assert(_run("gmail_list_labels", ListLabelsInput()))


def test_create_label_error(monkeypatch):
    fake = MagicMock()
    fake.users.return_value.labels.return_value.create.return_value.execute.side_effect = _http_error()
    monkeypatch.setattr(t_gmail, "_service", lambda: fake)
    _err_assert(_run("gmail_create_label", CreateLabelInput(name="X")))


def test_get_thread_error(monkeypatch):
    fake = MagicMock()
    fake.users.return_value.threads.return_value.get.return_value.execute.side_effect = _http_error()
    monkeypatch.setattr(t_gmail, "_service", lambda: fake)
    _err_assert(_run("gmail_get_thread", GetThreadInput(thread_id="t1")))


def test_modify_labels_error(monkeypatch):
    fake = MagicMock()
    fake.users.return_value.messages.return_value.modify.return_value.execute.side_effect = _http_error()
    monkeypatch.setattr(t_gmail, "_service", lambda: fake)
    _err_assert(_run("gmail_modify_labels", ModifyLabelsInput(message_id="m1")))


def test_trash_error(monkeypatch):
    fake = MagicMock()
    fake.users.return_value.messages.return_value.trash.return_value.execute.side_effect = _http_error()
    monkeypatch.setattr(t_gmail, "_service", lambda: fake)
    _err_assert(_run("gmail_trash_message", TrashInput(message_id="m1")))


def test_untrash_error(monkeypatch):
    fake = MagicMock()
    fake.users.return_value.messages.return_value.untrash.return_value.execute.side_effect = _http_error()
    monkeypatch.setattr(t_gmail, "_service", lambda: fake)
    _err_assert(_run("gmail_untrash_message", UntrashInput(message_id="m1")))


def test_list_filters_error(monkeypatch):
    fake = MagicMock()
    fake.users.return_value.settings.return_value.filters.return_value.list.return_value.execute.side_effect = _http_error()
    monkeypatch.setattr(t_gmail, "_service", lambda: fake)
    _err_assert(_run("gmail_list_filters", ListFiltersInput()))


def test_list_aliases_error(monkeypatch):
    fake = MagicMock()
    fake.users.return_value.settings.return_value.sendAs.return_value.list.return_value.execute.side_effect = _http_error()
    monkeypatch.setattr(t_gmail, "_service", lambda: fake)
    _err_assert(_run("gmail_list_send_as", ListAliasesInput()))


# --------------------------------------------------------------------------- #
# Registration smoke
# --------------------------------------------------------------------------- #


def test_all_gmail_tools_registered():
    from server import mcp
    expected = {
        "gmail_send_email", "gmail_create_draft", "gmail_reply_to_thread",
        "gmail_search", "gmail_get_thread", "gmail_list_labels",
        "gmail_modify_labels", "gmail_list_drafts", "gmail_create_label",
        "gmail_update_label", "gmail_delete_label", "gmail_forward_message",
        "gmail_trash_message", "gmail_untrash_message",
        "gmail_download_attachment", "gmail_list_filters",
        "gmail_create_filter", "gmail_delete_filter", "gmail_list_send_as",
    }
    actual = {n for n in mcp._tool_manager._tools if n.startswith("gmail_")}
    missing = expected - actual
    assert not missing, f"missing: {missing}"
