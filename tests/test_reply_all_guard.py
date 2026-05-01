# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Tests for the reply-all guard pure-logic core."""

from __future__ import annotations

import reply_all_guard as core


# --------------------------------------------------------------------------- #
# Helpers — no fanout = no guard
# --------------------------------------------------------------------------- #


def test_single_recipient_returns_safe():
    v = core.score_draft(
        body="Hi Sarah, sounds good — let's lock that in.",
        to=["sarah@example.com"],
    )
    assert v.verdict == "safe"
    assert any(s.code == "single_recipient" for s in v.signals)


def test_empty_recipients_safe():
    v = core.score_draft(body="anything", to=[])
    assert v.verdict == "safe"


# --------------------------------------------------------------------------- #
# Signal: single-target greeting
# --------------------------------------------------------------------------- #


def test_single_target_greeting_with_local_part_match():
    v = core.score_draft(
        body="Hi Sarah, can you double-check the invoice numbers?",
        to=["sarah.fields@example.com", "brian@example.com", "conor@example.com"],
    )
    codes = {s.code for s in v.signals}
    assert "single_target_greeting" in codes
    assert v.addressed_recipient == "sarah.fields@example.com"
    assert v.suggested_to == ["sarah.fields@example.com"]
    assert v.suggested_cc == []


def test_single_target_greeting_with_display_name_match():
    v = core.score_draft(
        body="Hey Brian — quick one for you.",
        to=['"Brian Sweigart" <brian@xenture.com>', "sarah@example.com"],
    )
    assert v.addressed_recipient == '"Brian Sweigart" <brian@xenture.com>'
    assert any(s.code == "single_target_greeting" for s in v.signals)


def test_greeting_to_team_does_not_trigger():
    v = core.score_draft(
        body="Hi all, here's the update.",
        to=["a@x.com", "b@x.com", "c@x.com"],
    )
    codes = {s.code for s in v.signals}
    assert "single_target_greeting" not in codes


def test_greeting_with_two_names_does_not_trigger_single_target():
    v = core.score_draft(
        body="Hi Sarah and Brian, see below.",
        to=["sarah@x.com", "brian@x.com", "conor@x.com"],
    )
    codes = {s.code for s in v.signals}
    # Two names matched → not a single-target greeting.
    assert "single_target_greeting" not in codes


def test_no_greeting_no_trigger():
    v = core.score_draft(
        body="See attached.",
        to=["a@x.com", "b@x.com", "c@x.com"],
    )
    codes = {s.code for s in v.signals}
    assert "single_target_greeting" not in codes


# --------------------------------------------------------------------------- #
# Signal: ack-only body
# --------------------------------------------------------------------------- #


def test_thanks_only_body_warns():
    v = core.score_draft(
        body="thanks!",
        to=["a@x.com", "b@x.com", "c@x.com"],
    )
    assert any(s.code == "ack_only_body" for s in v.signals)
    assert v.verdict == "warn"


def test_plus_one_body_warns():
    v = core.score_draft(
        body="+1",
        to=["a@x.com", "b@x.com", "c@x.com"],
    )
    assert any(s.code == "ack_only_body" for s in v.signals)


def test_will_do_body_warns():
    v = core.score_draft(
        body="will do",
        to=["a@x.com", "b@x.com", "c@x.com"],
    )
    assert any(s.code == "ack_only_body" for s in v.signals)


def test_real_short_reply_with_question_does_not_trigger_ack():
    """Short body that ends in '?' is a real question, not an ack."""
    v = core.score_draft(
        body="Got it. By Friday or sooner?",
        to=["a@x.com", "b@x.com", "c@x.com"],
    )
    codes = {s.code for s in v.signals}
    assert "ack_only_body" not in codes


# --------------------------------------------------------------------------- #
# Signal: FYI body
# --------------------------------------------------------------------------- #


def test_fyi_opening_fires():
    v = core.score_draft(
        body="FYI — heads up that the contract is signed.",
        to=["a@x.com", "b@x.com", "c@x.com"],
    )
    assert any(s.code == "fyi_body" for s in v.signals)


def test_fyi_in_middle_does_not_fire():
    v = core.score_draft(
        body="Quick context: I just wanted to FYI everyone here.",
        to=["a@x.com", "b@x.com", "c@x.com"],
    )
    codes = {s.code for s in v.signals}
    assert "fyi_body" not in codes


# --------------------------------------------------------------------------- #
# Signal: CC fanout
# --------------------------------------------------------------------------- #


def test_cc_fanout_fires_with_one_to_and_many_cc():
    v = core.score_draft(
        body="Quick question on the timeline.",
        to=["primary@x.com"],
        cc=["a@x.com", "b@x.com", "c@x.com"],
    )
    assert any(s.code == "cc_fanout" for s in v.signals)


def test_cc_fanout_does_not_fire_with_few_cc():
    v = core.score_draft(
        body="Quick question on the timeline.",
        to=["primary@x.com"],
        cc=["a@x.com"],
    )
    codes = {s.code for s in v.signals}
    assert "cc_fanout" not in codes


# --------------------------------------------------------------------------- #
# Verdict synthesis
# --------------------------------------------------------------------------- #


def test_block_when_single_target_greeting_plus_ack():
    """Strong single-target greeting + ack content = block."""
    v = core.score_draft(
        body="Hi Sarah, thanks!",
        to=["sarah@example.com", "brian@example.com", "conor@example.com"],
    )
    codes = {s.code for s in v.signals}
    assert "single_target_greeting" in codes
    assert "ack_only_body" in codes
    assert v.verdict == "block"


def test_warn_when_only_strong_signal():
    v = core.score_draft(
        body="Hi Sarah, can you confirm the numbers when you get a chance?",
        to=["sarah@example.com", "brian@example.com", "conor@example.com"],
    )
    assert v.verdict == "warn"


def test_warn_when_only_ack():
    v = core.score_draft(
        body="thanks!",
        to=["a@x.com", "b@x.com"],
    )
    assert v.verdict == "warn"


def test_safe_when_substantive_body_no_targeting():
    v = core.score_draft(
        body=(
            "Team — pulling together the Q3 review. "
            "Key things I'd like everyone's input on: revenue forecast, "
            "headcount plan, and the platform roadmap. "
            "Will share a draft Friday."
        ),
        to=["a@x.com", "b@x.com", "c@x.com"],
    )
    assert v.verdict == "safe"


# --------------------------------------------------------------------------- #
# Sender exclusion
# --------------------------------------------------------------------------- #


def test_sender_excluded_from_recipient_count():
    """If you're CC'd on your own draft (e.g. via reply-all to your own mail),
    the guard shouldn't double-count you as a recipient."""
    v = core.score_draft(
        body="Hi Sarah, quick question.",
        to=["sarah@example.com", "finn@example.com"],
        sender="finn@example.com",
    )
    # Only sarah is a real recipient; verdict should be safe (single recipient).
    assert v.verdict == "safe"


# --------------------------------------------------------------------------- #
# Output shape
# --------------------------------------------------------------------------- #


def test_to_dict_serializes_all_fields():
    v = core.score_draft(
        body="Hi Sarah, thanks!",
        to=["sarah@example.com", "brian@example.com"],
    )
    d = v.to_dict()
    assert "verdict" in d
    assert "signals" in d
    assert "suggested_to" in d
    assert "suggested_cc" in d
    assert "addressed_recipient" in d
    for s in d["signals"]:
        assert {"code", "message", "severity"} <= set(s.keys())
