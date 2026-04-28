# © 2026 CoAssisted Workspace. Licensed for non-redistribution use only.
# See LICENSE file for terms. Removing or altering this header is prohibited.
"""User-configurable defaults loaded from config.json.

The file is optional. If it's missing, defaults are used. If a key is missing
from the file, that key's default is used. No strict schema — forgiving loader.

Lookup order for any value:
    1. Tool argument (always wins)
    2. config.json value
    3. hard-coded default here

Example config.json:
    {
      "default_timezone": "America/Los_Angeles",
      "default_calendar_id": "primary",
      "default_from_alias": "finnn@surefox.com",
      "dry_run": false,
      "log_level": "INFO",
      "retry": {
        "max_attempts": 4,
        "initial_backoff_seconds": 1.0,
        "max_backoff_seconds": 30.0
      }
    }
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_PROJECT_DIR = Path(__file__).resolve().parent
_CONFIG_PATH = _PROJECT_DIR / "config.json"

_DEFAULTS: dict[str, Any] = {
    "default_timezone": None,          # fall back to Google's account default
    "default_calendar_id": "primary",
    "default_from_alias": None,        # fall back to primary address
    "dry_run": False,                  # global kill-switch for destructive ops
    "log_level": "INFO",
    "crm_window_days": 60,             # window for Sent/Received tallies on contacts
    "log_sent_emails_to_contacts": True,  # append activity notes to contact biographies
    # Attachments larger than this auto-upload to Drive and are shared via link
    # rather than inlined. Prevents stdio BrokenPipeErrors on Cowork's MCP channel
    # and bypasses corporate mail filters that block .tar.gz / .zip.
    "large_attachment_threshold_kb": 500,
    # Hard cap on inline base64 returned in MCP responses (download tools).
    # Anything bigger MUST use save_to_path. Default 5MB — under stdio buffer limits
    # AND keeps context window manageable.
    "max_inline_download_kb": 5120,
    # Gmail's hard limit on total per-message size (incl. base64-encoded attachments).
    # Used as a safety pre-check; we route ALL attachments to Drive if total approaches.
    "gmail_max_message_kb": 22528,
    # When a download (attachment, drive file) exceeds max_inline_download_kb AND
    # the caller didn't pass save_to_path, the file is auto-saved here so the
    # call never just fails.
    #
    # Default is ~/Gmail Downloads (a dedicated folder, kept separate from the
    # OS Downloads folder which is usually cluttered). Override in config.json
    # to anywhere — e.g. "~/Documents/MCP Files" or an absolute path.
    "default_download_dir": "~/Gmail Downloads",
    # Signature parser mode for the contact enrichment pipeline.
    # - "regex"            : regex heuristics only. Default. Free, fast.
    # - "regex_then_llm"   : try regex first; if title or organization is
    #                        missing, call Claude to fill the gaps. ~$0.001
    #                        per missed signature. Requires anthropic_api_key.
    # - "llm"              : always run regex + LLM and merge. More accurate,
    #                        more expensive. Requires anthropic_api_key.
    "signature_parser_mode": "regex",
    # Google Maps API key — separate from OAuth (Maps APIs use a static key).
    # Required for: 10 maps_* tools, workflow_email_with_map,
    # workflow_meeting_location_options, optional contact-address validation.
    # See GCP_SETUP.md for setup steps.
    "google_maps_api_key": None,
    # When True, contacts_create and contacts_update auto-canonicalize the
    # address through Google Maps Address Validation. No-op if no Maps key.
    "auto_validate_contact_addresses": False,
    # GCP project ID — required for Route Optimization API
    # (workflow_route_optimize_advanced). If left None, auto-detected from
    # credentials.json's installed.client_id project context. Override here
    # only if your OAuth project differs from the GCP project where Route
    # Optimization API is enabled (rare).
    "gcp_project_id": None,
    # Default home address used by workflow_commute_brief,
    # workflow_departure_reminder, and workflow_calendar_drive_time_blocks
    # when no per-call origin is given.
    "home_address": None,
    # Optional: address to email sanitized health reports to when the user
    # explicitly runs `system_share_health_report`. Default None means no
    # auto-send, no destination configured. Each coworker opts in by setting
    # this to the developer's address (or to themselves for self-debugging).
    "telemetry_email": None,
    # License key for CoAssisted Workspace paid tier. Format: caw-XXXX-XXXX-XXXX-XXXX
    # Personal/handoff tarballs ignore this (DISTRIBUTION_MODE='personal' in
    # tier.py means everything works regardless). Marketplace plugin builds
    # set DISTRIBUTION_MODE='marketplace' and validate this key before allowing
    # paid features. See tier.py for the free vs paid split.
    "license_key": None,
    # Receipt extractor (workflow_extract_receipts and friends).
    # If set, all receipt tools default to writing to this Sheet (otherwise
    # auto-create one named 'CoAssisted Receipts — YYYY' per year).
    "receipts_sheet_id": None,
    # Drive folder to archive receipt PDFs/images. If unset, auto-create
    # a folder named 'CoAssisted Receipts'.
    "receipts_drive_folder_id": None,
    # When True (default), strip the last_4 of card before persisting.
    # The LLM may still extract it transiently for confidence purposes,
    # but it never lands in the Sheet or any persisted file.
    "receipts_redact_payment_details": True,
    # Optional override of the QuickBooks account-name mapping.
    # Format: { "Travel — Airfare": "Flight Expense", ... }. See
    # receipts.py _DEFAULT_QB_ACCOUNT_MAP for the default table.
    "receipts_qb_account_map": None,
    "retry": {
        "max_attempts": 4,
        "initial_backoff_seconds": 1.0,
        "max_backoff_seconds": 30.0,
    },
}


_cache: dict[str, Any] | None = None


def _load() -> dict[str, Any]:
    global _cache
    if _cache is not None:
        return _cache

    cfg = dict(_DEFAULTS)
    if _CONFIG_PATH.exists():
        try:
            user_cfg = json.loads(_CONFIG_PATH.read_text())
            # Shallow merge at top level, one level deep for nested dicts.
            for k, v in user_cfg.items():
                if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                    cfg[k] = {**cfg[k], **v}
                else:
                    cfg[k] = v
        except Exception:
            # Corrupt config → fall back to defaults silently. We log it
            # elsewhere (see logging module) — don't crash the server.
            pass

    _cache = cfg
    return cfg


def get(key: str, default: Any = None) -> Any:
    """Fetch a top-level config value."""
    return _load().get(key, default)


def retry_settings() -> dict[str, Any]:
    """Shortcut for the retry block."""
    return _load().get("retry", _DEFAULTS["retry"])


def reload() -> None:
    """Force re-read of config.json. Mostly for tests."""
    global _cache
    _cache = None


def gcp_project_id() -> str | None:
    """Resolve the GCP project ID for Cloud / Route Optimization APIs.

    Order: explicit config value → credentials.json auto-detect → None.
    """
    explicit = get("gcp_project_id")
    if explicit:
        return explicit
    creds_path = _PROJECT_DIR / "credentials.json"
    if not creds_path.exists():
        return None
    try:
        creds = json.loads(creds_path.read_text())
    except Exception:
        return None
    # OAuth client files have either {"installed": {...}} or {"web": {...}}.
    for key in ("installed", "web"):
        block = creds.get(key) or {}
        # Old credentials had project_id at the top level; newer ones embed it
        # in client_id (format: <project>-...apps.googleusercontent.com)
        if block.get("project_id"):
            return block["project_id"]
        client_id = block.get("client_id") or ""
        # client_id format: <numeric>-<project>.apps.googleusercontent.com
        # The project is NOT directly in client_id; we need project_id field.
        # Returning None if we can't find an explicit project_id.
        if "project_id" in block:
            return block["project_id"]
    return None


def resolve_auto_download_path(filename: str | None) -> Path:
    """Pick an absolute, unique path under `default_download_dir` for an auto-save.

    Defaults to ~/Gmail Downloads (a dedicated folder kept separate from the
    cluttered OS Downloads). Caller can override via config.json. The directory
    is created if missing. If a file with the same name already exists, a
    timestamp suffix is appended so auto-saves never overwrite previous ones.
    """
    import datetime as _dt

    dir_str = get("default_download_dir") or "~/Gmail Downloads"
    base_dir = Path(dir_str).expanduser()
    base_dir.mkdir(parents=True, exist_ok=True)

    safe_name = (filename or "download.bin").strip() or "download.bin"
    # Strip any path separators that might have snuck into the filename.
    safe_name = Path(safe_name).name
    candidate = base_dir / safe_name
    if candidate.exists():
        stem = candidate.stem
        suffix = candidate.suffix
        ts = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        candidate = base_dir / f"{stem}.{ts}{suffix}"
    return candidate
