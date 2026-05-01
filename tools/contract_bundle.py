# © 2026 CoAssisted Workspace. Licensed under MIT.
"""MCP tool wrapper for the contract bundle generator.

Exposes one tool: workflow_contract_bundle.
Pipeline:
  1. Search Drive for likely-contract files (using name patterns + mime type)
  2. Filter by year + contract type
  3. Download each file's bytes
  4. Pack into a ZIP under /tmp
  5. Upload the ZIP back to Drive (or return the local path)
  6. Generate an index Doc with counterparty + link table
"""

from __future__ import annotations

import io
import json
import os
import tempfile
import zipfile
from typing import Optional

from googleapiclient.http import MediaIoBaseDownload, MediaInMemoryUpload
from pydantic import BaseModel, ConfigDict, Field

import contract_bundle as core
import gservices
from errors import format_error
from logging_util import log


def _drive():
    return gservices.drive()


# Default Drive search if nothing more specific provided.
# We use a coarse query and let core.filter_contracts do the heavy lifting.
def _drive_search_query(year: int | None) -> str:
    parts = [
        "trashed=false",
        # one of these tokens in the name
        "(name contains 'NDA' or name contains 'agreement' or name contains 'contract' "
        "or name contains 'MSA' or name contains 'SOW' or name contains 'DPA' "
        "or name contains 'signed' or name contains 'executed')",
    ]
    if year:
        parts.append(f"modifiedTime >= '{year:04d}-01-01T00:00:00'")
        parts.append(f"modifiedTime < '{year + 1:04d}-01-01T00:00:00'")
    return " and ".join(parts)


def _search_drive_for_contracts(year: int | None, page_size: int = 200) -> list[dict]:
    drive = _drive()
    q = _drive_search_query(year)
    files: list[dict] = []
    page_token = None
    while True:
        resp = (
            drive.files()
            .list(
                q=q,
                fields="nextPageToken, files(id, name, mimeType, modifiedTime, webViewLink, size)",
                pageSize=page_size,
                pageToken=page_token,
                orderBy="modifiedTime desc",
            )
            .execute()
        )
        files.extend(resp.get("files", []))
        page_token = resp.get("nextPageToken")
        if not page_token or len(files) >= 500:  # safety cap
            break
    return files


def _download_file_bytes(file_id: str, mime_type: str) -> tuple[bytes, str]:
    """Return (bytes, suggested_extension) for one Drive file."""
    drive = _drive()
    # Google Docs/Sheets/Slides need export, not direct download.
    if mime_type == "application/vnd.google-apps.document":
        request = drive.files().export_media(fileId=file_id, mimeType="application/pdf")
        ext = "pdf"
    elif mime_type == "application/vnd.google-apps.spreadsheet":
        request = drive.files().export_media(fileId=file_id, mimeType="application/pdf")
        ext = "pdf"
    else:
        request = drive.files().get_media(fileId=file_id)
        ext = ""  # use original extension from filename
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buf.getvalue(), ext


def _pack_zip(files: list[core.ContractFile]) -> str:
    """Download each file and pack into a ZIP. Returns the ZIP path."""
    fd, zip_path = tempfile.mkstemp(prefix="contracts_", suffix=".zip")
    os.close(fd)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            try:
                data, force_ext = _download_file_bytes(f.file_id, f.mime_type)
            except Exception as e:
                log.warning("contract_bundle: skip %s (%s) — %s", f.name, f.file_id, e)
                continue
            arcname = f.name
            if force_ext and "." not in arcname:
                arcname = f"{arcname}.{force_ext}"
            zf.writestr(arcname, data)
    return zip_path


def _upload_to_drive(local_path: str, dest_name: str,
                     mime_type: str = "application/zip") -> dict:
    drive = _drive()
    with open(local_path, "rb") as fh:
        data = fh.read()
    media = MediaInMemoryUpload(data, mimetype=mime_type)
    return (
        drive.files()
        .create(
            body={"name": dest_name},
            media_body=media,
            fields="id, name, webViewLink",
        )
        .execute()
    )


