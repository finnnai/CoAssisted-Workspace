"""Tests for rules.py — auto-tagging logic, no network."""

import rules


def _set_rules(cfg):
    rules._cache = cfg


def teardown_function(_):
    rules._cache = None


def test_no_email_returns_empty():
    _set_rules({"domain_rules": {"acme.com": {"tier": "enterprise"}}})
    top, custom, applied = rules.apply_rules(None)
    assert top == {}
    assert custom == {}
    assert applied == []


def test_domain_match_fills_blanks():
    _set_rules({"domain_rules": {"acme.com": {"organization": "Acme", "tier": "enterprise"}}})
    top, custom, applied = rules.apply_rules("x@acme.com")
    assert top == {"organization": "Acme"}
    assert custom == {"tier": "enterprise"}
    assert applied == ["acme.com"]


def test_existing_fields_not_overwritten():
    _set_rules({"domain_rules": {"acme.com": {"organization": "Acme Corp"}}})
    top, _custom, applied = rules.apply_rules(
        "x@acme.com",
        existing_fields={"organization": "Already Set"},
    )
    assert "organization" not in top
    assert applied == ["acme.com"]


def test_existing_custom_not_overwritten():
    _set_rules({"domain_rules": {"acme.com": {"tier": "enterprise"}}})
    _top, custom, _applied = rules.apply_rules(
        "x@acme.com",
        existing_custom={"tier": "already_set"},
    )
    assert custom == {}


def test_at_prefix_tolerated():
    _set_rules({"domain_rules": {"@acme.com": {"tier": "enterprise"}}})
    _top, custom, applied = rules.apply_rules("x@acme.com")
    assert custom == {"tier": "enterprise"}
    assert applied == ["@acme.com"]


def test_subdomain_match():
    _set_rules({"domain_rules": {"acme.com": {"tier": "enterprise"}}})
    _top, custom, applied = rules.apply_rules("x@mail.acme.com")
    assert custom == {"tier": "enterprise"}


def test_no_matching_rule():
    _set_rules({"domain_rules": {"acme.com": {"tier": "enterprise"}}})
    top, custom, applied = rules.apply_rules("x@notacme.com")
    assert top == {} and custom == {} and applied == []


def test_missing_rules_file_safe():
    _set_rules({})
    top, custom, applied = rules.apply_rules("x@acme.com")
    assert top == {} and custom == {} and applied == []
