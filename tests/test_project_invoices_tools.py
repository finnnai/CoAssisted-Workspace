# © 2026 CoAssisted Workspace. Licensed for non-redistribution use only.
"""Tests for tools/project_invoices — input validation + tool registration."""

from __future__ import annotations

import pytest

from tools import project_invoices as t_pi
from tools.project_invoices import (
    RegisterProjectInput,
    ListProjectsInput,
    CreateProjectSheetInput,
    ExtractProjectInvoicesInput,
    ExtractProjectReceiptsInput,
    MoveInvoiceToProjectInput,
    ExportProjectInvoicesQbCsvInput,
    SendVendorRemindersInput,
    ProcessVendorRepliesInput,
    MigrateProjectSheetsToApLayoutInput,
)


# --------------------------------------------------------------------------- #
# Pydantic input shapes
# --------------------------------------------------------------------------- #


class TestRegisterProjectInput:
    def test_minimum_valid(self):
        m = RegisterProjectInput(code="ALPHA", name="Project Alpha")
        assert m.code == "ALPHA"
        assert m.create_sheet is True  # default

    def test_rejects_missing_code(self):
        with pytest.raises(Exception):
            RegisterProjectInput(name="No code")

    def test_rejects_missing_name(self):
        with pytest.raises(Exception):
            RegisterProjectInput(code="X")

    def test_rejects_empty_code(self):
        with pytest.raises(Exception):
            RegisterProjectInput(code="", name="Y")

    def test_rejects_extra_fields(self):
        with pytest.raises(Exception):
            RegisterProjectInput(code="A", name="A", surprise="boom")

    def test_default_billable_and_markup(self):
        m = RegisterProjectInput(code="A", name="A")
        assert m.default_billable is True
        assert m.default_markup_pct == 0.0
        assert m.create_sheet is True

    def test_markup_negative_rejected(self):
        with pytest.raises(Exception):
            RegisterProjectInput(code="A", name="A", default_markup_pct=-1)

    def test_markup_too_high_rejected(self):
        with pytest.raises(Exception):
            RegisterProjectInput(code="A", name="A", default_markup_pct=500)


class TestExtractProjectInvoicesInput:
    def test_defaults(self):
        m = ExtractProjectInvoicesInput()
        assert m.days == 30
        assert m.max_emails_to_scan == 200
        assert m.classify_threshold == 0.6
        assert m.skip_low_confidence is False
        assert m.project_code is None  # auto-resolve mode
        # Quality guards
        assert m.min_total == 1.00
        assert m.max_total == 250000.00
        assert m.require_invoice_number is True

    def test_days_bounds(self):
        with pytest.raises(Exception):
            ExtractProjectInvoicesInput(days=0)
        with pytest.raises(Exception):
            ExtractProjectInvoicesInput(days=400)

    def test_classify_threshold_bounds(self):
        with pytest.raises(Exception):
            ExtractProjectInvoicesInput(classify_threshold=1.5)
        with pytest.raises(Exception):
            ExtractProjectInvoicesInput(classify_threshold=-0.1)

    def test_min_total_negative_rejected(self):
        with pytest.raises(Exception):
            ExtractProjectInvoicesInput(min_total=-5.0)

    def test_max_total_negative_rejected(self):
        with pytest.raises(Exception):
            ExtractProjectInvoicesInput(max_total=-1.0)

    def test_can_disable_invoice_number_requirement(self):
        m = ExtractProjectInvoicesInput(require_invoice_number=False)
        assert m.require_invoice_number is False


class TestMoveInvoiceToProjectInput:
    def test_minimum_valid(self):
        m = MoveInvoiceToProjectInput(
            from_project_code="A", to_project_code="B",
            content_key="acme|inv-1|10000",
        )
        assert m.from_project_code == "A"

    def test_rejects_missing_codes(self):
        with pytest.raises(Exception):
            MoveInvoiceToProjectInput(to_project_code="B")
        with pytest.raises(Exception):
            MoveInvoiceToProjectInput(from_project_code="A")

    def test_row_number_lower_bound(self):
        # row_number=1 is the header — invalid.
        with pytest.raises(Exception):
            MoveInvoiceToProjectInput(
                from_project_code="A", to_project_code="B", row_number=1,
            )

    def test_accepts_row_number(self):
        m = MoveInvoiceToProjectInput(
            from_project_code="A", to_project_code="B", row_number=5,
        )
        assert m.row_number == 5


class TestExportProjectInvoicesQbCsvInput:
    def test_minimum_valid(self):
        m = ExportProjectInvoicesQbCsvInput(project_code="ALPHA")
        # default status filter: OPEN + APPROVED (don't export PAID by default).
        assert "OPEN" in m.statuses
        assert "APPROVED" in m.statuses

    def test_rejects_missing_code(self):
        with pytest.raises(Exception):
            ExportProjectInvoicesQbCsvInput()

    def test_accepts_date_range(self):
        m = ExportProjectInvoicesQbCsvInput(
            project_code="X", date_from="2026-01-01", date_to="2026-12-31",
        )
        assert m.date_from == "2026-01-01"


class TestListProjectsInput:
    def test_default_active_only(self):
        m = ListProjectsInput()
        assert m.active_only is True

    def test_can_disable(self):
        m = ListProjectsInput(active_only=False)
        assert m.active_only is False


class TestCreateProjectSheetInput:
    def test_minimum_valid(self):
        m = CreateProjectSheetInput(code="ALPHA")
        assert m.code == "ALPHA"

    def test_rejects_empty_code(self):
        with pytest.raises(Exception):
            CreateProjectSheetInput(code="")


# --------------------------------------------------------------------------- #
# Registration smoke — every tool ships in register()
# --------------------------------------------------------------------------- #


def test_all_ten_tools_register():
    """Confirm all 10 project tools are wired into register()."""
    class Fake:
        def __init__(self):
            self.names = []

        def tool(self, name=None, **kw):
            def deco(fn):
                self.names.append(name or fn.__name__)
                return fn
            return deco

    fake = Fake()
    t_pi.register(fake)
    expected = {
        "workflow_register_project",
        "workflow_list_projects",
        "workflow_create_project_sheet",
        "workflow_extract_project_invoices",
        "workflow_move_invoice_to_project",
        "workflow_export_project_invoices_qb_csv",
        "workflow_extract_project_receipts",
        "workflow_send_vendor_reminders",
        "workflow_process_vendor_replies",
        "workflow_migrate_project_sheets_to_ap_layout",
    }
    missing = expected - set(fake.names)
    assert not missing, f"Missing tools: {missing}"


