# © 2026 CoAssisted Workspace. Licensed under MIT.
"""MCP wrapper for the round-trip handoff workflow.

Exposes `workflow_receive_handoff` — point it at a returned tarball and
get back a structured report:

  - Sender's most recent HANDOFF_LOG.md entry (what they touched)
  - HANDOFF_STATE.json contents (version, open tasks, pick-up-here pointer)
  - File-level diff vs the local repo (added / modified / deleted)
  - Notes / warnings (missing manifest, no changes, etc.)

The companion sender flow (`workflow_send_handoff_archive`) already exists
and lives in `tools/workflows.py`.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

import handoff_receive
from errors import format_error
from logging_util import log


class ReceiveHandoffInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    archive_path: str = Field(
        ...,
        description=(
            "Path to the .tar.gz a collaborator sent back. May be absolute "
            "or relative to the project root. Tilde (~) and shell expansion "
            "are honored."
        ),
    )
    incoming_dir: str = Field(
        default="incoming",
        description=(
            "Where to extract the archive. Default 'incoming' (relative to "
            "the project root). Wiped + recreated on each call."
        ),
    )


def register(mcp) -> None:

    @mcp.tool(
        name="workflow_receive_handoff",
        annotations={
            "title": "Receive a returned handoff archive and diff vs local",
            "readOnlyHint": False, "destructiveHint": False,
            "idempotentHint": True, "openWorldHint": False,
        },
    )
    async def workflow_receive_handoff(params: ReceiveHandoffInput) -> str:
        """Untar a returned handoff archive and surface what changed.

        Inspects the incoming HANDOFF_LOG.md + HANDOFF_STATE.json,
        diffs files vs the current local repo, and returns a compact
        JSON report so you can decide what to merge.

        Per-machine state files (token.json, *.cache, logs/, etc.) are
        intentionally excluded from the diff.
        """
        try:
            report = handoff_receive.receive_handoff(
                archive_path=params.archive_path,
                incoming_dir=params.incoming_dir,
            )
            log.info(
                "received handoff: %s — %d added, %d modified, %d deleted",
                params.archive_path,
                len(report.diff.added),
                len(report.diff.modified),
                len(report.diff.deleted),
            )
            return json.dumps(report.to_dict(), indent=2, default=str)
        except FileNotFoundError as e:
            return format_error("workflow_receive_handoff", e)
        except Exception as e:
            return format_error("workflow_receive_handoff", e)
