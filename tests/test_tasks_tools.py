"""Baseline unit tests for tools/tasks.py — P0-3 spec."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock

import pytest
from googleapiclient.errors import HttpError
from pydantic import ValidationError

from tools import tasks as t_tasks
from tools.tasks import (
    ListTaskListsInput, ListTasksInput, CreateTaskInput,
    UpdateTaskInput, CompleteTaskInput, DeleteTaskInput,
)


def _resolve(name):
    from server import mcp
    return mcp._tool_manager._tools[name].fn


def _run(name, params):
    return asyncio.run(_resolve(name)(params))


def _http_error():
    return HttpError(MagicMock(status=500, reason="boom"),
                     b'{"error": {"message": "boom"}}')


def _err_assert(out):
    assert isinstance(out, str)
    assert ("error" in out.lower() or "failed" in out.lower()
            or "boom" in out.lower() or "http" in out.lower())


# Input validation
def test_list_task_lists_no_args():
    ListTaskListsInput()


def test_list_tasks_defaults():
    m = ListTasksInput()
    assert m.task_list_id == "@default"
    assert m.show_completed is False
    assert m.limit == 50


def test_list_tasks_limit_bounds():
    ListTasksInput(limit=1)
    ListTasksInput(limit=100)
    with pytest.raises(ValidationError):
        ListTasksInput(limit=101)


def test_create_task_requires_title():
    with pytest.raises(ValidationError):
        CreateTaskInput()
    CreateTaskInput(title="Buy milk")


def test_update_task_requires_task_id():
    with pytest.raises(ValidationError):
        UpdateTaskInput()
    UpdateTaskInput(task_id="t1")


def test_complete_task_requires_task_id():
    with pytest.raises(ValidationError):
        CompleteTaskInput()
    CompleteTaskInput(task_id="t1")


def test_delete_task_requires_task_id():
    with pytest.raises(ValidationError):
        DeleteTaskInput()
    DeleteTaskInput(task_id="t1")


# Error paths
def test_list_task_lists_error(monkeypatch):
    fake = MagicMock()
    fake.tasklists.return_value.list.return_value.execute.side_effect = _http_error()
    monkeypatch.setattr(t_tasks, "_service", lambda: fake)
    _err_assert(_run("tasks_list_task_lists", ListTaskListsInput()))


def test_list_tasks_error(monkeypatch):
    fake = MagicMock()
    fake.tasks.return_value.list.return_value.execute.side_effect = _http_error()
    monkeypatch.setattr(t_tasks, "_service", lambda: fake)
    _err_assert(_run("tasks_list_tasks", ListTasksInput()))


def test_create_task_error(monkeypatch):
    fake = MagicMock()
    fake.tasks.return_value.insert.return_value.execute.side_effect = _http_error()
    monkeypatch.setattr(t_tasks, "_service", lambda: fake)
    _err_assert(_run("tasks_create_task", CreateTaskInput(title="x")))


def test_delete_task_error(monkeypatch):
    fake = MagicMock()
    fake.tasks.return_value.delete.return_value.execute.side_effect = _http_error()
    monkeypatch.setattr(t_tasks, "_service", lambda: fake)
    _err_assert(_run("tasks_delete_task", DeleteTaskInput(task_id="t1")))


def test_all_tasks_tools_registered():
    from server import mcp
    expected = {"tasks_list_task_lists", "tasks_list_tasks",
                "tasks_create_task", "tasks_update_task",
                "tasks_complete_task", "tasks_delete_task"}
    assert expected.issubset(set(mcp._tool_manager._tools))
