# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Auto-generated PandaDoc API tools — module: misc.

DO NOT EDIT BY HAND. Regenerate via:
    python3 scripts/generate_pandadoc_tools.py

This module wraps 18 PandaDoc operations under tag(s): API Logs, Document Link to CRM, Document Reminders, Notary, OAuth 2.0 Authentication.

Pydantic input classes live at MODULE scope (not inside register())
so FastMCP's typing.get_type_hints can resolve them. Earlier rev
nested them in register() and triggered InvalidSignature on startup.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

import pandadoc_client

class _Input_accessToken(BaseModel):
    model_config = ConfigDict(extra='forbid')
    pass

class _Input_createLinkedObject(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(..., description="Path: Specify document ID.")
    body: dict[str, Any] = Field(..., description="JSON body — see PandaDoc docs for the schema of this endpoint.")

class _Input_createManualReminder(BaseModel):
    model_config = ConfigDict(extra='forbid')
    document_id: str = Field(..., description="Path: The UUID of the document.")
    body: dict[str, Any] = Field(..., description="JSON body — see PandaDoc docs for the schema of this endpoint.")

class _Input_createNotarizationRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    body: dict[str, Any] = Field(..., description="JSON body — see PandaDoc docs for the schema of this endpoint.")

class _Input_deleteLinkedObject(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(..., description="Path: Specify document ID.")
    linked_object_id: str = Field(..., description="Path: Specify linked object ID.")

class _Input_deleteNotarizationRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    session_request_id: str = Field(..., description="Path: Notarization Request ID.")

class _Input_detailsLog(BaseModel):
    model_config = ConfigDict(extra='forbid')
    pass

class _Input_detailsLogV2(BaseModel):
    model_config = ConfigDict(extra='forbid')
    pass

class _Input_getDocumentAutoReminderSettings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    document_id: str = Field(..., description="Path: The UUID of the document.")

class _Input_listDocumentsByLinkedObject(BaseModel):
    model_config = ConfigDict(extra='forbid')
    entity_id: str = Field(..., description="Query: You can get entity id from your integration, for example, from a url of a HubSpot deal.")
    entity_type: str = Field(..., description="Query: See the available entity types: https://developers.pandadoc.com/reference/link-service#examples-of-the-most-popular-crms")
    provider: str = Field(..., description="Query: See the available providers: https://developers.pandadoc.com/reference/link-service#examples-of-the-most-popular-crms")
    order_by: Optional[str] = Field(None, description="Query: ")
    owner_ids: Optional[list[str]] = Field(None, description="Query: ")

class _Input_listLinkedObjects(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(..., description="Path: Specify document ID.")

class _Input_listLogs(BaseModel):
    model_config = ConfigDict(extra='forbid')
    pass

class _Input_listLogsV2(BaseModel):
    model_config = ConfigDict(extra='forbid')
    pass

class _Input_listNotaries(BaseModel):
    model_config = ConfigDict(extra='forbid')
    status: Optional[list[str]] = Field(None, description="Query: Filter by status (comma-separated values supported). Valid values are INVITED, UNDER_REVIEW, ACTIVE, REJECTED, INACTIVE")
    commission_state: Optional[list[str]] = Field(None, description="Query: Filter by commission state (comma-separated values supported)")
    offset: Optional[int] = Field(None, description="Query: Number of results to skip")
    limit: Optional[int] = Field(None, description="Query: Maximum number of results to return")
    order_by: Optional[str] = Field(None, description="Query: Sort by name, email, or status (default is email). Use a - prefix for descending order (e.g., -email)")

class _Input_listNotarizationRequests(BaseModel):
    model_config = ConfigDict(extra='forbid')
    status: Optional[list[str]] = Field(None, description="Query: Filter by status (comma-separated values supported).")
    created_by_user_id: Optional[list[str]] = Field(None, description="Query: Filter by creator user ID (comma-separated values supported).")
    document_id: Optional[list[str]] = Field(None, description="Query: Filter by document ID (comma-separated values supported).")
    offset: Optional[int] = Field(None, description="Query: Number of results to skip.")
    limit: Optional[int] = Field(None, description="Query: Maximum number of results to return.")
    order_by: Optional[str] = Field(None, description="Query: Sort field. Use a `-` prefix for descending order (e.g., `-date_created`). When omitted, results are sorted by `date_created` ascending (oldest first).")

class _Input_notarizationRequestDetails(BaseModel):
    model_config = ConfigDict(extra='forbid')
    session_request_id: str = Field(..., description="Path: Notarization Request ID.")

class _Input_statusDocumentAutoReminder(BaseModel):
    model_config = ConfigDict(extra='forbid')
    document_id: str = Field(..., description="Path: The UUID of the document.")

class _Input_updateDocumentAutoReminderSettings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    document_id: str = Field(..., description="Path: The UUID of the document.")
    body: dict[str, Any] = Field(..., description="JSON body — see PandaDoc docs for the schema of this endpoint.")


def register(mcp) -> None:  # noqa: ANN001
    """Register every tool in this module with the FastMCP instance."""

    @mcp.tool()
    def pandadoc_access_token(params: _Input_accessToken) -> Any:
        """Create/Refresh Access Token"""
        return pandadoc_client.call(
            "accessToken",
        )

    @mcp.tool()
    def pandadoc_create_linked_object(params: _Input_createLinkedObject) -> Any:
        """Create Linked Object"""
        return pandadoc_client.call(
            "createLinkedObject",
            path_params={"id": params.id},
            json_body=params.body,
        )

    @mcp.tool()
    def pandadoc_create_manual_reminder(params: _Input_createManualReminder) -> Any:
        """Send Manual Reminder"""
        return pandadoc_client.call(
            "createManualReminder",
            path_params={"document_id": params.document_id},
            json_body=params.body,
        )

    @mcp.tool()
    def pandadoc_create_notarization_request(params: _Input_createNotarizationRequest) -> Any:
        """Create Notarization Request"""
        return pandadoc_client.call(
            "createNotarizationRequest",
            json_body=params.body,
        )

    @mcp.tool()
    def pandadoc_delete_linked_object(params: _Input_deleteLinkedObject) -> Any:
        """Delete Linked Object"""
        return pandadoc_client.call(
            "deleteLinkedObject",
            path_params={"id": params.id, "linked_object_id": params.linked_object_id},
        )

    @mcp.tool()
    def pandadoc_delete_notarization_request(params: _Input_deleteNotarizationRequest) -> Any:
        """Delete Notarization Request"""
        return pandadoc_client.call(
            "deleteNotarizationRequest",
            path_params={"session_request_id": params.session_request_id},
        )

    @mcp.tool()
    def pandadoc_details_log(params: _Input_detailsLog) -> Any:
        """[DEPRECATED] API Log Details"""
        return pandadoc_client.call(
            "detailsLog",
        )

    @mcp.tool()
    def pandadoc_details_log_v2(params: _Input_detailsLogV2) -> Any:
        """API Log Details"""
        return pandadoc_client.call(
            "detailsLogV2",
        )

    @mcp.tool()
    def pandadoc_get_document_auto_reminder_settings(params: _Input_getDocumentAutoReminderSettings) -> Any:
        """Document Auto Reminder Settings"""
        return pandadoc_client.call(
            "getDocumentAutoReminderSettings",
            path_params={"document_id": params.document_id},
        )

    @mcp.tool()
    def pandadoc_list_documents_by_linked_object(params: _Input_listDocumentsByLinkedObject) -> Any:
        """List Documents by Linked Object"""
        return pandadoc_client.call(
            "listDocumentsByLinkedObject",
            query={"entity_id": params.entity_id, "entity_type": params.entity_type, "provider": params.provider, "order_by": params.order_by, "owner_ids": params.owner_ids},
        )

    @mcp.tool()
    def pandadoc_list_linked_objects(params: _Input_listLinkedObjects) -> Any:
        """List Linked Objects"""
        return pandadoc_client.call(
            "listLinkedObjects",
            path_params={"id": params.id},
        )

    @mcp.tool()
    def pandadoc_list_logs(params: _Input_listLogs) -> Any:
        """[DEPRECATED] List API Log"""
        return pandadoc_client.call(
            "listLogs",
        )

    @mcp.tool()
    def pandadoc_list_logs_v2(params: _Input_listLogsV2) -> Any:
        """List API Log"""
        return pandadoc_client.call(
            "listLogsV2",
        )

    @mcp.tool()
    def pandadoc_list_notaries(params: _Input_listNotaries) -> Any:
        """List Notaries"""
        return pandadoc_client.call(
            "listNotaries",
            query={"status": params.status, "commission_state": params.commission_state, "offset": params.offset, "limit": params.limit, "order_by": params.order_by},
        )

    @mcp.tool()
    def pandadoc_list_notarization_requests(params: _Input_listNotarizationRequests) -> Any:
        """List Notarization Requests"""
        return pandadoc_client.call(
            "listNotarizationRequests",
            query={"status": params.status, "created_by_user_id": params.created_by_user_id, "document_id": params.document_id, "offset": params.offset, "limit": params.limit, "order_by": params.order_by},
        )

    @mcp.tool()
    def pandadoc_notarization_request_details(params: _Input_notarizationRequestDetails) -> Any:
        """Notarization Request Details"""
        return pandadoc_client.call(
            "notarizationRequestDetails",
            path_params={"session_request_id": params.session_request_id},
        )

    @mcp.tool()
    def pandadoc_status_document_auto_reminder(params: _Input_statusDocumentAutoReminder) -> Any:
        """Document Auto Reminder Status"""
        return pandadoc_client.call(
            "statusDocumentAutoReminder",
            path_params={"document_id": params.document_id},
        )

    @mcp.tool()
    def pandadoc_update_document_auto_reminder_settings(params: _Input_updateDocumentAutoReminderSettings) -> Any:
        """Update Document Auto Reminder Settings"""
        return pandadoc_client.call(
            "updateDocumentAutoReminderSettings",
            path_params={"document_id": params.document_id},
            json_body=params.body,
        )
