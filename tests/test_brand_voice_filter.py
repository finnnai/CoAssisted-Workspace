"""Tests for the auto-email filter in refresh_brand_voice.py.

These auto-emails appear in the 'sent' folder because Google attributes
them to the user, but they're template boilerplate — they poison the
brand-voice corpus if not filtered.
"""
from __future__ import annotations

import sys
from pathlib import Path

# refresh_brand_voice.py lives at the project root, not in a package
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_drive_share_body_filtered():
    from refresh_brand_voice import _is_google_auto_body
    body = "Joshua Szott has shared the following document with you. Open it in Google Docs."
    assert _is_google_auto_body(body)


def test_meet_invite_body_filtered():
    from refresh_brand_voice import _is_google_auto_body
    body = "Join with Google Meet\nhttps://meet.google.com/abc-defg-hij"
    assert _is_google_auto_body(body)


def test_calendar_reminder_body_filtered():
    from refresh_brand_voice import _is_google_auto_body
    body = "This is a reminder for the upcoming event you organized."
    assert _is_google_auto_body(body)


def test_form_response_body_filtered():
    from refresh_brand_voice import _is_google_auto_body
    body = "Someone has responded to your form. Click below to view results."
    assert _is_google_auto_body(body)


def test_real_prose_not_filtered():
    """A normal authored email should pass — false positive would shrink corpus."""
    from refresh_brand_voice import _is_google_auto_body
    body = (
        "Hey Tom, quick follow-up on the meeting yesterday. "
        "Could you send the latest deck when you have a sec? Thanks."
    )
    assert not _is_google_auto_body(body)


def test_email_mentioning_meet_link_in_body_not_filtered():
    """Edge case: Joshua's authored email may mention 'google meet' in passing.
    
    The filter looks at the first 300 chars; legitimate prose usually
    starts with greeting + content, not the auto-email opener.
    """
    from refresh_brand_voice import _is_google_auto_body
    body = (
        "Hi Sarah,\n\nLet's chat tomorrow at 2pm. "
        "I'll send a Google Meet link separately."
    )
    # First 300 chars start with "Hi Sarah" — should not match
    assert not _is_google_auto_body(body)


def test_invitation_subject_filter_present_in_query():
    """Sanity check: the query string should include subject exclusions."""
    import refresh_brand_voice as mod
    src = Path(mod.__file__).read_text()
    # Pulling a direct sample of the query string from the source — easier
    # than calling fetch_sent_prose which needs auth.
    assert "Invitation:" in src
    assert "Updated invitation:" in src
    assert "Canceled event:" in src