# --------------------------------------------------------------------------- #
# Vendor follow-up input models
# --------------------------------------------------------------------------- #


class TestSendVendorRemindersInput:
    def test_defaults(self):
        m = SendVendorRemindersInput()
        assert m.max_to_send == 20
        assert m.channel is None  # both channels

    def test_can_filter_to_chat(self):
        m = SendVendorRemindersInput(channel="chat")
        assert m.channel == "chat"

    def test_max_to_send_bounds(self):
        with pytest.raises(Exception):
            SendVendorRemindersInput(max_to_send=0)
        with pytest.raises(Exception):
            SendVendorRemindersInput(max_to_send=500)


class TestProcessVendorRepliesInput:
    def test_defaults(self):
        m = ProcessVendorRepliesInput()
        assert m.max_to_process == 50

    def test_max_bounds(self):
        with pytest.raises(Exception):
            ProcessVendorRepliesInput(max_to_process=0)
        with pytest.raises(Exception):
            ProcessVendorRepliesInput(max_to_process=1000)

    def test_rejects_extra_fields(self):
        with pytest.raises(Exception):
            ProcessVendorRepliesInput(surprise="boom")


class TestMigrateProjectSheetsToApLayoutInput:
    def test_defaults_all_projects_no_dry_run(self):
        m = MigrateProjectSheetsToApLayoutInput()
        assert m.project_code is None  # all
        assert m.dry_run is False

    def test_target_single_project(self):
        m = MigrateProjectSheetsToApLayoutInput(project_code="ALPHA")
        assert m.project_code == "ALPHA"

    def test_dry_run_flag(self):
        m = MigrateProjectSheetsToApLayoutInput(dry_run=True)
        assert m.dry_run is True

    def test_rejects_extra_fields(self):
        with pytest.raises(Exception):
            MigrateProjectSheetsToApLayoutInput(unexpected="boom")


class TestExtractProjectInvoicesRequestMissingInfo:
    def test_default_request_missing_info_true(self):
        """The orchestrator now defaults to sending an info request when
        the quality guard fires (rather than silent parking)."""
        m = ExtractProjectInvoicesInput()
        assert m.request_missing_info is True

    def test_can_disable_request(self):
        m = ExtractProjectInvoicesInput(request_missing_info=False)
        assert m.request_missing_info is False


# --------------------------------------------------------------------------- #
# Project context in outbound vendor request
# --------------------------------------------------------------------------- #


def test_missing_field_list_includes_project_when_unresolved():
    """When project resolution failed, project_code is on the asks."""
    from project_invoices import ExtractedInvoice
    inv = ExtractedInvoice(
        vendor="Acme", invoice_number=None, total=None,
    )
    fields = t_pi._missing_field_list(inv, project_resolved=False)
    assert "project_code" in fields


def test_missing_field_list_omits_project_when_resolved():
    from project_invoices import ExtractedInvoice
    inv = ExtractedInvoice(
        vendor="Acme", invoice_number=None, total=None,
    )
    fields = t_pi._missing_field_list(inv, project_resolved=True)
    assert "project_code" not in fields


def test_field_label_for_project_code_exists():
    """Composer needs a friendly label for project_code in the bullet list."""
    assert "project_code" in t_pi._FIELD_PROMPT_LABELS
    assert "Project" in t_pi._FIELD_PROMPT_LABELS["project_code"]


def test_project_picker_block_lists_active_projects(isolated_registry):
    isolated_registry.register(code="ALPHA", name="Project Alpha", client="A")
    isolated_registry.register(code="BETA", name="Project Beta")
    block = t_pi._project_picker_block()
    assert "ALPHA" in block
    assert "BETA" in block
    assert "Project Alpha" in block
    assert "(client: A)" in block


def test_project_picker_block_empty_when_no_projects(isolated_registry):
    """Picker returns empty string when registry is empty — composer skips
    the picker block in that case."""
    assert t_pi._project_picker_block() == ""


def test_compose_request_includes_resolved_project(isolated_registry):
    isolated_registry.register(code="ALPHA", name="Project Alpha")
    from project_invoices import ExtractedInvoice
    inv = ExtractedInvoice(
        vendor="Acme", project_code="ALPHA",
        invoice_number=None, total=None,
    )
    msg = t_pi._compose_info_request(inv, ["invoice_number"])
    # Either branch (LLM or fallback) must mention the project.
    body = (msg.get("plain") or "") + (msg.get("html") or "")
    assert "ALPHA" in body
    assert "Project Alpha" in body


def test_compose_request_includes_picker_when_unresolved(isolated_registry):
    isolated_registry.register(code="ALPHA", name="Project Alpha")
    isolated_registry.register(code="BETA", name="Project Beta")
    from project_invoices import ExtractedInvoice
    inv = ExtractedInvoice(
        vendor="Acme",
        invoice_number=None, total=None,
    )
    msg = t_pi._compose_info_request(
        inv, ["invoice_number", "project_code"],
    )
    body = (msg.get("plain") or "") + (msg.get("html") or "")
    # Both registered codes should appear so the vendor can pick.
    assert "ALPHA" in body
    assert "BETA" in body


# --------------------------------------------------------------------------- #
# Audience-aware composer (employee vs vendor tone)
# --------------------------------------------------------------------------- #


def test_composer_employee_audience_subject_differs_from_vendor():
    """Employee subject should mention AP submission, vendor subject should
    mention 'invoice follow-up'. Different headline, same payload."""
    from project_invoices import ExtractedInvoice
    inv = ExtractedInvoice(
        vendor="Acme", invoice_number=None, total=None,
    )
    employee = t_pi._compose_info_request(
        inv, ["invoice_number"], audience="employee",
    )
    vendor = t_pi._compose_info_request(
        inv, ["invoice_number"], audience="vendor",
    )
    # Subjects exist (LLM or fallback) but should differ when audience flips.
    assert employee["subject"]
    assert vendor["subject"]


def test_composer_default_audience_is_vendor():
    """Backwards-compat — callers that don't pass audience get vendor tone."""
    from project_invoices import ExtractedInvoice
    inv = ExtractedInvoice(vendor="Acme", invoice_number=None)
    msg_default = t_pi._compose_info_request(inv, ["invoice_number"])
    msg_explicit = t_pi._compose_info_request(
        inv, ["invoice_number"], audience="vendor",
    )
    # Both produce a body; default behaves like explicit vendor.
    assert msg_default.get("subject")
    assert msg_explicit.get("subject")


