#!/usr/bin/env python3
"""Interactive post-install setup wizard.

Walks the user through each optional integration with skip-able steps:

    1. OAuth flow (Google Workspace) — required, redirects to authenticate.py
    2. Anthropic API key — optional; unlocks brand voice, signature parser,
       chat digest LLM summaries.
    3. Google Maps API key — optional; unlocks 10 maps_* tools, email-with-map,
       meeting-location-options.
    4. Telemetry opt-in — optional; lets system_doctor share sanitized error
       reports with the developer to improve future releases. Default decline.
    5. Cron jobs — optional; sets up daily refresh + enrichment + (quarterly)
       brand voice regeneration.

Each step explains what it unlocks, what it costs, and offers a Skip option.

Run:
    /Users/YOUR/google_workspace_mcp/.venv/bin/python setup_wizard.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


_PROJECT_DIR = Path(__file__).resolve().parent
_CONFIG_PATH = _PROJECT_DIR / "config.json"
_VENV_PYTHON = _PROJECT_DIR / ".venv" / "bin" / "python"

# When forking this MCP for your own team, change this to your email so
# coworkers see your address as the default in the telemetry opt-in step.
# Coworkers can still override it per-install or skip entirely.
_DEVELOPER_EMAIL = "finnn@surefox.com"


# --------------------------------------------------------------------------- #
# Pretty-printing helpers
# --------------------------------------------------------------------------- #


def header(s: str) -> None:
    print()
    print("\033[1m" + "=" * 70 + "\033[0m")
    print("\033[1m  " + s + "\033[0m")
    print("\033[1m" + "=" * 70 + "\033[0m")
    print()


def section(s: str) -> None:
    print()
    print("\033[1m▶ " + s + "\033[0m")
    print("─" * 70)


def info(s: str) -> None:
    print(s)


def ok(s: str) -> None:
    print("\033[32m✓\033[0m " + s)


def warn(s: str) -> None:
    print("\033[33m⚠\033[0m " + s)


def err(s: str) -> None:
    print("\033[31m✗\033[0m " + s)


def ask(prompt: str, default: str = "y") -> bool:
    """Yes/no prompt. Default in caps. Returns bool."""
    suffix = " [Y/n] " if default.lower() == "y" else " [y/N] "
    raw = input(prompt + suffix).strip().lower()
    if not raw:
        return default.lower() == "y"
    return raw in ("y", "yes")


def prompt_value(prompt: str, allow_blank: bool = True) -> str:
    """Free-text prompt. Returns the user's input (stripped)."""
    val = input(prompt + " ").strip()
    if not val and not allow_blank:
        return prompt_value(prompt, allow_blank=allow_blank)
    return val


# --------------------------------------------------------------------------- #
# Config helpers
# --------------------------------------------------------------------------- #


def load_config() -> dict:
    if not _CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(_CONFIG_PATH.read_text())
    except Exception:
        warn("config.json is unparseable — starting fresh.")
        return {}


def save_config(cfg: dict) -> None:
    _CONFIG_PATH.write_text(json.dumps(cfg, indent=2) + "\n")
    ok(f"Saved → {_CONFIG_PATH}")


# --------------------------------------------------------------------------- #
# Step 1 — OAuth
# --------------------------------------------------------------------------- #


def step_oauth() -> bool:
    section("Step 1 of 5 — Google Workspace OAuth")
    info(
        "Required. Authorizes the MCP to act on your Gmail/Calendar/Drive/etc.\n"
        "If credentials.json doesn't exist yet, you need to do GCP setup first\n"
        "(see GCP_SETUP.md) before this step works."
    )
    print()

    if not (_PROJECT_DIR / "credentials.json").exists():
        err("credentials.json not found in the project folder.")
        info("→ See GCP_SETUP.md for the one-time GCP project + OAuth client setup.")
        info("   Once you have credentials.json placed here, re-run this wizard.")
        return False

    if (_PROJECT_DIR / "token.json").exists():
        ok("token.json already exists — you've authenticated before.")
        if not ask("Re-run OAuth flow anyway? (e.g. to refresh scopes after an update)", default="n"):
            return True

    info("Launching OAuth — your browser will open and ask you to grant scopes.")
    if not ask("Continue?", default="y"):
        warn("OAuth skipped. Run `python authenticate.py` later to do this manually.")
        return False

    auth_script = _PROJECT_DIR / "authenticate.py"
    if not auth_script.exists():
        err("authenticate.py missing — your install is incomplete.")
        return False

    py = str(_VENV_PYTHON if _VENV_PYTHON.exists() else sys.executable)
    try:
        subprocess.run([py, str(auth_script)], check=True, cwd=str(_PROJECT_DIR))
        ok("OAuth flow complete. token.json saved.")
        return True
    except subprocess.CalledProcessError as e:
        err(f"OAuth flow failed: {e}")
        return False


