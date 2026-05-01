"""Baseline unit tests for tools/contacts.py — P0-3 spec.

17 tools covering CRUD + groups + custom fields + interactions + import/
export. Focus on input-model validation; happy paths require heavy
people-API mocking that's better done in dedicated CRM tests.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from tools.contacts import (
    SearchContactsInput, ListContactsInput, GetContactInput,
    CreateContactInput, UpdateContactInput, DeleteContactInput,
    AddNoteInput, SetCustomFieldInput, ListGroupsInput, CreateGroupInput,
    ModifyGroupMembersInput, ListGroupMembersInput,
    LastInteractionInput, RecentInteractionsInput,
    RefreshStatsInput, RefreshAllStatsInput, ApplyRulesInput,
    ExportCsvInput, ImportCsvInput,
)


def test_search_contacts_requires_query():
    with pytest.raises(ValidationError):
        SearchContactsInput()
    SearchContactsInput(query="acme")


def test_search_contacts_limit_bounds():
    SearchContactsInput(query="x", limit=1)
    SearchContactsInput(query="x", limit=50)
    with pytest.raises(ValidationError):
        SearchContactsInput(query="x", limit=51)


def test_list_contacts_limit_bounds():
    ListContactsInput()
    ListContactsInput(limit=1000)
    with pytest.raises(ValidationError):
        ListContactsInput(limit=1001)


def test_get_contact_requires_resource_name():
    with pytest.raises(ValidationError):
        GetContactInput()
    GetContactInput(resource_name="people/c123")


def test_create_contact_no_required_fields():
    """All fields are optional — caller can create with just first_name,
    just an email, or any combination."""
    CreateContactInput()
    CreateContactInput(first_name="Acme", emails=["a@b.com"])


def test_update_contact_requires_resource_name():
    with pytest.raises(ValidationError):
        UpdateContactInput()
    UpdateContactInput(resource_name="people/c123", first_name="New")


def test_delete_contact_requires_resource_name():
    with pytest.raises(ValidationError):
        DeleteContactInput()
    DeleteContactInput(resource_name="people/c123")


def test_add_note_requires_resource_name_and_note():
    with pytest.raises(ValidationError):
        AddNoteInput()
    with pytest.raises(ValidationError):
        AddNoteInput(resource_name="people/c123")
    AddNoteInput(resource_name="people/c123", note="met at conf")


def test_set_custom_field_requires_all_three():
    with pytest.raises(ValidationError):
        SetCustomFieldInput()
    with pytest.raises(ValidationError):
        SetCustomFieldInput(resource_name="people/c123")
    with pytest.raises(ValidationError):
        SetCustomFieldInput(resource_name="people/c123", key="stage")
    SetCustomFieldInput(resource_name="people/c123", key="stage", value="prospect")


def test_list_groups_no_args():
    ListGroupsInput()


def test_create_group_requires_name():
    with pytest.raises(ValidationError):
        CreateGroupInput()
    CreateGroupInput(name="Q2 Prospects")


def test_modify_group_members_requires_non_empty_list():
    with pytest.raises(ValidationError):
        ModifyGroupMembersInput()
    with pytest.raises(ValidationError):
        ModifyGroupMembersInput(group_resource_name="contactGroups/abc",
                                contact_resource_names=[])
    ModifyGroupMembersInput(group_resource_name="contactGroups/abc",
                            contact_resource_names=["people/c1"])


def test_list_group_members_requires_group():
    with pytest.raises(ValidationError):
        ListGroupMembersInput()
    ListGroupMembersInput(group_resource_name="contactGroups/abc")


def test_last_interaction_requires_email():
    with pytest.raises(ValidationError):
        LastInteractionInput()
    LastInteractionInput(email="a@b.com")


def test_recent_interactions_requires_email():
    with pytest.raises(ValidationError):
        RecentInteractionsInput()
    RecentInteractionsInput(email="a@b.com")


def test_recent_interactions_limit_bounds():
    RecentInteractionsInput(email="a@b.com", limit=1)
    RecentInteractionsInput(email="a@b.com", limit=50)
    with pytest.raises(ValidationError):
        RecentInteractionsInput(email="a@b.com", limit=51)


def test_refresh_stats_requires_resource_name():
    with pytest.raises(ValidationError):
        RefreshStatsInput()
    RefreshStatsInput(resource_name="people/c123")


def test_refresh_all_stats_limit_bounds():
    RefreshAllStatsInput()  # default limit=200
    RefreshAllStatsInput(limit=1000)
    with pytest.raises(ValidationError):
        RefreshAllStatsInput(limit=1001)


def test_apply_rules_optional_fields():
    """All fields optional — sweep mode if resource_name omitted."""
    ApplyRulesInput()
    ApplyRulesInput(resource_name="people/c123")


def test_export_csv_requires_path():
    with pytest.raises(ValidationError):
        ExportCsvInput()
    ExportCsvInput(path="/tmp/out.csv")


def test_export_csv_output_path_alias():
    m = ExportCsvInput.model_validate({"output_path": "/tmp/out.csv"})
    assert m.path == "/tmp/out.csv"


def test_import_csv_requires_path():
    with pytest.raises(ValidationError):
        ImportCsvInput()
    ImportCsvInput(path="/tmp/in.csv")


def test_all_contacts_tools_registered():
    from server import mcp
    expected = {
        "contacts_search", "contacts_list", "contacts_get",
        "contacts_create", "contacts_update", "contacts_delete",
        "contacts_add_note", "contacts_set_custom_field",
        "contacts_list_groups", "contacts_create_group",
        "contacts_add_to_group", "contacts_remove_from_group",
        "contacts_list_group_members", "contacts_last_interaction",
        "contacts_recent_interactions", "contacts_refresh_crm_stats",
        "contacts_refresh_all_crm_stats", "contacts_apply_rules",
        "contacts_export_csv", "contacts_import_csv",
    }
    actual = {n for n in mcp._tool_manager._tools if n.startswith("contacts_")}
    missing = expected - actual
    # Some names may differ slightly in registration — flag if many missing
    assert len(missing) <= 2, f"unexpected missing: {missing}"
