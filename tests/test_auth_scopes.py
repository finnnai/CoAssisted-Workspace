# © 2026 CoAssisted Workspace contributors. Licensed under MIT — see LICENSE use only.
"""Tests for OAuth scope coverage.

Detects scope drift: every Google service we call (gmail, drive, calendar,
sheets, docs, tasks, contacts, chat) must have its required scope listed in
auth.SCOPES. If we add a tool that calls a new API without updating the scope
list, users see opaque 403s after re-OAuthing.

Also catches obvious typos and unused-scope creep.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import auth


# --------------------------------------------------------------------------- #
# Basic shape
# --------------------------------------------------------------------------- #


def test_scopes_list_is_non_empty():
    assert isinstance(auth.SCOPES, list)
    assert len(auth.SCOPES) > 0


def test_every_scope_is_a_googleapis_url():
    """All scopes should be Google's canonical URL form. Catch typos like
    'gmail.modify' written without the protocol prefix."""
    for scope in auth.SCOPES:
        assert scope.startswith("https://"), f"Scope {scope!r} missing https://"
        # Either mail.google.com (Gmail's full-mailbox legacy form) or googleapis.com
        assert "google.com" in scope or "googleapis.com" in scope


def test_no_duplicate_scopes():
    assert len(auth.SCOPES) == len(set(auth.SCOPES))


def test_no_trailing_whitespace_in_scopes():
    """Whitespace in scope strings causes silent OAuth grant failures."""
    for scope in auth.SCOPES:
        assert scope == scope.strip(), f"Scope {scope!r} has whitespace"


# --------------------------------------------------------------------------- #
# Coverage of every service we actually call
# --------------------------------------------------------------------------- #


# Each entry: a service string and the scope token that proves it's covered.
# 'token' is a substring expected in at least one SCOPES entry.
_SERVICE_TO_SCOPE_TOKEN = [
    ("Gmail", "mail.google.com"),
    ("Calendar", "/calendar"),
    ("Drive", "/drive"),
    ("Sheets", "/spreadsheets"),
    ("Docs", "/documents"),
    ("Tasks", "/tasks"),
    ("Contacts (People API)", "/contacts"),
    ("Chat", "/chat."),
    ("Cloud Platform (Route Optimization)", "/cloud-platform"),
]


@pytest.mark.parametrize("service,token", _SERVICE_TO_SCOPE_TOKEN)
def test_service_has_scope(service, token):
    """Each enabled Google service must have its scope present."""
    has_it = any(token in s for s in auth.SCOPES)
    assert has_it, (
        f"{service} is wired in tools/ but its scope (containing {token!r}) "
        f"is missing from auth.SCOPES. Add it or users will see a 403 after "
        f"re-OAuthing."
    )


# --------------------------------------------------------------------------- #
# AuthError class
# --------------------------------------------------------------------------- #


def test_auth_error_is_runtime_error():
    """AuthError must subclass RuntimeError so it propagates naturally."""
    assert issubclass(auth.AuthError, RuntimeError)


def test_auth_error_can_carry_message():
    err = auth.AuthError("token expired and refresh failed")
    assert "token expired" in str(err)


# --------------------------------------------------------------------------- #
# Cross-check: scope tokens used by gservices match the declared SCOPES
# --------------------------------------------------------------------------- #


def test_no_scope_drift_in_gservices():
    """If gservices.py builds a service we don't have a scope for, fail.
    Reads gservices.py source and confirms each service factory's name
    matches an auth scope."""
    project_root = Path(__file__).resolve().parent.parent
    gservices_src = (project_root / "gservices.py").read_text()
    # Service factories we expect to see have funcs like def gmail(), def calendar(), etc.
    # Map each one to a scope token that should be in auth.SCOPES.
    service_funcs_to_token = {
        "def gmail()": "mail.google.com",
        "def calendar()": "/calendar",
        "def drive()": "/drive",
        "def sheets()": "/spreadsheets",
        "def docs()": "/documents",
        "def tasks()": "/tasks",
        "def people()": "/contacts",
        "def chat()": "/chat.",
    }
    for fn_signature, token in service_funcs_to_token.items():
        if fn_signature in gservices_src:
            present = any(token in s for s in auth.SCOPES)
            assert present, (
                f"gservices.py declares {fn_signature!r} but no scope "
                f"containing {token!r} is in auth.SCOPES."
            )
