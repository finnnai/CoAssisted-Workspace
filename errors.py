"""Shared error-formatting helpers so every tool returns actionable messages."""

from __future__ import annotations

from googleapiclient.errors import HttpError

from auth import AuthError


def format_error(*args) -> str:
    """Convert any exception raised by a tool into a human-readable error string.

    Tools should call this in their `except` clauses so the LLM (and user) gets
    a consistent, actionable message instead of a raw stack trace.

    Two call shapes supported (both kept for backwards compat with existing
    workflow handlers — some pass the workflow name as a prefix, others just
    the exception):

        format_error(e)             # legacy
        format_error(name, e)       # newer — prefixes message with name
    """
    if len(args) == 1:
        name, e = None, args[0]
    elif len(args) == 2:
        name, e = args
    else:
        return f"format_error called with unexpected args: {args!r}"

    prefix = f"[{name}] " if name else ""

    if isinstance(e, AuthError):
        return f"{prefix}Auth error: {e}"

    if isinstance(e, HttpError):
        status = e.resp.status if e.resp is not None else "?"
        reason = getattr(e, "reason", "") or ""
        if status == 401:
            return (
                f"{prefix}Auth error: Google rejected the token (401). "
                "Delete token.json and let the MCP re-authenticate."
            )
        if status == 403:
            return (
                f"{prefix}Permission denied (403): {reason}. "
                f"Check that the relevant API is enabled in Google Cloud and "
                f"that your account has access to the resource."
            )
        if status == 404:
            return f"{prefix}Not found (404): {reason}. Check the ID/path."
        if status == 429:
            return f"{prefix}Rate limit exceeded (429). Wait a moment and try again."
        return f"{prefix}Google API error {status}: {reason or e}"

    return f"{prefix}Unexpected error ({type(e).__name__}): {e}"
