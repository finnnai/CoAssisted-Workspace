# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Auto-generated PandaDoc API tools — module: workspace.

DO NOT EDIT BY HAND. Regenerate via:
    python3 scripts/generate_pandadoc_tools.py

This module wraps 25 PandaDoc operations under tag(s): Communication Preferences, Contacts, Folders, Members, User and Workspace management.

Pydantic input classes live at MODULE scope (not inside register())
so FastMCP's typing.get_type_hints can resolve them. Earlier rev
nested them in register() and triggered InvalidSignature on startup.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

import pandadoc_client

class _Input_addMember(BaseModel):
    model_config = ConfigDict(extra='forbid')
    workspace_id: str = Field(..., description="Path: ")
    notify_user: Optional[bool] = Field(None, description="Query: Send a confirmation email to the user that was added to workspace(s).")
    notify_ws_admins: Optional[bool] = Field(None, description="Query: Send a confirmation email to all workspace admins indicating that the user has been added to the workspace.")
    body: dict[str, Any] = Field(..., description="JSON body — see PandaDoc docs for the schema of this endpoint.")

class _Input_createApiKey(BaseModel):
    model_config = ConfigDict(extra='forbid')
    workspace_id: str = Field(..., description="Path: Workspace id.")
    body: dict[str, Any] = Field(..., description="JSON body — see PandaDoc docs for the schema of this endpoint.")

class _Input_createContact(BaseModel):
    model_config = ConfigDict(extra='forbid')
    body: dict[str, Any] = Field(..., description="JSON body — see PandaDoc docs for the schema of this endpoint.")

class _Input_createDocumentFolder(BaseModel):
    model_config = ConfigDict(extra='forbid')
    body: dict[str, Any] = Field(..., description="JSON body — see PandaDoc docs for the schema of this endpoint.")

class _Input_createMemberToken(BaseModel):
    model_config = ConfigDict(extra='forbid')
    member_id: str = Field(..., description="Path: Member id.")
    body: Optional[dict[str, Any]] = Field(None, description="Optional JSON body — see PandaDoc docs.")

class _Input_createTemplateFolder(BaseModel):
    model_config = ConfigDict(extra='forbid')
    body: dict[str, Any] = Field(..., description="JSON body — see PandaDoc docs for the schema of this endpoint.")

class _Input_createUser(BaseModel):
    model_config = ConfigDict(extra='forbid')
    notify_user: Optional[bool] = Field(None, description="Query: Send a confirmation email to the user that was added to workspace(s).")
    notify_ws_admins: Optional[bool] = Field(None, description="Query: Send a confirmation email to all workspace admins indicating that the user has been added to the workspace.")
    body: dict[str, Any] = Field(..., description="JSON body — see PandaDoc docs for the schema of this endpoint.")

class _Input_createWorkspace(BaseModel):
    model_config = ConfigDict(extra='forbid')
    body: dict[str, Any] = Field(..., description="JSON body — see PandaDoc docs for the schema of this endpoint.")

class _Input_deactivateWorkspace(BaseModel):
    model_config = ConfigDict(extra='forbid')
    workspace_id: str = Field(..., description="Path: ")
    body: Optional[dict[str, Any]] = Field(None, description="Optional JSON body — see PandaDoc docs.")

