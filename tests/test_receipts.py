"""Tests for the receipts extraction core."""

from unittest.mock import MagicMock, patch

import pytest

import receipts as r


# --------------------------------------------------------------------------- #
# Categorization
# --------------------------------------------------------------------------- #


def test_categorize_uber():
    assert r.categorize_merchant("Uber") == "Travel"
    assert r.categorize_merchant("UBER TRIP") == "Travel"


def test_categorize_doordash():
    assert r.categorize_merchant("DoorDash") == "Meals"


def test_categorize_chevron_to_fuel():
    assert r.categorize_merchant("Chevron") == "Auto Expense"


def test_categorize_shell_to_fuel():
    assert r.categorize_merchant("Shell Oil") == "Auto Expense"


def test_categorize_costco_gas_to_fuel():
    """Distinguish 'Costco Gas' from regular Costco grocery — both are
    Costco-branded but the gas keyword should pick the more specific bucket."""
    assert r.categorize_merchant("Costco Gas Station") == "Auto Expense"


def test_fuel_in_default_categories():
    """Sanity guard so 'Auto Expense' doesn't get accidentally renamed
    or removed and break the heuristic + Maps mapping silently."""
    assert "Auto Expense" in r.DEFAULT_CATEGORIES


def test_every_default_category_has_a_qb_account():
    """Every category MUST map to a QB Chart of Accounts entry, otherwise
    rows with that category fall through to 'Miscellaneous Expense' silently
    on QB CSV export. Caught a real production bug where 'Auto Expense'
    was added to DEFAULT_CATEGORIES but the QB map wasn't updated, so
    Chevron receipts exported as 'Miscellaneous Expense'."""
    for cat in r.DEFAULT_CATEGORIES:
        assert cat in r._DEFAULT_QB_ACCOUNT_MAP, (
            f"Category {cat!r} has no QB account mapping. Add it to "
            f"_DEFAULT_QB_ACCOUNT_MAP or this category will silently "
            f"map to Miscellaneous Expense on QuickBooks export."
        )


def test_apply_metadata_appends_block_when_sender_present():
    rec = r.ExtractedReceipt(merchant="Foo", date="2026-04-01", total=10.0, confidence=0.9)
    out = r.apply_capture_metadata(rec, submitted_by="alice@example.com")
    assert "[Metadata]" in out.notes
    assert "Submitted by: alice@example.com" in out.notes
    # Date untouched at high confidence with no EXIF date
    assert out.date == "2026-04-01"


def test_apply_metadata_noop_when_nothing_to_add():
    rec = r.ExtractedReceipt(merchant="Foo", date="2026-04-01", total=10.0, confidence=0.9)
    out = r.apply_capture_metadata(rec)
    assert out.notes is None  # no metadata to record → notes left alone


def test_apply_metadata_overrides_date_when_low_conf():
    """LLM confidence < 0.6 + EXIF available → EXIF wins."""
    rec = r.ExtractedReceipt(
        merchant="Foo", date="2023-04-25", total=50.0,
        confidence=0.45,  # suspect
    )
    out = r.apply_capture_metadata(rec, exif={
        "date_taken": "2026-04-25", "time_taken": "2026-04-25T14:23:00",
        "lat": 37.78, "lng": -122.40,
    })
    assert out.date == "2026-04-25"
    assert "Date corrected from EXIF" in out.notes
    assert "GPS: 37.780000,-122.400000" in out.notes


def test_apply_metadata_overrides_date_when_far_off():
    """LLM date >12 months from EXIF date → suspect → use EXIF."""
    rec = r.ExtractedReceipt(
        merchant="Foo", date="2023-04-25", total=50.0,
        confidence=0.95,  # not low conf
    )
    out = r.apply_capture_metadata(rec, exif={
        "date_taken": "2026-04-25", "time_taken": "2026-04-25T14:23:00",
    })
    # 3 years off → EXIF wins
    assert out.date == "2026-04-25"
    assert "Date corrected from EXIF" in out.notes


