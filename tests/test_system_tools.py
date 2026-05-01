"""Baseline unit tests for tools/system.py — P0-3 spec.

system_doctor + 17 individual checks. Most checks take an empty `_NoArgs`
input. We verify input model validation + every tool registers. The
end-to-end system_doctor run is a network test (hits 8 Workspace APIs +
Anthropic + Maps); covered by the live doctor run we ran earlier this
session, not by unit tests here.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from tools.system import CheckAnthropicKeyInput, CheckMapsKeyInput


def test_check_anthropic_key_default_live_test_true():
    m = CheckAnthropicKeyInput()
    assert m.live_test is True


def test_check_anthropic_key_can_disable_live():
    m = CheckAnthropicKeyInput(live_test=False)
    assert m.live_test is False


def test_check_anthropic_key_extra_forbidden():
    with pytest.raises(ValidationError):
        CheckAnthropicKeyInput.model_validate({"unexpected_field": "x"})


def test_check_maps_key_default_live_test_true():
    m = CheckMapsKeyInput()
    assert m.live_test is True


def test_check_maps_key_can_disable_live():
    m = CheckMapsKeyInput(live_test=False)
    assert m.live_test is False


def test_all_system_doctor_checks_registered():
    from server import mcp
    expected = {
        "system_check_anthropic_key", "system_check_maps_api_key",
        "system_check_oauth", "system_check_location_services",
        "system_check_workspace_apis", "system_check_route_optimization",
        "system_check_maps_api_key_full", "system_check_config",
        "system_check_filesystem", "system_check_dependencies",
        "system_check_clock", "system_check_tools",
        "system_check_unit_tests", "system_check_quota_usage",
        "system_check_license", "system_recent_actions",
        "system_doctor", "system_share_health_report",
    }
    actual = {n for n in mcp._tool_manager._tools if n.startswith("system_")}
    missing = expected - actual
    assert not missing, f"missing system_*: {missing}"
