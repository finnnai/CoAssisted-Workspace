# © 2026 CoAssisted Workspace. Licensed under MIT.
"""MCP tool wrapper for the schedule defrag.

Exposes one tool: workflow_schedule_defrag.
"""

from __future__ import annotations

import datetime as _dt
import json
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

import gservices
import schedule_defrag as core
from errors import format_error
from logging_util import log


def _calendar():
    return gservices.calendar()


def _fetch_events(time_min_iso: str, time_max_iso: str,
                  calendar_id: str = "primary") -> list[dict]:
    resp = (
        _calendar()
        .events()
        .list(
            calendarId=calendar_id,
            timeMin=time_min_iso,
            timeMax=time_max_iso,
            singleEvents=True,
            orderBy="startTime",
            maxResults=250,
        )
        .execute()
    )
    return resp.get("items", [])


class DefragInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    days_ahead: int = Field(
        default=7, ge=1, le=30,
        description="How many days from now to analyze (default 7).",
    )
    working_start_hour: int = Field(
        default=core.WORKING_HOURS_START, ge=0, le=23,
        description="Working day start hour, 0-23 (default 8 = 8am).",
    )
    working_end_hour: int = Field(
        default=core.WORKING_HOURS_END, ge=1, le=24,
        description="Working day end hour, 1-24 (default 18 = 6pm).",
    )
    min_useful_block_min: int = Field(
        default=core.MIN_USEFUL_BLOCK_MIN, ge=15, le=240,
        description="Below this, a gap counts as a fragment (default 45 min).",
    )
    calendar_id: str = Field(
        default="primary",
        description="Calendar ID to analyze (default 'primary').",
    )


def register(mcp) -> None:
    @mcp.tool(
        name="workflow_schedule_defrag",
        annotations={
            "title": "Find fragmented calendar gaps + defrag suggestions",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def workflow_schedule_defrag(params: DefragInput) -> str:
        """Identify small calendar fragments that, if reorganized, would
        create useful focus blocks.

        Returns a list of fragments (gaps below the useful-block threshold)
        and 'defrag suggestions' — pairs of fragments separated by a single
        meeting that, if moved, would unlock a contiguous focus block.

        Use this weekly to spot calendar Tetris opportunities.
        """
        try:
            now = _dt.datetime.now().astimezone()
            time_min = now.isoformat()
            time_max = (now + _dt.timedelta(days=params.days_ahead)).isoformat()

            events = _fetch_events(time_min, time_max, params.calendar_id)
            report = core.find_fragments(
                events,
                working_start_h=params.working_start_hour,
                working_end_h=params.working_end_hour,
                min_useful_block_min=params.min_useful_block_min,
            )
            log.info(
                "schedule_defrag: %d days analyzed, %d fragments, %d suggestions",
                len(report.days_analyzed),
                len(report.fragments),
                len(report.suggestions),
            )
            return json.dumps(report.to_dict(), indent=2, default=str)
        except Exception as e:
            return format_error("workflow_schedule_defrag", e)