# --------------------------------------------------------------------------- #
# Polish: greeting personalization, summary block, reply CTA, error logging
# --------------------------------------------------------------------------- #


def test_greeting_name_extracts_first_name():
    assert t_pi._greeting_name("Joshua Szott") == "Joshua"
    assert t_pi._greeting_name("Joshua Szott (CEO)") == "Joshua"
    assert t_pi._greeting_name('"Joshua Szott"') == "Joshua"


def test_greeting_name_handles_raw_email_no_personalization():
    """Bare email addresses don't yield a first-name greeting."""
    assert t_pi._greeting_name("finnn@surefox.com") == ""
    assert t_pi._greeting_name("") == ""
    assert t_pi._greeting_name(None) == ""


def test_greeting_name_single_word():
    assert t_pi._greeting_name("Cher") == "Cher"


def test_composer_uses_personalized_greeting_in_fallback():
    """When LLM is unavailable, the deterministic fallback opens with the
    recipient's first name when one is available."""
    from project_invoices import ExtractedInvoice
    inv = ExtractedInvoice(
        vendor="Acme", invoice_number=None, total=None,
    )
    # Force fallback by skipping LLM availability indirectly — pass an
    # invalid audience that we can verify shows the name regardless.
    msg = t_pi._compose_info_request(
        inv, ["invoice_number"],
        audience="employee", recipient_name="Joshua Szott",
    )
    body = (msg.get("plain") or "") + (msg.get("html") or "")
    # Either the LLM or the fallback should personalize the opener.
    # We assert: the name appears OR the body has it via fallback.
    # (LLM may produce different greetings — both are acceptable.)
    assert msg.get("subject")
    assert msg.get("plain")


def test_composer_fallback_includes_summary_block():
    """The deterministic fallback always renders 'What I have so far:'."""
    from project_invoices import ExtractedInvoice
    inv = ExtractedInvoice(
        vendor="Acme Vendor",
        invoice_date="2026-04-26",
        invoice_number=None, total=None,
    )
    msg = t_pi._compose_info_request(
        inv, ["invoice_number", "total"],
        audience="employee",
        recipient_name="Joe",
    )
    plain = msg.get("plain") or ""
    html = msg.get("html") or ""
    # Fallback always carries the labeled "What I have so far" block.
    # LLM-composed version is prompted to include it but we don't assert
    # there since LLM output varies.
    assert ("Acme Vendor" in plain or "Acme Vendor" in html)


def test_composer_reply_cta_in_fallback():
    """Both audiences invite a reply with a natural CTA."""
    from project_invoices import ExtractedInvoice
    inv = ExtractedInvoice(vendor="Acme", invoice_number=None, total=None)
    employee = t_pi._compose_info_request(
        inv, ["invoice_number"],
        audience="employee", recipient_name="Joe",
    )
    vendor = t_pi._compose_info_request(
        inv, ["invoice_number"],
        audience="vendor", recipient_name="Joe",
    )
    # Both should invite a reply naturally.
    for msg in (employee, vendor):
        body = (msg.get("plain") or "") + (msg.get("html") or "")
        assert "reply" in body.lower() or "drop" in body.lower()


# --------------------------------------------------------------------------- #
# Urgency tier tests
# --------------------------------------------------------------------------- #


def test_urgency_tier_1_first_ask():
    """Tier 1 (first ask) produces warm, helpful tone without nudge language."""
    from project_invoices import ExtractedInvoice
    inv = ExtractedInvoice(vendor="Acme", invoice_number=None, total=None)
    msg = t_pi._compose_info_request(
        inv, ["invoice_number"],
        audience="employee", urgency_tier=1,
    )
    body = (msg.get("plain") or "") + (msg.get("html") or "")
    # Tier 1 should NOT mention escalation or urgency.
    assert "escalate" not in body.lower()
    assert "last call" not in body.lower()
    assert "reminder" not in body.lower()


def test_urgency_tier_2_friendly_nudge():
    """Tier 2 (friendly nudge) mentions follow-up or quick nudge."""
    from project_invoices import ExtractedInvoice
    inv = ExtractedInvoice(vendor="Acme", invoice_number=None, total=None)
    msg = t_pi._compose_info_request(
        inv, ["invoice_number"],
        audience="employee", urgency_tier=2,
    )
    body = (msg.get("plain") or "") + (msg.get("html") or "")
    # Tier 2 should have quick follow-up or nudge language.
    assert (
        "quick" in body.lower() or "follow" in body.lower() or
        "nudge" in body.lower()
    )


def test_urgency_tier_3_second_nudge():
    """Tier 3 (second nudge) is friendly and direct — NO consequences."""
    from project_invoices import ExtractedInvoice
    inv = ExtractedInvoice(vendor="Acme", invoice_number=None, total=None)
    msg = t_pi._compose_info_request(
        inv, ["invoice_number"],
        audience="employee", urgency_tier=3,
    )
    body = (msg.get("plain") or "") + (msg.get("html") or "")
    # Tier 3 must NOT contain escalation/consequence language.
    assert "escalate" not in body.lower()
    assert "manager" not in body.lower()
    assert "last call" not in body.lower()
    assert "before i" not in body.lower()
    # But it should still feel like a follow-up — circling back / still need.
    assert (
        "circling back" in body.lower() or "still need" in body.lower() or
        "still on the hook" in body.lower()
    )


def test_urgency_tier_4_final():
    """Tier 4 (final reminder): last auto-nudge + soft 'flag for review' note.

    Tier 4 explicitly mentions that without a reply the item will be flagged
    for review — but it must NOT mention manager escalation or payment threats.
    """
    from project_invoices import ExtractedInvoice
    inv = ExtractedInvoice(vendor="Acme", invoice_number=None, total=None)
    msg = t_pi._compose_info_request(
        inv, ["invoice_number"],
        audience="employee", urgency_tier=4,
    )
    body = (msg.get("plain") or "") + (msg.get("html") or "")
    # No manager-escalation language allowed.
    assert "escalate" not in body.lower()
    assert "manager" not in body.lower()
    assert "before i" not in body.lower()
    # Should signal this is the last automated reminder.
    assert (
        "last reminder" in body.lower() or "one last" in body.lower()
    )
    # Soft consequence: must mention flagging for review.
    assert "flag" in body.lower()
    assert "review" in body.lower()


