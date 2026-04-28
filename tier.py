# © 2026 CoAssisted Workspace. Licensed for non-redistribution use only.
# See LICENSE file for terms. Removing or altering this header is prohibited.
"""Tier-aware feature gating for CoAssisted Workspace.

Splits the 183 tools into:

  - FREE_TOOLS  — 53 tools covering Workspace basics + low-cost admin
                  helpers (project registry CRUD, sheet bootstrapping).
                  Always work, no license needed. The hook that gets
                  users in AND lets them evaluate the AP loop's routing
                  structure before paying.
  - Paid tier   — everything else: Maps, all workflows, advanced CRM,
                  brand voice, bulk ops, advanced Chat, the full
                  Project-AP pipeline (LLM-driven extraction +
                  vendor follow-up loop + hybrid Drive layout). Costs
                  YOU per-call money OR delivers high enough value to
                  charge for.

Today's default: `DISTRIBUTION_MODE = "personal"` — every tool works
regardless of license. Nothing breaks for current users (you, your trust
group, anyone with a tarball you handed off).

When packaging for the official plugin marketplace, flip to
`"marketplace"`. With that mode, paid tools return `gated_response()`
unless the user has a valid license_key configured.

License format (v1, format-check only — easy to bypass, intentionally so
for trust-group launch):
    caw-XXXX-XXXX-XXXX-XXXX     where X is uppercase alphanumeric (A-Z, 0-9)

Future v2: server-side validation against an endpoint you run.
"""

from __future__ import annotations

import re
from typing import Optional


# --------------------------------------------------------------------------- #
# Build identifier — non-secret, embedded in every copy of this build.
# --------------------------------------------------------------------------- #
# Derived from _version.py — single source of truth for VERSION + CHANNEL +
# RELEASE_DATE. Surfaced in system_check_license output. NOT a secret —
# anyone running the binary can read it. Useful as a telemetry /
# fork-detection signal: if you ever see a support ticket referencing a
# BUILD_HASH from a license that never validated, you know how widespread
# an unauthorized fork is.
try:
    from _version import VERSION as _V, CHANNEL as _C, RELEASE_DATE as _D
    BUILD_HASH: str = f"caw-v{_V}-{_C}-{_D}"
except ImportError:
    # Fallback if _version.py isn't on the path (shouldn't happen in practice
    # since _version.py sits at project root alongside this file).
    BUILD_HASH: str = "caw-unknown-build"


# --------------------------------------------------------------------------- #
# Distribution mode — controls whether tier gating is enforced.
# --------------------------------------------------------------------------- #
# `personal`    — full features for current users. NOTHING is gated. Default.
# `marketplace` — paid-tier tools return gated_response() unless license_key set.
DISTRIBUTION_MODE: str = "personal"


# --------------------------------------------------------------------------- #
# The free-tier feature set (~50 tools).
# --------------------------------------------------------------------------- #
# Curated to cover: send/read mail, basic calendar, basic Drive, basic Sheets/
# Docs, basic Tasks, basic Contacts/Chat reads, plus the meta-tools that help
# people debug their setup. Anyone trying the product can complete a real task
# in 60 seconds without paying. Everything beyond is the paid-tier value prop.
FREE_TOOLS: frozenset[str] = frozenset({
    # Gmail core (12) — send, read, organize. NOT: filters, send-as, templates,
    # mail merge, attachment download (high-value automation).
    "gmail_send_email",
    "gmail_create_draft",
    "gmail_list_drafts",
    "gmail_search",
    "gmail_get_thread",
    "gmail_reply_to_thread",
    "gmail_forward_message",
    "gmail_trash_message",
    "gmail_untrash_message",
    "gmail_list_labels",
    "gmail_create_label",
    "gmail_modify_labels",

    # Calendar core (7) — full basic CRUD. Free advanced features stay because
    # they're table-stakes for any productivity user.
    "calendar_list_events",
    "calendar_list_calendars",
    "calendar_create_event",
    "calendar_update_event",
    "calendar_delete_event",
    "calendar_quick_add",
    "calendar_respond_to_event",

    # Drive core (6) — search, read, basic upload, share, delete. NOT: binary
    # download (large file handling) or move (advanced).
    "drive_search_files",
    "drive_read_file",
    "drive_upload_text_file",
    "drive_create_folder",
    "drive_share_file",
    "drive_delete_file",

    # Sheets core (4) — single-tab CRUD. NOT: tab management, list_sheets.
    "sheets_create_spreadsheet",
    "sheets_read_range",
    "sheets_write_range",
    "sheets_append_rows",

    # Docs core (3) — create, read, append. NOT: replace_text (advanced).
    "docs_create_document",
    "docs_read_document",
    "docs_insert_text",

    # Tasks core (4) — lists, create, update, complete. NOT: delete or list_lists.
    "tasks_list_tasks",
    "tasks_create_task",
    "tasks_update_task",
    "tasks_complete_task",

    # Contacts read-only core (3) — search/list/get. CRM features (create,
    # update, custom fields, groups, refresh, csv) are paid because that's the
    # real value driver.
    "contacts_search",
    "contacts_list",
    "contacts_get",

    # Chat read core (4) — see your spaces, send a basic message. Advanced
    # Chat (DMs by email, search, attachments, reactions) are paid.
    "chat_list_spaces",
    "chat_list_messages",
    "chat_send_message",
    "chat_find_or_create_dm",

    # System / health (6) — meta tools. ALWAYS free regardless of mode so
    # users can self-diagnose problems without paying. Critical for support.
    "system_doctor",
    "system_check_oauth",
    "system_check_workspace_apis",
    "system_check_dependencies",
    "system_check_anthropic_key",
    "system_recent_actions",

    # One simple workflow (1) — gateway drug. Saves attachments to Drive in
    # one call. Demonstrates "this tool does cross-service work" without
    # giving away the most valuable workflows.
    "workflow_save_email_attachments_to_drive",

    # Project AP — admin / setup helpers (3). Lets evaluators register a
    # project, see the routing-rule shape, and bootstrap a sheet — but
    # NOT extract anything. The actual LLM extraction + vendor follow-up
    # loop + Drive archive flow are paid tools.
    "workflow_register_project",
    "workflow_list_projects",
    "workflow_create_project_sheet",
})

