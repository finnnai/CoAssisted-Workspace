"""Shared error-formatting helpers so every tool returns actionable messages."""

from __future__ import annotations

from googleapiclient.errors import HttpError

from auth import AuthError


def format_error(e: Exception) -> str:
    """Convert any exception raised by a tool into a human-readable error string.

    Tools should call this in their `except` clauses so the LLM (and user) gets
    a consistent, actionable message instead of a raw stack trace.
    """
    if isinstance(e, AuthError):
        return f"Auth error: {e}"

    if isinstance(e, HttpError):
        status = e.resp.status if e.resp is not None else "?"
        reason = getattr(e, "reason", "") or ""
        if status == 401:
            return (
                "Auth error: Google rejected the token (401). "
                "Delete token.json and let the MCP re-authenticate."
            )
        if status == 403:
            return (
                f"Permission denied (403): {reason}. "
                f"Check that the relevant API is enabled in Google Cloud and "
                f"that your account has access to the resource."
            )
        if status == 404:
            return f"Not found (404): {reason}. Check the ID/path."
        if status == 429:
            return "Rate limit exceeded (429). Wait a moment and try again."
        return f"Google API error {status}: {reason or e}"

    return f"Unexpected error ({type(e).__name__}): {e}"
