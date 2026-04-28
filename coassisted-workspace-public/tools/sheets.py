"""Sheets tools: create, read a range, write a range, append rows."""

from __future__ import annotations

import gservices

import json
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from dryrun import dry_run_preview, is_dry_run
from errors import format_error


def _service():
    return gservices.sheets()


# --------------------------------------------------------------------------- #
# Input models
# --------------------------------------------------------------------------- #


class CreateSheetInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    title: str = Field(..., description="Spreadsheet title.")


class ReadRangeInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    spreadsheet_id: str = Field(...)
    range: str = Field(
        ...,
        description="A1 range, e.g. 'Sheet1!A1:D10' or 'Sheet1' for the whole sheet.",
    )


class WriteRangeInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    spreadsheet_id: str = Field(...)
    range: str = Field(..., description="A1 range to overwrite.")
    values: list[list] = Field(..., description="2D array of cell values.")
    value_input_option: str = Field(
        default="USER_ENTERED",
        description="'USER_ENTERED' parses formulas/dates; 'RAW' writes literally.",
    )


class AppendRowsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    spreadsheet_id: str = Field(...)
    range: str = Field(
        ...,
        description="A1 range the API uses to find the table to append to (e.g. 'Sheet1!A1').",
    )
    values: list[list] = Field(..., description="2D array of rows to append.")
    value_input_option: str = Field(default="USER_ENTERED")


class AddSheetInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    spreadsheet_id: str = Field(...)
    title: str = Field(..., description="Name for the new sheet/tab.")


class DeleteSheetInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    spreadsheet_id: str = Field(...)
    sheet_id: Optional[int] = Field(
        default=None,
        description="Numeric sheet ID to delete. Provide either sheet_id OR title.",
    )
    title: Optional[str] = Field(
        default=None, description="Sheet/tab title (will be resolved to its ID)."
    )
    dry_run: Optional[bool] = Field(default=None)


class ListSheetsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    spreadsheet_id: str = Field(...)


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #


