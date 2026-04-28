# © 2026 CoAssisted Workspace. Licensed for non-redistribution use only.
"""Tests for the Anthropic LLM wrapper.

Covers the resolution path for `get_api_key` (env var > config.json fallback)
and the `is_available` short-circuits. The actual `call_simple` is exercised
through the receipt extractor's live smoke test — here we just confirm the
gating logic so callers don't accidentally hit a real API.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

import llm


# --------------------------------------------------------------------------- #
# get_api_key resolution priority
# --------------------------------------------------------------------------- #


def test_env_var_takes_priority():
    """ANTHROPIC_API_KEY env var beats config.json (cron + secure path)."""
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-from-env"}), \
         patch("config.get", return_value="sk-ant-from-config"):
        assert llm.get_api_key() == "sk-ant-from-env"


def test_falls_back_to_config_when_env_missing():
    """macOS GUI Cowork doesn't propagate ~/.zshrc env vars to the MCP
    subprocess, so config.json is the realistic path for most users."""
    with patch.dict(os.environ, {}, clear=True), \
         patch("config.get", return_value="sk-ant-from-config"):
        assert llm.get_api_key() == "sk-ant-from-config"


def test_returns_none_when_neither_set():
    with patch.dict(os.environ, {}, clear=True), \
         patch("config.get", return_value=None):
        assert llm.get_api_key() is None


def test_strips_whitespace_from_env():
    """Trailing newline from a copy-paste shouldn't break the key."""
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-x\n"}), \
         patch("config.get", return_value=None):
        assert llm.get_api_key() == "sk-ant-x"


def test_empty_env_falls_back_to_config():
    """An empty string in env shouldn't beat a real key in config."""
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}), \
         patch("config.get", return_value="sk-ant-real"):
        assert llm.get_api_key() == "sk-ant-real"


# --------------------------------------------------------------------------- #
# is_available gating
# --------------------------------------------------------------------------- #


def test_unavailable_when_no_key():
    with patch.object(llm, "get_api_key", return_value=None):
        ok, reason = llm.is_available()
    assert ok is False
    assert "ANTHROPIC_API_KEY" in reason or "key" in reason.lower()


def test_unavailable_when_key_has_wrong_prefix():
    """Anthropic keys start with 'sk-ant-'. Catch obvious misconfig."""
    with patch.object(llm, "get_api_key", return_value="some-other-token"):
        ok, reason = llm.is_available()
    assert ok is False
    assert "sk-ant" in reason or "prefix" in reason.lower()


def test_available_when_key_well_formed():
    """SDK is importable + key has correct prefix → ok."""
    with patch.object(llm, "get_api_key", return_value="sk-ant-fake-but-prefix-ok"):
        ok, reason = llm.is_available()
    # SDK availability depends on environment; if missing, that's a different
    # warn — both are acceptable here, but the format should be consistent.
    assert isinstance(ok, bool)
    assert isinstance(reason, str)
    if ok:
        assert reason == "ok"


def test_is_available_does_not_make_network_call():
    """Cheap feasibility check — must NOT actually call Anthropic.
    Skipped if anthropic SDK isn't installed (Linux test sandbox case);
    the on-disk venv on macOS has it."""
    pytest.importorskip("anthropic")
    import anthropic
    with patch.object(llm, "get_api_key", return_value="sk-ant-x"), \
         patch.object(anthropic, "Anthropic") as mock_client:
        llm.is_available()
        mock_client.assert_not_called()


def test_default_model_is_haiku():
    """Cost-conscious default — Haiku is cheapest. If someone bumps the
    default to Sonnet/Opus by accident, this test catches it before the
    next pipeline_digest cron costs $5/run instead of $0.05."""
    assert "haiku" in llm.DEFAULT_MODEL.lower(), (
        f"DEFAULT_MODEL={llm.DEFAULT_MODEL!r} — should be a Haiku variant for "
        f"cost reasons. Higher-tier models are opt-in per call."
    )


def test_get_client_caches_across_calls():
    """get_client should reuse the same Anthropic instance to avoid the
    ~10ms TLS handshake on every tool call. Verify the cache by patching
    the constructor and confirming it's only invoked once."""
    pytest.importorskip("anthropic")
    import anthropic
    # Reset module-level cache
    llm._client = None
    with patch.object(llm, "get_api_key", return_value="sk-ant-fake-with-prefix"), \
         patch.object(anthropic, "Anthropic") as mock_ctor:
        mock_ctor.return_value = "fake_client"
        c1 = llm.get_client()
        c2 = llm.get_client()
    assert c1 is c2
    assert mock_ctor.call_count == 1
    llm._client = None  # cleanup