# --------------------------------------------------------------------------- #
# Step 2 — Anthropic
# --------------------------------------------------------------------------- #


def step_anthropic(cfg: dict) -> dict:
    section("Step 2 of 5 — Anthropic API key (optional)")
    info(
        "Unlocks LLM-backed features:\n"
        "  • refresh_brand_voice.py  — quarterly voice extraction (~$0.05/run)\n"
        "  • signature_parser_mode='regex_then_llm'  — fills in missing title/org "
        "(~$0.001 per gap-fill)\n"
        "  • workflow_chat_digest LLM summarization (~$0.02-0.05 per run)\n"
        "Typical monthly cost: $1-15. New Anthropic accounts get $5 free credit."
    )
    print()

    existing = cfg.get("anthropic_api_key")
    if existing:
        ok(f"Already configured: {existing[:15]}... (length {len(existing)})")
        if not ask("Replace with a different key?", default="n"):
            return cfg

    if not ask("Set up Anthropic API key now?", default="y"):
        info("Skipped. You can paste a key into config.json later under `anthropic_api_key`.")
        return cfg

    info(
        "1. Open: https://console.anthropic.com → Settings → API Keys → Create Key\n"
        "2. Set up billing: Settings → Billing\n"
        "3. Copy the key (starts with sk-ant-api03-...)"
    )
    print()
    key = prompt_value("Paste your Anthropic API key:")
    if not key:
        warn("Empty input — skipping.")
        return cfg
    if not key.startswith("sk-ant-"):
        warn(f"Key doesn't look like an Anthropic key (got prefix '{key[:8]}...').")
        if not ask("Save anyway?", default="n"):
            return cfg

    cfg["anthropic_api_key"] = key
    save_config(cfg)

    info("Optional: also enable smart signature parsing.")
    if ask("Set signature_parser_mode to 'regex_then_llm'?", default="y"):
        cfg["signature_parser_mode"] = "regex_then_llm"
        save_config(cfg)
        ok("signature_parser_mode = regex_then_llm")
    return cfg


# --------------------------------------------------------------------------- #
# Step 3 — Google Maps
# --------------------------------------------------------------------------- #


def step_maps(cfg: dict) -> dict:
    section("Step 3 of 5 — Google Maps API key (optional)")
    info(
        "Unlocks 10 maps_* tools + 2 cross-service workflows:\n"
        "  • Geocoding, reverse geocoding, places search, place details\n"
        "  • Directions, distance matrix, time zone, address validation\n"
        "  • Static map images for emails / Chat\n"
        "  • workflow_email_with_map  — embed map in 'where to meet' emails\n"
        "  • workflow_meeting_location_options  — fair venue picker\n"
        "Cost: $200/month free credit covers typical use. Beyond that, ~$5/1000\n"
        "calls for most APIs. Personal use rarely exceeds $5/month."
    )
    print()

    existing = cfg.get("google_maps_api_key")
    if existing:
        ok(f"Already configured: {existing[:10]}... (length {len(existing)})")
        if not ask("Replace with a different key?", default="n"):
            return cfg

    if not ask("Set up Maps API key now?", default="y"):
        info("Skipped. You can paste a key into config.json later under `google_maps_api_key`.")
        return cfg

    info(
        "1. Enable billing: https://console.cloud.google.com/billing  (required even on free tier)\n"
        "2. Enable these 7 Maps APIs in your existing GCP project:\n"
        "     • Geocoding API\n"
        "     • Places API (New)\n"
        "     • Directions API\n"
        "     • Distance Matrix API\n"
        "     • Time Zone API\n"
        "     • Address Validation API\n"
        "     • Maps Static API\n"
        "   Direct link: https://console.cloud.google.com/google/maps-apis/start\n"
        "3. Create an API key: https://console.cloud.google.com/apis/credentials\n"
        "   → 'Create Credentials' → 'API key'\n"
        "4. Restrict the key to those 7 APIs (best practice)."
    )
    print()
    key = prompt_value("Paste your Maps API key (starts with AIza...):")
    if not key:
        warn("Empty input — skipping.")
        return cfg
    if not key.startswith("AIza"):
        warn(f"Key doesn't look like a Maps key (got prefix '{key[:6]}...').")
        if not ask("Save anyway?", default="n"):
            return cfg

    cfg["google_maps_api_key"] = key
    save_config(cfg)

    info("Optional: auto-canonicalize addresses on contact create/update.")
    if ask("Enable auto_validate_contact_addresses?", default="n"):
        cfg["auto_validate_contact_addresses"] = True
        save_config(cfg)
    return cfg


# --------------------------------------------------------------------------- #
# Step 4 — Telemetry opt-in
# --------------------------------------------------------------------------- #


