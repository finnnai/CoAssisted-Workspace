# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Auto-generated PandaDoc API tools — module: templates.

DO NOT EDIT BY HAND. Regenerate via:
    python3 scripts/generate_pandadoc_tools.py

This module wraps 10 PandaDoc operations under tag(s): Template Settings, Templates.

Pydantic input classes live at MODULE scope (not inside register())
so FastMCP's typing.get_type_hints can resolve them. Earlier rev
nested them in register() and triggered InvalidSignature on startup.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

import pandadoc_client

class _Input_createTemplate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    fields: Optional[list[str]] = Field(None, description="Query: A comma-separated list of additional fields to include in the response.")
    body: dict[str, Any] = Field(..., description="JSON body — see PandaDoc docs for the schema of this endpoint.")

class _Input_createTemplateEditingSession(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(..., description="Path: Template ID")
    body: dict[str, Any] = Field(..., description="JSON body — see PandaDoc docs for the schema of this endpoint.")

class _Input_createTemplateWithUpload(BaseModel):
    model_config = ConfigDict(extra='forbid')
    fields: Optional[list[str]] = Field(None, description="Query: A comma-separated list of additional fields to include in the response.")
    file_path: Optional[str] = Field(None, description="Local path to the file to upload. At least one of file_path or file_bytes_b64 is required.")
    file_bytes_b64: Optional[str] = Field(None, description="Base64-encoded file bytes (alternative to file_path).")
    file_name: Optional[str] = Field(None, description="Filename to send. Defaults to basename of file_path or upload.bin.")
    content_type: Optional[str] = Field(None, description="MIME type. Guessed from extension if unset.")
    multipart_extra_fields: Optional[dict[str, Any]] = Field(None, description="Extra non-file form fields to include in the multipart body.")

class _Input_deleteTemplate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(..., description="Path: Template ID")

class _Input_detailsTemplate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(..., description="Path: Template ID")

class _Input_listTemplates(BaseModel):
    model_config = ConfigDict(extra='forbid')
    q: Optional[str] = Field(None, description="Query: Search query. Filter by template name.")
    shared: Optional[bool] = Field(None, description="Query: Returns only the shared templates.")
    deleted: Optional[bool] = Field(None, description="Query: Returns only the deleted templates.")
    count: Optional[int] = Field(None, description="Query: Specify how many templates to return.")
    page: Optional[int] = Field(None, description="Query: Specify which page of the dataset to return.")
    id: Optional[str] = Field(None, description="Query: Specify template ID.")
    folder_uuid: Optional[str] = Field(None, description="Query: UUID of the folder where the templates are stored.")
    tag: Optional[list[str]] = Field(None, description="Query: Search tag. Filter by template tag.")
    fields: Optional[list[str]] = Field(None, description="Query: A comma-separated list of additional fields to include in the response.")

class _Input_statusTemplate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(..., description="Path: Template ID")

class _Input_templateSettingsGet(BaseModel):
    model_config = ConfigDict(extra='forbid')
    template_id: str = Field(..., description="Path: Unique identifier of the template to retrieve settings for.")

class _Input_templateSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    template_id: str = Field(..., description="Path: Unique identifier of the template to update settings for.")
    body: dict[str, Any] = Field(..., description="JSON body — see PandaDoc docs for the schema of this endpoint.")

class _Input_updateTemplate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(..., description="Path: Template ID")
    body: dict[str, Any] = Field(..., description="JSON body — see PandaDoc docs for the schema of this endpoint.")


def register(mcp) -> None:  # noqa: ANN001
    """Register every tool in this module with the FastMCP instance."""

    @mcp.tool()
    def pandadoc_create_template(params: _Input_createTemplate) -> Any:
        """Create Template"""
        return pandadoc_client.call(
            "createTemplate",
            query={"fields": params.fields},
            json_body=params.body,
        )

    @mcp.tool()
    def pandadoc_create_template_editing_session(params: _Input_createTemplateEditingSession) -> Any:
        """Create Template Editing Session"""
        return pandadoc_client.call(
            "createTemplateEditingSession",
            path_params={"id": params.id},
            json_body=params.body,
        )

    @mcp.tool()
    def pandadoc_create_template_with_upload(params: _Input_createTemplateWithUpload) -> Any:
        """Create Template from File Upload"""
        import base64, mimetypes, os
        filename = params.file_name or "upload.bin"
        ctype = params.content_type
        if params.file_path:
            with open(params.file_path, "rb") as fp:
                payload_bytes = fp.read()
            if not params.file_name:
                filename = os.path.basename(params.file_path)
            if not ctype:
                ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        elif params.file_bytes_b64:
            payload_bytes = base64.b64decode(params.file_bytes_b64)
            if not ctype:
                ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        else:
            return {"error": "Provide either file_path or file_bytes_b64."}
        mp_parts = [("file", (filename, payload_bytes, ctype))]
        if params.multipart_extra_fields:
            import json as _json
            for k, v in params.multipart_extra_fields.items():
                if isinstance(v, (dict, list)):
                    v = _json.dumps(v)
                mp_parts.append((k, (None, str(v).encode("utf-8"), "text/plain")))
        return pandadoc_client.call(
            "createTemplateWithUpload",
            query={"fields": params.fields},
            multipart=mp_parts,
        )

    @mcp.tool()
    def pandadoc_delete_template(params: _Input_deleteTemplate) -> Any:
        """Delete Template"""
        return pandadoc_client.call(
            "deleteTemplate",
            path_params={"id": params.id},
        )

    @mcp.tool()
    def pandadoc_details_template(params: _Input_detailsTemplate) -> Any:
        """Template Details"""
        return pandadoc_client.call(
            "detailsTemplate",
            path_params={"id": params.id},
        )

    @mcp.tool()
    def pandadoc_list_templates(params: _Input_listTemplates) -> Any:
        """List Templates"""
        return pandadoc_client.call(
            "listTemplates",
            query={"q": params.q, "shared": params.shared, "deleted": params.deleted, "count": params.count, "page": params.page, "id": params.id, "folder_uuid": params.folder_uuid, "tag": params.tag, "fields": params.fields},
        )

    @mcp.tool()
    def pandadoc_status_template(params: _Input_statusTemplate) -> Any:
        """Template Status"""
        return pandadoc_client.call(
            "statusTemplate",
            path_params={"id": params.id},
        )

    @mcp.tool()
    def pandadoc_template_settings_get(params: _Input_templateSettingsGet) -> Any:
        """Get template settings"""
        return pandadoc_client.call(
            "templateSettingsGet",
            path_params={"template_id": params.template_id},
        )

    @mcp.tool()
    def pandadoc_template_settings_update(params: _Input_templateSettingsUpdate) -> Any:
        """Update template settings"""
        return pandadoc_client.call(
            "templateSettingsUpdate",
            path_params={"template_id": params.template_id},
            json_body=params.body,
        )

    @mcp.tool()
    def pandadoc_update_template(params: _Input_updateTemplate) -> Any:
        """Template Update"""
        return pandadoc_client.call(
            "updateTemplate",
            path_params={"id": params.id},
            json_body=params.body,
        )
