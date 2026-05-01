"""Baseline unit tests for tools/chat.py — P0-3 spec.

18 tools. Focus on input-model validation since chat tool implementations
have heavy dependencies (cross-domain DM resolution, sender classifier,
contact logging, etc.) that make happy-path mocking high-effort vs
the value of catching schema regressions.
"""
from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from tools.chat import (
    ListSpacesInput, FindOrCreateDmInput, GetSpaceInput, ListMessagesInput,
    GetMessageInput, SendMessageInput, UpdateMessageInput,
    DeleteMessageInput, ListMembersInput, DownloadChatAttachmentInput,
    SendDmInput, SendToSpaceByNameInput, SearchChatInput, WhoIsInDmInput,
    SendChatAttachmentInput, RecentChatActivityInput,
    ChatDigestInput, ChatToContactGroupInput,
    ReactToMessageInput, GetThreadInput,
)


# Input validation — each tool's required fields
def test_list_spaces_defaults():
    m = ListSpacesInput()
    assert m.limit == 100


def test_list_spaces_limit_bounds():
    ListSpacesInput(limit=1)
    ListSpacesInput(limit=1000)
    with pytest.raises(ValidationError):
        ListSpacesInput(limit=1001)


def test_find_or_create_dm_requires_email():
    with pytest.raises(ValidationError):
        FindOrCreateDmInput()
    FindOrCreateDmInput(email="a@b.com")


def test_get_space_requires_space_name():
    with pytest.raises(ValidationError):
        GetSpaceInput()
    GetSpaceInput(space_name="spaces/AAA")


def test_list_messages_requires_space_name():
    with pytest.raises(ValidationError):
        ListMessagesInput()
    ListMessagesInput(space_name="spaces/AAA")


def test_send_message_requires_space_and_text():
    with pytest.raises(ValidationError):
        SendMessageInput()
    with pytest.raises(ValidationError):
        SendMessageInput(space_name="spaces/AAA")
    SendMessageInput(space_name="spaces/AAA", text="hello")


def test_update_message_requires_message_name_and_text():
    with pytest.raises(ValidationError):
        UpdateMessageInput()
    with pytest.raises(ValidationError):
        UpdateMessageInput(message_name="spaces/A/messages/m1")
    UpdateMessageInput(message_name="spaces/A/messages/m1", text="new")


def test_delete_message_requires_message_name():
    with pytest.raises(ValidationError):
        DeleteMessageInput()
    DeleteMessageInput(message_name="spaces/A/messages/m1")


def test_list_members_requires_space_name():
    with pytest.raises(ValidationError):
        ListMembersInput()
    ListMembersInput(space_name="spaces/AAA")


def test_download_attachment_requires_message_name():
    with pytest.raises(ValidationError):
        DownloadChatAttachmentInput()
    DownloadChatAttachmentInput(message_name="spaces/A/messages/m1")


def test_send_dm_requires_email_and_text():
    with pytest.raises(ValidationError):
        SendDmInput()
    with pytest.raises(ValidationError):
        SendDmInput(email="a@b.com")
    SendDmInput(email="a@b.com", text="hi")


def test_send_to_space_by_name_requires_query_and_text():
    with pytest.raises(ValidationError):
        SendToSpaceByNameInput()
    SendToSpaceByNameInput(space_query="ISOC", text="hello")


def test_search_chat_days_bounds():
    SearchChatInput()  # defaults OK
    SearchChatInput(days=1)
    SearchChatInput(days=365)
    with pytest.raises(ValidationError):
        SearchChatInput(days=366)
    with pytest.raises(ValidationError):
        SearchChatInput(days=0)


def test_who_is_in_dm_requires_space_name():
    with pytest.raises(ValidationError):
        WhoIsInDmInput()
    WhoIsInDmInput(space_name="spaces/dm1")


def test_send_chat_attachment_requires_space():
    with pytest.raises(ValidationError):
        SendChatAttachmentInput()
    SendChatAttachmentInput(space_name="spaces/AAA", path="/tmp/x.pdf")


def test_recent_activity_hours_bounds():
    RecentChatActivityInput(hours=1)
    RecentChatActivityInput(hours=720)  # 30 days
    with pytest.raises(ValidationError):
        RecentChatActivityInput(hours=721)


def test_chat_digest_hours_bounds():
    ChatDigestInput(hours=1)
    ChatDigestInput(hours=168)  # 7 days
    with pytest.raises(ValidationError):
        ChatDigestInput(hours=169)


def test_chat_to_contact_group_requires_group_and_text():
    with pytest.raises(ValidationError):
        ChatToContactGroupInput()
    with pytest.raises(ValidationError):
        ChatToContactGroupInput(group_resource_name="contactGroups/abc")
    ChatToContactGroupInput(group_resource_name="contactGroups/abc", text="hi")


def test_react_to_message_requires_message_and_emoji():
    with pytest.raises(ValidationError):
        ReactToMessageInput()
    with pytest.raises(ValidationError):
        ReactToMessageInput(message_name="spaces/A/messages/m1")
    ReactToMessageInput(message_name="spaces/A/messages/m1", emoji="👍")


def test_get_thread_requires_thread_name():
    with pytest.raises(ValidationError):
        GetThreadInput()
    GetThreadInput(thread_name="spaces/A/threads/t1")


def test_get_message_requires_message_name():
    with pytest.raises(ValidationError):
        GetMessageInput()
    GetMessageInput(message_name="spaces/A/messages/m1")


def test_all_chat_tools_registered():
    from server import mcp
    expected = {
        "chat_list_spaces", "chat_find_or_create_dm", "chat_get_space",
        "chat_list_messages", "chat_get_message", "chat_send_message",
        "chat_update_message", "chat_delete_message", "chat_list_members",
        "chat_download_attachment", "chat_send_dm",
        "chat_send_to_space_by_name", "chat_search", "chat_who_is_in_dm",
        "chat_send_attachment", "chat_recent_activity",
        "chat_react_to_message", "chat_get_thread",
    }
    actual = {n for n in mcp._tool_manager._tools if n.startswith("chat_")}
    assert expected.issubset(actual), f"missing: {expected - actual}"