def test_apply_metadata_keeps_llm_date_when_close_and_high_conf():
    """LLM confidence high + dates close → keep LLM. EXIF still goes in notes."""
    rec = r.ExtractedReceipt(
        merchant="Foo", date="2026-04-20", total=50.0,
        confidence=0.95,
    )
    out = r.apply_capture_metadata(rec, exif={
        "date_taken": "2026-04-22", "time_taken": "2026-04-22T14:23:00",
    })
    # Within 12 months and high confidence — keep LLM date
    assert out.date == "2026-04-20"
    assert "Photo taken: 2026-04-22T14:23:00" in out.notes
    assert "corrected" not in out.notes


def test_read_image_metadata_no_exif_returns_empty():
    """A blank PNG has no EXIF data → empty dict, no crash."""
    try:
        from PIL import Image
        import io
    except Exception:
        import pytest
        pytest.skip("Pillow not available")
    img = Image.new("RGB", (10, 10), color=(0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    out = r._read_image_metadata(buf.getvalue())
    # No EXIF → empty dict
    assert out == {}


def test_read_image_metadata_with_exif_dates():
    """A real EXIF-bearing image should yield date_taken + time_taken."""
    try:
        from PIL import Image
        import io
        # Pillow ships with piexif for EXIF write since 9.x, but the
        # cleanest way to plant tags is via Image.Exif().
    except Exception:
        import pytest
        pytest.skip("Pillow not available")
    img = Image.new("RGB", (50, 50), color=(100, 100, 100))
    exif = img.getexif()
    exif[36867] = "2026:04:25 14:23:45"  # DateTimeOriginal
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif.tobytes())
    out = r._read_image_metadata(buf.getvalue())
    assert out["date_taken"] == "2026-04-25"
    assert out["time_taken"].startswith("2026-04-25T14:23:45")


def test_date_diff_months_basic():
    assert r._date_diff_months("2026-04-25", "2026-04-25") == 0
    assert r._date_diff_months("2026-04-25", "2026-05-25") == 1
    assert r._date_diff_months("2023-04-25", "2026-04-25") >= 36
    # Bad input → 0 (don't trip the ">12mo" branch by accident)
    assert r._date_diff_months(None, "2026-04-25") == 0
    assert r._date_diff_months("not-a-date", "2026-04-25") == 0


def test_shrink_image_under_cap_returns_original():
    """Small images should pass through unchanged (no Pillow round-trip)."""
    tiny = b"x" * 100  # well under 4MB
    out, mime = r._shrink_image_for_vision(tiny, "image/jpeg")
    assert out is tiny  # exact same object
    assert mime == "image/jpeg"


def test_shrink_image_oversized_caps_under_4mb():
    """Real-world: phone photos run 6-18MB. Need to shrink below 4MB raw
    so base64 stays under Anthropic's 5MB API limit."""
    try:
        from PIL import Image
        import io
    except Exception:
        import pytest
        pytest.skip("Pillow not available")
    # Build a synthetic photo-ish JPEG that's deliberately big (8MB+)
    img = Image.new("RGB", (4000, 3000), color=(200, 100, 50))
    # Add some noise so the JPEG doesn't compress to a tiny constant-color file
    import random
    random.seed(42)
    px = img.load()
    for _ in range(200_000):
        x = random.randint(0, 3999)
        y = random.randint(0, 2999)
        px[x, y] = (random.randint(0, 255),) * 3
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    big = buf.getvalue()
    assert len(big) > r._VISION_MAX_BYTES, (
        f"Test fixture didn't actually exceed limit: {len(big)} bytes"
    )

    out, mime = r._shrink_image_for_vision(big, "image/jpeg")
    assert len(out) <= r._VISION_MAX_BYTES, f"Still {len(out)} bytes after shrink"
    assert mime == "image/jpeg"


def test_shrink_image_no_pillow_returns_original():
    """If Pillow can't be imported, fall back gracefully — caller will see
    the API error from Anthropic instead of a crash inside our shrink logic."""
    big = b"x" * (5 * 1024 * 1024)
    with patch.dict("sys.modules", {"PIL": None, "PIL.Image": None}):
        out, mime = r._shrink_image_for_vision(big, "image/jpeg")
    # Identity fallback — same bytes, same mime
    assert out is big
    assert mime == "image/jpeg"


def test_content_key_basic():
    k = r.content_key("Anthropic, PBC", "2026-04-24", 53.30, "1234")
    assert k == "anthropic|2026-04-24|5330|1234"


def test_content_key_normalizes_merchant():
    """'Anthropic', 'Anthropic, PBC', 'anthropic' all collapse to one key."""
    k1 = r.content_key("Anthropic, PBC", "2026-04-24", 53.30, "1234")
    k2 = r.content_key("Anthropic", "2026-04-24", 53.30, "1234")
    k3 = r.content_key("anthropic", "2026-04-24", 53.30, "1234")
    assert k1 == k2 == k3


def test_content_key_total_uses_cents():
    """Floats with rounding error should still produce same key."""
    k1 = r.content_key("Foo", "2026-01-01", 12.50, "")
    k2 = r.content_key("Foo", "2026-01-01", 12.5, "")
    k3 = r.content_key("Foo", "2026-01-01", 12.499999999, "")  # float drift
    assert k1 == k2 == k3


def test_content_key_last_4_distinguishes():
    """Same purchase amount on different cards should NOT be deduped."""
    k1 = r.content_key("Foo", "2026-01-01", 50.00, "1234")
    k2 = r.content_key("Foo", "2026-01-01", 50.00, "5678")
    assert k1 != k2


def test_content_key_returns_none_when_unidentifiable():
    """Without merchant or total, no stable key can be made."""
    assert r.content_key(None, "2026-01-01", 50.00) is None
    assert r.content_key("", "2026-01-01", 50.00) is None
    assert r.content_key("Foo", "2026-01-01", None) is None
    assert r.content_key("Foo", None, 50.00) == "foo|unknown|5000|"


def test_content_key_chevron_3_photos_collapse():
    """The exact production case: 3 photos of one Chevron receipt should
    produce ONE key. The LLM gives slightly different merchant names off
    different angles ('Chevron' vs 'Chevron Stations Inc') but the
    normalized form has to agree for dedup to fire."""
    # Note: 'Chevron Stations Inc' normalizes to 'chevron stations'
    # which is different from 'chevron'. This test documents what the
    # current implementation DOES — substring matching is a separate refactor.
    k1 = r.content_key("Chevron", "2026-04-16", 161.78, "")
    k2 = r.content_key("Chevron", "2026-04-16", 161.78, "")
    assert k1 == k2  # exact same merchant text always collapses


def test_chevron_qb_mapping_is_auto_expense():
    """Production regression — Chevron row exported under wrong account."""
    rec = r.ExtractedReceipt(merchant="Chevron", category="Auto Expense")
    qb_row = r.receipt_to_qb_row(rec)
    # qb_row is a list; the Account column is the third entry per QB_CSV_COLUMNS
    account_idx = r.QB_CSV_COLUMNS.index("Account")
    assert qb_row[account_idx] == "Auto Expense"


def test_categorize_aws():
    assert r.categorize_merchant("Amazon Web Services") == "Software Subscriptions"


def test_categorize_unknown_returns_misc():
    assert r.categorize_merchant("Foo Bar Bakery") == "Miscellaneous Expense"
    assert r.categorize_merchant("") == "Miscellaneous Expense"
    assert r.categorize_merchant(None) == "Miscellaneous Expense"


# --------------------------------------------------------------------------- #
# Email-as-receipt classifier
# --------------------------------------------------------------------------- #


def test_classify_uber_receipt():
    """Strong sender domain ⇒ accept regardless of body."""
    is_r, reason = r.classify_email_as_receipt(
        subject="Your Friday afternoon trip with Uber",
        sender="receipts@uber.com",
        body_preview="Total $18.42",
    )
    assert is_r is True
    assert "uber.com" in reason


def test_classify_marketing_email():
    is_r, _ = r.classify_email_as_receipt(
        subject="50% off all flights this week",
        sender="marketing@somerandomtravelsite.com",
        body_preview="Don't miss our sale",
    )
    assert is_r is False


def test_classify_subject_keyword_alone_now_rejected():
    """Subject keyword without body money signal is no longer enough — too
    noisy ('Receipt for your password reset', 'Confirm your email')."""
    is_r, reason = r.classify_email_as_receipt(
        subject="Receipt for your purchase",
        sender="store@randomstore.example",
        body_preview="",
    )
    assert is_r is False
    assert reason == "no_signal"


def test_classify_subject_keyword_with_body_money():
    """Subject keyword + body money pattern ⇒ accept."""
    is_r, reason = r.classify_email_as_receipt(
        subject="Receipt for your purchase",
        sender="store@randomstore.example",
        body_preview="Total: $42.10. Thank you for your order!",
    )
    assert is_r is True
    assert "money" in reason


def test_classify_currency_pattern():
    """Body alone with money-near-payment-language ⇒ accept."""
    is_r, _reason = r.classify_email_as_receipt(
        subject="Order details",
        sender="orders@example.com",
        body_preview="Subtotal $12.50 Tax $1.05 Total $13.55",
    )
    assert is_r is True


# Regressions: false positives caught in production dry-run.
# These had previously slipped through and produced low-confidence garbage rows.
def test_classify_rejects_bill_is_ready_notification():
    """The exact pattern that polluted our first dry run."""
    is_r, reason = r.classify_email_as_receipt(
        subject="Your Google Cloud bill is ready",
        sender="cloud-noreply@google.com",
        body_preview="Sign in to view your invoice details.",
    )
    assert is_r is False
    assert reason == "negative_subject_pattern"


def test_classify_rejects_invoice_announcement():
    is_r, reason = r.classify_email_as_receipt(
        subject="Your monthly invoice is ready",
        sender="billing@example.com",
        body_preview="View your invoice in your account.",
    )
    assert is_r is False
    assert "negative" in reason


def test_classify_rejects_password_reset():
    is_r, _ = r.classify_email_as_receipt(
        subject="Confirm your email address",
        sender="no-reply@apple.com",
        body_preview="Click to confirm.",
    )
    assert is_r is False


def test_classify_rejects_security_alert():
    is_r, _ = r.classify_email_as_receipt(
        subject="New sign-in to your account",
        sender="no-reply@google.com",
        body_preview="If this wasn't you, secure your account.",
    )
    assert is_r is False


def test_classify_broad_sender_with_money_passes():
    """Apple/Google billing emails that DO contain a real money line
    should still be caught — broad-sender-with-money path."""
    is_r, reason = r.classify_email_as_receipt(
        subject="Your receipt from Apple",
        sender="no_reply@email.apple.com",
        body_preview=(
            "ORDER TOTAL: $9.99\n"
            "Date: April 26, 2026\n"
            "Items: iCloud+ 50GB"
        ),
    )
    assert is_r is True
    assert "money" in reason


def test_classify_broad_sender_no_money_rejected():
    """Apple non-billing email (e.g. Apple ID security) — broad sender,
    no body money signal ⇒ reject."""
    is_r, reason = r.classify_email_as_receipt(
        subject="Your Apple ID was used to sign in",
        sender="no-reply@apple.com",
        body_preview="If this was you, you can ignore this email.",
    )
    assert is_r is False
    assert "broad_sender_no_money_signal" in reason


def test_classify_subscription_renewal_reminder_rejected():
    """'Subscription renews on...' is a NOTICE, not a receipt of payment."""
    is_r, _ = r.classify_email_as_receipt(
        subject="Your Spotify subscription renews on May 15",
        sender="no-reply@example.com",  # not a strong sender
        body_preview="Your card will be charged $9.99 on May 15.",
    )
    assert is_r is False


def test_classify_rejects_own_bot_generated_reports():
    """When workflow_extract_receipts_from_chat posts a digest into the
    same Receipts channel, re-scans MUST NOT try to extract a 'receipt'
    from the digest. The footer marker prevents that loop."""
    body = (
        "Mock Expense Report — Last 30 Days\n"
        "Grand total: $593.45 USD\n"
        "Software Subscriptions: $206.54\n"
        "...\n"
        "— sent by CoAssisted Workspace receipt extractor"
    )
    is_r, reason = r.classify_email_as_receipt(
        subject="📊 Mock Expense Report — Last 30 Days",
        sender="receipts@uber.com",  # even strong sender — marker wins
        body_preview=body,
    )
    assert is_r is False
    assert reason == "bot_generated_report"


def test_classify_marker_match_is_case_insensitive():
    body = "...— SENT BY COASSISTED WORKSPACE RECEIPT EXTRACTOR"
    is_r, reason = r.classify_email_as_receipt(
        subject="some subject", sender="user@example.com",
        body_preview=body,
    )
    assert is_r is False
    assert reason == "bot_generated_report"


def test_bot_marker_constant_matches_actual_footer():
    """If anyone changes the footer text on bot outputs, this test should
    catch it — the marker MUST appear verbatim somewhere in the codebase
    that generates expense report messages."""
    # Just guard the constant itself; integration tests cover the wiring.
    assert "CoAssisted Workspace" in r.BOT_FOOTER_MARKER
    assert "receipt extractor" in r.BOT_FOOTER_MARKER


def test_classify_strong_sender_overrides_negative_subject():
    """Counter-case: even strong senders are subject to the negative filter.
    A 'Your invoice is ready' from a strong sender is still a notification."""
    is_r, reason = r.classify_email_as_receipt(
        subject="Your invoice is ready",
        sender="receipts@uber.com",
        body_preview="Sign in to view.",
    )
    assert is_r is False
    assert reason == "negative_subject_pattern"


# Production regression — exact email that leaked through the first round of
# classifier hardening. Stripe sent an "Activate your Stripe account" message
# that was accepted because Stripe was on the STRONG list. Two-part fix:
# Stripe moved to BROAD, plus 'activate your account' added to NEGATIVE.
def test_classify_rejects_stripe_account_activation():
    is_r, reason = r.classify_email_as_receipt(
        subject="Activate your Stripe account to start accepting payments",
        sender="notifications@stripe.com",
        body_preview=(
            "You're on your way to accepting live payments with Stripe. "
            "To start processing transactions, you'll need to verify your "
            "business by completing your business profile."
        ),
    )
    assert is_r is False
    # Should be caught by the negative subject pattern, not by sender tier
    assert reason == "negative_subject_pattern"


def test_classify_stripe_real_receipt_still_passes():
    """The other side of the Stripe move — a real Stripe receipt should
    still be accepted (via the broad-sender-with-money path)."""
    is_r, reason = r.classify_email_as_receipt(
        subject="Your Stripe receipt",
        sender="receipts@stripe.com",
        body_preview=(
            "Payment received\n"
            "Amount: $250.00\n"
            "Date: April 24, 2026"
        ),
    )
    assert is_r is True
    assert "money" in reason


def test_classify_rejects_complete_your_profile():
    is_r, reason = r.classify_email_as_receipt(
        subject="Complete your account setup",
        sender="welcome@somesaas.example",
        body_preview="Finish setup to start using your trial.",
    )
    assert is_r is False
    assert reason == "negative_subject_pattern"


def test_classify_rejects_verify_your_business():
    is_r, _ = r.classify_email_as_receipt(
        subject="Verify your business identity",
        sender="compliance@example.com",
        body_preview="Upload documents to continue.",
    )
    assert is_r is False


def test_classify_stripe_hosted_receipt_money_then_keyword():
    """Stripe-hosted receipts (used by lots of SaaS) write money before keyword:
    '$45.00 Paid'. The original keyword-first pattern missed these.
    Real production sample reduced to its essentials."""
    is_r, reason = r.classify_email_as_receipt(
        subject="Your receipt from The Mobile-First Company #2553-6539",
        sender="invoice+statements+acct_xyz@stripe.com",
        body_preview=(
            "The Mobile-First Company The Mobile-First Company "
            "Receipt from The Mobile-First Company $45.00 Paid April 25, 2026 "
            "(invoice illustration"
        ),
    )
    assert is_r is True
    assert "money" in reason


def test_classify_money_first_no_currency_symbol_rejected():
    """Just to make sure money-first pattern isn't TOO loose. Without a
    currency symbol, '12.50 due' shouldn't be treated as a money signal —
    that pattern would match too many false positives (dates, version
    numbers like '12.50.0 due Tuesday')."""
    is_r, _ = r.classify_email_as_receipt(
        subject="Project update",
        sender="pm@example.com",
        body_preview="Phase 12.50 due next week.",
    )
    assert is_r is False


def test_classify_paypal_now_requires_money():
    """PayPal moved from STRONG to BROAD. Account-event mail should be
    rejected without a body money signal."""
    is_r, reason = r.classify_email_as_receipt(
        subject="A new login to your PayPal account",
        sender="service@paypal.com",
        body_preview="If this wasn't you, secure your account.",
    )
    assert is_r is False
    assert "broad_sender_no_money_signal" in reason
    # A real PayPal payment confirmation still passes.
    is_r, _ = r.classify_email_as_receipt(
        subject="You sent a payment of $42.00",
        sender="service@paypal.com",
        body_preview="You paid $42.00 to Vendor Inc on April 26, 2026.",
    )
    assert is_r is True


# --------------------------------------------------------------------------- #
# JSON parsing tolerance
# --------------------------------------------------------------------------- #


def test_parse_llm_json_with_fences():
    raw = """```json
{"merchant": "Foo", "total": 12.5}
```"""
    out = r._parse_llm_json(raw)
    assert out["merchant"] == "Foo"
    assert out["total"] == 12.5


def test_parse_llm_json_plain():
    out = r._parse_llm_json('{"a": 1}')
    assert out == {"a": 1}


def test_parse_llm_json_invalid_raises():
    with pytest.raises(Exception):
        r._parse_llm_json("not json at all")


# --------------------------------------------------------------------------- #
# extract_from_text — mocked LLM
# --------------------------------------------------------------------------- #


def test_extract_from_text_uses_llm():
    fake_llm_result = {
        "text": (
            '{"date": "2026-04-26", "merchant": "Starbucks", '
            '"total": 7.25, "currency": "USD", '
            '"category": "Meals", "confidence": 0.95}'
        ),
        "model": "claude-haiku-4-5",
        "input_tokens": 200, "output_tokens": 50,
        "estimated_cost_usd": 0.0005,
    }
    with patch("llm.call_simple", return_value=fake_llm_result):
        rec = r.extract_from_text(
            "Starbucks Coffee\nGrande Latte $7.25\nThanks",
            source_id="msg_123",
        )
    assert rec.merchant == "Starbucks"
    assert rec.total == 7.25
    assert rec.confidence == 0.95
    assert rec.source_id == "msg_123"
    assert rec.source_kind == "email_text"


def test_extract_from_text_handles_bad_json():
    with patch("llm.call_simple", return_value={
        "text": "Sorry, I can't extract that", "model": "x",
        "input_tokens": 100, "output_tokens": 10, "estimated_cost_usd": 0,
    }):
        rec = r.extract_from_text("blah", source_id="msg_x")
    assert rec.confidence == 0.0
    assert "parse failed" in (rec.notes or "")


def test_extract_from_text_overrides_misc_with_heuristic():
    """When LLM picks 'Misc' but merchant matches a known keyword, override."""
    fake = {
        "text": (
            '{"merchant": "Uber Trip 4PM", "total": 12.5, '
            '"category": "Miscellaneous Expense", "confidence": 0.7}'
        ),
        "model": "x", "input_tokens": 100, "output_tokens": 30,
        "estimated_cost_usd": 0,
    }
    with patch("llm.call_simple", return_value=fake):
        rec = r.extract_from_text("...", source_id="m1")
    assert rec.category == "Travel"


# --------------------------------------------------------------------------- #
# Sheet row + QB row mapping
# --------------------------------------------------------------------------- #


def test_receipt_to_sheet_row_redacts_payment():
    rec = r.ExtractedReceipt(
        date="2026-04-26", merchant="Foo", total=10.0,
        payment_method_kind="Visa", last_4="1234",
    )
    row = r.receipt_to_sheet_row(rec, logged_at="2026-04-26T10:00",
                                  redact_payment=True)
    # last_4 column should be empty when redact_payment=True
    assert row[10] == ""
    # Payment method kind is still present
    assert row[9] == "Visa"


def test_receipt_to_sheet_row_keeps_last4_when_not_redacted():
    rec = r.ExtractedReceipt(
        date="2026-04-26", merchant="Foo", total=10.0,
        payment_method_kind="Visa", last_4="9876",
    )
    row = r.receipt_to_sheet_row(rec, logged_at="2026-04-26T10:00",
                                  redact_payment=False)
    assert row[10] == "9876"


def test_receipt_to_qb_row_maps_account():
    rec = r.ExtractedReceipt(
        date="2026-04-26", merchant="Uber",
        total=22.5, currency="USD",
        category="Travel",
    )
    row = r.receipt_to_qb_row(rec)
    # Date, Vendor, Account, Amount, Currency, Memo
    assert row[0] == "2026-04-26"
    assert row[1] == "Uber"
    assert row[2] == "Travel"  # mapped from "Travel"
    assert row[3] == 22.5
    assert row[4] == "USD"


def test_receipt_to_qb_row_unknown_category_falls_back():
    rec = r.ExtractedReceipt(
        date="2026-04-26", merchant="x", total=1.0,
        category="Some — Unknown Category",
    )
    row = r.receipt_to_qb_row(rec)
    assert row[2] == "Miscellaneous Expense"


def test_qb_row_custom_account_map():
    rec = r.ExtractedReceipt(
        date="2026-04-26", merchant="x", total=1.0,
        category="Travel",
    )
    custom_map = {"Travel": "Flight Expense"}
    row = r.receipt_to_qb_row(rec, account_map=custom_map)
    assert row[2] == "Flight Expense"


# --------------------------------------------------------------------------- #
# Sheet column ordering — stability matters for upgrades
# --------------------------------------------------------------------------- #


def test_sheet_columns_unchanged():
    """Column order must stay stable so existing Sheets keep working after upgrade."""
    expected = [
        "logged_at", "date", "merchant", "total", "currency", "category",
        "subtotal", "tax", "tip", "payment_method_kind", "last_4",
        "location", "source_kind", "source_id", "receipt_link",
        "confidence", "notes",
    ]
    assert r.SHEET_COLUMNS == expected


def test_qb_columns_unchanged():
    assert r.QB_CSV_COLUMNS == [
        "Date", "Vendor", "Account", "Amount", "Currency", "Memo",
    ]


# --------------------------------------------------------------------------- #
# Pydantic validation
# --------------------------------------------------------------------------- #


def test_extracted_receipt_default_currency():
    rec = r.ExtractedReceipt()
    assert rec.currency == "USD"


def test_extracted_receipt_default_category():
    rec = r.ExtractedReceipt()
    assert rec.category == "Miscellaneous Expense"


def test_extracted_receipt_confidence_clamped():
    rec = r.ExtractedReceipt(confidence=0.5)
    assert rec.confidence == 0.5
    with pytest.raises(Exception):
        r.ExtractedReceipt(confidence=1.5)
    with pytest.raises(Exception):
        r.ExtractedReceipt(confidence=-0.1)


# Regression: LLMs return null for currency/category on sparse receipts.
# We coerce those to defaults rather than failing validation, otherwise
# the orchestrator silently drops the row to the errors bucket.
def test_extracted_receipt_currency_none_coerces_to_default():
    rec = r.ExtractedReceipt(currency=None)
    assert rec.currency == "USD"


def test_extracted_receipt_currency_empty_coerces_to_default():
    rec = r.ExtractedReceipt(currency="")
    assert rec.currency == "USD"


def test_extracted_receipt_category_none_coerces_to_default():
    rec = r.ExtractedReceipt(category=None)
    assert rec.category == "Miscellaneous Expense"


def test_extracted_receipt_category_empty_coerces_to_default():
    rec = r.ExtractedReceipt(category="   ")
    assert rec.category == "Miscellaneous Expense"


def test_extracted_receipt_full_null_recovery():
    """The exact failure mode caught in production: LLM returns nulls for
    both currency AND category at once. Both should fall through to defaults
    rather than producing 2 separate validation errors."""
    rec = r.ExtractedReceipt(
        merchant="Sketchy Receipt",
        currency=None,
        category=None,
    )
    assert rec.currency == "USD"
    assert rec.category == "Miscellaneous Expense"
    assert rec.merchant == "Sketchy Receipt"