def _create_index_doc(title: str, markdown: str) -> dict:
    """Upload a markdown body as a Google Doc. Drive auto-converts."""
    drive = _drive()
    media = MediaInMemoryUpload(markdown.encode("utf-8"), mimetype="text/markdown")
    return (
        drive.files()
        .create(
            body={"name": title, "mimeType": "application/vnd.google-apps.document"},
            media_body=media,
            fields="id, name, webViewLink",
        )
        .execute()
    )


# --------------------------------------------------------------------------- #
# Pydantic input
# --------------------------------------------------------------------------- #


class ContractBundleInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    year: Optional[int] = Field(
        default=None,
        description=("Year to filter by (modifiedTime). E.g. 2025. "
                     "Omit for all years."),
    )
    contract_type: Optional[str] = Field(
        default=None,
        description=("Contract type filter ('NDA', 'MSA', 'SOW', 'agreement', etc). "
                     "Omit for any."),
    )
    bundle_title: Optional[str] = Field(
        default=None,
        description=("Title for the index Doc + ZIP. "
                     "Default: 'Contracts <type> <year>'."),
    )
    upload_zip: bool = Field(
        default=True,
        description="Upload the ZIP to Drive. False returns local path only.",
    )
    create_index_doc: bool = Field(
        default=True,
        description="Create a Google Doc index alongside the ZIP.",
    )
    dry_run: bool = Field(
        default=False,
        description="Search + filter only — don't download or package anything.",
    )


# --------------------------------------------------------------------------- #
# Tool registration
# --------------------------------------------------------------------------- #


def register(mcp) -> None:
    @mcp.tool(
        name="workflow_contract_bundle",
        annotations={
            "title": "Generate a contract bundle (ZIP + index Doc)",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def workflow_contract_bundle(params: ContractBundleInput) -> str:
        """Find contracts (NDA/MSA/SOW/etc) in your Drive matching a year +
        type filter, package them into a ZIP, and create an index Doc.

        Filename heuristics: anything with NDA/MSA/SOW/DPA/agreement/contract/
        signed/executed in the name + a contracty mime type (PDF, Word, GDoc).
        Counterparty is parsed from the filename.

        Returns JSON with:
          - search_count: how many candidate files were found
          - bundled_count: how many made it into the ZIP
          - zip_drive: { id, name, link } if upload_zip=True
          - index_doc: { id, name, link } if create_index_doc=True
          - dry_run: true + filtered file list if dry_run=True

        Use `dry_run=True` first to preview which files would be included.
        """
        try:
            year = params.year
            ctype = params.contract_type
            title = params.bundle_title or _default_title(year, ctype)

            log.info("contract_bundle: year=%s type=%s dry_run=%s",
                     year, ctype, params.dry_run)

            raw = _search_drive_for_contracts(year)
            filtered = core.filter_contracts(raw, year=year, contract_type=ctype)

            bundle = core.ContractBundle(
                title=title, year=year, contract_type=ctype, files=filtered,
            )

            result: dict = {
                "search_count": len(raw),
                "bundled_count": len(filtered),
                "title": title,
            }

            if params.dry_run:
                result["dry_run"] = True
                result["files"] = [f.to_dict() for f in filtered]
                return json.dumps(result, indent=2, default=str)

            if not filtered:
                result["note"] = "No matching contracts found."
                return json.dumps(result, indent=2, default=str)

            # Pack ZIP
            zip_path = _pack_zip(filtered)
            result["local_zip_path"] = zip_path

            if params.upload_zip:
                uploaded = _upload_to_drive(zip_path, f"{title}.zip")
                result["zip_drive"] = {
                    "id": uploaded["id"],
                    "name": uploaded["name"],
                    "link": uploaded.get("webViewLink"),
                }

            if params.create_index_doc:
                md = core.build_index_markdown(bundle)
                doc = _create_index_doc(f"{title} — index", md)
                result["index_doc"] = {
                    "id": doc["id"],
                    "name": doc["name"],
                    "link": doc.get("webViewLink"),
                }

            return json.dumps(result, indent=2, default=str)
        except Exception as e:
            return format_error("workflow_contract_bundle", e)


def _default_title(year: int | None, ctype: str | None) -> str:
    bits = ["Contracts"]
    if ctype and ctype.lower() != "all":
        bits.append(ctype)
    if year:
        bits.append(str(year))
    return " ".join(bits)
