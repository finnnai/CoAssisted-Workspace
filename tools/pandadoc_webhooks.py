# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Auto-generated PandaDoc API tools — module: webhooks.

DO NOT EDIT BY HAND. Regenerate via:
    python3 scripts/generate_pandadoc_tools.py

This module wraps 8 PandaDoc operations under tag(s): Webhook events, Webhook subscriptions.

Pydantic input classes live at MODULE scope (not inside register())
so FastMCP's typing.get_type_hints can resolve them. Earlier rev
nested them in register() and triggered InvalidSignature on startup.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

import pandadoc_client

class _Input_createWebhookSubscription(BaseModel):
    model_config = ConfigDict(extra='forbid')
    body: dict[str, Any] = Field(..., description="JSON body — see PandaDoc docs for the schema of this endpoint.")

class _Input_deleteWebhookSubscription(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(..., description="Path: Webhook subscription uuid.")

class _Input_detailsWebhookEvent(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(..., description="Path: Webhook event uuid.")

class _Input_detailsWebhookSubscription(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(..., description="Path: Webhook subscription uuid")

class _Input_listWebhookEvent(BaseModel):
    model_config = ConfigDict(extra='forbid')
    count: int = Field(..., description="Query: Specify how many event results to return.")
    page: int = Field(..., description="Query: Specify which page of the dataset to return.")
    since: Optional[str] = Field(None, description="Query: Return results where the event creation time is greater than or equal to this value.")
    to: Optional[str] = Field(None, description="Query: Return results where the event creation time is less than this value.")
    type: Optional[list[dict[str, Any]]] = Field(None, description="Query: Returns results by the specified event types.")
    http_status_code: Optional[list[dict[str, Any]]] = Field(None, description="Query: Returns results with the specified HTTP status codes.")
    error: Optional[list[dict[str, Any]]] = Field(None, description="Query: Returns results with the following errors.")

class _Input_listWebhookSubscriptions(BaseModel):
    model_config = ConfigDict(extra='forbid')
    pass

class _Input_updateWebhookSubscription(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(..., description="Path: Webhook subscription uuid")
    body: dict[str, Any] = Field(..., description="JSON body — see PandaDoc docs for the schema of this endpoint.")

class _Input_updateWebhookSubscriptionSharedKey(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(..., description="Path: Webhook subscription uuid")


def register(mcp) -> None:  # noqa: ANN001
    """Register every tool in this module with the FastMCP instance."""

    @mcp.tool()
    def pandadoc_create_webhook_subscription(params: _Input_createWebhookSubscription) -> Any:
        """Create Webhook Subscription"""
        return pandadoc_client.call(
            "createWebhookSubscription",
            json_body=params.body,
        )

    @mcp.tool()
    def pandadoc_delete_webhook_subscription(params: _Input_deleteWebhookSubscription) -> Any:
        """Delete Webhook Subscription"""
        return pandadoc_client.call(
            "deleteWebhookSubscription",
            path_params={"id": params.id},
        )

    @mcp.tool()
    def pandadoc_details_webhook_event(params: _Input_detailsWebhookEvent) -> Any:
        """Webhook Event Details"""
        return pandadoc_client.call(
            "detailsWebhookEvent",
            path_params={"id": params.id},
        )

    @mcp.tool()
    def pandadoc_details_webhook_subscription(params: _Input_detailsWebhookSubscription) -> Any:
        """Webhook Subscription Details"""
        return pandadoc_client.call(
            "detailsWebhookSubscription",
            path_params={"id": params.id},
        )

    @mcp.tool()
    def pandadoc_list_webhook_event(params: _Input_listWebhookEvent) -> Any:
        """List Webhook Events"""
        return pandadoc_client.call(
            "listWebhookEvent",
            query={"count": params.count, "page": params.page, "since": params.since, "to": params.to, "type": params.type, "http_status_code": params.http_status_code, "error": params.error},
        )

    @mcp.tool()
    def pandadoc_list_webhook_subscriptions(params: _Input_listWebhookSubscriptions) -> Any:
        """List Webhook Subscriptions"""
        return pandadoc_client.call(
            "listWebhookSubscriptions",
        )

    @mcp.tool()
    def pandadoc_update_webhook_subscription(params: _Input_updateWebhookSubscription) -> Any:
        """Update Webhook Subscription"""
        return pandadoc_client.call(
            "updateWebhookSubscription",
            path_params={"id": params.id},
            json_body=params.body,
        )

    @mcp.tool()
    def pandadoc_update_webhook_subscription_shared_key(params: _Input_updateWebhookSubscriptionSharedKey) -> Any:
        """Update Webhook Subscription Shared Key"""
        return pandadoc_client.call(
            "updateWebhookSubscriptionSharedKey",
            path_params={"id": params.id},
        )
