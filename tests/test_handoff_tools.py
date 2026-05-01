"""Baseline unit tests for tools/handoff.py — P0-3 spec."""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from tools.handoff import ReceiveHandoffInput


def _resolve(name):
    from server import mcp
    return mcp._tool_manager._tools[name].fn


def test_receive_handoff_input_requires_archive_path():
    with pytest.raises(ValidationError):
        ReceiveHandoffInput()


def test_receive_handoff_tool_registered():
    from server import mcp
    assert "workflow_receive_handoff" in mcp._tool_manager._tools
