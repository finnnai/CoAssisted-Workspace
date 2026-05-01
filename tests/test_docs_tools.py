"""Baseline unit tests for tools/docs.py — P0-3 spec."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock

import pytest
from googleapiclient.errors import HttpError
from pydantic import ValidationError

from tools import docs as t_docs
from tools.docs import (
    CreateDocInput, ReadDocInput, InsertTextInput, ReplaceTextInput,
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


def test_create_doc_requires_title():
    with pytest.raises(ValidationError):
        CreateDocInput()
    CreateDocInput(title="My Doc")


def test_read_doc_requires_document_id():
    with pytest.raises(ValidationError):
        ReadDocInput()
    ReadDocInput(document_id="d1")


def test_insert_text_requires_doc_and_text():
    with pytest.raises(ValidationError):
        InsertTextInput()
    with pytest.raises(ValidationError):
        InsertTextInput(document_id="d1")
    InsertTextInput(document_id="d1", text="hello")


def test_replace_text_requires_all_three():
    with pytest.raises(ValidationError):
        ReplaceTextInput()
    with pytest.raises(ValidationError):
        ReplaceTextInput(document_id="d1")
    with pytest.raises(ValidationError):
        ReplaceTextInput(document_id="d1", find="foo")
    ReplaceTextInput(document_id="d1", find="foo", replace="bar")


# Error paths
def test_create_doc_error(monkeypatch):
    fake = MagicMock()
    fake.documents.return_value.create.return_value.execute.side_effect = _http_error()
    monkeypatch.setattr(t_docs, "_service", lambda: fake)
    _err_assert(_run("docs_create_document", CreateDocInput(title="x")))


def test_read_doc_error(monkeypatch):
    fake = MagicMock()
    fake.documents.return_value.get.return_value.execute.side_effect = _http_error()
    monkeypatch.setattr(t_docs, "_service", lambda: fake)
    _err_assert(_run("docs_read_document", ReadDocInput(document_id="d1")))


def test_insert_text_error(monkeypatch):
    fake = MagicMock()
    fake.documents.return_value.batchUpdate.return_value.execute.side_effect = _http_error()
    fake.documents.return_value.get.return_value.execute.return_value = {"body": {"content": []}}
    monkeypatch.setattr(t_docs, "_service", lambda: fake)
    _err_assert(_run("docs_insert_text",
                     InsertTextInput(document_id="d1", text="hi")))


def test_all_docs_tools_registered():
    from server import mcp
    expected = {"docs_create_document", "docs_read_document",
                "docs_insert_text", "docs_replace_text"}
    assert expected.issubset(set(mcp._tool_manager._tools))
