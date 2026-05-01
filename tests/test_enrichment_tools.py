"""Baseline unit tests for tools/enrichment.py — P0-3 spec."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from tools.enrichment import EnrichContactInput, EnrichFromRecentMailInput


# Input validation
def test_enrich_contact_no_required_fields():
    """Either resource_name OR email — neither strictly required at
    Pydantic level (the tool itself errors if both are omitted)."""
    EnrichContactInput()
    EnrichContactInput(resource_name="people/c1")
    EnrichContactInput(email="a@b.com")


def test_enrich_contact_days_bounds():
    EnrichContactInput(days=1)
    EnrichContactInput(days=3650)
    with pytest.raises(ValidationError):
        EnrichContactInput(days=0)
    with pytest.raises(ValidationError):
        EnrichContactInput(days=3651)


def test_enrich_from_recent_defaults():
    m = EnrichFromRecentMailInput()
    assert m.days == 1  # daily-job friendly default
    assert m.limit_messages_scanned == 500
    assert m.overwrite is True


def test_enrich_from_recent_limit_bounds():
    EnrichFromRecentMailInput(limit_messages_scanned=1)
    EnrichFromRecentMailInput(limit_messages_scanned=5000)
    with pytest.raises(ValidationError):
        EnrichFromRecentMailInput(limit_messages_scanned=5001)


def test_all_enrichment_tools_registered():
    from server import mcp
    assert {"workflow_enrich_contact_from_inbox",
            "workflow_enrich_contacts_from_recent_mail"}.issubset(
        set(mcp._tool_manager._tools)
    )
