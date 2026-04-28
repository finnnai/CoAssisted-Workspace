# © 2026 CoAssisted Workspace. Licensed for non-redistribution use only.
# See LICENSE file for terms.
"""Tests for the brand-voice response template seeder.

Covers the offline pieces — render-to-disk format, category list shape,
brand-voice fallback behavior. The actual LLM call (`_generate_one`) is
exercised separately via the live smoke test.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make scripts/ importable for the test
SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import seed_response_templates as srt  # noqa: E402


def test_categories_has_eight_entries():
    assert len(srt.CATEGORIES) == 8


def test_categories_have_required_fields():
    """Every category must have slug, scenario, description, subject_hint."""
    required = {"slug", "scenario", "description", "subject_hint"}
    for cat in srt.CATEGORIES:
        missing = required - set(cat.keys())
        assert not missing, f"{cat.get('slug')} missing fields: {missing}"


def test_category_slugs_are_unique_and_filename_safe():
    slugs = [c["slug"] for c in srt.CATEGORIES]
    assert len(slugs) == len(set(slugs)), "duplicate slug"
    for slug in slugs:
        assert "/" not in slug and ".." not in slug
        assert slug == slug.lower()
        assert " " not in slug


def test_render_template_file_yaml_parses_via_templates_loader(tmp_path, monkeypatch):
    """The generated file must round-trip through the templates module's
    frontmatter parser (no quoting bugs on fancy chars, multi-line html
    body indentation correct, etc.)."""
    import templates as tmpl_mod

    fake_generated = {
        "subject": "Re: {subject|your message} — let's sort this out",
        "description": "Acknowledge complaint, take ownership, propose next step.",
        "body_plain": (
            "Hi {first_name|there},\n\n"
            "Thanks for flagging this — appreciate the heads up.\n\n"
            "— Finnn"
        ),
        "body_html": (
            "<p>Hi {first_name|there},</p>\n"
            "<p>Thanks for flagging this — appreciate the heads up.</p>\n"
            "<p>— Finnn</p>"
        ),
    }
    rendered = srt._render_template_file(srt.CATEGORIES[0], fake_generated)
    p = tmp_path / "demo.md"
    p.write_text(rendered, encoding="utf-8")

    # Point the loader at our temp dir
    monkeypatch.setattr(tmpl_mod, "_TEMPLATES_DIR", tmp_path)
    tpl = tmpl_mod.load("demo")

    assert tpl.subject == fake_generated["subject"]
    assert tpl.description == fake_generated["description"]
    assert tpl.html_body == fake_generated["body_html"]
    # Body is the plain-text section after the frontmatter
    assert "Thanks for flagging" in tpl.body
    assert "— Finnn" in tpl.body


def test_render_handles_html_with_indentation_safely():
    """HTML blocks with leading spaces shouldn't get their structure mangled
    when wrapped under YAML's `|` multi-line indicator."""
    fake = {
        "subject": "Test",
        "description": "Test desc",
        "body_plain": "Hi.",
        "body_html": "<ul>\n  <li>nested</li>\n  <li>list</li>\n</ul>",
    }
    out = srt._render_template_file(srt.CATEGORIES[0], fake)
    assert "html_body: |" in out
    # Body section should still be present after frontmatter
    assert out.endswith("Hi.\n")


def test_read_brand_voice_falls_back_when_missing(monkeypatch, tmp_path):
    """If brand-voice.md doesn't exist, return generic placeholder text
    rather than crashing — supports first-run users without a voice file."""
    monkeypatch.setattr(srt, "_PROJECT", tmp_path)
    out = srt._read_brand_voice()
    assert "(No brand-voice.md found" in out
    assert len(out) > 50  # has some sensible content


def test_read_brand_voice_returns_file_when_present(monkeypatch, tmp_path):
    monkeypatch.setattr(srt, "_PROJECT", tmp_path)
    voice_path = tmp_path / "brand-voice.md"
    voice_path.write_text("Direct, warm, em-dashes.\n", encoding="utf-8")
    out = srt._read_brand_voice()
    assert out.strip() == "Direct, warm, em-dashes."


def test_expected_slugs_match_user_request():
    """Sanity guard — the user explicitly asked for these 8 categories."""
    expected = {
        "inbound_customer_complaint",
        "inbound_upset_client",
        "inbound_thanks_for_reply",
        "inbound_great_to_meet",
        "inbound_client_feedback",
        "inbound_welcome",
        "inbound_renewal_response",
        "inbound_followup_response",
    }
    assert {c["slug"] for c in srt.CATEGORIES} == expected


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
