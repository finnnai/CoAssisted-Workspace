# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Workflow-level tests for tools/pandadoc_workflows.py.

Exercises all 5 Wave-4 workflows end-to-end with pandadoc_client.call
mocked. Each test owns its own FakeMCP instance + monkeypatches so they're
independent.

Approach
--------
The workflow functions are defined inside `register()` closures. We use a
minimal FakeMCP that captures @mcp.tool() decoratees by name, then call
them like normal functions with the workflow's Pydantic input model.
"""

from __future__ import annotations

import datetime as _dt
import sys
from types import ModuleType
from unittest import mock

import pytest


# --------------------------------------------------------------------------- #
# Heavy-dep stubs — install BEFORE importing tools.pandadoc_workflows.
#
# tools/__init__.py eagerly imports every tool module, several of which
# pull in gservices → googleapiclient + auth + a long dep chain. None of
# that is needed for these workflow tests; we only exercise
# pandadoc_workflows + pandadoc_client (which itself doesn't touch
# googleapiclient). Stub the upstream deps so the package import succeeds
# in any environment.
# --------------------------------------------------------------------------- #


def _install_heavy_dep_stubs() -> None:
    if "googleapiclient" not in sys.modules:
        ga = ModuleType("googleapiclient")
        ga_disc = ModuleType("googleapiclient.discovery")
        ga_disc.build = lambda *a, **kw: mock.MagicMock()  # type: ignore[attr-defined]
        ga_err = ModuleType("googleapiclient.errors")

        class _HttpError(Exception):
            pass

        ga_err.HttpError = _HttpError  # type: ignore[attr-defined]
        ga_http = ModuleType("googleapiclient.http")
        ga_http.MediaInMemoryUpload = mock.MagicMock()  # type: ignore[attr-defined]
        ga_http.MediaIoBaseDownload = mock.MagicMock()  # type: ignore[attr-defined]
        ga_http.MediaFileUpload = mock.MagicMock()  # type: ignore[attr-defined]

        class _HttpRequest:
            def __init__(self, *a, **kw):
                pass

        ga_http.HttpRequest = _HttpRequest  # type: ignore[attr-defined]
        sys.modules["googleapiclient"] = ga
        sys.modules["googleapiclient.discovery"] = ga_disc
        sys.modules["googleapiclient.errors"] = ga_err
        sys.modules["googleapiclient.http"] = ga_http

    for name in (
        "google", "google.auth", "google.oauth2",
        "google.oauth2.credentials", "google.oauth2.service_account",
        "google_auth_oauthlib", "google_auth_oauthlib.flow",
        "google.auth.transport", "google.auth.transport.requests",
    ):
        if name not in sys.modules:
            mod = ModuleType(name)
            # The few attributes the in-tree code touches.
            if name.endswith(".credentials"):
                mod.Credentials = mock.MagicMock()  # type: ignore[attr-defined]
            if name.endswith(".flow"):
                mod.InstalledAppFlow = mock.MagicMock()  # type: ignore[attr-defined]
            if name.endswith(".requests"):
                mod.Request = mock.MagicMock()  # type: ignore[attr-defined]
            sys.modules[name] = mod


_install_heavy_dep_stubs()


# --------------------------------------------------------------------------- #
# FakeMCP — captures registered tools so we can invoke them in tests.
# --------------------------------------------------------------------------- #


class FakeMCP:
    def __init__(self) -> None:
        self.tools: dict = {}

    def tool(self, *args, **kwargs):  # noqa: ANN001
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn
        return decorator


@pytest.fixture
def workflows():
    """Registered tools dict keyed by workflow_* name."""
    from tools import pandadoc_workflows
    mcp = FakeMCP()
    pandadoc_workflows.register(mcp)
    return mcp.tools


# --------------------------------------------------------------------------- #
# workflow_send_quote
# --------------------------------------------------------------------------- #


class TestSendQuote:
    def test_happy_path_send_immediately(self, workflows, monkeypatch):
        from tools import pandadoc_workflows as pw
        from tools.pandadoc_workflows import _SendQuoteInput

        calls: list[tuple] = []

        def fake_call(op, **kw):
            calls.append((op, kw))
            if op == "createDocument":
                return {"id": "DOC-123", "name": kw["json_body"]["name"],
                        "status": "document.uploaded"}
            if op == "statusDocument":
                return {"status": "document.draft"}
            if op == "sendDocument":
                return {"id": "DOC-123", "status": "document.sent"}
            raise AssertionError(f"unexpected op: {op}")

        monkeypatch.setattr(pw.pandadoc_client, "call", fake_call)

        send_quote = workflows["workflow_send_quote"]
        result = send_quote(_SendQuoteInput(
            template_uuid="tmpl-1",
            document_name="Q-2026-001",
            recipients=[{"email": "buyer@acme.com"}],
            tokens={"Client.Name": "Acme"},
            send_immediately=True,
        ))
        assert result["document_id"] == "DOC-123"
        assert result["document_status"] == "document.sent"
        assert "next_action" in result
        # createDocument body had tokens transformed to [{name, value}].
        body = calls[0][1]["json_body"]
        assert body["tokens"] == [{"name": "Client.Name", "value": "Acme"}]
        # Sequence: create → poll → send.
        assert [c[0] for c in calls] == [
            "createDocument", "statusDocument", "sendDocument",
        ]

    def test_no_send_immediately_skips_send(self, workflows, monkeypatch):
        from tools import pandadoc_workflows as pw
        from tools.pandadoc_workflows import _SendQuoteInput

        def fake_call(op, **kw):  # noqa: ANN001
            if op == "createDocument":
                return {"id": "DOC-XYZ", "status": "document.uploaded"}
            raise AssertionError(f"only createDocument should fire, not {op}")

        monkeypatch.setattr(pw.pandadoc_client, "call", fake_call)

        send_quote = workflows["workflow_send_quote"]
        result = send_quote(_SendQuoteInput(
            template_uuid="tmpl-2",
            document_name="Draft only",
            recipients=[{"email": "x@y.com"}],
            send_immediately=False,
        ))
        assert result["document_id"] == "DOC-XYZ"
        assert result["document_status"] == "document.uploaded"
        assert "send_result" not in result

    def test_create_returns_no_id_surfaces_error(self, workflows, monkeypatch):
        from tools import pandadoc_workflows as pw
        from tools.pandadoc_workflows import _SendQuoteInput
        monkeypatch.setattr(
            pw.pandadoc_client, "call",
            lambda op, **kw: {"error": "missing template"},
        )
        result = workflows["workflow_send_quote"](_SendQuoteInput(
            template_uuid="bogus",
            document_name="X",
            recipients=[{"email": "x@y.com"}],
        ))
        assert "error" in result and "didn't return a document id" in result["error"]

    def test_poll_timeout_short_circuits_to_send_error(self, workflows, monkeypatch):
        from tools import pandadoc_workflows as pw
        from tools.pandadoc_workflows import _SendQuoteInput

        def fake_call(op, **kw):  # noqa: ANN001
            if op == "createDocument":
                return {"id": "DOC-TO", "status": "document.uploaded"}
            if op == "statusDocument":
                # Stays in 'document.uploaded' forever.
                return {"status": "document.uploaded"}
            raise AssertionError(f"sendDocument should not fire after timeout, got {op}")

        monkeypatch.setattr(pw.pandadoc_client, "call", fake_call)
        # Force a short poll window so the test runs fast.
        monkeypatch.setattr(pw, "_wait_for_draft", _make_timeout_helper(pw))

        result = workflows["workflow_send_quote"](_SendQuoteInput(
            template_uuid="tmpl-3",
            document_name="Times out",
            recipients=[{"email": "x@y.com"}],
            send_immediately=True,
        ))
        assert "send_error" in result
        assert "Timed out" in result["send_error"]
        assert result["document_status"] == "document.uploaded"


def _make_timeout_helper(pw):
    """Returns a substitute _wait_for_draft that always raises
    PandaDocPollTimeout immediately."""
    def _raises(*args, **kwargs):  # noqa: ANN002
        raise pw.pandadoc_client.PandaDocPollTimeout("forced timeout for test")
    return _raises


# --------------------------------------------------------------------------- #
# workflow_signature_status
# --------------------------------------------------------------------------- #


class TestSignatureStatus:
    def test_sent_doc_with_age_under_stale(self, workflows, monkeypatch):
        from tools import pandadoc_workflows as pw
        from tools.pandadoc_workflows import _SignatureStatusInput

        sent_iso = (
            _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=2)
        ).isoformat().replace("+00:00", "Z")
        monkeypatch.setattr(
            pw.pandadoc_client, "call",
            lambda op, **kw: {
                "status": "document.sent",
                "date_sent": sent_iso,
                "recipients": [{"email": "x@y.com", "has_completed": False}],
            },
        )
        out = workflows["workflow_signature_status"](
            _SignatureStatusInput(document_id="DOC-1"),
        )
        assert out["status"] == "document.sent"
        assert out["days_in_stage"] == 2
        assert out["is_stalled"] is False
        assert out["next_action"]

    def test_sent_doc_past_stale_threshold_is_stalled(self, workflows, monkeypatch):
        from tools import pandadoc_workflows as pw
        from tools.pandadoc_workflows import _SignatureStatusInput

        sent_iso = (
            _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=14)
        ).isoformat().replace("+00:00", "Z")
        monkeypatch.setattr(
            pw.pandadoc_client, "call",
            lambda op, **kw: {
                "status": "document.sent",
                "date_sent": sent_iso,
                "recipients": [],
            },
        )
        out = workflows["workflow_signature_status"](
            _SignatureStatusInput(document_id="DOC-2"),
        )
        assert out["is_stalled"] is True
        assert out["days_in_stage"] >= 14

    def test_completed_doc_uses_completion_date_for_age(self, workflows, monkeypatch):
        from tools import pandadoc_workflows as pw
        from tools.pandadoc_workflows import _SignatureStatusInput

        sent = (
            _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=5)
        ).isoformat().replace("+00:00", "Z")
        completed = (
            _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=1)
        ).isoformat().replace("+00:00", "Z")
        monkeypatch.setattr(
            pw.pandadoc_client, "call",
            lambda op, **kw: {
                "status": "document.completed",
                "date_sent": sent,
                "date_completed": completed,
                "recipients": [],
            },
        )
        out = workflows["workflow_signature_status"](
            _SignatureStatusInput(document_id="DOC-3"),
        )
        assert out["status"] == "document.completed"
        # 5 days sent → 1 day completed = 4 days in flight.
        assert out["days_in_stage"] == 4
        assert out["is_stalled"] is False


# --------------------------------------------------------------------------- #
# workflow_quote_pipeline
# --------------------------------------------------------------------------- #


class TestQuotePipeline:
    def _docs(self, *, sent_long_ago=True, draft_count=2):
        now = _dt.datetime.now(_dt.timezone.utc)
        old_iso = (
            now - _dt.timedelta(days=14 if sent_long_ago else 2)
        ).isoformat().replace("+00:00", "Z")
        out = []
        for i in range(draft_count):
            out.append({
                "id": f"DRAFT-{i}", "name": f"Draft {i}",
                "status": "document.draft", "date_modified": old_iso,
            })
        out.append({
            "id": "SENT-OLD", "name": "Sent stale",
            "status": "document.sent", "date_modified": old_iso,
        })
        out.append({
            "id": "DONE", "name": "Done",
            "status": "document.completed",
            "date_modified": old_iso,
        })
        return out

    def test_groups_by_status_and_flags_stalled(self, workflows, monkeypatch):
        from tools import pandadoc_workflows as pw
        from tools.pandadoc_workflows import _QuotePipelineInput

        docs = self._docs(sent_long_ago=True)

        def fake_call(op, **kw):  # noqa: ANN001
            assert op == "listDocuments"
            page = kw["query"].get("page", 1)
            # Single page in this test.
            return {"results": docs if page == 1 else []}

        monkeypatch.setattr(pw.pandadoc_client, "call", fake_call)

        out = workflows["workflow_quote_pipeline"](
            _QuotePipelineInput(stale_days=7),
        )
        assert out["total_count"] == 4
        assert out["by_status"]["document.draft"]["count"] == 2
        assert out["by_status"]["document.sent"]["count"] == 1
        assert out["by_status"]["document.completed"]["count"] == 1
        assert len(out["stalled"]) == 1
        assert out["stalled"][0]["id"] == "SENT-OLD"
        assert out["stalled"][0]["days_old"] >= 7

    def test_no_stalled_when_under_threshold(self, workflows, monkeypatch):
        from tools import pandadoc_workflows as pw
        from tools.pandadoc_workflows import _QuotePipelineInput

        docs = self._docs(sent_long_ago=False)
        monkeypatch.setattr(
            pw.pandadoc_client, "call",
            lambda op, **kw: {"results": docs},
        )
        out = workflows["workflow_quote_pipeline"](
            _QuotePipelineInput(stale_days=7),
        )
        assert out["stalled"] == []

    def test_pagination_walks_pages_until_empty(self, workflows, monkeypatch):
        from tools import pandadoc_workflows as pw
        from tools.pandadoc_workflows import _QuotePipelineInput

        page_responses = {
            1: [{"id": f"D{i}", "status": "document.draft"} for i in range(100)],
            2: [{"id": f"E{i}", "status": "document.draft"} for i in range(50)],
        }

        def fake_call(op, **kw):  # noqa: ANN001
            page = kw["query"].get("page", 1)
            return {"results": page_responses.get(page, [])}

        monkeypatch.setattr(pw.pandadoc_client, "call", fake_call)
        out = workflows["workflow_quote_pipeline"](_QuotePipelineInput())
        assert out["total_count"] == 150


# --------------------------------------------------------------------------- #
# workflow_quote_to_invoice
# --------------------------------------------------------------------------- #


class TestQuoteToInvoice:
    def test_rejects_non_completed_status(self, workflows, monkeypatch):
        from tools import pandadoc_workflows as pw
        from tools.pandadoc_workflows import _QuoteToInvoiceInput

        monkeypatch.setattr(
            pw.pandadoc_client, "call",
            lambda op, **kw: {"status": "document.sent"},
        )
        out = workflows["workflow_quote_to_invoice"](
            _QuoteToInvoiceInput(document_id="DOC-X", project_code="ALPHA"),
        )
        assert "error" in out
        assert "document.completed" in out["error"]

    def test_rejects_missing_project(self, workflows, monkeypatch):
        from tools import pandadoc_workflows as pw
        from tools.pandadoc_workflows import _QuoteToInvoiceInput

        monkeypatch.setattr(
            pw.pandadoc_client, "call",
            lambda op, **kw: {
                "status": "document.completed",
                "grand_total": {"amount": 1000.0},
                "recipients": [{"email": "buyer@acme.com",
                                "first_name": "B", "last_name": "Buyer"}],
            },
        )
        # ar_invoicing / ar_send / project_registry are imported lazily.
        # Patch project_registry.get to return None.
        import sys

        fake_pr = mock.MagicMock()
        fake_pr.get.return_value = None
        monkeypatch.setitem(sys.modules, "project_registry", fake_pr)
        # ar_invoicing / ar_send shouldn't be touched on this path, but
        # provide stubs so the import doesn't blow up.
        monkeypatch.setitem(sys.modules, "ar_invoicing", mock.MagicMock())
        monkeypatch.setitem(sys.modules, "ar_send", mock.MagicMock())

        out = workflows["workflow_quote_to_invoice"](
            _QuoteToInvoiceInput(document_id="DOC-Y", project_code="GHOST"),
        )
        assert "error" in out
        assert "not in registry" in out["error"]


# --------------------------------------------------------------------------- #
# workflow_resend_quote
# --------------------------------------------------------------------------- #


class TestResendQuote:
    def test_happy_path(self, workflows, monkeypatch):
        from tools import pandadoc_workflows as pw
        from tools.pandadoc_workflows import _ResendQuoteInput

        captured = {}

        def fake_call(op, **kw):  # noqa: ANN001
            captured["op"] = op
            captured["body"] = kw.get("json_body")
            return {"id": "RM-1", "status": "ok"}

        monkeypatch.setattr(pw.pandadoc_client, "call", fake_call)

        out = workflows["workflow_resend_quote"](_ResendQuoteInput(
            document_id="DOC-1",
        ))
        assert out["sent"] is True
        assert out["document_id"] == "DOC-1"
        assert captured["op"] == "createManualReminder"
        # Default polite reminder text used when custom_message is empty.
        assert "nudge" in (captured["body"] or {}).get("message", "").lower()

    def test_custom_message_is_passed_through(self, workflows, monkeypatch):
        from tools import pandadoc_workflows as pw
        from tools.pandadoc_workflows import _ResendQuoteInput

        captured = {}
        monkeypatch.setattr(
            pw.pandadoc_client, "call",
            lambda op, **kw: captured.setdefault("body", kw.get("json_body")) or {"id": "RM-2"},
        )
        workflows["workflow_resend_quote"](_ResendQuoteInput(
            document_id="DOC-2",
            custom_message="Hi! quick check-in on this quote.",
        ))
        assert captured["body"]["message"] == "Hi! quick check-in on this quote."

    def test_api_error_returns_clean_failure(self, workflows, monkeypatch):
        from tools import pandadoc_workflows as pw
        from tools.pandadoc_workflows import _ResendQuoteInput

        def boom(op, **kw):  # noqa: ANN001
            raise pw.pandadoc_client.PandaDocAPIError(
                429, {"detail": "rate limited"}, message="rate limited",
            )

        monkeypatch.setattr(pw.pandadoc_client, "call", boom)
        out = workflows["workflow_resend_quote"](_ResendQuoteInput(
            document_id="DOC-3",
        ))
        assert out["sent"] is False
        assert "rate limited" in out["error"]


# --------------------------------------------------------------------------- #
# Pydantic input model coverage (parity with test_new_workflows.py style)
# --------------------------------------------------------------------------- #


class TestInputModels:
    def test_send_quote_rejects_extra_fields(self):
        from tools.pandadoc_workflows import _SendQuoteInput
        with pytest.raises(Exception):
            _SendQuoteInput(
                template_uuid="t", document_name="x",
                recipients=[], surprise="boom",
            )

    def test_signature_status_requires_document_id(self):
        from tools.pandadoc_workflows import _SignatureStatusInput
        with pytest.raises(Exception):
            _SignatureStatusInput()

    def test_quote_pipeline_defaults(self):
        from tools.pandadoc_workflows import _QuotePipelineInput
        m = _QuotePipelineInput()
        assert m.stale_days == 7
        assert m.statuses is None

    def test_quote_to_invoice_defaults(self):
        from tools.pandadoc_workflows import _QuoteToInvoiceInput
        m = _QuoteToInvoiceInput(document_id="d", project_code="p")
        assert m.send_invoice_immediately is False
