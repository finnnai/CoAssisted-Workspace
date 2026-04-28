"""Drive tools: search, read, create folder/file, move, share."""

from __future__ import annotations

import gservices

import base64
import io
import json
from pathlib import Path
from typing import Optional

from googleapiclient.http import MediaIoBaseDownload, MediaInMemoryUpload
from pydantic import BaseModel, ConfigDict, Field

from dryrun import dry_run_preview, is_dry_run
from errors import format_error
from logging_util import log


def _service():
    return gservices.drive()


# --------------------------------------------------------------------------- #
# Input models
# --------------------------------------------------------------------------- #


class SearchInput(BaseModel):
    model_config = ConfigDict(
        str_strip_whitespace=True, extra="forbid", populate_by_name=True
    )

    query: str = Field(
        ...,
        alias="q",
        description=(
            "Drive query string. Examples: \"name contains 'budget'\", "
            "\"mimeType='application/vnd.google-apps.folder'\", "
            "\"'PARENT_FOLDER_ID' in parents and trashed=false\". "
            "Alias `q` is also accepted."
        ),
    )
    limit: int = Field(
        default=25, ge=1, le=1000,
        alias="page_size",
        description="Max files to return. Alias `page_size` is also accepted.",
    )


class ReadFileInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    file_id: str = Field(..., description="Drive file ID.")
    export_mime_type: Optional[str] = Field(
        default=None,
        description=(
            "For Google Docs/Sheets/Slides, override the export MIME type. "
            "Common values: 'text/plain', 'text/markdown', 'text/csv', "
            "'application/pdf'. Defaults picked automatically."
        ),
    )


class CreateFolderInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: str = Field(..., description="Folder name.")
    parent_id: Optional[str] = Field(
        default=None, description="Parent folder ID. Defaults to My Drive root."
    )


class UploadFileInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: str = Field(..., description="Destination file name.")
    content: str = Field(..., description="File content as text.")
    mime_type: str = Field(
        default="text/plain", description="MIME type, e.g. 'text/plain', 'text/markdown'."
    )
    parent_id: Optional[str] = Field(default=None)