class _Input_deleteContact(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(..., description="Path: Contact id.")

class _Input_detailsContact(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(..., description="Path: Contact id.")

class _Input_detailsCurrentMember(BaseModel):
    model_config = ConfigDict(extra='forbid')
    pass

class _Input_detailsMember(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(..., description="Path: Membership id.")

class _Input_detailsUser(BaseModel):
    model_config = ConfigDict(extra='forbid')
    user_id: str = Field(..., description="Path: A unique identifier of the user.")

class _Input_getWorkspacesList(BaseModel):
    model_config = ConfigDict(extra='forbid')
    count: Optional[int] = Field(None, description="Query: Number of elements in page.")
    page: Optional[int] = Field(None, description="Query: Page number.")

class _Input_listContacts(BaseModel):
    model_config = ConfigDict(extra='forbid')
    email: Optional[str] = Field(None, description="Query: Optional search parameter. Filter results by exact match.")

class _Input_listDocumentFolders(BaseModel):
    model_config = ConfigDict(extra='forbid')
    parent_uuid: Optional[str] = Field(None, description="Query: The UUID of the folder containing folders. To list the folders located in the root folder, remove this parameter in the request.")
    count: Optional[int] = Field(None, description="Query: Optionally, specify how many folders to return.")
    page: Optional[int] = Field(None, description="Query: Optionally, specify which page of the dataset to return.")

class _Input_listMembers(BaseModel):
    model_config = ConfigDict(extra='forbid')
    pass

class _Input_listRecentSmsOptOuts(BaseModel):
    model_config = ConfigDict(extra='forbid')
    timestamp_from: Optional[str] = Field(None, description="Query: The start of the timestamp.   If no timestamp is provided, 1 hour before the current time will be used.")
    timestamp_to: Optional[str] = Field(None, description="Query: The end of the timestamp range.   If no timestamp is provided the current time will be used.")

class _Input_listTemplateFolders(BaseModel):
    model_config = ConfigDict(extra='forbid')
    parent_uuid: Optional[str] = Field(None, description="Query: The UUID of the folder containing folders. To list the folders located in the root folder, remove this parameter in the request.")
    count: Optional[int] = Field(None, description="Query: Optionally, specify how many folders to return.")
    page: Optional[int] = Field(None, description="Query: Optionally, specify which page of the dataset to return.")

class _Input_listUsers(BaseModel):
    model_config = ConfigDict(extra='forbid')
    count: Optional[int] = Field(None, description="Query: Number of elements in page.")
    page: Optional[int] = Field(None, description="Query: Page number.")
    show_removed: Optional[bool] = Field(None, description="Query: Filter option - show users with removed memberships.")

class _Input_removeMember(BaseModel):
    model_config = ConfigDict(extra='forbid')
    workspace_id: str = Field(..., description="Path: Workspace id")
    member_id: str = Field(..., description="Path: Member id")

class _Input_renameDocumentFolder(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(..., description="Path: The UUID of the folder that you are renaming.")
    body: dict[str, Any] = Field(..., description="JSON body — see PandaDoc docs for the schema of this endpoint.")

class _Input_renameTemplateFolder(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(..., description="Path: The UUID of the folder which you are renaming.")
    body: dict[str, Any] = Field(..., description="JSON body — see PandaDoc docs for the schema of this endpoint.")

class _Input_updateContact(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(..., description="Path: Contact id.")
    body: dict[str, Any] = Field(..., description="JSON body — see PandaDoc docs for the schema of this endpoint.")


def register(mcp) -> None:  # noqa: ANN001
    """Register every tool in this module with the FastMCP instance."""

    @mcp.tool()
    def pandadoc_add_member(params: _Input_addMember) -> Any:
        """Add Member to Workspace"""
        return pandadoc_client.call(
            "addMember",
            path_params={"workspace_id": params.workspace_id},
            query={"notify_user": params.notify_user, "notify_ws_admins": params.notify_ws_admins},
            json_body=params.body,
        )

    @mcp.tool()
    def pandadoc_create_api_key(params: _Input_createApiKey) -> Any:
        """Create API Key"""
        return pandadoc_client.call(
            "createApiKey",
            path_params={"workspace_id": params.workspace_id},
            json_body=params.body,
        )

    @mcp.tool()
    def pandadoc_create_contact(params: _Input_createContact) -> Any:
        """Create contact"""
        return pandadoc_client.call(
            "createContact",
            json_body=params.body,
        )

    @mcp.tool()
    def pandadoc_create_document_folder(params: _Input_createDocumentFolder) -> Any:
        """Create Documents Folder"""
        return pandadoc_client.call(
            "createDocumentFolder",
            json_body=params.body,
        )

    @mcp.tool()
    def pandadoc_create_member_token(params: _Input_createMemberToken) -> Any:
        """Create Member Token"""
        return pandadoc_client.call(
            "createMemberToken",
            path_params={"member_id": params.member_id},
            json_body=params.body,
        )

    @mcp.tool()
    def pandadoc_create_template_folder(params: _Input_createTemplateFolder) -> Any:
        """Create Templates Folder"""
        return pandadoc_client.call(
            "createTemplateFolder",
            json_body=params.body,
        )

    @mcp.tool()
    def pandadoc_create_user(params: _Input_createUser) -> Any:
        """Create User"""
        return pandadoc_client.call(
            "createUser",
            query={"notify_user": params.notify_user, "notify_ws_admins": params.notify_ws_admins},
            json_body=params.body,
        )

    @mcp.tool()
    def pandadoc_create_workspace(params: _Input_createWorkspace) -> Any:
        """Create Workspace"""
        return pandadoc_client.call(
            "createWorkspace",
            json_body=params.body,
        )

    @mcp.tool()
    def pandadoc_deactivate_workspace(params: _Input_deactivateWorkspace) -> Any:
        """Deactivate Workspace"""
        return pandadoc_client.call(
            "deactivateWorkspace",
            path_params={"workspace_id": params.workspace_id},
            json_body=params.body,
        )

    @mcp.tool()
    def pandadoc_delete_contact(params: _Input_deleteContact) -> Any:
        """Delete Contact"""
        return pandadoc_client.call(
            "deleteContact",
            path_params={"id": params.id},
        )

    @mcp.tool()
    def pandadoc_details_contact(params: _Input_detailsContact) -> Any:
        """Contact Details"""
        return pandadoc_client.call(
            "detailsContact",
            path_params={"id": params.id},
        )

    @mcp.tool()
    def pandadoc_details_current_member(params: _Input_detailsCurrentMember) -> Any:
        """Current Member Details"""
        return pandadoc_client.call(
            "detailsCurrentMember",
        )

    @mcp.tool()
    def pandadoc_details_member(params: _Input_detailsMember) -> Any:
        """Member Details"""
        return pandadoc_client.call(
            "detailsMember",
            path_params={"id": params.id},
        )

    @mcp.tool()
    def pandadoc_details_user(params: _Input_detailsUser) -> Any:
        """Get User Details by ID"""
        return pandadoc_client.call(
            "detailsUser",
            path_params={"user_id": params.user_id},
        )

    @mcp.tool()
    def pandadoc_get_workspaces_list(params: _Input_getWorkspacesList) -> Any:
        """List Workspaces"""
        return pandadoc_client.call(
            "getWorkspacesList",
            query={"count": params.count, "page": params.page},
        )

    @mcp.tool()
    def pandadoc_list_contacts(params: _Input_listContacts) -> Any:
        """List contacts"""
        return pandadoc_client.call(
            "listContacts",
            query={"email": params.email},
        )

    @mcp.tool()
    def pandadoc_list_document_folders(params: _Input_listDocumentFolders) -> Any:
        """List Documents Folders"""
        return pandadoc_client.call(
            "listDocumentFolders",
            query={"parent_uuid": params.parent_uuid, "count": params.count, "page": params.page},
        )

    @mcp.tool()
    def pandadoc_list_members(params: _Input_listMembers) -> Any:
        """List Members"""
        return pandadoc_client.call(
            "listMembers",
        )

    @mcp.tool()
    def pandadoc_list_recent_sms_opt_outs(params: _Input_listRecentSmsOptOuts) -> Any:
        """Recent SMS Opt-out"""
        return pandadoc_client.call(
            "listRecentSmsOptOuts",
            query={"timestamp_from": params.timestamp_from, "timestamp_to": params.timestamp_to},
        )

    @mcp.tool()
    def pandadoc_list_template_folders(params: _Input_listTemplateFolders) -> Any:
        """List Templates Folders"""
        return pandadoc_client.call(
            "listTemplateFolders",
            query={"parent_uuid": params.parent_uuid, "count": params.count, "page": params.page},
        )

    @mcp.tool()
    def pandadoc_list_users(params: _Input_listUsers) -> Any:
        """List Users"""
        return pandadoc_client.call(
            "listUsers",
            query={"count": params.count, "page": params.page, "show_removed": params.show_removed},
        )

    @mcp.tool()
    def pandadoc_remove_member(params: _Input_removeMember) -> Any:
        """Remove Member from Workspace"""
        return pandadoc_client.call(
            "removeMember",
            path_params={"workspace_id": params.workspace_id, "member_id": params.member_id},
        )

    @mcp.tool()
    def pandadoc_rename_document_folder(params: _Input_renameDocumentFolder) -> Any:
        """Rename Documents Folder"""
        return pandadoc_client.call(
            "renameDocumentFolder",
            path_params={"id": params.id},
            json_body=params.body,
        )

    @mcp.tool()
    def pandadoc_rename_template_folder(params: _Input_renameTemplateFolder) -> Any:
        """Rename Templates Folder"""
        return pandadoc_client.call(
            "renameTemplateFolder",
            path_params={"id": params.id},
            json_body=params.body,
        )

    @mcp.tool()
    def pandadoc_update_contact(params: _Input_updateContact) -> Any:
        """Update Contact"""
        return pandadoc_client.call(
            "updateContact",
            path_params={"id": params.id},
            json_body=params.body,
        )
