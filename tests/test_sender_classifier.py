# © 2026 CoAssisted Workspace. Licensed for non-redistribution use only.
"""Tests for sender_classifier — internal vs external detection."""

from __future__ import annotations

from unittest.mock import patch

import pytest

import sender_classifier as sc


@pytest.fixture(autouse=True)
def _clear_caches():
    """Each test gets a fresh classifier — no cross-test pollution from
    the process-lifetime caches."""
    sc._reset_for_tests()
    yield
    sc._reset_for_tests()


# --------------------------------------------------------------------------- #
# Email + domain helpers
# --------------------------------------------------------------------------- #


def test_extract_email_plain():
    assert sc._extract_email("billing@vendor.io") == "billing@vendor.io"


def test_extract_email_rfc_format():
    assert sc._extract_email(
        "Billing Dept <billing@vendor.io>"
    ) == "billing@vendor.io"


def test_extract_email_lowercases():
    assert sc._extract_email("Billing@Vendor.IO") == "billing@vendor.io"


def test_extract_email_empty_returns_empty():
    assert sc._extract_email("") == ""
    assert sc._extract_email(None) == ""


def test_domain_of():
    assert sc._domain_of("a@example.com") == "example.com"
    assert sc._domain_of("a@SUB.example.COM") == "sub.example.com"
    assert sc._domain_of("not-an-email") == ""


# --------------------------------------------------------------------------- #
# classify() — the headline behavior
# --------------------------------------------------------------------------- #


def test_classify_auto_domain_match():
    sc._override_for_tests(auto_domain="surefox.com", send_as=set())
    out = sc.classify("pm@surefox.com")
    assert out["internal"] is True
    assert out["tier"] == "auto_domain"
    assert "surefox.com" in out["reason"]


def test_classify_external_when_no_match():
    sc._override_for_tests(auto_domain="surefox.com", send_as=set())
    out = sc.classify("billing@unum.com")
    assert out["internal"] is False
    assert out["tier"] == "external"


def test_classify_handles_rfc_format_sender():
    sc._override_for_tests(auto_domain="surefox.com", send_as=set())
    out = sc.classify("PM Joe <joe@surefox.com>")
    assert out["internal"] is True
    assert out["email"] == "joe@surefox.com"


def test_classify_empty_sender_external():
    sc._override_for_tests(auto_domain="surefox.com", send_as=set())
    assert sc.classify("").get("internal") is False
    assert sc.classify(None).get("internal") is False


def test_classify_subdomain_does_not_match():
    """Subdomains aren't auto-included — must be added explicitly."""
    sc._override_for_tests(auto_domain="surefox.com", send_as=set())
    out = sc.classify("user@subsidiary.surefox.com")
    assert out["internal"] is False


def test_classify_config_internal_domains_extends():
    """An explicit internal_domains entry adds to the auto-derived set."""
    sc._override_for_tests(auto_domain="surefox.com", send_as=set())
    with patch.object(sc, "_config_internal_domains",
                      return_value={"affiliate.com"}):
        out = sc.classify("partner@affiliate.com")
    assert out["internal"] is True
    assert out["tier"] == "config_internal"


def test_classify_subsidiary_domains_treated_internal():
    sc._override_for_tests(auto_domain="surefox.com", send_as=set())
    with patch.object(sc, "_config_subsidiary_domains",
                      return_value={"surefox-eu.com"}):
        out = sc.classify("rep@surefox-eu.com")
    assert out["internal"] is True
    assert out["tier"] == "config_subsidiary"


def test_classify_send_as_alias_internal():
    """When sender exactly matches one of the user's Gmail send-as aliases,
    they're treated as internal (e.g. user forwards from another mailbox)."""
    sc._override_for_tests(
        auto_domain="surefox.com",
        send_as={"finnn@otherdomain.io"},
    )
    out = sc.classify("finnn@otherdomain.io")
    assert out["internal"] is True
    assert out["tier"] == "send_as_alias"


def test_classify_priority_auto_beats_config():
    """If a domain matches BOTH auto and config, auto wins (it's the
    'primary' identity)."""
    sc._override_for_tests(auto_domain="surefox.com", send_as=set())
    with patch.object(sc, "_config_internal_domains",
                      return_value={"surefox.com"}):
        out = sc.classify("user@surefox.com")
    assert out["tier"] == "auto_domain"  # not config_internal


def test_classify_external_includes_diagnostic_reason():
    sc._override_for_tests(auto_domain="surefox.com", send_as=set())
    out = sc.classify("vendor@example.com")
    assert "example.com" in out["reason"]
    assert "not in any internal allowlist" in out["reason"]


# --------------------------------------------------------------------------- #
# is_internal() convenience
# --------------------------------------------------------------------------- #


def test_is_internal_true():
    sc._override_for_tests(auto_domain="surefox.com", send_as=set())
    assert sc.is_internal("hr@surefox.com") is True


def test_is_internal_false():
    sc._override_for_tests(auto_domain="surefox.com", send_as=set())
    assert sc.is_internal("pm@unum.com") is False


# --------------------------------------------------------------------------- #
# internal_domains() inventory
# --------------------------------------------------------------------------- #


def test_internal_domains_unions_all_sources():
    sc._override_for_tests(auto_domain="surefox.com", send_as=set())
    with patch.object(sc, "_config_internal_domains",
                      return_value={"affiliate.com"}):
        with patch.object(sc, "_config_subsidiary_domains",
                          return_value={"surefox-eu.com"}):
            out = sc.internal_domains()
    assert out == {"surefox.com", "affiliate.com", "surefox-eu.com"}


def test_norm_domain_list_strips_at_sign():
    """Users may write '@example.com' in config — normalize to 'example.com'."""
    out = sc._norm_domain_list(["@example.com", "  Other.IO  ", "", None])
    assert out == {"example.com", "other.io"}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
