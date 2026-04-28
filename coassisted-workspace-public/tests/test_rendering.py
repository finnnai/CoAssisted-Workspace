# © 2026 CoAssisted Workspace contributors contributors. Licensed under MIT — see LICENSE.
"""Tests for rendering.py — pure string logic, no network."""

import rendering


def test_basic_substitution():
    assert rendering.render("Hi {name}", {"name": "Josh"}) == "Hi Josh"


def test_fallback_when_missing():
    assert rendering.render("Hi {name|there}", {}) == "Hi there"


def test_fallback_when_empty_string():
    # Empty strings should fall back too — empty `first_name` is effectively "missing".
    assert rendering.render("Hi {name|there}", {"name": ""}) == "Hi there"


def test_fallback_when_whitespace_only():
    assert rendering.render("Hi {name|there}", {"name": "   "}) == "Hi there"


def test_global_default_when_no_per_placeholder_fallback():
    assert rendering.render("Hi {name}", {}, default="friend") == "Hi friend"


def test_double_brace_escape():
    assert rendering.render("Use {{x}}, not {x}", {"x": "Y"}) == "Use {x}, not Y"


def test_multiple_placeholders():
    out = rendering.render(
        "Hi {first_name}, work at {org}?", {"first_name": "Josh", "org": "Acme"}
    )
    assert out == "Hi Josh, work at Acme?"


def test_non_string_value_stringified():
    assert rendering.render("Count: {n}", {"n": 7}) == "Count: 7"


def test_extract_placeholders_simple():
    assert rendering.extract_placeholders("Hi {first_name}, {company}") == [
        "first_name",
        "company",
    ]


def test_extract_placeholders_dedupes():
    assert rendering.extract_placeholders("{x} and {x} and {y}") == ["x", "y"]


def test_extract_placeholders_with_fallback():
    assert rendering.extract_placeholders("{first|there}") == ["first"]


def test_extract_ignores_braces_escape():
    assert rendering.extract_placeholders("{{literal}}") == []