# Total: 53 tools (12 + 7 + 6 + 4 + 3 + 4 + 3 + 4 + 6 + 1 + 3)


# --------------------------------------------------------------------------- #
# License key handling
# --------------------------------------------------------------------------- #

# Format: caw-XXXX-XXXX-XXXX-XXXX (16 uppercase alphanumeric chars in groups of 4)
_LICENSE_RE = re.compile(r"^caw-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}$")


def validate_license_key(key: Optional[str]) -> bool:
    """Format-check a license key. Returns True if it looks valid.

    v1: regex-only. Any well-formed string passes — you're trusting the
    user not to forge keys. Acceptable for trust-group launch; replace with
    server-side check for real enforcement.
    """
    if not key or not isinstance(key, str):
        return False
    return bool(_LICENSE_RE.match(key.strip()))


def current_tier() -> str:
    """Return 'free' or 'paid' based on current distribution mode + config.

    - personal mode: always 'paid' (no gating)
    - marketplace mode: 'paid' iff license_key validates, else 'free'
    """
    if DISTRIBUTION_MODE == "personal":
        return "paid"
    try:
        import config
        key = config.get("license_key")
    except Exception:
        return "free"
    return "paid" if validate_license_key(key) else "free"


# --------------------------------------------------------------------------- #
# Tool gating
# --------------------------------------------------------------------------- #


def is_paid(tool_name: str) -> bool:
    """True if this tool is in the paid tier (i.e. NOT in FREE_TOOLS)."""
    return tool_name not in FREE_TOOLS


def is_unlocked(tool_name: str) -> bool:
    """True if this tool can run RIGHT NOW given mode + license state."""
    if DISTRIBUTION_MODE == "personal":
        return True
    if not is_paid(tool_name):
        return True
    return current_tier() == "paid"


def gated_response(tool_name: str) -> dict:
    """The structured response a paid tool returns when called without a license.

    Returned as a dict so the caller can json.dumps() it consistently with
    every other tool's response shape.
    """
    return {
        "status": "paid_feature",
        "tool": tool_name,
        "tier": current_tier(),
        "message": (
            f"`{tool_name}` is a paid-tier feature.\n"
            "\n"
            "Free tier includes: all Workspace basics (Gmail send/read, "
            "Calendar CRUD, Drive search/upload, Sheets/Docs basics, "
            "Tasks, basic Chat + Contacts), system_doctor + recent_actions, "
            "and project-registry admin (register_project, list_projects, "
            "create_project_sheet) so you can evaluate the AP loop's "
            "routing-rule shape.\n"
            "\n"
            "Paid tier unlocks: all Maps tools, the 16 Maps×CRM×Calendar "
            "workflows, advanced VRP routing, brand voice extraction, "
            "bulk operations with rollback, advanced Chat (DM, search, "
            "attachments), templates + mail merge, full CRM (custom "
            "fields, groups, stats refresh), AND the full Project-AP "
            "pipeline: LLM-driven invoice + receipt extraction with "
            "5-tier project routing, internal/external sender split, "
            "DM-or-email vendor follow-up loop, automated reply "
            "parsing + row promotion, hybrid Drive layout (per-employee "
            "folders + per-project sheets + PDF archive), and QuickBooks "
            "Bills CSV export."
        ),
        "upgrade_hint": (
            'Add to config.json: { "license_key": "caw-XXXX-XXXX-XXXX-XXXX" }'
        ),
        "build": BUILD_HASH,
    }
