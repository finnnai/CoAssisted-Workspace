# © 2026 CoAssisted Workspace contributors. Licensed under MIT — see LICENSE use only.
"""Tests for ap_drive_layout — folder/sheet hierarchy + name derivation."""

from __future__ import annotations

import pytest

import ap_drive_layout as ap


@pytest.fixture(autouse=True)
def _clear_caches():
    ap._reset_caches_for_tests()
    yield
    ap._reset_caches_for_tests()


# --------------------------------------------------------------------------- #
# _last_first — name flipping
# --------------------------------------------------------------------------- #


def test_last_first_basic():
    assert ap._last_first("Alice Smith") == "Smith, Alice"


def test_last_first_strips_parenthetical():
    assert ap._last_first("Alice Smith (CEO)") == "Smith, Alice"
    assert ap._last_first("Jane Doe (Operations)") == "Doe, Jane"


def test_last_first_keeps_existing_comma_form():
    """Already-flipped names pass through unchanged."""
    assert ap._last_first("Smith Jr., John") == "Smith Jr., John"


def test_last_first_strips_quotes():
    assert ap._last_first('"Alice Smith"') == "Smith, Alice"


def test_last_first_single_word():
    assert ap._last_first("Cher") == "Cher"
    assert ap._last_first("madonna") == "Madonna"


def test_last_first_empty_returns_unknown():
    assert ap._last_first("") == "Unknown"
    assert ap._last_first(None) == "Unknown"


def test_last_first_three_word_name():
    """First-and-middle stays in 'rest', last token is surname."""
    assert ap._last_first("John Allen Smith") == "Smith, John Allen"


# --------------------------------------------------------------------------- #
# Constants + config
# --------------------------------------------------------------------------- #


def test_default_root_name():
    assert ap.DEFAULT_ROOT_NAME == "AP Submissions"


def test_master_subfolder_name():
    assert ap.MASTER_SUBFOLDER == "Master"


def test_employee_sheet_prefix():
    assert ap.EMPLOYEE_SHEET_PREFIX.startswith("Project Invoices")


# --------------------------------------------------------------------------- #
# employee_display_name — local-part fallback
# --------------------------------------------------------------------------- #


def test_employee_display_name_fallback_to_local_part(monkeypatch):
    """When People API has no answer, fall back to the email's local-part."""
    # Stub the People API calls so they raise — this is what 'no match' looks like.
    class _StubPeople:
        def people(self):
            return self
        def searchDirectoryPeople(self, **kw):
            raise RuntimeError("no directory")
        def searchContacts(self, **kw):
            raise RuntimeError("no contacts")

    monkeypatch.setattr(ap, "_people", lambda: _StubPeople())
    name = ap.employee_display_name("john.doe@example.com")
    # Underscores + dots → spaces, title-cased.
    assert name == "John Doe"


def test_employee_display_name_handles_underscore_local(monkeypatch):
    class _Stub:
        def people(self): return self
        def searchDirectoryPeople(self, **kw): raise RuntimeError("nope")
        def searchContacts(self, **kw): raise RuntimeError("nope")

    monkeypatch.setattr(ap, "_people", lambda: _Stub())
    assert ap.employee_display_name("jane_smith@x.io") == "Jane Smith"


def test_employee_display_name_empty():
    assert ap.employee_display_name("") == "Unknown"
    assert ap.employee_display_name(None) == "Unknown"


def test_employee_display_name_directory_hit_uses_last_first(monkeypatch):
    class _Stub:
        def people(self): return self
        def searchDirectoryPeople(self, **kw):
            return self
        def execute(self):
            return {"people": [{"names": [{"displayName": "Alice Smith"}]}]}
        def searchContacts(self, **kw):
            raise RuntimeError("not reached")

    monkeypatch.setattr(ap, "_people", lambda: _Stub())
    name = ap.employee_display_name("alice@example.com")
    assert name == "Smith, Alice"


def test_employee_display_name_caches_result(monkeypatch):
    """Second lookup hits the cache, no API call."""
    call_count = {"n": 0}

    class _Stub:
        def people(self): return self
        def searchDirectoryPeople(self, **kw):
            call_count["n"] += 1
            return self
        def execute(self):
            return {"people": [{"names": [{"displayName": "A B"}]}]}
        def searchContacts(self, **kw):
            return self

    monkeypatch.setattr(ap, "_people", lambda: _Stub())
    ap.employee_display_name("test@x.com")
    ap.employee_display_name("test@x.com")
    assert call_count["n"] == 1


# --------------------------------------------------------------------------- #
# Folder hierarchy guards
# --------------------------------------------------------------------------- #


def test_ensure_employee_folder_requires_email():
    with pytest.raises(ValueError):
        ap.ensure_employee_folder("")


def test_ensure_project_subfolder_requires_code():
    with pytest.raises(ValueError):
        ap.ensure_project_subfolder("folder-id", "")


def test_ensure_master_sheet_requires_code():
    with pytest.raises(ValueError):
        ap.ensure_master_sheet("", "Some Project", ["a", "b"])


def test_ensure_employee_project_sheet_requires_both():
    with pytest.raises(ValueError):
        ap.ensure_employee_project_sheet("", "ALPHA", "Alpha", ["a"])
    with pytest.raises(ValueError):
        ap.ensure_employee_project_sheet("user@x.com", "", "Alpha", ["a"])


# --------------------------------------------------------------------------- #
# Cache reset isolation
# --------------------------------------------------------------------------- #


def test_reset_for_tests_clears_caches(monkeypatch):
    ap._FOLDER_CACHE["x"] = "y"
    ap._DISPLAY_NAME_CACHE["a"] = "b"
    ap._reset_caches_for_tests()
    assert ap._FOLDER_CACHE == {}
    assert ap._DISPLAY_NAME_CACHE == {}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
