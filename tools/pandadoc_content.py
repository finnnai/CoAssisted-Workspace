# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Auto-generated PandaDoc API tools — module: content.

DO NOT EDIT BY HAND. Regenerate via:
    python3 scripts/generate_pandadoc_tools.py

This module wraps 12 PandaDoc operations under tag(s): Content Library Items, Forms, Product catalog, Quotes.

Pydantic input classes live at MODULE scope (not inside register())
so FastMCP's typing.get_type_hints can resolve them. Earlier rev
nested them in register() and triggered InvalidSignature on startup.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

import pandadoc_client

class _Input_createCatalogItem(BaseModel):
    model_config = ConfigDict(extra='forbid')
    body: dict[str, Any] = Field(..., description="JSON body — see PandaDoc docs for the schema of this endpoint.")

class _Input_createContentLibraryItem(BaseModel):
    model_config = ConfigDict(extra='forbid')
    body: dict[str, Any] = Field(..., description="JSON body — see PandaDoc docs for the schema of this endpoint.")

class _Input_createContentLibraryItemFromUpload(BaseModel):
    model_config = ConfigDict(extra='forbid')
    file_path: Optional[str] = Field(None, description="Local path to the file to upload. At least one of file_path or file_bytes_b64 is required.")
    file_bytes_b64: Optional[str] = Field(None, description="Base64-encoded file bytes (alternative to file_path).")
    file_name: Optional[str] = Field(None, description="Filename to send. Defaults to basename of file_path or upload.bin.")
    content_type: Optional[str] = Field(None, description="MIME type. Guessed from extension if unset.")
    multipart_extra_fields: Optional[dict[str, Any]] = Field(None, description="Extra non-file form fields to include in the multipart body.")

class _Input_deleteCatalogItem(BaseModel):
    model_config = ConfigDict(extra='forbid')
    item_uuid: str = Field(..., description="Path: Catalog item UUID")

