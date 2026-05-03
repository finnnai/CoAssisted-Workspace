# © 2026 CoAssisted Workspace. Licensed under MIT.
"""StaffWizard pipeline MCP tools — 6 wrappers around staffwizard_pipeline.

  workflow_staffwizard_refresh_all          — orchestrator (the one you usually call)
  workflow_staffwizard_ingest_latest_report — Gmail → .xls
  workflow_staffwizard_build_master_xlsx    — .xls → master xlsx
  workflow_staffwizard_push_master_sheet    — .xls → live Google Sheets
  workflow_staffwizard_build_dashboards     — master xlsx → HTML/JSON + Drive
  workflow_staffwizard_send_dashboards_email — zip + email recipients

Each tool calls into staffwizard_pipeline.* and returns the JSON-shaped
dict the pipeline produces, with errors translated into clean MCP-friendly
{status: error, error: <message>} responses so a failure in one step
doesn't crash the chat thread.
"""

from __future__ import annotations

import datetime as _dt
import logging
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

import staffwizard_pipeline as _pipe

_log = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Pydantic input models — module scope so FastMCP's get_type_hints resolves.
# (Same lesson as the PandaDoc workflows: don't nest Pydantic classes inside
# register() or func_metadata raises InvalidSignature on startup.)
# -----------------------------------------------------------------------------

class _IngestLatestReportInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    work_date: Optional[str] = Field(
        None,
        description=(
            "Optional ISO date (YYYY-MM-DD) to fetch a specific day's "
            "Overall Report. Default: most recent message."
        ),
    )


class _BuildMasterXlsxInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    output_path: Optional[str] = Field(
        None,
        description=(
            "Optional override for the master xlsx output path. "
            "Defaults to config.staffwizard.master_xlsx_path."
        ),
    )


class _PushMasterSheetInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    window_days: Optional[int] = Field(
        None,
        description=(
            "Override the rolling window. Default: "
            "config.staffwizard.window_days (90)."
        ),
    )


class _BuildDashboardsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    window_days: Optional[int] = Field(
        None,
        description="Override the rolling window in days.",
    )
    upload: bool = Field(
        True,
        description=(
            "If True, upload the generated dashboards to the configured "
            "Drive folder. If False, leave them only on the local "
            "filesystem."
        ),
    )


class _SendDashboardsEmailInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recipients: list[str] = Field(
        ...,
        description="Email addresses to send the dashboard zip to.",
    )
    subject: Optional[str] = Field(
        None,
        description="Override subject. Default: a sensible boilerplate.",
    )
    body: Optional[str] = Field(
        None,
        description="Override body. Default: a sensible how-to-open boilerplate.",
    )


class _RefreshAllInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fetch_latest: bool = Field(
        True,
        description=(
            "Run the Gmail-ingest step first. Set False to skip if the "
            ".xls is already on disk."
        ),
    )
    work_date: Optional[str] = Field(
        None,
        description=(
            "Optional ISO date (YYYY-MM-DD) to ingest a specific day. "
            "Only relevant when fetch_latest=True."
        ),
    )
    skip_dashboards: bool = Field(
        False,
        description="Skip the HTML/JSON dashboard rebuild step.",
    )
    upload_dashboards: bool = Field(
        True,
        description=(
            "If False, generate dashboards locally but skip the Drive "
            "upload."
        ),
    )


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def _coerce_date(s: Optional[str]) -> Optional[_dt.date]:
    if not s:
        return None
    try:
        return _dt.date.fromisoformat(s)
    except ValueError as e:
        raise ValueError(f"Bad work_date {s!r}: {e}") from e


def _err(e: Exception) -> dict[str, Any]:
    """Translate exceptions into the MCP error envelope."""
    return {"status": "error", "error": f"{type(e).__name__}: {e}"}


# -----------------------------------------------------------------------------
# MCP registration
# -----------------------------------------------------------------------------

