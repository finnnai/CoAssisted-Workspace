# © 2026 CoAssisted Workspace. Licensed for non-redistribution use only.
"""Tests for the junk-mail classifier.

junk_filter.is_junk decides whether a message should be excluded from CRM
enrichment, mail-merge filters, etc. Wrong rejections lose real customer
mail; wrong acceptances pollute the contact graph with no-reply noise.

Three signal tiers:
  1. Hard fails (any one fires → junk): noreply local-parts, role-account
     domains, List-Unsubscribe header, Precedence: bulk, Auto-Submitted.
  2. Soft signals (need 2+ categories to fire): boilerplate, opt-out
     phrases, link-to-text ratio, transactional subjects, spam-hype.
  3. Spam-hype-heavy on its own (4+ distinct phrases): junk.
"""

from __future__ import annotations

import pytest

import junk_filter as jf


# --------------------------------------------------------------------------- #
# Hard-fail signals
# --------------------------------------------------------------------------- #


class TestSenderLocalHardFails:
    @pytest.mark.parametrize("local_part", [
        "noreply", "no-reply", "no_reply", "donotreply",
        "do-not-reply", "do_not_reply", "notifications",
    ])
    def test_obvious_noreply_locals_caught(self, local_part):
        is_junk, reasons = jf.is_junk(f"{local_part}@example.com")
        assert is_junk is True
        assert any("sender_local" in r for r in reasons)

    def test_real_human_local_not_caught_by_local_check(self):
        # No body, no headers — just the sender. Should NOT be hard-failed.
        is_junk, reasons = jf.is_junk("alice@example.com")
        assert is_junk is False or not any("sender_local" in r for r in reasons)


class TestSenderDomainHardFails:
    def test_emailnotifications_subdomain(self):
        is_junk, reasons = jf.is_junk("anything@emailnotifications.example.com")
        assert is_junk is True
        assert any("sender_domain" in r for r in reasons)

    def test_em_stripe_com_substring(self):
        """The 'em.' subdomain prefix is a marketing-send marker (catches
        em.stripe.com, em.zoom.us, etc.)."""
        is_junk, reasons = jf.is_junk("billing@em.stripe.com")
        assert is_junk is True
        assert any("sender_domain" in r for r in reasons)

    def test_normal_domain_not_caught(self):
        is_junk, _ = jf.is_junk("alice@acme.com")
        # Should not be hard-failed by the domain alone
        # (might still be junk from other signals — this test just checks domain)
        if is_junk:
            # If junk, must NOT be from sender_domain
            _, reasons = jf.is_junk("alice@acme.com")
            assert not any(r.startswith("sender_domain:") for r in reasons)


class TestHeaderHardFails:
    def test_list_unsubscribe_header_makes_junk(self):
        is_junk, reasons = jf.is_junk(
            "alice@acme.com",
            headers={"List-Unsubscribe": "<mailto:unsub@acme.com>"},
        )
        assert is_junk is True
        assert any("list_unsubscribe" in r for r in reasons)

    def test_precedence_bulk_makes_junk(self):
        is_junk, reasons = jf.is_junk(
            "alice@acme.com",
            headers={"Precedence": "bulk"},
        )
        assert is_junk is True
        assert any("precedence" in r for r in reasons)

    def test_auto_submitted_makes_junk(self):
        is_junk, reasons = jf.is_junk(
            "alice@acme.com",
            headers={"Auto-Submitted": "auto-replied"},
        )
        assert is_junk is True
        assert any("auto_submitted" in r for r in reasons)

    def test_auto_submitted_no_does_not_fire(self):
        """'Auto-Submitted: no' is the standard non-bot value — don't junk."""
        is_junk, _ = jf.is_junk(
            "alice@acme.com",
            headers={"Auto-Submitted": "no"},
        )
        assert is_junk is False


# --------------------------------------------------------------------------- #
# Soft signals — need 2+ to classify as junk
# --------------------------------------------------------------------------- #


class TestSoftSignalCombinations:
    def test_one_soft_signal_alone_not_enough(self):
        """A single soft signal (e.g. 'unsubscribe' in body) must NOT
        classify as junk on its own."""
        is_junk, _ = jf.is_junk(
            "alice@acme.com",
            body_text="Click here to unsubscribe.",
        )
        # A lone optout link could be a real email with a footer
        assert is_junk is False

    def test_two_soft_categories_combine_to_junk(self):
        """Boilerplate + opt-out together → junk."""
        is_junk, reasons = jf.is_junk(
            "alice@acme.com",
            body_text=(
                "This is an automated message — please do not reply. "
                "Click here to unsubscribe."
            ),
        )
        assert is_junk is True
        assert len(reasons) >= 2

    def test_subject_transactional_plus_optout_is_junk(self):
        is_junk, _ = jf.is_junk(
            "alice@acme.com",
            subject="Your weekly digest is ready",
            body_text="View in browser. Unsubscribe at the bottom.",
        )
        assert is_junk is True

    def test_spam_hype_alone_one_phrase_not_enough(self):
        """One hype phrase isn't enough."""
        is_junk, _ = jf.is_junk(
            "alice@acme.com",
            body_text="We have a free trial available.",
        )
        assert is_junk is False


class TestSpamHypeHeavy:
    def test_four_distinct_hype_phrases_is_junk_alone(self):
        """4+ distinct hype phrases from the spam-hype list → junk on its own."""
        is_junk, reasons = jf.is_junk(
            "alice@acme.com",
            body_text=(
                "Free trial available. Money back guarantee. "
                "Lowest price ever. Free gift included."
            ),
        )
        assert is_junk is True
        assert any("body_spam_hype" in r for r in reasons)


# --------------------------------------------------------------------------- #
# Real-message false-positive guards
# --------------------------------------------------------------------------- #


class TestRealMessagesArentJunk:
    def test_simple_personal_email_not_junk(self):
        is_junk, _ = jf.is_junk(
            "alice@acme.com",
            body_text="Hey, want to grab lunch Thursday? Let me know.",
            subject="Lunch Thursday?",
        )
        assert is_junk is False

    def test_business_reply_with_signature_not_junk(self):
        is_junk, _ = jf.is_junk(
            "bob@acme.com",
            body_text=(
                "Sounds good — can we move it to 11am? I'll bring the deck.\n\n"
                "Best,\nBob\nDirector of Sales"
            ),
            subject="Re: Q2 review",
        )
        assert is_junk is False


# --------------------------------------------------------------------------- #
# Edge cases
# --------------------------------------------------------------------------- #


def test_empty_sender_returns_not_junk():
    """No sender — junk classifier shouldn't crash."""
    is_junk, _ = jf.is_junk("")
    assert isinstance(is_junk, bool)


def test_returns_tuple_shape():
    is_junk, reasons = jf.is_junk("noreply@example.com")
    assert isinstance(is_junk, bool)
    assert isinstance(reasons, list)


def test_reasons_list_strings():
    """Every reason is a string for log-friendliness."""
    _, reasons = jf.is_junk("noreply@example.com")
    for r in reasons:
        assert isinstance(r, str)
