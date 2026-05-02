# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Tier-0.5 receipt classifier bypass — Finnn 2026-05-01 patch.

Allan Renazco's USPS-receipt-with-thin-body case is the canonical
test: internal sender + image attachment + body "receipt" + subject
"test" → bypass fires, regular classifier ladder rejects.

Per the patch (questions 1a + 2a from Joshua):
  - Default base confidence: 0.85 (HIGH).
  - Backfill default: ON (n/a here, that's Part A).
"""

from __future__ import annotations

import sys
import types

import pytest

import receipts


@pytest.fixture
def fake_config_and_sender(monkeypatch):
    """Stub config + sender_classifier so tests don't depend on user setup."""
    fake_cfg = types.SimpleNamespace(
        get=lambda key, default=None: (
            True if key == "receipts_internal_image_bypass" else default
        ),
    )
    monkeypatch.setitem(sys.modules, "config", fake_cfg)

    fake_sc = types.SimpleNamespace(
        internal_domains=lambda: {"surefox.com", "xenture.com"},
    )
    monkeypatch.setitem(sys.modules, "sender_classifier", fake_sc)
    yield


# -----------------------------------------------------------------------------
# Happy path — the actual bug fix
# -----------------------------------------------------------------------------

def test_allans_case_fires_bypass(fake_config_and_sender):
    """Allan's 2026-05-01 USPS receipt: subject 'test', body 'receipt', JPG."""
    ok, reason, conf = receipts.classify_internal_image_bypass(
        subject="test",
        sender="Allan Renazco <allan.renazco@surefox.com>",
        body_preview="receipt",
        attachments=[
            {
                "mimeType": "image/jpeg",
                "filename": "20250129_154047.jpg",
                "size": 1_600_000,
            }
        ],
    )
    assert ok is True
    assert conf == 0.85
    assert "internal" in reason
    assert "surefox.com" in reason


def test_pdf_attachment_also_fires(fake_config_and_sender):
    """application/pdf is in the eligible-mime set."""
    ok, reason, conf = receipts.classify_internal_image_bypass(
        subject="receipt",
        sender="josh@surefox.com",
        body_preview="invoice",
        attachments=[{"mimeType": "application/pdf", "filename": "r.pdf", "size": 50_000}],
    )
    assert ok is True
    assert conf == 0.85


def test_keyword_in_subject_only_works(fake_config_and_sender):
    """Empty body but 'expense' in subject still passes the keyword gate."""
    ok, _reason, _conf = receipts.classify_internal_image_bypass(
        subject="Q1 expense",
        sender="josh@surefox.com",
        body_preview="",
        attachments=[{"mimeType": "image/png", "filename": "r.png", "size": 5000}],
    )
    assert ok is True


def test_subdomain_internal_match(fake_config_and_sender):
    """Subdomain matches when root domain is in internal_domains()."""
    ok, _reason, _conf = receipts.classify_internal_image_bypass(
        subject="test",
        sender="ops.it@accounts.surefox.com",
        body_preview="receipt",
        attachments=[{"mimeType": "image/heic", "filename": "r.heic", "size": 100}],
    )
    assert ok is True


# -----------------------------------------------------------------------------
# Misses — each gate failing in isolation
# -----------------------------------------------------------------------------

def test_external_sender_rejected(fake_config_and_sender):
    ok, reason, conf = receipts.classify_internal_image_bypass(
        subject="test",
        sender="vendor@external.com",
        body_preview="receipt",
        attachments=[{"mimeType": "image/jpeg", "filename": "r.jpg", "size": 1000}],
    )
    assert ok is False
    assert conf == 0.0
    assert "sender_not_internal" in reason


def test_no_attachment_rejected(fake_config_and_sender):
    ok, reason, conf = receipts.classify_internal_image_bypass(
        subject="test",
        sender="josh@surefox.com",
        body_preview="receipt",
        attachments=[],
    )
    assert ok is False
    assert "no_attachments" in reason


def test_non_image_attachment_rejected(fake_config_and_sender):
    """ZIP attachments don't qualify for Vision."""
    ok, reason, _conf = receipts.classify_internal_image_bypass(
        subject="test",
        sender="josh@surefox.com",
        body_preview="receipt",
        attachments=[{"mimeType": "application/zip", "filename": "r.zip", "size": 1000}],
    )
    assert ok is False
    assert "no_eligible_attachment" in reason


def test_long_body_falls_through(fake_config_and_sender):
    """Bodies >= 200 chars don't need the bypass — regular classifier handles."""
    long_body = "Hi team — please find attached receipt for the trip. " * 20
    ok, reason, _conf = receipts.classify_internal_image_bypass(
        subject="receipt",
        sender="josh@surefox.com",
        body_preview=long_body,
        attachments=[{"mimeType": "image/jpeg", "filename": "r.jpg", "size": 1000}],
    )
    assert ok is False
    assert "body_not_thin" in reason


def test_thin_body_no_keyword_rejected(fake_config_and_sender):
    """Internal sender + image but no receipt-shaped keyword anywhere."""
    ok, reason, _conf = receipts.classify_internal_image_bypass(
        subject="lol",
        sender="josh@surefox.com",
        body_preview="look at this dog",
        attachments=[{"mimeType": "image/jpeg", "filename": "dog.jpg", "size": 1000}],
    )
    assert ok is False
    assert "no_bypass_keyword" in reason


def test_unparseable_sender_rejected(fake_config_and_sender):
    """No '@' in the sender field → can't extract domain → reject."""
    ok, reason, _conf = receipts.classify_internal_image_bypass(
        subject="test",
        sender="josh",  # no @ sign
        body_preview="receipt",
        attachments=[{"mimeType": "image/jpeg", "filename": "r.jpg", "size": 1000}],
    )
    assert ok is False
    assert "unparseable" in reason


# -----------------------------------------------------------------------------
# Config kill-switch
# -----------------------------------------------------------------------------

def test_config_disabled_returns_false(monkeypatch):
    """When `config.receipts_internal_image_bypass = False`, the rule is skipped."""
    fake_cfg = types.SimpleNamespace(
        get=lambda key, default=None: (
            False if key == "receipts_internal_image_bypass" else default
        ),
    )
    monkeypatch.setitem(sys.modules, "config", fake_cfg)
    fake_sc = types.SimpleNamespace(internal_domains=lambda: {"surefox.com"})
    monkeypatch.setitem(sys.modules, "sender_classifier", fake_sc)

    ok, reason, _conf = receipts.classify_internal_image_bypass(
        subject="test",
        sender="josh@surefox.com",
        body_preview="receipt",
        attachments=[{"mimeType": "image/jpeg", "filename": "r.jpg", "size": 1000}],
    )
    assert ok is False
    assert "disabled_in_config" in reason


# -----------------------------------------------------------------------------
# Mime-type case-insensitivity
# -----------------------------------------------------------------------------

def test_mime_type_uppercase_still_matches(fake_config_and_sender):
    """Some mail clients send MIME types in mixed case."""
    ok, _reason, _conf = receipts.classify_internal_image_bypass(
        subject="test",
        sender="josh@surefox.com",
        body_preview="receipt",
        attachments=[{"mimeType": "IMAGE/JPEG", "filename": "r.jpg", "size": 1000}],
    )
    assert ok is True


def test_mime_key_alias_mime_underscore_type_works(fake_config_and_sender):
    """Some attachment dicts use 'mime_type' instead of 'mimeType'."""
    ok, _reason, _conf = receipts.classify_internal_image_bypass(
        subject="test",
        sender="josh@surefox.com",
        body_preview="receipt",
        attachments=[{"mime_type": "image/jpeg", "filename": "r.jpg", "size": 1000}],
    )
    assert ok is True