class _Input_detailsContentLibraryItem(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(..., description="Path: Content Library Item ID")

class _Input_getCatalogItem(BaseModel):
    model_config = ConfigDict(extra='forbid')
    item_uuid: str = Field(..., description="Path: Catalog item UUID")

class _Input_listContentLibraryItems(BaseModel):
    model_config = ConfigDict(extra='forbid')
    q: Optional[str] = Field(None, description="Query: Search query. Filter by content library item name.")
    id: Optional[str] = Field(None, description="Query: Specify content library item ID.")
    deleted: Optional[bool] = Field(None, description="Query: Returns only the deleted content library items.")
    folder_uuid: Optional[str] = Field(None, description="Query: The UUID of the folder where the content library items are stored.")
    count: Optional[int] = Field(None, description="Query: Specify how many content library items to return. Default is 50 content library items, maximum is 100 content library items.")
    page: Optional[int] = Field(None, description="Query: Specify which page of the dataset to return.")
    tag: Optional[str] = Field(None, description="Query: Search tag. Filter by content library item tag.")

class _Input_listForm(BaseModel):
    model_config = ConfigDict(extra='forbid')
    count: Optional[int] = Field(None, description="Query: Specify how many forms to return. Default is 50 forms, maximum is 100 forms.")
    page: Optional[int] = Field(None, description="Query: Specify which page of the dataset to return.")
    status: Optional[list[str]] = Field(None, description="Query: Specify which status of the forms dataset to return.")
    order_by: Optional[str] = Field(None, description="Query: Specify the form dataset order to return.")
    asc: Optional[bool] = Field(None, description="Query: Specify sorting the result-set in ascending or descending order.")
    name: Optional[str] = Field(None, description="Query: Specify the form name.")

class _Input_quoteUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    document_id: str = Field(..., description="Path: Document ID")
    quote_id: str = Field(..., description="Path: Quote ID")
    body: dict[str, Any] = Field(..., description="JSON body — see PandaDoc docs for the schema of this endpoint.")

class _Input_searchCatalogItems(BaseModel):
    model_config = ConfigDict(extra='forbid')
    page: Optional[float] = Field(None, description="Query: Page number.")
    per_page: Optional[float] = Field(None, description="Query: Items per page.")
    query: Optional[str] = Field(None, description="Query: Search query. Searches the following fields: Title, SKU, description, category name, custom fields name and value.")
    order_by: Optional[str] = Field(None, description="Query: Ordering principle for displaying search results.")
    types: Optional[list[dict[str, Any]]] = Field(None, description="Query: Filter by catalog item types.")
    billing_types: Optional[list[str]] = Field(None, description="Query: Filter by billing types.")
    exclude_uuids: Optional[list[str]] = Field(None, description="Query: A list of item uuids to be excluded from search.")
    category_id: Optional[str] = Field(None, description="Query: Category id.")
    no_category: Optional[bool] = Field(None, description="Query: ")

class _Input_statusContentLibraryItem(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(..., description="Path: Content Library Item ID")

class _Input_updateCatalogItem(BaseModel):
    model_config = ConfigDict(extra='forbid')
    item_uuid: str = Field(..., description="Path: Catalog item UUID")
    body: dict[str, Any] = Field(..., description="JSON body — see PandaDoc docs for the schema of this endpoint.")


def register(mcp) -> None:  # noqa: ANN001
    """Register every tool in this module with the FastMCP instance."""

    @mcp.tool()
    def pandadoc_create_catalog_item(params: _Input_createCatalogItem) -> Any:
        """Create Catalog Item"""
        return pandadoc_client.call(
            "createCatalogItem",
            json_body=params.body,
        )

    @mcp.tool()
    def pandadoc_create_content_library_item(params: _Input_createContentLibraryItem) -> Any:
        """Create Content Library Item"""
        return pandadoc_client.call(
            "createContentLibraryItem",
            json_body=params.body,
        )

    @mcp.tool()
    def pandadoc_create_content_library_item_from_upload(params: _Input_createContentLibraryItemFromUpload) -> Any:
        """Create Content Library Item from File Upload"""
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
            "createContentLibraryItemFromUpload",
            multipart=mp_parts,
        )

    @mcp.tool()
    def pandadoc_delete_catalog_item(params: _Input_deleteCatalogItem) -> Any:
        """Delete Catalog Item"""
        return pandadoc_client.call(
            "deleteCatalogItem",
            path_params={"item_uuid": params.item_uuid},
        )

    @mcp.tool()
    def pandadoc_details_content_library_item(params: _Input_detailsContentLibraryItem) -> Any:
        """Content Library Item Details"""
        return pandadoc_client.call(
            "detailsContentLibraryItem",
            path_params={"id": params.id},
        )

    @mcp.tool()
    def pandadoc_get_catalog_item(params: _Input_getCatalogItem) -> Any:
        """Catalog Item Details"""
        return pandadoc_client.call(
            "getCatalogItem",
            path_params={"item_uuid": params.item_uuid},
        )

    @mcp.tool()
    def pandadoc_list_content_library_items(params: _Input_listContentLibraryItems) -> Any:
        """List Content Library Item"""
        return pandadoc_client.call(
            "listContentLibraryItems",
            query={"q": params.q, "id": params.id, "deleted": params.deleted, "folder_uuid": params.folder_uuid, "count": params.count, "page": params.page, "tag": params.tag},
        )

    @mcp.tool()
    def pandadoc_list_form(params: _Input_listForm) -> Any:
        """List Forms"""
        return pandadoc_client.call(
            "listForm",
            query={"count": params.count, "page": params.page, "status": params.status, "order_by": params.order_by, "asc": params.asc, "name": params.name},
        )

    @mcp.tool()
    def pandadoc_quote_update(params: _Input_quoteUpdate) -> Any:
        """Quote update"""
        return pandadoc_client.call(
            "quoteUpdate",
            path_params={"document_id": params.document_id, "quote_id": params.quote_id},
            json_body=params.body,
        )

    @mcp.tool()
    def pandadoc_search_catalog_items(params: _Input_searchCatalogItems) -> Any:
        """List Catalog Items Search"""
        return pandadoc_client.call(
            "searchCatalogItems",
            query={"page": params.page, "per_page": params.per_page, "query": params.query, "order_by": params.order_by, "types": params.types, "billing_types": params.billing_types, "exclude_uuids": params.exclude_uuids, "category_id": params.category_id, "no_category": params.no_category},
        )

    @mcp.tool()
    def pandadoc_status_content_library_item(params: _Input_statusContentLibraryItem) -> Any:
        """Content Library Item Status"""
        return pandadoc_client.call(
            "statusContentLibraryItem",
            path_params={"id": params.id},
        )

    @mcp.tool()
    def pandadoc_update_catalog_item(params: _Input_updateCatalogItem) -> Any:
        """Update Catalog Item"""
        return pandadoc_client.call(
            "updateCatalogItem",
            path_params={"item_uuid": params.item_uuid},
            json_body=params.body,
        )
