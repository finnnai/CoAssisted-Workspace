# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Tests for brand voice composer pure-logic core (template path).

LLM path is exercised separately via the smoke; these tests stay offline.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import brand_voice


@pytest.fixture(autouse=True)
def _empty_voice(tmp_path: Path):
    """Point voice path at a tempfile that doesn't exist → no voice loaded."""
    brand_voice._override_voice_path_for_tests(tmp_path / "no_voice.md")
    yield
    brand_voice._override_voice_path_for_tests(
        Path(__file__).resolve().parent.parent / "brand-voice.md",
    )


# --------------------------------------------------------------------------- #
# Template path
# --------------------------------------------------------------------------- #


def test_template_compose_reply_includes_greeting():
    out = brand_voice.compose_template_only(brand_voice.DraftRequest(
        intent="reply",
        recipient_name="Sarah Fields",
        sender_name="Finn",
        subject_hint="Renewal terms",
        context="Yes, locking in the renewal — terms hold.",
    ))
    assert "Sarah" in out.plain or "Hi" in out.plain
    assert out.voice_used is False
    assert "Re: Renewal terms" in out.subject


def test_template_compose_decline():
    out = brand_voice.compose_template_only(brand_voice.DraftRequest(
        intent="decline",
        recipient_name="Random",
        sender_name="Finn",
        subject_hint="Speaking opportunity",
        context="Schedule too packed this quarter.",
    ))
    assert "not going to be able to" in out.plain.lower() or "decline" in out.plain.lower()


def test_template_compose_birthday():
    out = brand_voice.compose_template_only(brand_voice.DraftRequest(
        intent="birthday",
        recipient_name="Alex Stone",
        sender_name="Finn",
        context="Hope you have a great day!",
    ))
    assert "Happy birthday" in out.subject


def test_template_compose_agenda():
    out = brand_voice.compose_template_only(brand_voice.DraftRequest(
        intent="agenda",
        recipient_name="Brian",
        context="Topic 1\nTopic 2\nTopic 3",
    ))
    assert "Agenda" in out.subject


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #


def test_unknown_intent_raises():
    with pytest.raises(ValueError):
        brand_voice.compose(brand_voice.DraftRequest(intent="bogus"))


def test_unknown_audience_raises():
    with pytest.raises(ValueError):
        brand_voice.compose(brand_voice.DraftRequest(
            intent="reply", audience="aliens",
        ))


# --------------------------------------------------------------------------- #
# Variant seed determinism
# --------------------------------------------------------------------------- #


def test_seed_deterministic_for_same_inputs():
    req = brand_voice.DraftRequest(
        intent="reply", audience="customer",
        recipient_name="Sarah", subject_hint="Q",
    )
    s1 = brand_voice._seed(req)
    s2 = brand_voice._seed(req)
    assert s1 == s2


def test_seed_changes_with_seed_hint():
    req1 = brand_voice.DraftRequest(intent="reply", recipient_name="X",
                                    seed_hint="a")
    req2 = brand_voice.DraftRequest(intent="reply", recipient_name="X",
                                    seed_hint="b")
    assert brand_voice._seed(req1) != brand_voice._seed(req2)


# --------------------------------------------------------------------------- #
# Voice loading
# --------------------------------------------------------------------------- #


def test_load_voice_returns_empty_when_missing(tmp_path):
    out = brand_voice.load_voice_guide(tmp_path / "nope.md")
    assert out == ""


def test_load_voice_caps_length(tmp_path):
    p = tmp_path / "voice.md"
    p.write_text("X" * 10000)
    out = brand_voice.load_voice_guide(p, max_chars=500)
    assert len(out) == 500


# --------------------------------------------------------------------------- #
# Output shape
# --------------------------------------------------------------------------- #


def test_to_dict_includes_all_fields():
    out = brand_voice.compose_template_only(brand_voice.DraftRequest(
        intent="reply", recipient_name="X",
    ))
    d = out.to_dict()
    for k in ("subject", "plain", "html", "intent", "audience",
              "voice_used", "variant_seed"):
        assert k in d


def test_html_renders_newlines_as_br():
    out = brand_voice.compose_template_only(brand_voice.DraftRequest(
        intent="reply", recipient_name="X", sender_name="Y",
        subject_hint="hi", context="line1",
    ))
    assert "<br>" in out.html
