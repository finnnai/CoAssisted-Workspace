# © 2026 CoAssisted Workspace contributors. Licensed under MIT — see LICENSE use only.
"""Tests for dry-run resolution + canonical preview shape.

Tools accept an optional `dry_run` arg. is_dry_run resolves the effective
value: explicit caller value wins, else falls back to global config.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

import dryrun


# --------------------------------------------------------------------------- #
# is_dry_run resolution order
# --------------------------------------------------------------------------- #


def test_explicit_true_wins_over_config():
    with patch("config.get", return_value=False):
        assert dryrun.is_dry_run(True) is True


def test_explicit_false_wins_over_config():
    """Even if the global is True, an explicit False from a caller overrides."""
    with patch("config.get", return_value=True):
        assert dryrun.is_dry_run(False) is False


def test_none_falls_back_to_config_true():
    with patch("config.get", return_value=True):
        assert dryrun.is_dry_run(None) is True


def test_none_falls_back_to_config_false():
    with patch("config.get", return_value=False):
        assert dryrun.is_dry_run(None) is False


def test_none_with_no_config_setting():
    """Default config is not dry-run — be safe."""
    with patch("config.get", return_value=None):
        assert dryrun.is_dry_run(None) is False


# --------------------------------------------------------------------------- #
# dry_run_preview canonical shape
# --------------------------------------------------------------------------- #


def test_preview_returns_valid_json():
    out = dryrun.dry_run_preview("workflow_send_email", {"to": "x@y.com"})
    parsed = json.loads(out)
    assert parsed["status"] == "dry_run"
    assert parsed["tool"] == "workflow_send_email"
    assert parsed["would_do"] == {"to": "x@y.com"}


def test_preview_handles_nested_payload():
    payload = {
        "would_append_rows": 7,
        "stats": {"extracted": 7, "errors": 0},
        "sample": [{"merchant": "Anthropic", "total": 53.30}],
    }
    out = dryrun.dry_run_preview("workflow_extract_receipts", payload)
    parsed = json.loads(out)
    assert parsed["would_do"]["stats"]["extracted"] == 7
    assert parsed["would_do"]["sample"][0]["merchant"] == "Anthropic"


def test_preview_indented_for_human_reading():
    """Preview output should be indented JSON, not a single line."""
    out = dryrun.dry_run_preview("foo", {"a": 1})
    assert "\n" in out
    assert "  " in out  # has indentation