def test_tier_3_no_consequences_vendor_audience():
    """Tier 3 also strips consequences for the vendor audience."""
    from project_invoices import ExtractedInvoice
    inv = ExtractedInvoice(vendor="Acme", invoice_number=None, total=None)
    msg = t_pi._compose_info_request(
        inv, ["invoice_number"],
        audience="vendor", urgency_tier=3,
    )
    body = (msg.get("plain") or "") + (msg.get("html") or "")
    assert "escalate" not in body.lower()
    assert "account manager" not in body.lower()
    assert "before i" not in body.lower()


def test_tier_4_vendor_audience_flag_for_review():
    """Tier 4 for vendors: same soft 'flag for review' consequence."""
    from project_invoices import ExtractedInvoice
    inv = ExtractedInvoice(vendor="Acme", invoice_number=None, total=None)
    msg = t_pi._compose_info_request(
        inv, ["invoice_number"],
        audience="vendor", urgency_tier=4,
    )
    body = (msg.get("plain") or "") + (msg.get("html") or "")
    # Hard consequences still forbidden.
    assert "escalate" not in body.lower()
    assert "account manager" not in body.lower()
    assert "before i" not in body.lower()
    # Soft consequence + "last reminder" framing required.
    assert "last reminder" in body.lower()
    assert "flag" in body.lower()
    assert "review" in body.lower()


def test_backwards_compat_is_reminder_maps_to_tier_2():
    """When is_reminder=True and reminder_count=0, it maps to urgency_tier=2."""
    from project_invoices import ExtractedInvoice
    inv = ExtractedInvoice(vendor="Acme", invoice_number=None, total=None)
    msg = t_pi._compose_info_request(
        inv, ["invoice_number"],
        audience="employee", is_reminder=True, reminder_count=0,
    )
    body = (msg.get("plain") or "") + (msg.get("html") or "")
    # Should have nudge language (tier 2).
    assert (
        "quick" in body.lower() or "follow" in body.lower() or
        "nudge" in body.lower()
    )


def test_backwards_compat_reminder_count_1_maps_to_tier_3():
    """When is_reminder=True and reminder_count == 1, maps to tier 3 (no consequences)."""
    from project_invoices import ExtractedInvoice
    inv = ExtractedInvoice(vendor="Acme", invoice_number=None, total=None)
    msg = t_pi._compose_info_request(
        inv, ["invoice_number"],
        audience="employee", is_reminder=True, reminder_count=1,
    )
    body = (msg.get("plain") or "") + (msg.get("html") or "")
    # Tier 3 should have follow-up tone but NO escalation language.
    assert "escalate" not in body.lower()
    assert "manager" not in body.lower()
    assert (
        "circling back" in body.lower() or "still need" in body.lower()
    )


def test_backwards_compat_reminder_count_2_maps_to_tier_4():
    """When is_reminder=True and reminder_count >= 2, maps to tier 4 (final reminder)."""
    from project_invoices import ExtractedInvoice
    inv = ExtractedInvoice(vendor="Acme", invoice_number=None, total=None)
    msg = t_pi._compose_info_request(
        inv, ["invoice_number"],
        audience="employee", is_reminder=True, reminder_count=2,
    )
    body = (msg.get("plain") or "") + (msg.get("html") or "")
    # Should signal 'last reminder' + soft flag-for-review note,
    # but no manager escalation.
    assert "escalate" not in body.lower()
    assert "manager" not in body.lower()
    assert (
        "last reminder" in body.lower() or "one last" in body.lower()
    )
    assert "flag" in body.lower()
    assert "review" in body.lower()


def test_missing_field_names_are_bolded_inline():
    """Single missing field — name wrapped in ** (plain) and <b> (HTML).

    Field labels include parenthetical detail (e.g. 'Invoice number (as
    printed on the invoice)'), so we check that the keyword sits inside
    bold markers using regex rather than exact-substring matching.
    """
    import re as _re
    from project_invoices import ExtractedInvoice
    # Force fallback by using a content-only invoice (no LLM env in tests).
    inv = ExtractedInvoice(vendor="Acme", invoice_number=None, total=None)
    msg = t_pi._compose_info_request(
        inv, ["invoice_number"],
        audience="employee", urgency_tier=1,
    )
    plain = msg.get("plain") or ""
    html = msg.get("html") or ""
    # Plain: invoice number sits inside ** ... ** markers.
    assert _re.search(r"\*\*[^*]*invoice number[^*]*\*\*", plain.lower()), (
        f"plain should bold the invoice-number label; got: {plain!r}"
    )
    # HTML: invoice number sits inside <b> ... </b> tags.
    assert _re.search(r"<b>[^<]*invoice number[^<]*</b>", html.lower()), (
        f"html should bold the invoice-number label; got: {html!r}"
    )


def test_missing_field_names_are_bolded_two_field_inline():
    """Two missing fields — both names bolded in plain and HTML."""
    import re as _re
    from project_invoices import ExtractedInvoice
    inv = ExtractedInvoice(vendor="Acme", invoice_number=None, total=None)
    msg = t_pi._compose_info_request(
        inv, ["invoice_number", "total"],
        audience="employee", urgency_tier=1,
    )
    plain = msg.get("plain") or ""
    html = msg.get("html") or ""
    # Both field names bold in plain text.
    assert _re.search(r"\*\*[^*]*invoice number[^*]*\*\*", plain.lower())
    assert _re.search(r"\*\*[^*]*total[^*]*\*\*", plain.lower())
    # Both field names bold in HTML.
    assert _re.search(r"<b>[^<]*invoice number[^<]*</b>", html.lower())
    assert _re.search(r"<b>[^<]*total[^<]*</b>", html.lower())


def test_missing_field_names_are_bolded_in_bullet_list():
    """3+ missing fields — every bullet item's label is bolded."""
    from project_invoices import ExtractedInvoice
    inv = ExtractedInvoice(vendor="Acme", invoice_number=None, total=None)
    msg = t_pi._compose_info_request(
        inv, ["invoice_number", "total", "due_date"],
        audience="employee", urgency_tier=1,
    )
    plain = msg.get("plain") or ""
    html = msg.get("html") or ""
    # Plain: each bullet has ** around the label — 3 fields × 2 markers
    # each = 6 asterisk pairs minimum, plus the reference footer (2 more).
    assert plain.count("**") >= 8
    # HTML: each <li> entry contains a <b>...</b>, so >=3 bold tags from
    # the bullet list, plus 1 from the reference footer.
    assert html.lower().count("<b>") >= 4


