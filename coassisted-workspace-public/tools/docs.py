"""Docs tools: create, read, insert text, replace text."""

from __future__ import annotations

import gservices

import json
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from errors import format_error


def _service():
    return gservices.docs()


# --------------------------------------------------------------------------- #
# Input models
# --------------------------------------------------------------------------- #


class CreateDocInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    title: str = Field(..., description="Document title.")


class ReadDocInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    document_id: str = Field(...)


class InsertTextInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    document_id: str = Field(...)
    text: str = Field(..., description="Text to insert.")
    index: Optional[int] = Field(
        default=None,
        description="1-based character index. If omitted, inserts at end of document.",
    )


class ReplaceTextInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    document_id: str = Field(...)
    find: str = Field(..., description="Exact text to find.")
    replace: str = Field(..., description="Replacement text.")
    match_case: bool = Field(default=True)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _extract_doc_text(body: dict) -> str:
    """Walk a Docs body and concatenate all text runs."""
    out: list[str] = []
    for el in body.get("content", []):
        para = el.get("paragraph")
        if not para:
            continue
        for run in para.get("elements", []):
            tr = run.get("textRun")
            if tr and tr.get("content"):
                out.append(tr["content"])
    return "".join(out)


def _end_index(doc: dict) -> int:
    """Find the last valid insertion index in a Doc."""
    content = doc.get("body", {}).get("content", [])
    if not content:
        return 1
    # The very last segment's endIndex points past the final newline — insert
    # one character before that to stay inside the body.
    return max(1, content[-1].get("endIndex", 1) - 1)


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #


def register(mcp) -> None:
    @mcp.tool(
        name="docs_create_document",
        annotations={
            "title": "Create a Google Doc",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def docs_create_document(params: CreateDocInput) -> str:
        """Create a new empty Google Doc. Returns document ID."""
        try:
            created = _service().documents().create(body={"title": params.title}).execute()
            return json.dumps(
                {"document_id": created["documentId"], "title": created["title"]},
                indent=2,
            )
        except Exception as e:
            return format_error(e)

    @mcp.tool(
        name="docs_read_document",
        annotations={
            "title": "Read a Google Doc as plain text",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def docs_read_document(params: ReadDocInput) -> str:
        """Return the title and plain-text content of a Google Doc."""
        try:
            doc = _service().documents().get(documentId=params.document_id).execute()
            return json.dumps(
                {
                    "document_id": doc["documentId"],
                    "title": doc.get("title"),
                    "content": _extract_doc_text(doc.get("body", {})),
                },
                indent=2,
            )
        except Exception as e:
            return format_error(e)

    @mcp.tool(
        name="docs_insert_text",
        annotations={
            "title": "Insert text into a Google Doc",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def docs_insert_text(params: InsertTextInput) -> str:
        """Insert text at a specific index, or append to the end if no index given."""
        try:
            svc = _service()
            index = params.index
            if index is None:
                doc = svc.documents().get(documentId=params.document_id).execute()
                index = _end_index(doc)

            svc.documents().batchUpdate(
                documentId=params.document_id,
                body={
                    "requests": [
                        {
                            "insertText": {
                                "location": {"index": index},
                                "text": params.text,
                            }
                        }
                    ]
                },
            ).execute()
            return json.dumps({"status": "ok", "inserted_at": index})
        except Exception as e:
            return format_error(e)

    @mcp.tool(
        name="docs_replace_text",
        annotations={
            "title": "Find and replace text in a Google Doc",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def docs_replace_text(params: ReplaceTextInput) -> str:
        """Find all occurrences of `find` and replace with `replace`."""
        try:
            resp = (
                _service()
                .documents()
                .batchUpdate(
                    documentId=params.document_id,
                    body={
                        "requests": [
                            {
                                "replaceAllText": {
                                    "containsText": {
                                        "text": params.find,
                                        "matchCase": params.match_case,
                                    },
                                    "replaceText": params.replace,
                                }
                            }
                        ]
                    },
                )
                .execute()
            )
            replies = resp.get("replies", [])
            count = (
                replies[0].get("replaceAllText", {}).get("occurrencesChanged", 0)
                if replies
                else 0
            )
            return json.dumps({"status": "ok", "occurrences_replaced": count})
        except Exception as e:
            return format_error(e)
