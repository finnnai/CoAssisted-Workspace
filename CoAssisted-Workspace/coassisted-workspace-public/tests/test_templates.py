# © 2026 CoAssisted Workspace contributors contributors. Licensed under MIT — see LICENSE.
"""Tests for templates.py — frontmatter parsing + file loading."""

from pathlib import Path

import pytest

import templates as templates_mod


def test_parse_no_frontmatter():
    fm, body = templates_mod._parse_frontmatter("Just a body.")
    assert fm == {}
    assert body == "Just a body."


def test_parse_simple_frontmatter():
    text = '---\nsubject: "Hi {name}"\ndescription: test\n---\nBody here.'
    fm, body = templates_mod._parse_frontmatter(text)
    assert fm["subject"] == "Hi {name}"
    assert fm["description"] == "test"
    assert body == "Body here."


def test_parse_multiline_block():
    text = "---\nsubject: s\nhtml_body: |\n  <p>Line 1</p>\n  <p>Line 2</p>\n---\nBody"
    fm, body = templates_mod._parse_frontmatter(text)
    assert "<p>Line 1</p>" in fm["html_body"]
    assert "<p>Line 2</p>" in fm["html_body"]
    assert body == "Body"


def test_parse_strips_quotes():
    fm, _ = templates_mod._parse_frontmatter('---\nsubject: "Quoted"\n---\n')
    assert fm["subject"] == "Quoted"

    fm2, _ = templates_mod._parse_frontmatter("---\nsubject: 'Single'\n---\n")
    assert fm2["subject"] == "Single"


def test_load_invalid_name_raises():
    with pytest.raises(templates_mod.TemplateError):
        templates_mod.load("..evil")
    with pytest.raises(templates_mod.TemplateError):
        templates_mod.load("nested/name")


def test_load_missing_raises():
    with pytest.raises(templates_mod.TemplateError):
        templates_mod.load("definitely_does_not_exist_12345")


def test_load_existing_example(tmp_path, monkeypatch):
    """Load should succeed on a well-formed template file."""
    t_dir = tmp_path / "templates"
    t_dir.mkdir()
    (t_dir / "hello.md").write_text(
        '---\nsubject: "Hi {name|there}"\ndescription: a test\n---\nBody for {name|there}.'
    )
    monkeypatch.setattr(templates_mod, "_TEMPLATES_DIR", t_dir)
    tpl = templates_mod.load("hello")
    assert tpl.name == "hello"
    assert tpl.subject == "Hi {name|there}"
    assert "Body for {name|there}." in tpl.body
    assert tpl.description == "a test"


def test_list_templates_skips_bad_files(tmp_path, monkeypatch):
    t_dir = tmp_path / "templates"
    t_dir.mkdir()
    (t_dir / "good.md").write_text('---\nsubject: ok\n---\nbody')
    (t_dir / "bad.md").write_text('---\ndescription: no subject\n---\nbody')
    monkeypatch.setattr(templates_mod, "_TEMPLATES_DIR", t_dir)
    lst = templates_mod.list_templates()
    names = [t.get("name") for t in lst]
    assert "good" in names
    # Bad template should appear with an error field rather than crash the listing.
    assert "bad" in names
