# © 2026 CoAssisted Workspace. Licensed under MIT.
"""MCP wrappers for P3 workflows + watched-sheet management.

Tools:
  workflow_per_diem               — #62 GSA per-diem calc
  workflow_mileage_log            — #61 build/aggregate mileage entries
  workflow_license_reminders      — #36 expiring licenses needing reminder
  workflow_dsr_collate            — #47 GDPR/CCPA data subject report
  workflow_watched_sheet_register — register a watched-sheet rule
  workflow_watched_sheet_list     — list rules
  workflow_watched_sheet_remove   — remove a rule
"""

from __future__ import annotations

import datetime as _dt
import json
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

import external_feeds
import p3_workflows as p3
import watched_sheets
from errors import format_error
from logging_util import log


# --------------------------------------------------------------------------- #
# #62 Per-diem
# --------------------------------------------------------------------------- #


class PerDiemInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    city: str = Field(..., description="Destination city.")
    state: str = Field(..., description="Two-letter state code.")
    start_date: str = Field(..., description="Trip start (YYYY-MM-DD).")
    end_date: str = Field(..., description="Trip end (YYYY-MM-DD).")
    year: Optional[int] = Field(default=None, description="GSA fiscal year (default: start year).")


# --------------------------------------------------------------------------- #
# #61 Mileage
# --------------------------------------------------------------------------- #


class MileageLogInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    drive_blocks: list[dict] = Field(
        ...,
        description=("Drive-time blocks. Each: {date: 'YYYY-MM-DD', "
                     "distance_miles: float, note: str (optional)}."),
    )
    purpose: str = Field(default="business", description="'business' | 'medical' | 'charitable'.")
    year: Optional[int] = Field(default=None)


# --------------------------------------------------------------------------- #
# #36 License reminders
# --------------------------------------------------------------------------- #


class LicenseRemindersInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    today_override: Optional[str] = Field(
        default=None,
        description="Override 'today' for testing (YYYY-MM-DD). Default: real today.",
    )


# --------------------------------------------------------------------------- #
# #47 DSR
# --------------------------------------------------------------------------- #


class DsrCollateInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    target_email: str = Field(..., description="The data subject's email.")
    gmail_threads: list[dict] = Field(default_factory=list)
    calendar_events: list[dict] = Field(default_factory=list)
    drive_files: list[dict] = Field(default_factory=list)
    contacts: list[dict] = Field(default_factory=list)
    output_format: str = Field(default="both", description="'json' | 'markdown' | 'both'.")


# --------------------------------------------------------------------------- #
# Watched-sheet management
# --------------------------------------------------------------------------- #


class WatchedRegisterInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    family: str = Field(..., description="Rule family — 'license', 'retention', 'recurring', 'focus', 'deadline'.")
    slug: str = Field(..., description="Unique identifier within family.")
    fields: dict = Field(default_factory=dict, description="Domain-specific fields.")
    active: bool = Field(default=True)


class WatchedListInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    family: Optional[str] = Field(default=None)
    active_only: bool = Field(default=False)


class WatchedRemoveInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    family: str = Field(...)
    slug: str = Field(...)


# --------------------------------------------------------------------------- #
# Tool registration
# --------------------------------------------------------------------------- #