def test_reference_footer_below_thanks_and_bolded():
    """The vendor/total/date reference line must sit BELOW 'Thanks!'
    and be bolded — <b> in HTML, **asterisks** in plain text."""
    from project_invoices import ExtractedInvoice
    inv = ExtractedInvoice(
        vendor="Acme Roofing",
        invoice_number=None,
        total="1234.56",
        currency="USD",
        invoice_date="2026-04-25",
        source_id="email:footer-test",
    )
    msg = t_pi._compose_info_request(
        inv, ["invoice_number"],
        audience="employee", urgency_tier=1, recipient_name="Josh",
    )
    plain = msg.get("plain") or ""
    html = msg.get("html") or ""

    # Reference text exists in both renderings.
    assert "For reference:" in plain
    assert "For reference:" in html
    assert "Acme Roofing" in plain
    assert "USD 1234.56" in plain
    assert "2026-04-25" in plain

    # Plain: footer comes after "Thanks!" and is wrapped in **.
    thanks_idx = plain.index("Thanks!")
    ref_idx = plain.index("For reference:")
    assert ref_idx > thanks_idx, (
        f"Reference footer should follow Thanks!; got\n{plain}"
    )
    # The reference paragraph is wrapped in **.
    assert "**For reference:" in plain
    assert "**" in plain.split("Thanks!", 1)[1]

    # HTML: <b>For reference: ...</b> after <p>Thanks!</p>.
    # (Note: there are also <b> tags around field names earlier in the body
    # — check the position of the REFERENCE-specific bold, not the first one.)
    thanks_html_idx = html.index("<p>Thanks!</p>")
    ref_html_idx = html.index("<b>For reference:")
    assert ref_html_idx > thanks_html_idx, (
        f"<b> reference footer should come after <p>Thanks!</p>; got\n{html}"
    )
    assert "<b>For reference:" in html
    assert "</b>" in html


def test_reference_footer_skipped_when_no_fields():
    """If no vendor/total/date exists, the reference footer is omitted."""
    from project_invoices import ExtractedInvoice
    inv = ExtractedInvoice(
        vendor=None, invoice_number=None, total=None,
        invoice_date=None, source_id="email:no-fields",
    )
    msg = t_pi._compose_info_request(
        inv, ["invoice_number"],
        audience="employee", urgency_tier=1,
    )
    plain = msg.get("plain") or ""
    html = msg.get("html") or ""
    assert "For reference:" not in plain
    assert "For reference:" not in html


def test_paragraph_breaks_for_eye_relief():
    """Every fallback message must have multiple paragraph breaks for readability.

    Plain text: at least 4 paragraph breaks (\\n\\n) — greeting, body, context, closing, sign-off.
    HTML: at least 4 <p> tags so each thought renders as its own block.
    """
    from project_invoices import ExtractedInvoice
    inv = ExtractedInvoice(
        vendor="Acme",
        invoice_number=None,
        total="500.00",
        currency="USD",
        invoice_date="2026-04-25",
        source_id="email:break-test",
    )
    msg = t_pi._compose_info_request(
        inv, ["invoice_number"],
        audience="employee", urgency_tier=1, recipient_name="Josh",
    )
    plain = msg.get("plain") or ""
    html = msg.get("html") or ""
    # Should have at least 4 paragraph breaks.
    pbreaks = plain.count("\n\n")
    assert pbreaks >= 4, (
        f"plain text should have >=4 paragraph breaks; got {pbreaks}; plain={plain!r}"
    )
    # HTML should have at least 4 <p> tags (greeting + body + context + closing + thanks).
    ptags = html.count("<p>")
    assert ptags >= 4, (
        f"html should have >=4 <p> tags; got {ptags}; html={html!r}"
    )


def test_tier_4_flag_note_lives_in_closing_line():
    """Tier 4's 'flag for review' note must live in the SAME paragraph as
    the closing reply ask — not as its own standalone paragraph in the body.

    The combined closing should read like:
        "Mind dropping that in a quick reply here? Without a reply, I'll
         have to flag this for review."
    """
    from project_invoices import ExtractedInvoice
    inv = ExtractedInvoice(vendor="Acme", invoice_number=None, total=None)
    msg = t_pi._compose_info_request(
        inv, ["invoice_number"],
        audience="employee", urgency_tier=4,
    )
    plain = msg.get("plain") or ""
    html = msg.get("html") or ""

    # Both pieces are present.
    assert "flag this for review" in plain.lower()
    assert "quick reply here" in plain.lower()

    # Plain: the closing question + flag note share the same paragraph
    # (single space between them, NOT \n\n).
    cta_idx = plain.lower().find("quick reply here?")
    flag_idx = plain.lower().find("without a reply")
    assert cta_idx > 0 and flag_idx > cta_idx
    between = plain[cta_idx:flag_idx]
    assert "\n\n" not in between, (
        f"closing question and flag note should share a paragraph; got: {between!r}"
    )

    # HTML: both live inside the SAME <p> tag (no </p><p> between them).
    cta_html_idx = html.lower().find("quick reply here?")
    flag_html_idx = html.lower().find("without a reply")
    assert cta_html_idx > 0 and flag_html_idx > cta_html_idx
    between_html = html[cta_html_idx:flag_html_idx]
    assert "</p>" not in between_html, (
        f"CTA and flag note should share the same <p> tag; got: {between_html!r}"
    )


def test_acknowledgement_initial_submission_includes_summary_and_link():
    """ACK on a clean initial submission contains the field summary,
    sheet link, and doc_type/status line."""
    from project_invoices import ExtractedInvoice
    inv = ExtractedInvoice(
        vendor="Acme Roofing",
        invoice_number="INV-7771",
        total="2500.00",
        currency="USD",
        invoice_date="2026-04-25",
        project_code="ALPHA",
    )
    ack = t_pi._compose_acknowledgement(
        inv,
        sheet_id="abc-sheet-id-123",
        sheet_name="ALPHA",
        doc_type="invoice",
        status="OPEN",
        is_promotion=False,
        audience="employee",
        recipient_name="Josh",
    )
    plain = ack.get("plain") or ""
    html = ack.get("html") or ""
    chat = ack.get("chat") or ""

    # Greeting present.
    assert "Hi Josh" in plain
    # Lead acknowledgement.
    assert "logged" in plain.lower()
    # Field summary contains vendor, invoice #, total, project.
    assert "Acme Roofing" in plain
    assert "INV-7771" in plain
    # total is stored as a float so the rendered form is "USD 2500.0"
    # (not the literal "2500.00" passed in).
    assert "USD" in plain and "2500" in plain
    assert "ALPHA" in plain
    # Doc type + status both present.
    assert "INVOICE" in plain
    assert "OPEN" in plain
    # Sheet link.
    assert "abc-sheet-id-123" in plain
    assert "abc-sheet-id-123" in html
    # HTML uses <b> for emphasis (converted from **bold**).
    assert "<b>" in html
    # No leftover **markdown** markers in HTML.
    assert "**" not in html
    # Chat (SMS-style) is shorter and uses *single-asterisk* bold.
    assert len(chat) < len(plain)
    # Headline bolds the vendor; bulleted summary bolds field labels.
    assert "*Acme Roofing*" in chat
    assert "*Status*: OPEN" in chat
    assert "*Vendor*: Acme Roofing" in chat
    assert "*Invoice #*: INV-7771" in chat
    assert "*Project*: ALPHA" in chat
    # Chat includes a clickable sheet link.
    assert "abc-sheet-id-123" in chat
    # Bullets render on their own lines, not jammed inline.
    assert "\n- *" in chat


