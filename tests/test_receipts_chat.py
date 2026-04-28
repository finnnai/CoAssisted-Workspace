# © 2026 CoAssisted Workspace. Licensed for non-redistribution use only.
# See LICENSE file for terms.
"""Gchat receipt scan branch.

Covers _scan_chat_space() — the iteration loop that handles:
  - Pulling messages via Chat list + get
  - PDF/image attachment download via Chat media API
  - Drive-linked attachments routed through Drive
  - Text-body fallback through the receipt classifier
  - Source-ID dedup (chat:<message_name>) and content-key dedup
  - skip_low_confidence and skipped_not_receipt counting

Network and Google API are stubbed throughout; tests run offline.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import receipts as r
from tools import receipts as tr_mod


def _build_chat_stub(messages_resp, get_msg_side_effect=None):
    """Build a Chat client mock returning the given list/get sequences."""
    chat = MagicMock()
    list_call = MagicMock()
    list_call.execute.return_value = {"messages": messages_resp}
    chat.spaces.return_value.messages.return_value.list.return_value = list_call

    get_chain = chat.spaces.return_value.messages.return_value.get
    if get_msg_side_effect is not None:
        def _make_get(name):
            ret = MagicMock()
            try:
                msg = get_msg_side_effect(name)
            except StopIteration:
                msg = {}
            ret.execute.return_value = msg
            return ret
        get_chain.side_effect = lambda name: _make_get(name)
    return chat


def _common_args(seen_ids=None, seen_keys=None, **overrides):
    base = dict(
        space_id="spaces/TEST",
        days=30,
        max_messages=100,
        seen_source_ids=seen_ids if seen_ids is not None else set(),
        seen_content_keys=seen_keys if seen_keys is not None else set(),
        results={
            "extracted": 0, "skipped_dup": 0, "skipped_dup_content": 0,
            "skipped_low_conf": 0, "skipped_not_receipt": 0,
            "errors": 0, "scanned": 0,
        },
        redact_payment=True,
        now_iso="2026-04-26T12:00:00-07:00",
        archive_pdfs=False,
        archive_folder_id=None,
        skip_low_confidence=False,
    )
    base.update(overrides)
    return base


# --------------------------------------------------------------------------- #
# Source-ID dedup
# --------------------------------------------------------------------------- #


def test_chat_scan_skips_already_seen_message():
    msg_name = "spaces/TEST/messages/abc"
    chat = _build_chat_stub([{"name": msg_name}])
    args = _common_args(seen_ids={f"chat:{msg_name}"})
    with patch.object(tr_mod, "_chat", return_value=chat):
        rows, recs = tr_mod._scan_chat_space(**args)
    assert args["results"]["skipped_dup"] == 1
    assert args["results"]["extracted"] == 0
    assert rows == [] and recs == []


# --------------------------------------------------------------------------- #
# Text body path: classifier rejects non-receipt
# --------------------------------------------------------------------------- #


def test_chat_scan_classifier_rejects_random_chatter():
    msg_name = "spaces/TEST/messages/chitchat"
    chat = _build_chat_stub(
        [{"name": msg_name}],
        get_msg_side_effect=lambda n: {
            "name": n,
            "text": "anyone want lunch?",
            "sender": {"displayName": "Teammate"},
        },
    )
    args = _common_args()
    with patch.object(tr_mod, "_chat", return_value=chat):
        rows, _ = tr_mod._scan_chat_space(**args)
    assert args["results"]["skipped_not_receipt"] == 1
    assert args["results"]["extracted"] == 0
    assert rows == []


# --------------------------------------------------------------------------- #
# Text body path: classifier accepts, extraction runs
# --------------------------------------------------------------------------- #


def test_chat_scan_text_body_extracts_receipt():
    msg_name = "spaces/TEST/messages/textreceipt"
    body = (
        "Forwarding this from email — "
        "Receipt from Acme Coffee\n"
        "Total: $14.50\n"
        "April 26, 2026\n"
        "Card ending 1234"
    )
    chat = _build_chat_stub(
        [{"name": msg_name}],
        get_msg_side_effect=lambda n: {
            "name": n, "text": body,
            "sender": {"displayName": "User"},
        },
    )
    args = _common_args()

    fake_extract = r.ExtractedReceipt(
        merchant="Acme Coffee", date="2026-04-26", total=14.50,
        currency="USD", category="Meals",
        last_4="1234", confidence=0.9,
        source_id=f"chat:{msg_name}", source_kind="chat_text",
    )
    with patch.object(tr_mod, "_chat", return_value=chat), \
         patch.object(r, "extract_from_text", return_value=fake_extract), \
         patch.object(r, "enrich_low_confidence_receipt", side_effect=lambda x: x):
        rows, recs = tr_mod._scan_chat_space(**args)
    assert args["results"]["extracted"] == 1
    assert len(rows) == 1
    assert recs[0]["merchant"] == "Acme Coffee"
    assert recs[0]["source_id"] == f"chat:{msg_name}"


# --------------------------------------------------------------------------- #
# Attachment path: PDF wins over text fallback
# --------------------------------------------------------------------------- #


def test_chat_scan_pdf_attachment_extracts():
    msg_name = "spaces/TEST/messages/withpdf"
    chat = _build_chat_stub(
        [{"name": msg_name}],
        get_msg_side_effect=lambda n: {
            "name": n, "text": "fyi receipt attached",
            "sender": {"displayName": "User"},
            "attachment": [{
                "contentType": "application/pdf",
                "contentName": "receipt.pdf",
                "attachmentDataRef": {"resourceName": "media/abc"},
            }],
        },
    )
    args = _common_args()
    fake_pdf_rec = r.ExtractedReceipt(
        merchant="Hilton", date="2026-04-25", total=312.50,
        currency="USD", category="Travel", confidence=0.95,
        source_kind="chat_pdf",
    )
    with patch.object(tr_mod, "_chat", return_value=chat), \
         patch.object(tr_mod, "_download_chat_attachment",
                      return_value=(b"%PDF-1.4 fake", "application/pdf")), \
         patch.object(r, "extract_from_pdf", return_value=fake_pdf_rec) as mock_pdf, \
         patch.object(r, "extract_from_text") as mock_text, \
         patch.object(r, "enrich_low_confidence_receipt", side_effect=lambda x: x):
        rows, recs = tr_mod._scan_chat_space(**args)
    # PDF path was used, text path was NOT (PDF wins)
    mock_pdf.assert_called_once()
    mock_text.assert_not_called()
    assert len(rows) == 1
    assert recs[0]["merchant"] == "Hilton"


# --------------------------------------------------------------------------- #
# Attachment path: skip non-receipt content types (e.g. .docx)
# --------------------------------------------------------------------------- #


def test_chat_scan_ignores_non_pdf_image_attachments():
    msg_name = "spaces/TEST/messages/docx"
    chat = _build_chat_stub(
        [{"name": msg_name}],
        get_msg_side_effect=lambda n: {
            "name": n, "text": "",  # no text body either
            "attachment": [{
                "contentType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "contentName": "report.docx",
                "attachmentDataRef": {"resourceName": "media/xyz"},
            }],
        },
    )
    args = _common_args()
    with patch.object(tr_mod, "_chat", return_value=chat):
        rows, recs = tr_mod._scan_chat_space(**args)
    assert args["results"]["skipped_not_receipt"] == 1
    assert rows == []


# --------------------------------------------------------------------------- #
# Content-key dedup catches same purchase via different chat message
# --------------------------------------------------------------------------- #


def test_chat_scan_content_dedup_against_existing_sheet_row():
    msg_name = "spaces/TEST/messages/dupcontent"
    chat = _build_chat_stub(
        [{"name": msg_name}],
        get_msg_side_effect=lambda n: {
            "name": n,
            "text": "Receipt from Acme — Total: $14.50 paid Apr 26",
            "sender": {"displayName": "User"},
        },
    )
    # Pre-populate seen_content_keys to simulate the same purchase already
    # in the sheet from an earlier inbox scan.
    existing_key = r.content_key("Acme Coffee", "2026-04-26", 14.50, "1234")
    args = _common_args(seen_keys={existing_key})

    fake_rec = r.ExtractedReceipt(
        merchant="Acme Coffee", date="2026-04-26", total=14.50,
        currency="USD", category="Meals",
        last_4="1234", confidence=0.9,
    )
    with patch.object(tr_mod, "_chat", return_value=chat), \
         patch.object(r, "extract_from_text", return_value=fake_rec), \
         patch.object(r, "enrich_low_confidence_receipt", side_effect=lambda x: x):
        rows, recs = tr_mod._scan_chat_space(**args)
    assert args["results"]["skipped_dup_content"] == 1
    assert args["results"]["extracted"] == 0
    assert rows == []


# --------------------------------------------------------------------------- #
# skip_low_confidence threshold honored
# --------------------------------------------------------------------------- #


def test_chat_scan_skips_low_confidence_when_flag_set():
    msg_name = "spaces/TEST/messages/lowconf"
    chat = _build_chat_stub(
        [{"name": msg_name}],
        get_msg_side_effect=lambda n: {
            "name": n,
            "text": "Receipt — Total: $50.00 paid",
            "sender": {"displayName": "User"},
        },
    )
    args = _common_args(skip_low_confidence=True)

    fake_rec = r.ExtractedReceipt(
        merchant="Unknown", total=50.0, currency="USD",
        category="Miscellaneous Expense", confidence=0.2,  # below 0.4 threshold
    )
    with patch.object(tr_mod, "_chat", return_value=chat), \
         patch.object(r, "extract_from_text", return_value=fake_rec), \
         patch.object(r, "enrich_low_confidence_receipt", side_effect=lambda x: x):
        rows, _ = tr_mod._scan_chat_space(**args)
    assert args["results"]["skipped_low_conf"] == 1
    assert rows == []


# --------------------------------------------------------------------------- #
# Chat list call failure handled gracefully
# --------------------------------------------------------------------------- #


def test_chat_scan_list_failure_records_error():
    chat = MagicMock()
    list_call = MagicMock()
    list_call.execute.side_effect = Exception("403 forbidden")
    chat.spaces.return_value.messages.return_value.list.return_value = list_call
    args = _common_args()
    with patch.object(tr_mod, "_chat", return_value=chat):
        rows, recs = tr_mod._scan_chat_space(**args)
    assert args["results"]["errors"] == 1
    assert rows == []


# --------------------------------------------------------------------------- #
# ExtractReceiptsFromChatInput Pydantic validation
# --------------------------------------------------------------------------- #


class TestResolveChatSenderDisplay:
    """Translate Chat sender objects to human-readable display names."""

    def setup_method(self):
        # Clear the per-process cache between tests.
        tr_mod._CHAT_SENDER_NAME_CACHE.clear()

    def test_uses_display_name_when_present(self):
        out = tr_mod._resolve_chat_sender_display({
            "name": "users/123",
            "displayName": "Finnn Ai",
        })
        assert out == "Finnn Ai"

    def test_returns_none_for_empty_sender(self):
        assert tr_mod._resolve_chat_sender_display(None) is None
        assert tr_mod._resolve_chat_sender_display({}) is None

    def test_people_api_lookup_when_displayname_missing(self):
        people_resp = {
            "resourceName": "people/123",
            "names": [{"displayName": "Finnn Ai"}],
        }
        people_svc = MagicMock()
        people_svc.people.return_value.get.return_value.execute.return_value = people_resp
        with patch("gservices.people", return_value=people_svc):
            out = tr_mod._resolve_chat_sender_display({
                "name": "users/117223619593311240286",
                "displayName": None,
            })
        assert out == "Finnn Ai"
        # Cache hit on second call
        with patch("gservices.people", side_effect=AssertionError("no second call!")):
            out2 = tr_mod._resolve_chat_sender_display({
                "name": "users/117223619593311240286",
                "displayName": None,
            })
        assert out2 == "Finnn Ai"

    def test_people_api_falls_back_to_email_local_part(self):
        """If People API returns an email but no name, derive a display from it."""
        people_resp = {
            "resourceName": "people/456",
            "emailAddresses": [{"value": "alice@example.com"}],
        }
        people_svc = MagicMock()
        people_svc.people.return_value.get.return_value.execute.return_value = people_resp
        with patch("gservices.people", return_value=people_svc):
            out = tr_mod._resolve_chat_sender_display({
                "name": "users/456",
                "displayName": None,
            })
        assert out == "alice"

    def test_people_api_failure_falls_back_to_resource_id(self):
        """If People API raises, we keep the opaque id rather than crash."""
        people_svc = MagicMock()
        people_svc.people.return_value.get.return_value.execute.side_effect = Exception("403 forbidden")
        with patch("gservices.people", return_value=people_svc):
            out = tr_mod._resolve_chat_sender_display({
                "name": "users/789",
                "displayName": None,
            })
        # Conservative fallback — drops nothing, returns the opaque ID
        assert out == "users/789"


class TestChatInputModel:
    def test_requires_space_id(self):
        with pytest.raises(Exception):
            tr_mod.ExtractReceiptsFromChatInput()

    def test_defaults_sane(self):
        m = tr_mod.ExtractReceiptsFromChatInput(chat_space_id="spaces/X")
        assert m.days == 30
        assert m.max_messages == 200
        assert m.archive_pdfs is False
        assert m.skip_low_confidence is False

    def test_rejects_extra_fields(self):
        with pytest.raises(Exception):
            tr_mod.ExtractReceiptsFromChatInput(
                chat_space_id="spaces/X", surprise="boom",
            )

    def test_rejects_bad_days(self):
        with pytest.raises(Exception):
            tr_mod.ExtractReceiptsFromChatInput(
                chat_space_id="spaces/X", days=0,
            )
        with pytest.raises(Exception):
            tr_mod.ExtractReceiptsFromChatInput(
                chat_space_id="spaces/X", days=400,
            )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
