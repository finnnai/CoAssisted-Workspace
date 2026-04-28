"""Google Tasks tools: list/create/update/complete tasks and task lists."""

from __future__ import annotations

import gservices

import json
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from dryrun import dry_run_preview, is_dry_run
from errors import format_error


def _service():
    return gservices.tasks()


# --------------------------------------------------------------------------- #
# Input models
# --------------------------------------------------------------------------- #


class ListTaskListsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ListTasksInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    task_list_id: str = Field(
        default="@default",
        description="Task list ID. '@default' is your primary list.",
    )
    show_completed: bool = Field(default=False)
    limit: int = Field(default=50, ge=1, le=100)


class CreateTaskInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    task_list_id: str = Field(default="@default")
    title: str = Field(..., description="Task title.")
    notes: Optional[str] = Field(default=None, description="Task description/notes.")
    due: Optional[str] = Field(
        default=None,
        description=(
            "RFC3339 date or timestamp (e.g. '2026-04-30' or '2026-04-30T00:00:00Z'). "
            "IMPORTANT: Google Tasks API only stores the DATE portion — any time you "
            "pass is silently truncated to 00:00:00 UTC by Google's server. If you "
            "need precise time-of-day, use Calendar instead."
        ),
    )


class UpdateTaskInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    task_list_id: str = Field(default="@default")
    task_id: str = Field(...)
    title: Optional[str] = Field(default=None)
    notes: Optional[str] = Field(default=None)
    due: Optional[str] = Field(
        default=None,
        description=(
            "RFC3339 date or timestamp. Google ignores the time portion — date only."
        ),
    )


class CompleteTaskInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    task_list_id: str = Field(default="@default")
    task_id: str = Field(...)


class DeleteTaskInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    task_list_id: str = Field(default="@default")
    task_id: str = Field(...)
    dry_run: Optional[bool] = Field(default=None)


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #


def register(mcp) -> None:
    @mcp.tool(
        name="tasks_list_task_lists",
        annotations={
            "title": "List Google Tasks lists",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def tasks_list_task_lists(params: ListTaskListsInput) -> str:
        """List all task lists on this account."""
        try:
            resp = _service().tasklists().list().execute()
            out = [
                {"id": tl["id"], "title": tl["title"], "updated": tl.get("updated")}
                for tl in resp.get("items", [])
            ]
            return json.dumps(out, indent=2)
        except Exception as e:
            return format_error(e)

    @mcp.tool(
        name="tasks_list_tasks",
        annotations={
            "title": "List tasks in a list",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def tasks_list_tasks(params: ListTasksInput) -> str:
        """List tasks in a given task list, optionally including completed ones."""
        try:
            resp = (
                _service()
                .tasks()
                .list(
                    tasklist=params.task_list_id,
                    showCompleted=params.show_completed,
                    maxResults=params.limit,
                )
                .execute()
            )
            out = [
                {
                    "id": t["id"],
                    "title": t.get("title"),
                    "status": t.get("status"),
                    "due": t.get("due"),
                    "notes": t.get("notes"),
                    "completed": t.get("completed"),
                }
                for t in resp.get("items", [])
            ]
            return json.dumps({"count": len(out), "tasks": out}, indent=2)
        except Exception as e:
            return format_error(e)

    @mcp.tool(
        name="tasks_create_task",
        annotations={
            "title": "Create a Google Task",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def tasks_create_task(params: CreateTaskInput) -> str:
        """Create a new task in the specified task list."""
        try:
            body: dict = {"title": params.title}
            if params.notes:
                body["notes"] = params.notes
            if params.due:
                body["due"] = params.due
            created = (
                _service()
                .tasks()
                .insert(tasklist=params.task_list_id, body=body)
                .execute()
            )
            return json.dumps(created, indent=2)
        except Exception as e:
            return format_error(e)

    @mcp.tool(
        name="tasks_update_task",
        annotations={
            "title": "Update a Google Task",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def tasks_update_task(params: UpdateTaskInput) -> str:
        """Patch an existing task's title, notes, or due date."""
        try:
            patch: dict = {}
            if params.title is not None:
                patch["title"] = params.title
            if params.notes is not None:
                patch["notes"] = params.notes
            if params.due is not None:
                patch["due"] = params.due
            updated = (
                _service()
                .tasks()
                .patch(tasklist=params.task_list_id, task=params.task_id, body=patch)
                .execute()
            )
            return json.dumps(updated, indent=2)
        except Exception as e:
            return format_error(e)

    @mcp.tool(
        name="tasks_complete_task",
        annotations={
            "title": "Mark a Google Task complete",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def tasks_complete_task(params: CompleteTaskInput) -> str:
        """Mark a task as completed."""
        try:
            updated = (
                _service()
                .tasks()
                .patch(
                    tasklist=params.task_list_id,
                    task=params.task_id,
                    body={"status": "completed"},
                )
                .execute()
            )
            return json.dumps(
                {"status": "completed", "id": updated["id"], "title": updated.get("title")},
                indent=2,
            )
        except Exception as e:
            return format_error(e)

    @mcp.tool(
        name="tasks_delete_task",
        annotations={
            "title": "Delete a Google Task",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def tasks_delete_task(params: DeleteTaskInput) -> str:
        """Delete a task from a task list. Cannot be undone."""
        try:
            if is_dry_run(params.dry_run):
                return dry_run_preview(
                    "tasks_delete_task",
                    {"task_list_id": params.task_list_id, "task_id": params.task_id},
                )
            _service().tasks().delete(
                tasklist=params.task_list_id, task=params.task_id
            ).execute()
            return json.dumps({"status": "deleted", "task_id": params.task_id})
        except Exception as e:
            return format_error(e)