def step_telemetry(cfg: dict) -> dict:
    section("Step 4 of 5 — Help improve the MCP (opt-in, anonymized)")
    info(
        "When `system_doctor` finds a problem on your machine, you can\n"
        "optionally email a SANITIZED error report to the developer so the\n"
        "next release fixes the issue for everyone — not just you.\n"
        "\n"
        "Every send is opt-in per call. Nothing transmits automatically."
    )
    print()
    info("\033[1mWhat gets sent (when you choose to share):\033[0m")
    info("  • Names of failed health checks")
    info("  • Error details with emails, API keys, file paths, IPs redacted")
    info("  • macOS + Python version, integration booleans (no values)")
    info("  • Last 20 audit-log entries from recent_actions.jsonl, sanitized")
    print()
    info("\033[1mWhat NEVER gets sent:\033[0m")
    info("  • Your actual emails, contacts, calendar, files, Chat messages")
    info("  • Your API keys, OAuth tokens, or refresh tokens")
    info("  • Your name, exact location, or anything personally identifying")
    print()
    info("Reports always save locally first to logs/health_reports/.")
    info("You preview each one and confirm before any send.")
    print()
    if not ask("Enable opt-in error reporting?", default="n"):
        info(
            "Skipped. Reports still save locally; just no destination "
            "configured for sharing. Enable later via config.json or by "
            "re-running this wizard."
        )
        return cfg

    info(f"Suggested destination: \033[36m{_DEVELOPER_EMAIL}\033[0m")
    info("(Press Enter to accept, or type a different address.)")
    target = prompt_value(
        "Telemetry email", allow_blank=True,
    ) or _DEVELOPER_EMAIL
    cfg["telemetry_email"] = target
    save_config(cfg)
    ok(f"Set telemetry_email = {target}")
    info(
        "From now on, when system_doctor finds an issue it'll save a "
        "sanitized report and you can run `system_share_health_report` "
        "to email it. The send always shows a preview first."
    )
    return cfg


# --------------------------------------------------------------------------- #
# Step 5 — Cron jobs
# --------------------------------------------------------------------------- #


def step_cron() -> None:
    section("Step 5 of 5 — Daily / quarterly cron jobs (optional)")
    info(
        "Three scripts ship with this MCP, all designed for cron:\n"
        "  • refresh_stats.py      — daily 7am: refresh CRM managed fields\n"
        "  • enrich_inbox.py       — daily 7:05am: enrich contacts from inbox sigs\n"
        "  • refresh_brand_voice.py — quarterly: regenerate brand-voice.md\n"
        "All have --dry-run flags and self-rotate their cron logs at 10MB."
    )
    print()
    if not ask("Show the cron lines you can paste into `crontab -e`?", default="y"):
        return

    user = os.environ.get("USER", "YOUR_USER")
    project = str(_PROJECT_DIR)
    venv_py = str(_VENV_PYTHON)
    log_dir = project + "/logs"

    print()
    print("Add these lines to your crontab (run `crontab -e`):")
    print()
    print("\033[36m" + "─" * 70 + "\033[0m")
    print(f"0 7 * * *   {venv_py} {project}/refresh_stats.py >> {log_dir}/refresh_stats.cron.log 2>&1")
    print(f"5 7 * * *   {venv_py} {project}/enrich_inbox.py >> {log_dir}/enrich_inbox.cron.log 2>&1")
    print(f"0 6 1 */3 * {venv_py} {project}/refresh_brand_voice.py >> {log_dir}/refresh_brand_voice.cron.log 2>&1")
    print("\033[36m" + "─" * 70 + "\033[0m")
    print()
    info("On macOS, cron needs Full Disk Access for the bash binary —")
    info("System Settings → Privacy & Security → Full Disk Access → add /usr/sbin/cron.")


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #


def main() -> int:
    header("Google Workspace MCP — Setup Wizard")
    info(
        "This walks you through the 5 setup phases. Each is skippable —\n"
        "you can re-run this wizard anytime to add what you skipped before."
    )

    if not _VENV_PYTHON.exists():
        warn(f"Project venv not found at {_VENV_PYTHON}.")
        info("Run `./install.sh` first to create the venv.")
        return 1

    cfg = load_config()

    # 1. OAuth (required)
    if not step_oauth():
        warn("Skipping remaining steps until OAuth is complete.")
        return 1

    # 2. Anthropic (optional)
    cfg = step_anthropic(cfg)

    # 3. Maps (optional)
    cfg = step_maps(cfg)

    # 4. Telemetry opt-in (optional)
    cfg = step_telemetry(cfg)

    # 5. Cron (optional)
    step_cron()

    # Final verification.
    header("Setup complete")
    info("Next steps:")
    info("  • Restart Cowork so the MCP picks up the new config.")
    info("  • In Cowork, ask me 'list my Google calendars' to verify auth.")
    info("  • If you set up Anthropic: ask 'check my anthropic key'.")
    info("  • If you set up Maps: ask 'check my maps api key'.")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