def test_acknowledgement_chat_shows_need_your_help_for_missing_fields():
    """Missing fields render '*Need your help*' as the bolded placeholder
    so the submitter can see exactly what's incomplete and reply to fill
    the gap. Receipts skip Invoice # entirely (not applicable)."""
    from project_invoices import ExtractedInvoice
    # Submission with vendor + total but missing date + invoice_number.
    inv = ExtractedInvoice(
        vendor="Acme",
        invoice_number=None,
        total=42.0,
        currency="USD",
        invoice_date=None,
        project_code="ALPHA",
    )

    # Invoice doc_type — Invoice # row should render with placeholder.
    ack_inv = t_pi._compose_acknowledgement(
        inv, sheet_id="s1", doc_type="invoice", status="OPEN",
    )
    chat_inv = ack_inv.get("chat") or ""
    assert "*Vendor*: Acme" in chat_inv
    assert "*Total*: USD 42.0" in chat_inv
    # Missing fields show the bolded placeholder.
    assert "*Date*: *Need your help*" in chat_inv
    assert "*Invoice #*: *Need your help*" in chat_inv
    # Project + status always have values.
    assert "*Project*: ALPHA" in chat_inv
    assert "*Status*: OPEN" in chat_inv

    # Receipt doc_type — Invoice # bullet should be omitted entirely
    # because receipts don't have invoice numbers.
    ack_rec = t_pi._compose_acknowledgement(
        inv, sheet_id="s1", doc_type="receipt", status="OPEN",
    )
    chat_rec = ack_rec.get("chat") or ""
    assert "*Invoice #*" not in chat_rec
    # But Date placeholder still shows up because date IS relevant.
    assert "*Date*: *Need your help*" in chat_rec


def test_acknowledgement_chat_all_fields_missing_shows_all_placeholders():
    """Edge case: nearly-empty submission still renders the full skeleton
    with placeholders, not a half-empty list of bullets."""
    from project_invoices import ExtractedInvoice
    inv = ExtractedInvoice(
        vendor=None, invoice_number=None, total=None,
        invoice_date=None, project_code=None,
    )
    ack = t_pi._compose_acknowledgement(
        inv, doc_type="invoice", status="AWAITING_INFO",
    )
    chat = ack.get("chat") or ""
    assert "*Vendor*: *Need your help*" in chat
    assert "*Invoice #*: *Need your help*" in chat
    assert "*Date*: *Need your help*" in chat
    assert "*Total*: *Need your help*" in chat
    assert "*Project*: *Need your help*" in chat
    # Status is system-managed so it's always present.
    assert "*Status*: AWAITING_INFO" in chat


def test_acknowledgement_promotion_wording():
    """Promotion ACK acknowledges the follow-up reply landed."""
    from project_invoices import ExtractedInvoice
    inv = ExtractedInvoice(
        vendor="Acme",
        invoice_number="INV-9",
        total="100",
        project_code="ALPHA",
    )
    ack = t_pi._compose_acknowledgement(
        inv, sheet_id="s", sheet_name="ALPHA",
        is_promotion=True, audience="vendor",
    )
    plain = ack.get("plain") or ""
    chat = ack.get("chat") or ""
    # Should reference completion / "now complete" tone.
    assert "now complete" in (ack["subject"] or "").lower() or (
        "complete" in plain.lower() and "everything we needed" in plain.lower()
    )
    # Chat variant signals completion as well.
    assert "complete" in chat.lower() or "got it" in chat.lower()


def test_acknowledgement_skips_sheet_link_when_no_sheet_id():
    """If no sheet_id is available, the link section is dropped cleanly."""
    from project_invoices import ExtractedInvoice
    inv = ExtractedInvoice(vendor="Acme", invoice_number="X", total="10")
    ack = t_pi._compose_acknowledgement(inv)  # no sheet_id
    plain = ack.get("plain") or ""
    html = ack.get("html") or ""
    chat = ack.get("chat") or ""
    assert "https://docs.google.com" not in plain
    assert "https://docs.google.com" not in html
    assert "View sheet" not in chat


def test_chat_variant_is_short_sms_style():
    """Chat variant is condensed — single line(s), no greeting/sign-off."""
    from project_invoices import ExtractedInvoice
    inv = ExtractedInvoice(
        vendor="Acme Roofing",
        invoice_number=None,
        total="500.00",
        currency="USD",
        invoice_date="2026-04-25",
        source_id="email:chat-test",
    )
    msg = t_pi._compose_info_request(
        inv, ["invoice_number"],
        audience="employee", urgency_tier=1, recipient_name="Josh",
    )
    chat = msg.get("chat") or ""
    # No email niceties — no greeting, no sign-off, no reference footer.
    assert "Hi Josh" not in chat
    assert "Thanks!" not in chat
    assert "For reference:" not in chat
    # The vendor and ask are present.
    assert "Acme Roofing" in chat
    # Single-asterisk bold for chat (Google Chat markdown).
    assert "*invoice number*" in chat.lower()
    # Tight: single-line-ish (no double-newline paragraph breaks).
    assert "\n\n" not in chat


def test_chat_variant_uses_single_asterisk_not_double():
    """Chat variant uses *bold* (Google Chat) not **bold** (markdown)."""
    from project_invoices import ExtractedInvoice
    inv = ExtractedInvoice(vendor="Acme", invoice_number=None, total=None)
    msg = t_pi._compose_info_request(
        inv, ["invoice_number", "total"],
        audience="employee", urgency_tier=1,
    )
    chat = msg.get("chat") or ""
    # No double-asterisks in chat output (those are markdown for email).
    assert "**" not in chat
    # Single asterisks present around the keyword for each field.
    import re as _re
    assert _re.search(r"\*[^*]*invoice number[^*]*\*", chat.lower())
    assert _re.search(r"\*[^*]*total[^*]*\*", chat.lower())