def register(mcp) -> None:
    @mcp.tool(
        name="sheets_create_spreadsheet",
        annotations={
            "title": "Create a Google Sheet",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def sheets_create_spreadsheet(params: CreateSheetInput) -> str:
        """Create a new empty Google Sheet. Returns spreadsheetId and URL."""
        try:
            created = (
                _service()
                .spreadsheets()
                .create(body={"properties": {"title": params.title}}, fields="spreadsheetId,spreadsheetUrl")
                .execute()
            )
            return json.dumps(created, indent=2)
        except Exception as e:
            return format_error(e)

    @mcp.tool(
        name="sheets_read_range",
        annotations={
            "title": "Read cells from a Google Sheet",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def sheets_read_range(params: ReadRangeInput) -> str:
        """Read the values of an A1 range. Returns a 2D array."""
        try:
            resp = (
                _service()
                .spreadsheets()
                .values()
                .get(spreadsheetId=params.spreadsheet_id, range=params.range)
                .execute()
            )
            return json.dumps(
                {"range": resp.get("range"), "values": resp.get("values", [])},
                indent=2,
            )
        except Exception as e:
            return format_error(e)

    @mcp.tool(
        name="sheets_write_range",
        annotations={
            "title": "Write cells to a Google Sheet",
            "readOnlyHint": False,
            "destructiveHint": True,  # overwrites existing cells
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def sheets_write_range(params: WriteRangeInput) -> str:
        """Overwrite the cells in an A1 range with the given 2D array of values."""
        try:
            resp = (
                _service()
                .spreadsheets()
                .values()
                .update(
                    spreadsheetId=params.spreadsheet_id,
                    range=params.range,
                    valueInputOption=params.value_input_option,
                    body={"values": params.values},
                )
                .execute()
            )
            return json.dumps(resp, indent=2)
        except Exception as e:
            return format_error(e)

    @mcp.tool(
        name="sheets_append_rows",
        annotations={
            "title": "Append rows to a Google Sheet",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def sheets_append_rows(params: AppendRowsInput) -> str:
        """Append rows to the end of a table. The API finds the table starting at `range`."""
        try:
            resp = (
                _service()
                .spreadsheets()
                .values()
                .append(
                    spreadsheetId=params.spreadsheet_id,
                    range=params.range,
                    valueInputOption=params.value_input_option,
                    insertDataOption="INSERT_ROWS",
                    body={"values": params.values},
                )
                .execute()
            )
            return json.dumps(resp.get("updates", {}), indent=2)
        except Exception as e:
            return format_error(e)

    @mcp.tool(
        name="sheets_list_sheets",
        annotations={
            "title": "List tabs in a Google Sheet",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def sheets_list_sheets(params: ListSheetsInput) -> str:
        """List all tabs/sheets within a spreadsheet, with their titles and sheet IDs."""
        try:
            meta = (
                _service()
                .spreadsheets()
                .get(spreadsheetId=params.spreadsheet_id, fields="sheets(properties)")
                .execute()
            )
            out = [
                {
                    "sheet_id": s["properties"]["sheetId"],
                    "title": s["properties"]["title"],
                    "index": s["properties"].get("index"),
                    "row_count": s["properties"].get("gridProperties", {}).get("rowCount"),
                    "column_count": s["properties"].get("gridProperties", {}).get("columnCount"),
                }
                for s in meta.get("sheets", [])
            ]
            return json.dumps({"count": len(out), "sheets": out}, indent=2)
        except Exception as e:
            return format_error(e)

    @mcp.tool(
        name="sheets_add_sheet",
        annotations={
            "title": "Add a new tab to a Google Sheet",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def sheets_add_sheet(params: AddSheetInput) -> str:
        """Create a new tab with the given title inside an existing spreadsheet."""
        try:
            resp = (
                _service()
                .spreadsheets()
                .batchUpdate(
                    spreadsheetId=params.spreadsheet_id,
                    body={
                        "requests": [
                            {"addSheet": {"properties": {"title": params.title}}}
                        ]
                    },
                )
                .execute()
            )
            new_props = resp["replies"][0]["addSheet"]["properties"]
            return json.dumps(
                {
                    "status": "added",
                    "sheet_id": new_props["sheetId"],
                    "title": new_props["title"],
                },
                indent=2,
            )
        except Exception as e:
            return format_error(e)

    @mcp.tool(
        name="sheets_delete_sheet",
        annotations={
            "title": "Delete a tab from a Google Sheet",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def sheets_delete_sheet(params: DeleteSheetInput) -> str:
        """Delete a tab by sheet_id or by title. Provide exactly one."""
        try:
            if params.sheet_id is None and not params.title:
                return "Error: provide either sheet_id or title."
            svc = _service()

            sheet_id = params.sheet_id
            if sheet_id is None:
                meta = (
                    svc.spreadsheets()
                    .get(spreadsheetId=params.spreadsheet_id, fields="sheets(properties)")
                    .execute()
                )
                hit = next(
                    (
                        s
                        for s in meta.get("sheets", [])
                        if s["properties"]["title"] == params.title
                    ),
                    None,
                )
                if not hit:
                    return f"No sheet titled {params.title!r}."
                sheet_id = hit["properties"]["sheetId"]

            if is_dry_run(params.dry_run):
                return dry_run_preview(
                    "sheets_delete_sheet",
                    {"spreadsheet_id": params.spreadsheet_id, "sheet_id": sheet_id},
                )

            svc.spreadsheets().batchUpdate(
                spreadsheetId=params.spreadsheet_id,
                body={"requests": [{"deleteSheet": {"sheetId": sheet_id}}]},
            ).execute()
            return json.dumps({"status": "deleted", "sheet_id": sheet_id})
        except Exception as e:
            return format_error(e)