def register(mcp) -> None:
    @mcp.tool(
        name="workflow_per_diem",
        annotations={"title": "Calculate GSA per-diem for a trip",
                     "readOnlyHint": True, "destructiveHint": False,
                     "idempotentHint": True, "openWorldHint": True},
    )
    async def workflow_per_diem(params: PerDiemInput) -> str:
        """Compute per-diem totals (lodging + meals) for a trip using GSA rates.

        Falls back to standard CONUS rate for cities not in the lookup table.
        First + last day of multi-day trips are billed at 75% M&IE per GSA convention.
        """
        try:
            breakdown = p3.calculate_per_diem(
                params.city, params.state,
                params.start_date, params.end_date, year=params.year,
            )
            log.info("per_diem: %s, %s — $%.2f", params.city, params.state,
                     breakdown.grand_total)
            return json.dumps(breakdown.to_dict(), indent=2, default=str)
        except Exception as e:
            return format_error("workflow_per_diem", e)

    @mcp.tool(
        name="workflow_mileage_log",
        annotations={"title": "Build IRS-deductible mileage log from drive blocks",
                     "readOnlyHint": True, "destructiveHint": False,
                     "idempotentHint": True, "openWorldHint": True},
    )
    async def workflow_mileage_log(params: MileageLogInput) -> str:
        """Convert drive-time blocks into IRS-deductible mileage entries.

        Returns per-entry detail + quarterly + yearly aggregates.
        """
        try:
            entries = p3.compute_mileage(
                params.drive_blocks, purpose=params.purpose, year=params.year,
            )
            agg = p3.aggregate_mileage(entries)
            return json.dumps({
                "entry_count": len(entries),
                "entries": [e.to_dict() for e in entries],
                "aggregate": agg,
            }, indent=2, default=str)
        except Exception as e:
            return format_error("workflow_mileage_log", e)

    @mcp.tool(
        name="workflow_license_reminders",
        annotations={"title": "Find licenses needing renewal reminders",
                     "readOnlyHint": True, "destructiveHint": False,
                     "idempotentHint": True, "openWorldHint": False},
    )
    async def workflow_license_reminders(params: LicenseRemindersInput) -> str:
        """List licenses approaching expiration, with the threshold each crossed.

        Reads from watched_sheets 'license' family. Each entry's fields must
        include 'expires_at' (YYYY-MM-DD); recommended: 'jurisdiction', 'name'.
        """
        try:
            today = _dt.date.fromisoformat(params.today_override) if params.today_override else None
            rows = p3.licenses_to_remind(today=today)
            return json.dumps({"count": len(rows), "licenses": rows},
                              indent=2, default=str)
        except Exception as e:
            return format_error("workflow_license_reminders", e)

    @mcp.tool(
        name="workflow_dsr_collate",
        annotations={"title": "Collate a Data Subject Request report",
                     "readOnlyHint": False, "destructiveHint": False,
                     "idempotentHint": True, "openWorldHint": False},
    )
    async def workflow_dsr_collate(params: DsrCollateInput) -> str:
        """GDPR/CCPA-style 'give me everything you have on Person X'.

        Caller is responsible for fetching the four source lists; this tool
        collates them into a unified report (with optional markdown rendering).
        """
        try:
            report = p3.collate_dsr_results(
                params.target_email,
                gmail_threads=params.gmail_threads,
                calendar_events=params.calendar_events,
                drive_files=params.drive_files,
                contacts=params.contacts,
            )
            if params.output_format == "json":
                return json.dumps(report, indent=2, default=str)
            md = p3.render_dsr_markdown(report)
            if params.output_format == "markdown":
                return md
            return json.dumps({"report": report, "markdown": md}, indent=2, default=str)
        except Exception as e:
            return format_error("workflow_dsr_collate", e)

    @mcp.tool(
        name="workflow_watched_sheet_register",
        annotations={"title": "Register a watched-sheet rule",
                     "readOnlyHint": False, "destructiveHint": False,
                     "idempotentHint": True, "openWorldHint": False},
    )
    async def workflow_watched_sheet_register(params: WatchedRegisterInput) -> str:
        """Register or update a watched-sheet rule (idempotent)."""
        try:
            rec = watched_sheets.register(
                params.family, params.slug,
                fields=params.fields, active=params.active,
            )
            return json.dumps(rec, indent=2, default=str)
        except Exception as e:
            return format_error("workflow_watched_sheet_register", e)

    @mcp.tool(
        name="workflow_watched_sheet_list",
        annotations={"title": "List watched-sheet rules",
                     "readOnlyHint": True, "destructiveHint": False,
                     "idempotentHint": True, "openWorldHint": False},
    )
    async def workflow_watched_sheet_list(params: WatchedListInput) -> str:
        try:
            if params.family:
                rows = watched_sheets.list_family(
                    params.family, active_only=params.active_only,
                )
            else:
                rows = watched_sheets.list_all(active_only=params.active_only)
            return json.dumps({"count": len(rows), "rules": rows},
                              indent=2, default=str)
        except Exception as e:
            return format_error("workflow_watched_sheet_list", e)

    @mcp.tool(
        name="workflow_watched_sheet_remove",
        annotations={"title": "Remove a watched-sheet rule",
                     "readOnlyHint": False, "destructiveHint": True,
                     "idempotentHint": True, "openWorldHint": False},
    )
    async def workflow_watched_sheet_remove(params: WatchedRemoveInput) -> str:
        try:
            removed = watched_sheets.remove(params.family, params.slug)
            return json.dumps({"removed": removed,
                               "family": params.family, "slug": params.slug})
        except Exception as e:
            return format_error("workflow_watched_sheet_remove", e)
