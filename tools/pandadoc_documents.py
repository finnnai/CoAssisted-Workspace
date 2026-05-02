# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Auto-generated PandaDoc API tools — module: documents.

DO NOT EDIT BY HAND. Regenerate via:
    python3 scripts/generate_pandadoc_tools.py

This module wraps 49 PandaDoc operations under tag(s): Document Attachments, Document Audit Trail, Document Fields, Document Recipients, Document Sections (Bundles), Document Settings, Document Structure View, Documents.

Pydantic input classes live at MODULE scope (not inside register())
so FastMCP's typing.get_type_hints can resolve them. Earlier rev
nested them in register() and triggered InvalidSignature on startup.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

import pandadoc_client

class _Input_addDocumentRecipient(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(..., description="Path: Document UUID")
    body: dict[str, Any] = Field(..., description="JSON body — see PandaDoc docs for the schema of this endpoint.")

class _Input_addDsvNamedItems(BaseModel):
    model_config = ConfigDict(extra='forbid')
    document_id: str = Field(..., description="Path: Unique identifier of the document to which the DSV named items will be added.")
    body: dict[str, Any] = Field(..., description="JSON body — see PandaDoc docs for the schema of this endpoint.")

class _Input_appendContentLibraryItemToDocument(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(..., description="Path: Specify document id.")
    body: dict[str, Any] = Field(..., description="JSON body — see PandaDoc docs for the schema of this endpoint.")

class _Input_bulkDeleteDocuments(BaseModel):
    model_config = ConfigDict(extra='forbid')
    body: dict[str, Any] = Field(..., description="JSON body — see PandaDoc docs for the schema of this endpoint.")

class _Input_changeDocumentStatus(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(..., description="Path: Specify document ID.")
    body: dict[str, Any] = Field(..., description="JSON body — see PandaDoc docs for the schema of this endpoint.")

class _Input_changeDocumentStatusWithUpload(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(..., description="Path: Specify document ID.")
    file_path: Optional[str] = Field(None, description="Local path to the file to upload. At least one of file_path or file_bytes_b64 is required.")
    file_bytes_b64: Optional[str] = Field(None, description="Base64-encoded file bytes (alternative to file_path).")
    file_name: Optional[str] = Field(None, description="Filename to send. Defaults to basename of file_path or upload.bin.")
    content_type: Optional[str] = Field(None, description="MIME type. Guessed from extension if unset.")
    multipart_extra_fields: Optional[dict[str, Any]] = Field(None, description="Extra non-file form fields to include in the multipart body.")

class _Input_createDocument(BaseModel):
    model_config = ConfigDict(extra='forbid')
    editor_ver: Optional[str] = Field(None, description="Query: Set this parameter as `ev1` if you want to create a document from PDF with Classic Editor when both editors are enabled for the workspace.")
    use_form_field_properties: Optional[str] = Field(None, description="Query: Set this parameter as `yes` or `1` or `true` (only when upload pdf with form fields) if you want to  respect form fields properties, like `required`.")
    body: dict[str, Any] = Field(..., description="JSON body — see PandaDoc docs for the schema of this endpoint.")

class _Input_createDocumentAttachment(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(..., description="Path: Document UUID")
    body: dict[str, Any] = Field(..., description="JSON body — see PandaDoc docs for the schema of this endpoint.")

class _Input_createDocumentAttachmentFromFileUpload(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(..., description="Path: Document UUID")
    file_path: Optional[str] = Field(None, description="Local path to the file to upload. At least one of file_path or file_bytes_b64 is required.")
    file_bytes_b64: Optional[str] = Field(None, description="Base64-encoded file bytes (alternative to file_path).")
    file_name: Optional[str] = Field(None, description="Filename to send. Defaults to basename of file_path or upload.bin.")
    content_type: Optional[str] = Field(None, description="MIME type. Guessed from extension if unset.")
    multipart_extra_fields: Optional[dict[str, Any]] = Field(None, description="Extra non-file form fields to include in the multipart body.")

class _Input_createDocumentEditingSession(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(..., description="Path: Document ID")
    body: dict[str, Any] = Field(..., description="JSON body — see PandaDoc docs for the schema of this endpoint.")

class _Input_createDocumentFields(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(..., description="Path: Document UUID.")
    body: dict[str, Any] = Field(..., description="JSON body — see PandaDoc docs for the schema of this endpoint.")

class _Input_createDocumentFromMarkdownUpload(BaseModel):
    model_config = ConfigDict(extra='forbid')
    file_path: Optional[str] = Field(None, description="Local path to the file to upload. At least one of file_path or file_bytes_b64 is required.")
    file_bytes_b64: Optional[str] = Field(None, description="Base64-encoded file bytes (alternative to file_path).")
    file_name: Optional[str] = Field(None, description="Filename to send. Defaults to basename of file_path or upload.bin.")
    content_type: Optional[str] = Field(None, description="MIME type. Guessed from extension if unset.")
    multipart_extra_fields: Optional[dict[str, Any]] = Field(None, description="Extra non-file form fields to include in the multipart body.")

class _Input_createDocumentFromUpload(BaseModel):
    model_config = ConfigDict(extra='forbid')
    editor_ver: Optional[str] = Field(None, description="Query: Set this parameter as `ev1` if you want to create a document from PDF with Classic Editor when both editors are enabled for the workspace.")
    use_form_field_properties: Optional[str] = Field(None, description="Query: Set this parameter as `yes` or `1` or `true` (only when upload pdf with form fields) if you want to  respect form fields properties, like `required`.")
    file_path: Optional[str] = Field(None, description="Local path to the file to upload. At least one of file_path or file_bytes_b64 is required.")
    file_bytes_b64: Optional[str] = Field(None, description="Base64-encoded file bytes (alternative to file_path).")
    file_name: Optional[str] = Field(None, description="Filename to send. Defaults to basename of file_path or upload.bin.")
    content_type: Optional[str] = Field(None, description="MIME type. Guessed from extension if unset.")
    multipart_extra_fields: Optional[dict[str, Any]] = Field(None, description="Extra non-file form fields to include in the multipart body.")

class _Input_createDocumentLink(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(..., description="Path: Document ID")
    body: dict[str, Any] = Field(..., description="JSON body — see PandaDoc docs for the schema of this endpoint.")

class _Input_createExportDocxTask(BaseModel):
    model_config = ConfigDict(extra='forbid')
    document_id: str = Field(..., description="Path: Specify document id.")

class _Input_deleteDocument(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(..., description="Path: Document ID")

class _Input_deleteDocumentAttachment(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(..., description="Path: Document UUID.")
    attachment_id: str = Field(..., description="Path: Attachment UUID.")

class _Input_deleteDocumentRecipient(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(..., description="Path: Document UUID")
    recipient_id: str = Field(..., description="Path: Recipient UUID")

class _Input_deleteSection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    document_id: str = Field(..., description="Path: Specify document ID.")
    section_id: str = Field(..., description="Path: Specify section ID.")

class _Input_detailsDocument(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(..., description="Path: Document ID")

class _Input_detailsDocumentAttachment(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(..., description="Path: Document UUID.")
    attachment_id: str = Field(..., description="Path: Attachment UUID.")

class _Input_documentESignDisclosure(BaseModel):
    model_config = ConfigDict(extra='forbid')
    document_id: str = Field(..., description="Path: The UUID of the document.")

class _Input_documentMoveToFolder(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(..., description="Path: Specify document ID.")
    folder_id: str = Field(..., description="Path: Specify folder ID.")

class _Input_documentRevertToDraft(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(..., description="Path: Specify document ID.")

class _Input_documentSettingsGet(BaseModel):
    model_config = ConfigDict(extra='forbid')
    document_id: str = Field(..., description="Path: Unique identifier of the document to retrieve settings for.")

class _Input_documentSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    document_id: str = Field(..., description="Path: Unique identifier of the document to update settings for.")
    body: dict[str, Any] = Field(..., description="JSON body — see PandaDoc docs for the schema of this endpoint.")

class _Input_downloadDocument(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(..., description="Path: Specify document ID.")
    watermark_color: Optional[str] = Field(None, description="Query: HEX code (for example `#FF5733`).")
    watermark_font_size: Optional[int] = Field(None, description="Query: Font size of the watermark.")
    watermark_opacity: Optional[float] = Field(None, description="Query: In range 0.0-1.0")
    watermark_text: Optional[str] = Field(None, description="Query: Specify watermark text.")
    separate_files: Optional[bool] = Field(None, description="Query: Download document bundle as a zip-archive of separate PDFs (1 file per section).")

class _Input_downloadDocumentAttachment(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(..., description="Path: Document UUID.")
    attachment_id: str = Field(..., description="Path: Attachment UUID.")

class _Input_downloadProtectedDocument(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(..., description="Path: Specify document ID.")
    separate_files: Optional[bool] = Field(None, description="Query: Download document bundle as a zip-archive of separate PDFs (1 file per section).")

class _Input_editDocumentRecipient(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(..., description="Path: Document UUID.")
    recipient_id: str = Field(..., description="Path: Recipient UUID.")
    body: dict[str, Any] = Field(..., description="JSON body — see PandaDoc docs for the schema of this endpoint.")

class _Input_getDocumentContent(BaseModel):
    model_config = ConfigDict(extra='forbid')
    poll_max_seconds: Optional[int] = Field(None, description="Override config.pandadoc.poll_max_seconds for this call.")

class _Input_getDocumentSummary(BaseModel):
    model_config = ConfigDict(extra='forbid')
    poll_max_seconds: Optional[int] = Field(None, description="Override config.pandadoc.poll_max_seconds for this call.")

class _Input_getDocxExportTask(BaseModel):
    model_config = ConfigDict(extra='forbid')
    document_id: str = Field(..., description="Path: Specify document id.")
    task_id: str = Field(..., description="Path: Specify Task id.")

class _Input_listDocumentAttachments(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(..., description="Path: Document UUID")

class _Input_listDocumentAuditTrail(BaseModel):
    model_config = ConfigDict(extra='forbid')
    document_id: str = Field(..., description="Path: Unique identifier of the document to retrieve the audit trail for.")
    limit: Optional[int] = Field(None, description="Query: Maximum number of items to return.")
    offset: Optional[int] = Field(None, description="Query: Number of items to skip before starting to collect the result set.")

class _Input_listDocumentFields(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(..., description="Path: Document UUID.")

class _Input_listDocuments(BaseModel):
    model_config = ConfigDict(extra='forbid')
    template_id: Optional[str] = Field(None, description="Query: Filters by parent template. This Parameter can't be used with form_id.")
    form_id: Optional[str] = Field(None, description="Query: Filters by parent form. This parameter can't be used with template_id.")
    folder_uuid: Optional[str] = Field(None, description="Query: Filters by the folder where the documents are stored.")
    contact_id: Optional[str] = Field(None, description="Query: Filters by recipient or approver with this 'contact_id'.")
    count: Optional[int] = Field(None, description="Query: Limits the size of the response. Default is 50 documents, maximum is 100 documents.")
    page: Optional[int] = Field(None, description="Query: Paginates the search result. Increase value to get the next page of results.")
    order_by: Optional[dict[str, Any]] = Field(None, description="Query: Defines the sorting of the result. Use `date_created` for ASC and `-date_created` for DESC sorting.")
    created_from: Optional[str] = Field(None, description="Query: Limits results to the documents with the `date_created` greater than or equal to this value.")
    created_to: Optional[str] = Field(None, description="Query: Limits results to the documents with the `date_created` less than this value.")
    deleted: Optional[bool] = Field(None, description="Query: Returns only the deleted documents.")
    id: Optional[str] = Field(None, description="Query: ")
    completed_from: Optional[str] = Field(None, description="Query: Limits results to the documents with the `date_completed` greater than or equal to this value.")
    completed_to: Optional[str] = Field(None, description="Query: Limits results to the documents with the `date_completed` less than this value.")
    membership_id: Optional[str] = Field(None, description="Query: Filter documents by the owner's 'membership_id'.")
    metadata: Optional[list[str]] = Field(None, description="Query: Filters documents by metadata. Pass metadata in the format of `metadata_{metadata-key}={metadata-value}` such as `metadata_opportunity_id=2181432`. The `metadata_` prefix is always required.")
    modified_from: Optional[str] = Field(None, description="Query: Limits results to the documents with the `date_modified` greater than or equal to this value.")
    modified_to: Optional[str] = Field(None, description="Query: Limits results to the documents with the `date_modified` less than this value.")
    q: Optional[str] = Field(None, description="Query: Filters documents by name or reference number (stored on the template level).")
    status: Optional[dict[str, Any]] = Field(None, description="Query: Filters documents by the status.   * 0: document.draft   * 1: document.sent   * 2: document.completed   * 3: document.uploaded   * 4: document.error   * 5: document.viewed   * 6: document.waiting_appr")
    status__ne: Optional[dict[str, Any]] = Field(None, description="Query: Exludes documents with this status.   * 0: document.draft   * 1: document.sent   * 2: document.completed   * 3: document.uploaded   * 4: document.error   * 5: document.viewed   * 6: document.waiting_a")
    tag: Optional[str] = Field(None, description="Query: Filters documents by tag.")

class _Input_listSections(BaseModel):
    model_config = ConfigDict(extra='forbid')
    document_id: str = Field(..., description="Path: Document ID")

class _Input_reassignDocumentRecipient(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(..., description="Path: Document UUID")
    recipient_id: str = Field(..., description="Path: Recipient UUID")
    body: dict[str, Any] = Field(..., description="JSON body — see PandaDoc docs for the schema of this endpoint.")

class _Input_sectionDetails(BaseModel):
    model_config = ConfigDict(extra='forbid')
    document_id: str = Field(..., description="Path: Document ID.")
    upload_id: str = Field(..., description="Path: Upload ID.")

class _Input_sectionInfo(BaseModel):
    model_config = ConfigDict(extra='forbid')
    document_id: str = Field(..., description="Path: Document ID.")
    section_id: str = Field(..., description="Path: Section ID.")

class _Input_sendDocument(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(..., description="Path: Document ID")
    body: dict[str, Any] = Field(..., description="JSON body — see PandaDoc docs for the schema of this endpoint.")

class _Input_statusDocument(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(..., description="Path: Specify document ID.")

class _Input_transferAllDocumentsOwnership(BaseModel):
    model_config = ConfigDict(extra='forbid')
    body: dict[str, Any] = Field(..., description="JSON body — see PandaDoc docs for the schema of this endpoint.")

class _Input_transferDocumentOwnership(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(..., description="Path: Specify document ID.")
    body: dict[str, Any] = Field(..., description="JSON body — see PandaDoc docs for the schema of this endpoint.")

class _Input_updateDocument(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(..., description="Path: Document ID")
    body: dict[str, Any] = Field(..., description="JSON body — see PandaDoc docs for the schema of this endpoint.")

class _Input_updateDocumentFieldsAssignment(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(..., description="Path: Document UUID.")
    body: dict[str, Any] = Field(..., description="JSON body — see PandaDoc docs for the schema of this endpoint.")

class _Input_uploadSection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    document_id: str = Field(..., description="Path: Document ID")
    merge_field_scope: Optional[str] = Field(None, description="Query: Determines how the fields are mapped when creating a section.   * document: Default value. The fields of the entire document are updated.   * upload: Only the fields from the created section are updat")
    body: dict[str, Any] = Field(..., description="JSON body — see PandaDoc docs for the schema of this endpoint.")

class _Input_uploadSectionWithUpload(BaseModel):
    model_config = ConfigDict(extra='forbid')
    document_id: str = Field(..., description="Path: Document ID")
    merge_field_scope: Optional[str] = Field(None, description="Query: Determines how the fields are mapped when creating a section.   * document: Default value. The fields of the entire document are updated.   * upload: Only the fields from the created section are updat")
    file_path: Optional[str] = Field(None, description="Local path to the file to upload. At least one of file_path or file_bytes_b64 is required.")
    file_bytes_b64: Optional[str] = Field(None, description="Base64-encoded file bytes (alternative to file_path).")
    file_name: Optional[str] = Field(None, description="Filename to send. Defaults to basename of file_path or upload.bin.")
    content_type: Optional[str] = Field(None, description="MIME type. Guessed from extension if unset.")
    multipart_extra_fields: Optional[dict[str, Any]] = Field(None, description="Extra non-file form fields to include in the multipart body.")


def register(mcp) -> None:  # noqa: ANN001
    """Register every tool in this module with the FastMCP instance."""

    @mcp.tool()
    def pandadoc_add_document_recipient(params: _Input_addDocumentRecipient) -> Any:
        """Add Document Recipient"""
        return pandadoc_client.call(
            "addDocumentRecipient",
            path_params={"id": params.id},
            json_body=params.body,
        )

    @mcp.tool()
    def pandadoc_add_dsv_named_items(params: _Input_addDsvNamedItems) -> Any:
        """Add DSV Named Items to a Document"""
        return pandadoc_client.call(
            "addDsvNamedItems",
            path_params={"document_id": params.document_id},
            json_body=params.body,
        )

    @mcp.tool()
    def pandadoc_append_content_library_item_to_document(params: _Input_appendContentLibraryItemToDocument) -> Any:
        """Append Content Library Item to a document"""
        return pandadoc_client.call(
            "appendContentLibraryItemToDocument",
            path_params={"id": params.id},
            json_body=params.body,
        )

    @mcp.tool()
    def pandadoc_bulk_delete_documents(params: _Input_bulkDeleteDocuments) -> Any:
        """Delete documents (bulk)"""
        return pandadoc_client.call(
            "bulkDeleteDocuments",
            json_body=params.body,
        )

    @mcp.tool()
    def pandadoc_change_document_status(params: _Input_changeDocumentStatus) -> Any:
        """Document Status Change"""
        return pandadoc_client.call(
            "changeDocumentStatus",
            path_params={"id": params.id},
            json_body=params.body,
        )

    @mcp.tool()
    def pandadoc_change_document_status_with_upload(params: _Input_changeDocumentStatusWithUpload) -> Any:
        """Document Status Change with Upload"""
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
            "changeDocumentStatusWithUpload",
            path_params={"id": params.id},
            multipart=mp_parts,
        )

    @mcp.tool()
    def pandadoc_create_document(params: _Input_createDocument) -> Any:
        """Create Document"""
        return pandadoc_client.call(
            "createDocument",
            query={"editor_ver": params.editor_ver, "use_form_field_properties": params.use_form_field_properties},
            json_body=params.body,
        )

    @mcp.tool()
    def pandadoc_create_document_attachment(params: _Input_createDocumentAttachment) -> Any:
        """Create Document Attachment"""
        return pandadoc_client.call(
            "createDocumentAttachment",
            path_params={"id": params.id},
            json_body=params.body,
        )

    @mcp.tool()
    def pandadoc_create_document_attachment_from_file_upload(params: _Input_createDocumentAttachmentFromFileUpload) -> Any:
        """Create Document Attachment From Upload"""
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
            "createDocumentAttachmentFromFileUpload",
            path_params={"id": params.id},
            multipart=mp_parts,
        )

    @mcp.tool()
    def pandadoc_create_document_editing_session(params: _Input_createDocumentEditingSession) -> Any:
        """Create Document Editing Session"""
        return pandadoc_client.call(
            "createDocumentEditingSession",
            path_params={"id": params.id},
            json_body=params.body,
        )

    @mcp.tool()
    def pandadoc_create_document_fields(params: _Input_createDocumentFields) -> Any:
        """Create Document Fields"""
        return pandadoc_client.call(
            "createDocumentFields",
            path_params={"id": params.id},
            json_body=params.body,
        )

    @mcp.tool()
    def pandadoc_create_document_from_markdown_upload(params: _Input_createDocumentFromMarkdownUpload) -> Any:
        """Create Document from Markdown File Upload"""
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
            "createDocumentFromMarkdownUpload",
            multipart=mp_parts,
        )

    @mcp.tool()
    def pandadoc_create_document_from_upload(params: _Input_createDocumentFromUpload) -> Any:
        """Create Document from File Upload"""
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
            "createDocumentFromUpload",
            query={"editor_ver": params.editor_ver, "use_form_field_properties": params.use_form_field_properties},
            multipart=mp_parts,
        )

    @mcp.tool()
    def pandadoc_create_document_link(params: _Input_createDocumentLink) -> Any:
        """Create Document Session for Embedded Sign"""
        return pandadoc_client.call(
            "createDocumentLink",
            path_params={"id": params.id},
            json_body=params.body,
        )

    @mcp.tool()
    def pandadoc_create_export_docx_task(params: _Input_createExportDocxTask) -> Any:
        """[Beta] Create DOCX Export Task"""
        return pandadoc_client.call(
            "createExportDocxTask",
            path_params={"document_id": params.document_id},
        )

    @mcp.tool()
    def pandadoc_delete_document(params: _Input_deleteDocument) -> Any:
        """Delete Document"""
        return pandadoc_client.call(
            "deleteDocument",
            path_params={"id": params.id},
        )

    @mcp.tool()
    def pandadoc_delete_document_attachment(params: _Input_deleteDocumentAttachment) -> Any:
        """Delete Document Attachment"""
        return pandadoc_client.call(
            "deleteDocumentAttachment",
            path_params={"id": params.id, "attachment_id": params.attachment_id},
        )

    @mcp.tool()
    def pandadoc_delete_document_recipient(params: _Input_deleteDocumentRecipient) -> Any:
        """Delete Document Recipient"""
        return pandadoc_client.call(
            "deleteDocumentRecipient",
            path_params={"id": params.id, "recipient_id": params.recipient_id},
        )

    @mcp.tool()
    def pandadoc_delete_section(params: _Input_deleteSection) -> Any:
        """Delete Document Section"""
        return pandadoc_client.call(
            "deleteSection",
            path_params={"document_id": params.document_id, "section_id": params.section_id},
        )

    @mcp.tool()
    def pandadoc_details_document(params: _Input_detailsDocument) -> Any:
        """Document Details"""
        return pandadoc_client.call(
            "detailsDocument",
            path_params={"id": params.id},
        )

    @mcp.tool()
    def pandadoc_details_document_attachment(params: _Input_detailsDocumentAttachment) -> Any:
        """Document Attachment Details"""
        return pandadoc_client.call(
            "detailsDocumentAttachment",
            path_params={"id": params.id, "attachment_id": params.attachment_id},
        )

    @mcp.tool()
    def pandadoc_document_e_sign_disclosure(params: _Input_documentESignDisclosure) -> Any:
        """Document eSign disclosure"""
        return pandadoc_client.call(
            "documentESignDisclosure",
            path_params={"document_id": params.document_id},
        )

    @mcp.tool()
    def pandadoc_document_move_to_folder(params: _Input_documentMoveToFolder) -> Any:
        """Document move to folder"""
        return pandadoc_client.call(
            "documentMoveToFolder",
            path_params={"id": params.id, "folder_id": params.folder_id},
        )

    @mcp.tool()
    def pandadoc_document_revert_to_draft(params: _Input_documentRevertToDraft) -> Any:
        """Move Document to Draft"""
        return pandadoc_client.call(
            "documentRevertToDraft",
            path_params={"id": params.id},
        )

    @mcp.tool()
    def pandadoc_document_settings_get(params: _Input_documentSettingsGet) -> Any:
        """Get document settings"""
        return pandadoc_client.call(
            "documentSettingsGet",
            path_params={"document_id": params.document_id},
        )

    @mcp.tool()
    def pandadoc_document_settings_update(params: _Input_documentSettingsUpdate) -> Any:
        """Update document settings"""
        return pandadoc_client.call(
            "documentSettingsUpdate",
            path_params={"document_id": params.document_id},
            json_body=params.body,
        )

    @mcp.tool()
    def pandadoc_download_document(params: _Input_downloadDocument) -> Any:
        """Document Download"""
        return pandadoc_client.call(
            "downloadDocument",
            path_params={"id": params.id},
            query={"watermark_color": params.watermark_color, "watermark_font_size": params.watermark_font_size, "watermark_opacity": params.watermark_opacity, "watermark_text": params.watermark_text, "separate_files": params.separate_files},
        )

    @mcp.tool()
    def pandadoc_download_document_attachment(params: _Input_downloadDocumentAttachment) -> Any:
        """Download Document Attachment"""
        return pandadoc_client.call(
            "downloadDocumentAttachment",
            path_params={"id": params.id, "attachment_id": params.attachment_id},
        )

    @mcp.tool()
    def pandadoc_download_protected_document(params: _Input_downloadProtectedDocument) -> Any:
        """Download Completed Document"""
        return pandadoc_client.call(
            "downloadProtectedDocument",
            path_params={"id": params.id},
            query={"separate_files": params.separate_files},
        )

    @mcp.tool()
    def pandadoc_edit_document_recipient(params: _Input_editDocumentRecipient) -> Any:
        """Update Document Recipient"""
        return pandadoc_client.call(
            "editDocumentRecipient",
            path_params={"id": params.id, "recipient_id": params.recipient_id},
            json_body=params.body,
        )

    @mcp.tool()
    def pandadoc_get_document_content(params: _Input_getDocumentContent) -> Any:
        """[Beta] Document Content"""
        return pandadoc_client.call(
            "getDocumentContent",
            poll=True,
            poll_max_seconds=params.poll_max_seconds,
        )

    @mcp.tool()
    def pandadoc_get_document_summary(params: _Input_getDocumentSummary) -> Any:
        """[Beta] Document Summary"""
        return pandadoc_client.call(
            "getDocumentSummary",
            poll=True,
            poll_max_seconds=params.poll_max_seconds,
        )

    @mcp.tool()
    def pandadoc_get_docx_export_task(params: _Input_getDocxExportTask) -> Any:
        """[Beta] DOCX Export Task"""
        return pandadoc_client.call(
            "getDocxExportTask",
            path_params={"document_id": params.document_id, "task_id": params.task_id},
        )

    @mcp.tool()
    def pandadoc_list_document_attachments(params: _Input_listDocumentAttachments) -> Any:
        """List Document Attachments"""
        return pandadoc_client.call(
            "listDocumentAttachments",
            path_params={"id": params.id},
        )

    @mcp.tool()
    def pandadoc_list_document_audit_trail(params: _Input_listDocumentAuditTrail) -> Any:
        """List Document Audit Trail"""
        return pandadoc_client.call(
            "listDocumentAuditTrail",
            path_params={"document_id": params.document_id},
            query={"limit": params.limit, "offset": params.offset},
        )

    @mcp.tool()
    def pandadoc_list_document_fields(params: _Input_listDocumentFields) -> Any:
        """List Document Fields"""
        return pandadoc_client.call(
            "listDocumentFields",
            path_params={"id": params.id},
        )

    @mcp.tool()
    def pandadoc_list_documents(params: _Input_listDocuments) -> Any:
        """List Documents"""
        return pandadoc_client.call(
            "listDocuments",
            query={"template_id": params.template_id, "form_id": params.form_id, "folder_uuid": params.folder_uuid, "contact_id": params.contact_id, "count": params.count, "page": params.page, "order_by": params.order_by, "created_from": params.created_from, "created_to": params.created_to, "deleted": params.deleted, "id": params.id, "completed_from": params.completed_from, "completed_to": params.completed_to, "membership_id": params.membership_id, "metadata": params.metadata, "modified_from": params.modified_from, "modified_to": params.modified_to, "q": params.q, "status": params.status, "status__ne": params.status__ne, "tag": params.tag},
        )

    @mcp.tool()
    def pandadoc_list_sections(params: _Input_listSections) -> Any:
        """List Document Sections"""
        return pandadoc_client.call(
            "listSections",
            path_params={"document_id": params.document_id},
        )

    @mcp.tool()
    def pandadoc_reassign_document_recipient(params: _Input_reassignDocumentRecipient) -> Any:
        """Change Signer (Reassign Document Recipient)"""
        return pandadoc_client.call(
            "reassignDocumentRecipient",
            path_params={"id": params.id, "recipient_id": params.recipient_id},
            json_body=params.body,
        )

    @mcp.tool()
    def pandadoc_section_details(params: _Input_sectionDetails) -> Any:
        """Document Section Upload Status"""
        return pandadoc_client.call(
            "sectionDetails",
            path_params={"document_id": params.document_id, "upload_id": params.upload_id},
        )

    @mcp.tool()
    def pandadoc_section_info(params: _Input_sectionInfo) -> Any:
        """Document Section Details"""
        return pandadoc_client.call(
            "sectionInfo",
            path_params={"document_id": params.document_id, "section_id": params.section_id},
        )

    @mcp.tool()
    def pandadoc_send_document(params: _Input_sendDocument) -> Any:
        """Send Document"""
        return pandadoc_client.call(
            "sendDocument",
            path_params={"id": params.id},
            json_body=params.body,
        )

    @mcp.tool()
    def pandadoc_status_document(params: _Input_statusDocument) -> Any:
        """Document Status"""
        return pandadoc_client.call(
            "statusDocument",
            path_params={"id": params.id},
        )

    @mcp.tool()
    def pandadoc_transfer_all_documents_ownership(params: _Input_transferAllDocumentsOwnership) -> Any:
        """Transfer all documents ownership"""
        return pandadoc_client.call(
            "transferAllDocumentsOwnership",
            json_body=params.body,
        )

    @mcp.tool()
    def pandadoc_transfer_document_ownership(params: _Input_transferDocumentOwnership) -> Any:
        """Update document ownership"""
        return pandadoc_client.call(
            "transferDocumentOwnership",
            path_params={"id": params.id},
            json_body=params.body,
        )

    @mcp.tool()
    def pandadoc_update_document(params: _Input_updateDocument) -> Any:
        """Update Document"""
        return pandadoc_client.call(
            "updateDocument",
            path_params={"id": params.id},
            json_body=params.body,
        )

    @mcp.tool()
    def pandadoc_update_document_fields_assignment(params: _Input_updateDocumentFieldsAssignment) -> Any:
        """Update Document Fields Assignment"""
        return pandadoc_client.call(
            "updateDocumentFieldsAssignment",
            path_params={"id": params.id},
            json_body=params.body,
        )

    @mcp.tool()
    def pandadoc_upload_section(params: _Input_uploadSection) -> Any:
        """Create Document Section"""
        return pandadoc_client.call(
            "uploadSection",
            path_params={"document_id": params.document_id},
            query={"merge_field_scope": params.merge_field_scope},
            json_body=params.body,
        )

    @mcp.tool()
    def pandadoc_upload_section_with_upload(params: _Input_uploadSectionWithUpload) -> Any:
        """Create Document Section from File Upload"""
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
            "uploadSectionWithUpload",
            path_params={"document_id": params.document_id},
            query={"merge_field_scope": params.merge_field_scope},
            multipart=mp_parts,
        )