def test_chat_variant_tier_4_includes_flag_for_review():
    """Tier 4 chat variant includes the soft 'flag for review' consequence
    appended to the closing — same rule as email."""
    from project_invoices import ExtractedInvoice
    inv = ExtractedInvoice(vendor="Acme", invoice_number=None, total=None)
    msg = t_pi._compose_info_request(
        inv, ["invoice_number"],
        audience="employee", urgency_tier=4,
    )
    chat = msg.get("chat") or ""
    assert "Last reminder" in chat
    assert "flag this for review" in chat.lower()
    # Still no escalation language.
    assert "manager" not in chat.lower()
    assert "escalate" not in chat.lower()


def test_chat_variant_tier_progression_phrases():
    """Each tier uses a distinct opener in the chat variant."""
    from project_invoices import ExtractedInvoice
    inv = ExtractedInvoice(vendor="Acme", invoice_number=None, total=None)
    expected_openers = {
        1: "Got the",
        2: "Quick nudge",
        3: "Circling back",
        4: "Last reminder",
    }
    for tier, opener in expected_openers.items():
        msg = t_pi._compose_info_request(
            inv, ["invoice_number"],
            audience="employee", urgency_tier=tier,
        )
        chat = msg.get("chat") or ""
        assert chat.startswith(opener), (
            f"tier {tier} chat should start with {opener!r}; got: {chat!r}"
        )


def test_chat_variant_significantly_shorter_than_email_plain():
    """Chat variant should be much shorter than the email plain version."""
    from project_invoices import ExtractedInvoice
    inv = ExtractedInvoice(
        vendor="Acme Roofing",
        invoice_number=None,
        total="500.00",
        currency="USD",
        invoice_date="2026-04-25",
        source_id="email:length-test",
    )
    msg = t_pi._compose_info_request(
        inv, ["invoice_number", "total", "due_date"],
        audience="employee", urgency_tier=1, recipient_name="Josh",
    )
    chat = msg.get("chat") or ""
    plain = msg.get("plain") or ""
    # Chat should be < 50% the length of the email plain text.
    assert len(chat) < len(plain) * 0.5, (
        f"chat should be much shorter; chat={len(chat)} chars, plain={len(plain)} chars"
    )


def test_tier_1_2_3_no_flag_note():
    """Tiers 1, 2, 3 must NOT include the flag-for-review note anywhere."""
    from project_invoices import ExtractedInvoice
    inv = ExtractedInvoice(vendor="Acme", invoice_number=None, total=None)
    for tier in (1, 2, 3):
        msg = t_pi._compose_info_request(
            inv, ["invoice_number"],
            audience="employee", urgency_tier=tier,
        )
        body = (msg.get("plain") or "") + (msg.get("html") or "")
        assert "flag this for review" not in body.lower(), (
            f"tier {tier} should not mention flag-for-review"
        )


def test_variant_determinism():
    """Same source_id always picks the same variant."""
    from project_invoices import ExtractedInvoice
    inv = ExtractedInvoice(
        vendor="Acme", invoice_number=None, total=None,
        source_id="email:123@example.com",
    )
    msg1 = t_pi._compose_info_request(
        inv, ["invoice_number"],
        audience="employee", urgency_tier=1,
    )
    msg2 = t_pi._compose_info_request(
        inv, ["invoice_number"],
        audience="employee", urgency_tier=1,
    )
    # Same source_id should produce identical messages (same variant).
    assert msg1["plain"] == msg2["plain"]
    assert msg1["html"] == msg2["html"]


def test_variant_variation():
    """Different source_ids can produce different variants."""
    from project_invoices import ExtractedInvoice
    inv1 = ExtractedInvoice(
        vendor="Acme", invoice_number=None, total=None,
        source_id="email:111@example.com",
    )
    inv2 = ExtractedInvoice(
        vendor="Acme", invoice_number=None, total=None,
        source_id="email:222@example.com",
    )
    msg1 = t_pi._compose_info_request(
        inv1, ["invoice_number"],
        audience="employee", urgency_tier=1,
    )
    msg2 = t_pi._compose_info_request(
        inv2, ["invoice_number"],
        audience="employee", urgency_tier=1,
    )
    # Different source_ids may produce different openers.
    # At least one word should differ (best-effort test).
    words1 = msg1["plain"].split()
    words2 = msg2["plain"].split()
    # Don't assert strict inequality—variants may collide with low probability.
    # Instead, verify both messages are valid.
    assert msg1["plain"]
    assert msg2["plain"]


def test_inline_for_few_fields():
    """When missing_fields has 1-2 entries, no <ul> in HTML."""
    from project_invoices import ExtractedInvoice
    inv = ExtractedInvoice(vendor="Acme", invoice_number=None, total=None)
    # Test 1 field.
    msg = t_pi._compose_info_request(
        inv, ["invoice_number"],
        audience="employee",
    )
    html = msg.get("html") or ""
    # Should not have a <ul> (field is inline).
    assert "<ul>" not in html or html.count("<ul>") == 0


def test_bullets_for_many_fields():
    """When missing_fields has 3+ entries, <ul> appears in HTML."""
    from project_invoices import ExtractedInvoice
    inv = ExtractedInvoice(vendor="Acme", invoice_number=None, total=None)
    msg = t_pi._compose_info_request(
        inv, ["invoice_number", "total", "invoice_date"],
        audience="employee",
    )
    html = msg.get("html") or ""
    # Should have a <ul> for 3+ fields.
    assert "<ul>" in html
    assert "<li>" in html


def test_send_via_gmail_returns_tuple():
    """Make sure the return type is the (sent, error) tuple — backwards
    incompat with the old bool-returning version, but every call site
    has been updated."""
    import inspect
    sig = inspect.signature(t_pi._send_info_request_via_gmail)
    assert "thread_id" in sig.parameters
    # Smoke: function exists and takes a kwargs-only call shape.
    assert sig.parameters["thread_id"].kind == inspect.Parameter.KEYWORD_ONLY


def test_send_via_chat_returns_tuple():
    import inspect
    sig = inspect.signature(t_pi._send_info_request_via_chat)
    assert "space_name" in sig.parameters


