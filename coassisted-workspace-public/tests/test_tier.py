# © 2026 CoAssisted Workspace contributors contributors. Licensed under MIT — see LICENSE.
"""Tests for the tier (free/paid) gating module."""

import pytest

import tier


def test_free_tools_set_size():
    """Free tier should be ~50 tools as designed."""
    assert 45 <= len(tier.FREE_TOOLS) <= 55


def test_known_free_tools_in_set():
    """Spot-check that the gateway-drug tools ARE free."""
    assert "gmail_send_email" in tier.FREE_TOOLS
    assert "calendar_create_event" in tier.FREE_TOOLS
    assert "drive_search_files" in tier.FREE_TOOLS
    assert "system_doctor" in tier.FREE_TOOLS


def test_known_paid_tools_not_in_set():
    """Spot-check that high-value workflows are paid-tier."""
    assert "workflow_route_optimize_advanced" not in tier.FREE_TOOLS
    assert "workflow_calendar_drive_time_blocks" not in tier.FREE_TOOLS
    assert "maps_geocode" not in tier.FREE_TOOLS
    assert "workflow_bulk_update_contacts" not in tier.FREE_TOOLS
    assert "workflow_chat_meeting_brief" not in tier.FREE_TOOLS


def test_project_ap_admin_helpers_are_free():
    """Project-registry CRUD lets evaluators see the routing-rule shape
    without paying. Extraction is paid; setup isn't."""
    assert "workflow_register_project" in tier.FREE_TOOLS
    assert "workflow_list_projects" in tier.FREE_TOOLS
    assert "workflow_create_project_sheet" in tier.FREE_TOOLS


def test_project_ap_value_drivers_are_paid():
    """The actual LLM-driven AP loop is paid. Includes extraction,
    follow-up, reply parsing, exports, and the migrator."""
    paid_tools = {
        "workflow_extract_project_invoices",
        "workflow_extract_project_receipts",
        "workflow_send_vendor_reminders",
        "workflow_process_vendor_replies",
        "workflow_move_invoice_to_project",
        "workflow_export_project_invoices_qb_csv",
        "workflow_migrate_project_sheets_to_ap_layout",
    }
    for t in paid_tools:
        assert t not in tier.FREE_TOOLS, f"{t} should be paid"
        assert tier.is_paid(t), f"is_paid({t!r}) should be True"


def test_is_paid_inverts_free_set():
    """is_paid is just `not in FREE_TOOLS`."""
    assert tier.is_paid("workflow_route_optimize_advanced") is True
    assert tier.is_paid("gmail_send_email") is False
    # Even unknown tools default to paid (safer fallback)
    assert tier.is_paid("totally_made_up_tool") is True


def test_validate_license_key_format():
    assert tier.validate_license_key("caw-A1B2-C3D4-E5F6-G7H8") is True
    assert tier.validate_license_key("caw-AAAA-BBBB-CCCC-DDDD") is True
    # Lowercase rejected
    assert tier.validate_license_key("caw-a1b2-c3d4-e5f6-g7h8") is False
    # Wrong prefix
    assert tier.validate_license_key("xxx-A1B2-C3D4-E5F6-G7H8") is False
    # Old "wsp-" prefix is no longer valid (changed to caw- on rename)
    assert tier.validate_license_key("wsp-A1B2-C3D4-E5F6-G7H8") is False
    # Wrong segment length
    assert tier.validate_license_key("caw-A1B-C3D4-E5F6-G7H8") is False
    # Special chars
    assert tier.validate_license_key("caw-A1B2-C3D4-E5F6-G7H!") is False


def test_validate_license_key_empty():
    assert tier.validate_license_key(None) is False
    assert tier.validate_license_key("") is False
    assert tier.validate_license_key("   ") is False


def test_validate_license_key_strips_whitespace():
    """User pasting from email may include trailing whitespace."""
    assert tier.validate_license_key("  caw-A1B2-C3D4-E5F6-G7H8  ") is True


def test_validate_license_key_non_string():
    assert tier.validate_license_key(123) is False
    assert tier.validate_license_key([]) is False


def test_personal_mode_unlocks_everything(monkeypatch):
    """In personal mode (default), every tool — even unknown ones — is unlocked."""
    monkeypatch.setattr(tier, "DISTRIBUTION_MODE", "personal")
    assert tier.is_unlocked("workflow_route_optimize_advanced") is True
    assert tier.is_unlocked("any_random_paid_tool") is True
    assert tier.is_unlocked("gmail_send_email") is True


def test_marketplace_mode_no_license_locks_paid(monkeypatch):
    """Marketplace mode + no license: free tools work, paid tools don't."""
    monkeypatch.setattr(tier, "DISTRIBUTION_MODE", "marketplace")
    # Mock config to return no license
    import config
    monkeypatch.setattr(
        config, "get",
        lambda key, default=None: None if key == "license_key" else default,
    )
    # Free tools always unlocked
    assert tier.is_unlocked("gmail_send_email") is True
    assert tier.is_unlocked("system_doctor") is True
    # Paid tools locked
    assert tier.is_unlocked("workflow_route_optimize_advanced") is False
    assert tier.is_unlocked("maps_geocode") is False


def test_marketplace_mode_valid_license_unlocks_all(monkeypatch):
    """Marketplace mode + valid license: everything unlocks."""
    monkeypatch.setattr(tier, "DISTRIBUTION_MODE", "marketplace")
    import config
    monkeypatch.setattr(
        config, "get",
        lambda key, default=None: (
            "caw-A1B2-C3D4-E5F6-G7H8" if key == "license_key" else default
        ),
    )
    assert tier.is_unlocked("workflow_route_optimize_advanced") is True
    assert tier.is_unlocked("maps_geocode") is True
    assert tier.current_tier() == "paid"


def test_marketplace_mode_invalid_license_treated_as_free(monkeypatch):
    """Marketplace mode + bad-format license: paid tools stay locked."""
    monkeypatch.setattr(tier, "DISTRIBUTION_MODE", "marketplace")
    import config
    monkeypatch.setattr(
        config, "get",
        lambda key, default=None: (
            "totally-fake-key" if key == "license_key" else default
        ),
    )
    assert tier.current_tier() == "free"
    assert tier.is_unlocked("workflow_route_optimize_advanced") is False


def test_gated_response_shape():
    r = tier.gated_response("workflow_route_optimize_advanced")
    assert r["status"] == "paid_feature"
    assert r["tool"] == "workflow_route_optimize_advanced"
    assert "tier" in r
    assert "message" in r
    assert "upgrade_hint" in r
    assert "license_key" in r["upgrade_hint"]
