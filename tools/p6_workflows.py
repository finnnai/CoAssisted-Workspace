# © 2026 CoAssisted Workspace. Licensed under MIT.
"""MCP wrappers for P6 — Travel suite (3 workflows)."""

from __future__ import annotations

import datetime as _dt
import json
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

import p6_workflows as p6
from errors import format_error
from logging_util import log


# --------------------------------------------------------------------------- #
# Inputs
# --------------------------------------------------------------------------- #


class TravelClassifyInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    subject: str = Field(default="")
    body: str = Field(default="")


class TravelPackageInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    flight: Optional[dict] = Field(
        default=None,
        description=("Outbound flight: {origin_iata, origin_city, origin_state, "
                     "dest_iata, dest_city, dest_state, depart_iso, arrive_iso, "
                     "flight_number, confirmation_code}."),
    )
    return_flight: Optional[dict] = Field(default=None,
                                          description="Same shape as flight.")
    hotel: Optional[dict] = Field(
        default=None,
        description=("Hotel: {name, address, check_in, check_out, "
                     "confirmation_code}."),
    )
    drive_time_to_airport_min: int = Field(default=90, ge=15, le=240)
    drive_time_from_airport_min: int = Field(default=60, ge=15, le=240)


class TripExpensePackagerInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    trip_start: str = Field(..., description="YYYY-MM-DD")
    trip_end: str = Field(..., description="YYYY-MM-DD")
    destination: str = Field(...)
    all_receipts: list[dict] = Field(
        ...,
        description=("All receipts (caller fetches). Each: "
                     "{date, merchant, total, category, currency, note}."),
    )
    submitter_name: str = Field(default="")
    employee_id: Optional[str] = Field(default=None)
    project_code: Optional[str] = Field(default=None)


class ReceiptPromptCheckInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    trips: list[dict] = Field(
        ...,
        description="Active+upcoming trips. Each: {start, end, destination}.",
    )
    target_hour: int = Field(default=p6.DEFAULT_PROMPT_HOUR, ge=0, le=23)
    target_minute: int = Field(default=p6.DEFAULT_PROMPT_MINUTE, ge=0, le=59)
    last_prompt_iso: Optional[str] = Field(default=None)
    window_minutes: int = Field(default=90, ge=15, le=240)


# --------------------------------------------------------------------------- #
# Tool registration
# --------------------------------------------------------------------------- #


def register(mcp) -> None:
    @mcp.tool(
        name="workflow_travel_classify",
        annotations={"title": "Classify an email as flight/hotel confirmation",
                     "readOnlyHint": True, "destructiveHint": False,
                     "idempotentHint": True, "openWorldHint": False},
    )
    async def workflow_travel_classify(params: TravelClassifyInput) -> str:
        """Quick regex classifier for whether an email is a travel confirmation."""
        try:
            return json.dumps(
                p6.is_travel_confirmation(params.subject, params.body),
                indent=2, default=str,
            )
        except Exception as e:
            return format_error("workflow_travel_classify", e)

    @mcp.tool(
        name="workflow_travel_auto_package",
        annotations={"title": "Build calendar + drive-time blocks + per-diem from a trip",
                     "readOnlyHint": True, "destructiveHint": False,
                     "idempotentHint": True, "openWorldHint": True},
    )
    async def workflow_travel_auto_package(params: TravelPackageInput) -> str:
        """Compose a trip plan: calendar blocks (origin→dest), drive-time blocks
        to/from airport, and a per-diem estimate based on destination + length.

        Caller is responsible for parsing the flight/hotel info — pass already-parsed
        dicts. Returns the structured TravelPackage. Use Calendar tools to actually
        create the events from the calendar_blocks + drive_time_blocks lists.
        """
        try:
            pkg = p6.build_travel_package(
                flight=params.flight,
                return_flight=params.return_flight,
                hotel=params.hotel,
                drive_time_to_airport_min=params.drive_time_to_airport_min,
                drive_time_from_airport_min=params.drive_time_from_airport_min,
            )
            log.info("travel_auto_package: %d cal blocks, %d drive blocks",
                     len(pkg.calendar_blocks), len(pkg.drive_time_blocks))
            return json.dumps(pkg.to_dict(), indent=2, default=str)
        except Exception as e:
            return format_error("workflow_travel_auto_package", e)

    @mcp.tool(
        name="workflow_trip_expense_packager",
        annotations={"title": "Bundle trip-window receipts + draft submission email",
                     "readOnlyHint": True, "destructiveHint": False,
                     "idempotentHint": True, "openWorldHint": True},
    )
    async def workflow_trip_expense_packager(params: TripExpensePackagerInput) -> str:
        """Filter receipts to the trip window, summarize by category, draft a
        ready-to-send AP submission email. Currency-converts to USD via FX cache."""
        try:
            bundle = p6.package_trip_expenses(
                params.trip_start, params.trip_end, params.destination,
                params.all_receipts,
                submitter_name=params.submitter_name,
                employee_id=params.employee_id,
                project_code=params.project_code,
            )
            return json.dumps(bundle.to_dict(), indent=2, default=str)
        except Exception as e:
            return format_error("workflow_trip_expense_packager", e)

    @mcp.tool(
        name="workflow_receipt_photo_prompt",
        annotations={"title": "Decide if right now is a good time to nudge for receipts",
                     "readOnlyHint": True, "destructiveHint": False,
                     "idempotentHint": True, "openWorldHint": False},
    )
    async def workflow_receipt_photo_prompt(params: ReceiptPromptCheckInput) -> str:
        """Returns whether to send a receipt-photo prompt right now + the message
        text. Designed to be called by the scanner during trip windows."""
        try:
            decision = p6.should_prompt_receipts(
                trips=params.trips,
                target_hour=params.target_hour,
                target_minute=params.target_minute,
                last_prompt_iso=params.last_prompt_iso,
                window_minutes=params.window_minutes,
            )
            return json.dumps(decision.to_dict(), indent=2, default=str)
        except Exception as e:
            return format_error("workflow_receipt_photo_prompt", e)