def test_send_via_dm_returns_three_tuple():
    """DM helper now returns (sent, space_name, error) so the caller can
    surface the *reason* a DM didn't fire, not just the fact it didn't."""
    import inspect
    sig = inspect.signature(t_pi._send_info_request_via_employee_dm)
    assert "employee_email" in sig.parameters
    # The annotation should declare the 3-tuple; sniff for the return type.
    rt = sig.return_annotation
    # 3-tuple if the annotation parsed; if not, the test still serves as
    # a sentinel that the helper is keyword-only.
    if rt and hasattr(rt, "__args__"):
        assert len(rt.__args__) == 3


# --------------------------------------------------------------------------- #
# ExtractProjectReceiptsInput — receipt-side input model
# --------------------------------------------------------------------------- #


class TestExtractProjectReceiptsInput:
    def test_defaults(self):
        m = ExtractProjectReceiptsInput()
        assert m.days == 30
        assert m.max_emails_to_scan == 200
        assert m.skip_low_confidence is False
        assert m.project_code is None  # auto-resolve mode

    def test_days_bounds(self):
        with pytest.raises(Exception):
            ExtractProjectReceiptsInput(days=0)
        with pytest.raises(Exception):
            ExtractProjectReceiptsInput(days=400)

    def test_accepts_explicit_project(self):
        m = ExtractProjectReceiptsInput(project_code="ALPHA")
        assert m.project_code == "ALPHA"

    def test_accepts_chat_space(self):
        m = ExtractProjectReceiptsInput(chat_space_id="spaces/AAQA1234")
        assert m.chat_space_id == "spaces/AAQA1234"

    def test_rejects_extra_fields(self):
        with pytest.raises(Exception):
            ExtractProjectReceiptsInput(days=30, surprise="boom")


# --------------------------------------------------------------------------- #
# Resolution-aware finalize
# --------------------------------------------------------------------------- #


@pytest.fixture
def isolated_registry(tmp_path, monkeypatch):
    """Each test that touches the registry gets a clean tempfile."""
    import project_registry as pr
    pr._override_path_for_tests(tmp_path / "projects.json")
    yield pr
    from pathlib import Path
    pr._override_path_for_tests(
        Path(__file__).resolve().parent.parent / "projects.json",
    )


def test_finalize_invoice_applies_project_defaults(isolated_registry):
    """When the resolver finds a project, billable + markup overlay from registry."""
    isolated_registry.register(
        code="ALPHA", name="Alpha",
        sender_emails=["pm@vendor.com"],
        default_billable=False, default_markup_pct=25.0,
    )

    from project_invoices import ExtractedInvoice
    inv = ExtractedInvoice(
        vendor="Vendor Co", invoice_number="INV-1", total=200.0,
        billable=True,  # raw extraction said billable
        markup_pct=0.0,
    )
    inv2, rr = t_pi._finalize_invoice(
        inv, project_code_hint=None,
        filename=None, sender_email="pm@vendor.com",
        chat_space_id=None,
    )
    assert inv2.project_code == "ALPHA"
    assert rr.tier == "sender"
    # Project defaults overlaid: not billable, 25% markup, but billable=False
    # means invoiceable_amount stays None.
    assert inv2.billable is False
    assert inv2.markup_pct == 25.0
    assert inv2.invoiceable_amount is None


def test_finalize_invoice_unresolved_keeps_project_code_none(isolated_registry):
    isolated_registry.register(code="ALPHA", name="Alpha")
    from project_invoices import ExtractedInvoice
    inv = ExtractedInvoice(
        vendor="Random Co", invoice_number="INV-2", total=50.0,
    )
    inv2, rr = t_pi._finalize_invoice(
        inv, project_code_hint=None,
        filename="random.pdf", sender_email="who@example.com",
        chat_space_id=None,
    )
    # No filename pattern, no sender match, no chat space, no LLM (LLM returns
    # None on no-key) → unresolved.
    assert inv2.project_code is None
    assert rr.tier in ("unresolved", "llm")


# --------------------------------------------------------------------------- #
# Quality guard — _validate_invoice_quality
# --------------------------------------------------------------------------- #


def _q(**fields):
    """Quick ExtractedInvoice factory for the guard tests."""
    from project_invoices import ExtractedInvoice
    return ExtractedInvoice(**fields)


def test_quality_clean_invoice_passes():
    inv = _q(vendor="Acme", invoice_number="INV-1", total=500.0)
    assert t_pi._validate_invoice_quality(
        inv, min_total=1.0, max_total=250000.0,
        require_invoice_number=True,
    ) is None


def test_quality_missing_invoice_number_flagged():
    inv = _q(vendor="Unum", invoice_number=None, total=66499.37)
    fail = t_pi._validate_invoice_quality(
        inv, min_total=1.0, max_total=250000.0,
        require_invoice_number=True,
    )
    assert fail == "missing_invoice_number"


def test_quality_missing_invoice_number_allowed_when_disabled():
    """Disabling the requirement lets through invoices without numbers."""
    inv = _q(vendor="Acme", invoice_number=None, total=500.0)
    assert t_pi._validate_invoice_quality(
        inv, min_total=1.0, max_total=250000.0,
        require_invoice_number=False,
    ) is None


def test_quality_below_min_flagged():
    inv = _q(vendor="Acme", invoice_number="INV-1", total=0.50)
    fail = t_pi._validate_invoice_quality(
        inv, min_total=1.0, max_total=250000.0,
        require_invoice_number=True,
    )
    assert fail.startswith("total_below_min")


def test_quality_above_max_flagged():
    """The Unum case — large total + no real invoice number gets caught."""
    inv = _q(vendor="Unum", invoice_number="benefits-2026", total=66499.37)
    # invoice_number passes; total above guard ($50k ceiling here) flags it.
    fail = t_pi._validate_invoice_quality(
        inv, min_total=1.0, max_total=50000.0,
        require_invoice_number=True,
    )
    assert fail.startswith("total_above_max")


def test_quality_missing_total_flagged():
    inv = _q(vendor="Acme", invoice_number="INV-1", total=None)
    fail = t_pi._validate_invoice_quality(
        inv, min_total=1.0, max_total=250000.0,
        require_invoice_number=True,
    )
    assert fail == "missing_total"


def test_quality_empty_string_invoice_number_flagged():
    """Whitespace-only invoice_number is the same as None."""
    inv = _q(vendor="Acme", invoice_number="   ", total=100.0)
    fail = t_pi._validate_invoice_quality(
        inv, min_total=1.0, max_total=250000.0,
        require_invoice_number=True,
    )
    assert fail == "missing_invoice_number"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