class UploadBinaryInput(BaseModel):
    """Upload a binary file. Provide EITHER local_path OR content_b64, plus a name."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: str = Field(..., description="Destination file name in Drive.")
    local_path: Optional[str] = Field(
        default=None, description="Absolute path to a local file to upload."
    )
    content_b64: Optional[str] = Field(
        default=None, description="Base64-encoded file content (for in-memory data)."
    )
    mime_type: Optional[str] = Field(
        default=None, description="MIME type. Inferred from filename if omitted."
    )
    parent_id: Optional[str] = Field(default=None)
    dry_run: Optional[bool] = Field(default=None)


class DownloadBinaryInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    file_id: str = Field(..., description="Drive file ID.")
    save_to_path: Optional[str] = Field(
        default=None,
        description=(
            "If given, write bytes to this absolute path and return metadata. "
            "Otherwise returns base64 in the response (watch out for big files)."
        ),
    )


class MoveFileInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    file_id: str = Field(...)
    new_parent_id: str = Field(..., description="Destination folder ID.")
    dry_run: Optional[bool] = Field(default=None)


class DeleteFileInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    file_id: str = Field(...)
    permanent: bool = Field(
        default=False,
        description="If True, permanently delete. Otherwise move to Trash (recoverable).",
    )
    dry_run: Optional[bool] = Field(default=None)


class ShareFileInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    file_id: str = Field(...)
    email: Optional[str] = Field(
        default=None, description="Email to share with. Omit for anyone-with-link."
    )
    role: str = Field(
        default="reader", description="'reader', 'commenter', 'writer', or 'owner'."
    )
    notify: bool = Field(default=False, description="Send notification email.")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


_GOOGLE_EXPORT_DEFAULTS = {
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
    "application/vnd.google-apps.presentation": "text/plain",
}


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #


def register(mcp) -> None:
    @mcp.tool(
        name="drive_search_files",
        annotations={
            "title": "Search Drive for files",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def drive_search_files(params: SearchInput) -> str:
        """Search Drive using the native Drive query language."""
        try:
            resp = (
                _service()
                .files()
                .list(
                    q=params.query,
                    pageSize=params.limit,
                    fields="files(id, name, mimeType, modifiedTime, parents, webViewLink, size)",
                )
                .execute()
            )
            return json.dumps({"count": len(resp.get("files", [])), "files": resp.get("files", [])}, indent=2)
        except Exception as e:
            return format_error(e)

    @mcp.tool(
        name="drive_read_file",
        annotations={
            "title": "Read a Drive file's content",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def drive_read_file(params: ReadFileInput) -> str:
        """Return the content of a Drive file as text.

        - Regular files: downloaded bytes decoded as UTF-8 (with errors='replace').
        - Google Docs/Sheets/Slides: exported using `export_mime_type` or a sensible default.
        """
        try:
            svc = _service()
            meta = svc.files().get(fileId=params.file_id, fields="id, name, mimeType").execute()
            mime = meta["mimeType"]
            buf = io.BytesIO()

            if mime.startswith("application/vnd.google-apps."):
                export_mime = params.export_mime_type or _GOOGLE_EXPORT_DEFAULTS.get(mime, "text/plain")
                req = svc.files().export_media(fileId=params.file_id, mimeType=export_mime)
            else:
                req = svc.files().get_media(fileId=params.file_id)

            downloader = MediaIoBaseDownload(buf, req)
            done = False
            while not done:
                _, done = downloader.next_chunk()

            content = buf.getvalue().decode("utf-8", errors="replace")
            return json.dumps(
                {"id": meta["id"], "name": meta["name"], "mime_type": mime, "content": content},
                indent=2,
            )
        except Exception as e:
            return format_error(e)

    @mcp.tool(
        name="drive_create_folder",
        annotations={
            "title": "Create a Drive folder",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def drive_create_folder(params: CreateFolderInput) -> str:
        """Create a new folder, optionally nested under `parent_id`."""
        try:
            body = {
                "name": params.name,
                "mimeType": "application/vnd.google-apps.folder",
            }
            if params.parent_id:
                body["parents"] = [params.parent_id]
            folder = _service().files().create(body=body, fields="id, name, webViewLink").execute()
            return json.dumps(folder, indent=2)
        except Exception as e:
            return format_error(e)

    @mcp.tool(
        name="drive_upload_text_file",
        annotations={
            "title": "Upload a text file to Drive",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def drive_upload_text_file(params: UploadFileInput) -> str:
        """Create a new Drive file with the given text content."""
        try:
            body = {"name": params.name}
            if params.parent_id:
                body["parents"] = [params.parent_id]
            media = MediaInMemoryUpload(
                params.content.encode("utf-8"), mimetype=params.mime_type
            )
            created = (
                _service()
                .files()
                .create(body=body, media_body=media, fields="id, name, webViewLink")
                .execute()
            )
            return json.dumps(created, indent=2)
        except Exception as e:
            return format_error(e)

    @mcp.tool(
        name="drive_move_file",
        annotations={
            "title": "Move a Drive file",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def drive_move_file(params: MoveFileInput) -> str:
        """Move a file to a new parent folder."""
        try:
            if is_dry_run(params.dry_run):
                return dry_run_preview(
                    "drive_move_file",
                    {"file_id": params.file_id, "new_parent_id": params.new_parent_id},
                )
            svc = _service()
            meta = svc.files().get(fileId=params.file_id, fields="parents").execute()
            old_parents = ",".join(meta.get("parents", []))
            moved = (
                svc.files()
                .update(
                    fileId=params.file_id,
                    addParents=params.new_parent_id,
                    removeParents=old_parents,
                    fields="id, parents",
                )
                .execute()
            )
            return json.dumps(moved, indent=2)
        except Exception as e:
            return format_error(e)

    @mcp.tool(
        name="drive_share_file",
        annotations={
            "title": "Share a Drive file",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def drive_share_file(params: ShareFileInput) -> str:
        """Grant access to a file. If `email` is omitted, creates an anyone-with-link permission."""
        try:
            if params.email:
                perm = {"type": "user", "role": params.role, "emailAddress": params.email}
            else:
                perm = {"type": "anyone", "role": params.role}
            created = (
                _service()
                .permissions()
                .create(
                    fileId=params.file_id,
                    body=perm,
                    sendNotificationEmail=params.notify,
                    fields="id, type, role",
                )
                .execute()
            )
            return json.dumps(created, indent=2)
        except Exception as e:
            return format_error(e)

    @mcp.tool(
        name="drive_upload_binary_file",
        annotations={
            "title": "Upload a binary file to Drive",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def drive_upload_binary_file(params: UploadBinaryInput) -> str:
        """Upload a binary file (PDF, image, anything) from disk or base64.

        Provide EITHER `local_path` OR `content_b64`, plus `name`.
        """
        try:
            if not (params.local_path or params.content_b64):
                return "Error: provide either local_path or content_b64."

            import mimetypes
            from googleapiclient.http import MediaFileUpload
            mime = params.mime_type or (mimetypes.guess_type(params.name)[0] or "application/octet-stream")

            # When local_path is given, peek size first and use streaming upload —
            # avoids loading huge files into memory.
            if params.local_path:
                p = Path(params.local_path).expanduser()
                if not p.is_file():
                    return f"Error: local_path not found: {p}"
                size = p.stat().st_size
                if is_dry_run(params.dry_run):
                    return dry_run_preview(
                        "drive_upload_binary_file",
                        {"name": params.name, "mime_type": mime, "size": size, "parent_id": params.parent_id},
                    )
                body: dict = {"name": params.name}
                if params.parent_id:
                    body["parents"] = [params.parent_id]
                # Resumable streaming upload — chunked from disk, never fully in RAM.
                media = MediaFileUpload(
                    str(p), mimetype=mime, resumable=True, chunksize=8 * 1024 * 1024
                )
                request = (
                    _service()
                    .files()
                    .create(body=body, media_body=media, fields="id, name, webViewLink, mimeType, size")
                )
                response = None
                while response is None:
                    _, response = request.next_chunk()
                created = response
                log.info("drive_upload_binary_file (streamed) %s (%d bytes)", params.name, size)
            else:
                # In-memory base64 input.
                data = base64.b64decode(params.content_b64)

                if is_dry_run(params.dry_run):
                    return dry_run_preview(
                        "drive_upload_binary_file",
                        {"name": params.name, "mime_type": mime, "size": len(data), "parent_id": params.parent_id},
                    )

                body = {"name": params.name}
                if params.parent_id:
                    body["parents"] = [params.parent_id]
                media = MediaInMemoryUpload(data, mimetype=mime)
                created = (
                    _service()
                    .files()
                    .create(body=body, media_body=media, fields="id, name, webViewLink, mimeType, size")
                    .execute()
                )
                log.info("drive_upload_binary_file (in-memory) %s (%d bytes)", params.name, len(data))
            return json.dumps(created, indent=2)
        except Exception as e:
            return format_error(e)

    @mcp.tool(
        name="drive_download_binary_file",
        annotations={
            "title": "Download any Drive file as bytes",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def drive_download_binary_file(params: DownloadBinaryInput) -> str:
        """Download a Drive file's bytes.

        If `save_to_path` is provided, writes to disk and returns metadata only.
        Otherwise returns base64 content (bounded to avoid blowing the context
        window — consider save_to_path for anything over ~500KB).

        For Google-native files (Docs, Sheets, Slides), this will fail. Use
        `drive_read_file` with an export MIME type instead.
        """
        try:
            svc = _service()
            meta = svc.files().get(fileId=params.file_id, fields="id, name, mimeType, size").execute()
            if meta["mimeType"].startswith("application/vnd.google-apps."):
                return (
                    f"Error: {meta['name']} is a Google-native file "
                    f"({meta['mimeType']}). Use drive_read_file with an export_mime_type."
                )

            buf = io.BytesIO()
            downloader = MediaIoBaseDownload(buf, svc.files().get_media(fileId=params.file_id))
            done = False
            while not done:
                _, done = downloader.next_chunk()
            data = buf.getvalue()

            if params.save_to_path:
                path = Path(params.save_to_path).expanduser()
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                return json.dumps(
                    {
                        "status": "saved",
                        "path": str(path),
                        "filename": meta["name"],
                        "mime_type": meta["mimeType"],
                        "size": len(data),
                    },
                    indent=2,
                )

            # Hard cap on inline base64 returns. Auto-save if exceeded.
            import config as _config
            max_inline = int(_config.get("max_inline_download_kb", 5120)) * 1024
            if len(data) > max_inline:
                auto_path = _config.resolve_auto_download_path(meta["name"])
                auto_path.write_bytes(data)
                log.info(
                    "drive_download_binary_file auto-saved %s (%d KB > %d KB cap) to %s",
                    meta["name"], len(data) // 1024, max_inline // 1024, auto_path,
                )
                return json.dumps(
                    {
                        "status": "auto_saved",
                        "path": str(auto_path),
                        "filename": meta["name"],
                        "mime_type": meta["mimeType"],
                        "size": len(data),
                        "size_kb": round(len(data) / 1024),
                        "max_inline_kb": max_inline // 1024,
                        "note": (
                            f"File exceeded max_inline_download_kb ({max_inline // 1024} KB) "
                            "and was auto-saved to default_download_dir."
                        ),
                    },
                    indent=2,
                )

            return json.dumps(
                {
                    "filename": meta["name"],
                    "mime_type": meta["mimeType"],
                    "size": len(data),
                    "content_b64": base64.b64encode(data).decode("ascii"),
                },
                indent=2,
            )
        except Exception as e:
            return format_error(e)

    @mcp.tool(
        name="drive_delete_file",
        annotations={
            "title": "Delete a Drive file (trash or permanent)",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def drive_delete_file(params: DeleteFileInput) -> str:
        """Trash (recoverable) or permanently delete a Drive file."""
        try:
            if is_dry_run(params.dry_run):
                return dry_run_preview(
                    "drive_delete_file",
                    {"file_id": params.file_id, "permanent": params.permanent},
                )
            svc = _service()
            if params.permanent:
                svc.files().delete(fileId=params.file_id).execute()
                return json.dumps(
                    {"status": "permanently_deleted", "file_id": params.file_id}
                )
            svc.files().update(fileId=params.file_id, body={"trashed": True}).execute()
            return json.dumps({"status": "trashed", "file_id": params.file_id})
        except Exception as e:
            return format_error(e)