def register(mcp) -> None:  # noqa: ANN001
    """Register the 6 StaffWizard pipeline tools with FastMCP."""

    # --------------------------------------------------------------
    # ingest_latest_report
    # --------------------------------------------------------------
    @mcp.tool()
    def workflow_staffwizard_ingest_latest_report(
        params: _IngestLatestReportInput,
    ) -> dict[str, Any]:
        """Search Gmail for the latest StaffWizard Overall Report and
        download its .xls attachment.

        Returns the file path on disk + the work_date parsed from the
        subject line.
        """
        try:
            return _pipe.ingest_latest_report(
                work_date=_coerce_date(params.work_date),
            )
        except Exception as e:
            return _err(e)

    # --------------------------------------------------------------
    # build_master_xlsx
    # --------------------------------------------------------------
    @mcp.tool()
    def workflow_staffwizard_build_master_xlsx(
        params: _BuildMasterXlsxInput,
    ) -> dict[str, Any]:
        """Combine every Overall Report .xls in the reports dir into a
        single master xlsx with Daily Detail / Project Rollup / Daily
        Totals tabs.

        Idempotent — safe to re-run.
        """
        try:
            from pathlib import Path
            out_path = (
                Path(params.output_path).expanduser()
                if params.output_path else None
            )
            return _pipe.build_master_xlsx(out_path=out_path)
        except Exception as e:
            return _err(e)

    # --------------------------------------------------------------
    # push_master_sheet
    # --------------------------------------------------------------
    @mcp.tool()
    def workflow_staffwizard_push_master_sheet(
        params: _PushMasterSheetInput,
    ) -> dict[str, Any]:
        """Refresh the live Master Sheet + Historic Archive Sheet.

        Splits parsed rows into rolling-window-current and older. Recent
        rows go to Master, older ones to Archive. Tabs: Daily Detail,
        Project Rollup, Daily Totals, Daily Totals by Project, plus one
        tab per Job Description.
        """
        try:
            return _pipe.push_master_to_sheets(win_days=params.window_days)
        except Exception as e:
            return _err(e)

    # --------------------------------------------------------------
    # build_dashboards
    # --------------------------------------------------------------
    @mcp.tool()
    def workflow_staffwizard_build_dashboards(
        params: _BuildDashboardsInput,
    ) -> dict[str, Any]:
        """Rebuild the visual HTML + JSON dashboards from the master
        xlsx and (optionally) upload to the configured Drive folder.

        Three-tab overview + 30 per-project pages with Chart.js charts,
        action items (low margin / high OT / stalled), and a recent
        shifts table.
        """
        try:
            return _pipe.build_dashboards(
                win_days=params.window_days, upload=params.upload,
            )
        except Exception as e:
            return _err(e)

    # --------------------------------------------------------------
    # send_dashboards_email
    # --------------------------------------------------------------
    @mcp.tool()
    def workflow_staffwizard_send_dashboards_email(
        params: _SendDashboardsEmailInput,
    ) -> dict[str, Any]:
        """Bundle the dashboards into a self-contained zip (Chart.js
        bundled, no CDN required) and email it to one or more
        recipients via the operator's Gmail.

        Returns the local zip path + Gmail message IDs.
        """
        try:
            return _pipe.send_dashboards_email(
                recipients=params.recipients,
                subject=params.subject,
                body=params.body,
            )
        except Exception as e:
            return _err(e)

    # --------------------------------------------------------------
    # refresh_all (orchestrator)
    # --------------------------------------------------------------
    @mcp.tool()
    def workflow_staffwizard_refresh_all(
        params: _RefreshAllInput,
    ) -> dict[str, Any]:
        """Run the full StaffWizard pipeline:

            1. Ingest latest Overall Report from Gmail (skippable)
            2. Build master xlsx from all reports on disk
            3. Push to live Master + Archive Google Sheets
            4. Rebuild HTML/JSON dashboards + upload to Drive (skippable)

        Returns a dict with per-step results so any failure is visible
        in context.
        """
        try:
            return _pipe.refresh_all(
                fetch_latest=params.fetch_latest,
                work_date=_coerce_date(params.work_date),
                skip_dashboards=params.skip_dashboards,
                upload_dashboards=params.upload_dashboards,
            )
        except Exception as e:
            return _err(e)
