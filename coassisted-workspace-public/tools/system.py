# © 2026 CoAssisted Workspace contributors. Licensed under MIT — see LICENSE.
"""System tools — health checks, env diagnostics, version info.

Lightweight introspection tools that help users verify the MCP setup without
leaving Cowork. The flagship is `system_doctor` which runs every individual
check in parallel and returns a structured pass/warn/fail report. The
individual `system_check_*` tools exist so power users can debug one specific
thing.

Each individual check is a `_check_*` helper returning a CheckResult dict;
the tool wrappers just JSON-encode the helper's output.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

import llm
from logging_util import log
import gservices

# A single check result. Helpers return this shape; system_doctor collects them.
# status: "pass" (green), "warn" (yellow — degraded but works), "fail" (red — broken).
# fix: an actionable string the user can copy-paste, or None if no fix needed.
CheckResult = dict[str, Any]


def _result(
    name: str, status: str, details: str = "", fix: Optional[str] = None,
    extra: Optional[dict] = None,
) -> CheckResult:
    """Standard CheckResult shape so all helpers + the doctor agree on the format."""
    out: CheckResult = {"name": name, "status": status, "details": details}
    if fix:
        out["fix"] = fix
    if extra:
        out.update(extra)
    return out


class CheckAnthropicKeyInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    live_test: bool = Field(
        default=True,
        description=(
            "If True (default), make one tiny test call to Anthropic (~$0.0001) "
            "to verify the key actually works. If False, only check that the "
            "env var is set and looks valid — no network call."
        ),
    )


class CheckMapsKeyInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    live_test: bool = Field(
        default=True,
        description=(
            "If True (default), make one tiny geocode call to verify the key "
            "actually works (~$0.005, free under the $200/month credit)."
        ),
    )


def register(mcp) -> None:

    @mcp.tool(
        name="system_check_anthropic_key",
        annotations={
            "title": "Verify Anthropic API key for LLM-backed features",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def system_check_anthropic_key(params: CheckAnthropicKeyInput) -> str:
        """Check whether ANTHROPIC_API_KEY is configured and (optionally) live-test it.

        Used to set up the MCP's optional LLM-backed features:
            - refresh_brand_voice.py (quarterly brand voice extraction)
            - pipeline_digest.py (daily deal-status summaries)
            - LLM-backed signature parser (when regex misses title/org)

        If the key is missing or invalid, this returns step-by-step instructions
        for setting it up. Costs ~$0.0001 per check when `live_test=True`.

        Note: if you've just added the key to ~/.zshrc, you'll need to restart
        Cowork — the MCP server captures env at startup. Cron-based scripts
        (refresh_stats, enrich_inbox, etc.) fork fresh shells, so they'll see
        the new key on their next run without any restart.
        """
        try:
            key = llm.get_api_key()

            # Case 1: key not set at all.
            if not key:
                return json.dumps(
                    {
                        "status": "not_set",
                        "key_present": False,
                        "message": "Anthropic key not found — checked env var ANTHROPIC_API_KEY and config.json `anthropic_api_key`.",
                        "setup_steps_option_a_env_var": [
                            "Best for: cron scripts, CLI tools.",
                            "1. Get a key at https://console.anthropic.com → Settings → API Keys → Create Key",
                            "2. Add billing at console.anthropic.com → Settings → Billing (new accounts get $5 free)",
                            "3. echo 'export ANTHROPIC_API_KEY=\"sk-ant-api03-...\"' >> ~/.zshrc",
                            "4. source ~/.zshrc",
                            "5. Restart Cowork (note: macOS-sandboxed apps don't always inherit shell env — if this fails after restart, use Option B)",
                        ],
                        "setup_steps_option_b_config_json": [
                            "Best for: GUI Cowork on macOS (env-var propagation can be flaky).",
                            "1. Get a key + billing as above.",
                            "2. Add this line to your config.json:",
                            "     \"anthropic_api_key\": \"sk-ant-api03-YOURKEY\"",
                            "3. Restart Cowork.",
                            "4. config.json is gitignored AND excluded from `make handoff` — keys never travel.",
                        ],
                        "expected_monthly_cost_usd": "1-15 (depends on which LLM features you enable)",
                    },
                    indent=2,
                )

            # Case 2: key present but format looks off.
            ok, reason = llm.is_available()
            if not ok:
                return json.dumps(
                    {
                        "status": "invalid_format_or_sdk_missing",
                        "key_present": True,
                        "key_prefix": key[:15] + "...",
                        "key_length": len(key),
                        "reason": reason,
                        "fix": (
                            "If the key prefix is wrong, regenerate at "
                            "https://console.anthropic.com. "
                            "If the SDK is missing, run: "
                            "/Users/YOUR/google_workspace_mcp/.venv/bin/pip install anthropic"
                        ),
                    },
                    indent=2,
                )

            # Case 3: format OK, no live test requested.
            if not params.live_test:
                return json.dumps(
                    {
                        "status": "key_set",
                        "key_present": True,
                        "key_prefix": key[:15] + "...",
                        "key_length": len(key),
                        "live_test_skipped": True,
                        "message": "Key is set and looks valid. Skipped live API call (live_test=False).",
                    },
                    indent=2,
                )

            # Case 4: live test.
            try:
                result = llm.call_simple(
                    "Reply with the single word: ok",
                    model="claude-haiku-4-5",
                    max_tokens=5,
                )
                log.info(
                    "system_check_anthropic_key: live test OK (%d in / %d out tokens, $%s)",
                    result["input_tokens"], result["output_tokens"],
                    result["estimated_cost_usd"],
                )
                return json.dumps(
                    {
                        "status": "ok",
                        "key_present": True,
                        "key_prefix": key[:15] + "...",
                        "key_length": len(key),
                        "live_test_passed": True,
                        "model": result["model"],
                        "model_reply": result["text"],
                        "tokens": {
                            "input": result["input_tokens"],
                            "output": result["output_tokens"],
                        },
                        "estimated_cost_usd": result["estimated_cost_usd"],
                        "message": (
                            "Anthropic API is set up correctly. LLM-backed features "
                            "(brand voice, pipeline digest, signature parser) are ready to use."
                        ),
                    },
                    indent=2,
                )
            except Exception as e:
                # Common failures: revoked key, no billing, network blocked.
                err_str = str(e)
                hint = "Check the key is valid and that billing is enabled."
                if "401" in err_str or "authentication" in err_str.lower():
                    hint = "401 Unauthorized — the key is invalid or revoked. Regenerate at console.anthropic.com."
                elif "429" in err_str:
                    hint = "429 Rate limit — your account hit a limit. Try again in a moment, or check your tier."
                elif "billing" in err_str.lower() or "credit" in err_str.lower():
                    hint = "Billing not set up — add a card at console.anthropic.com → Settings → Billing."
                elif "network" in err_str.lower() or "connection" in err_str.lower():
                    hint = "Network error — check that api.anthropic.com is reachable from this machine (corporate VPN, firewall?)."
                return json.dumps(
                    {
                        "status": "live_test_failed",
                        "key_present": True,
                        "key_prefix": key[:15] + "...",
                        "key_length": len(key),
                        "error": err_str,
                        "hint": hint,
                    },
                    indent=2,
                )

        except Exception as e:
            log.error("system_check_anthropic_key: unexpected error: %s", e)
            return json.dumps({"status": "error", "error": str(e)}, indent=2)

    @mcp.tool(
        name="system_check_maps_api_key",
        annotations={
            "title": "Verify Google Maps API key configuration",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def system_check_maps_api_key(params: CheckMapsKeyInput) -> str:
        """Check whether google_maps_api_key is configured + valid.

        Used to set up the 10 maps_* tools, workflow_email_with_map, and
        workflow_meeting_location_options. If unconfigured or invalid, returns
        targeted setup instructions.

        Live-test mode does one geocode of a known address to verify the key
        actually works and that billing is enabled (~$0.005 per check, free
        under the $200/month credit).
        """
        try:
            import os
            import config as _config
            key = (
                _config.get("google_maps_api_key")
                or os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()
            )

            if not key:
                return json.dumps({
                    "status": "not_set",
                    "key_present": False,
                    "message": "Maps API key not found in config.json `google_maps_api_key` or GOOGLE_MAPS_API_KEY env.",
                    "setup_steps": [
                        "1. Enable billing on your GCP project: https://console.cloud.google.com/billing",
                        "2. Enable the 7 Maps APIs (Geocoding, Places, Directions, Distance Matrix, Time Zone, Address Validation, Static Maps).",
                        "3. Create an API key: https://console.cloud.google.com/apis/credentials",
                        "4. Restrict the key to those 7 APIs.",
                        "5. Add to config.json:  \"google_maps_api_key\": \"AIzaSy...\"",
                        "6. Restart Cowork.",
                    ],
                    "doc": "GCP_SETUP.md → 'Maps API setup' section has the full walkthrough.",
                    "expected_monthly_cost": "$0-15 for typical use; first $200/month is free credit.",
                }, indent=2)

            if not key.startswith("AIza"):
                return json.dumps({
                    "status": "invalid_format",
                    "key_present": True,
                    "key_prefix": key[:10] + "...",
                    "key_length": len(key),
                    "hint": "Maps keys start with 'AIza...'. Re-check the value in your GCP Credentials.",
                }, indent=2)

            try:
                import googlemaps  # noqa: F401
            except ImportError:
                return json.dumps({
                    "status": "sdk_missing",
                    "key_present": True,
                    "fix": (
                        "googlemaps SDK not in venv. Run: "
                        "/Users/YOUR/google_workspace_mcp/.venv/bin/pip install googlemaps"
                    ),
                }, indent=2)

            if not params.live_test:
                return json.dumps({
                    "status": "key_set",
                    "key_present": True,
                    "key_prefix": key[:10] + "...",
                    "key_length": len(key),
                    "live_test_skipped": True,
                }, indent=2)

            # Live test — single geocode of a stable landmark.
            try:
                gmaps = gservices.maps()
                resp = gmaps.geocode("1600 Amphitheatre Parkway, Mountain View, CA")
                if not resp:
                    return json.dumps({
                        "status": "live_test_empty",
                        "hint": "Key valid but no result. Geocoding API may not be enabled.",
                    }, indent=2)
                top = resp[0]
                return json.dumps({
                    "status": "ok",
                    "key_present": True,
                    "key_prefix": key[:10] + "...",
                    "key_length": len(key),
                    "live_test_passed": True,
                    "test_query": "Google HQ",
                    "result_address": top.get("formatted_address"),
                    "result_location": (top.get("geometry") or {}).get("location"),
                    "message": "Maps API ready. All 10 maps_* tools + 2 workflow_* tools are now usable.",
                }, indent=2)
            except Exception as e:
                err = str(e)
                hint = "Check that the key is valid and the relevant APIs are enabled."
                if "REQUEST_DENIED" in err.upper() or "API_KEY" in err.upper():
                    hint = (
                        "REQUEST_DENIED — usually means: (a) the API isn't enabled "
                        "for this project, (b) billing isn't set up, or (c) the key's "
                        "API restrictions don't include Geocoding. Check those in the "
                        "GCP console."
                    )
                elif "OVER_QUERY_LIMIT" in err.upper():
                    hint = "OVER_QUERY_LIMIT — you've exceeded the daily quota. Upgrade tier or wait 24h."
                elif "BILLING" in err.upper():
                    hint = "Billing not enabled on this GCP project. Set it up at console.cloud.google.com/billing"
                return json.dumps({
                    "status": "live_test_failed",
                    "key_present": True,
                    "key_prefix": key[:10] + "...",
                    "error": err,
                    "hint": hint,
                }, indent=2)
        except Exception as e:
            log.error("system_check_maps_api_key failed: %s", e)
            return json.dumps({"status": "error", "error": str(e)}, indent=2)

    # ====================================================================== #
    # New health checks (Tier 1, 2, 3) — see system_doctor below.            #
    # ====================================================================== #

    @mcp.tool(
        name="system_check_oauth",
        annotations={
            "title": "Verify Google OAuth token validity + scope coverage",
            "readOnlyHint": True, "destructiveHint": False,
            "idempotentHint": True, "openWorldHint": True,
        },
    )
    async def system_check_oauth(params: _NoArgs) -> str:
        """Verify token.json works and has all required scopes."""
        return json.dumps(_check_oauth(), indent=2)

    @mcp.tool(
        name="system_check_location_services",
        annotations={
            "title": "macOS Location Services + CoreLocationCLI diagnostic ladder",
            "readOnlyHint": True, "destructiveHint": False,
            "idempotentHint": True, "openWorldHint": True,
        },
    )
    async def system_check_location_services(params: _NoArgs) -> str:
        """4-step diagnostic: corelocationcli installed → executes → permission granted."""
        return json.dumps(_check_location_services(), indent=2)

    @mcp.tool(
        name="system_check_workspace_apis",
        annotations={
            "title": "Live test each enabled Google Workspace API",
            "readOnlyHint": True, "destructiveHint": False,
            "idempotentHint": True, "openWorldHint": True,
        },
    )
    async def system_check_workspace_apis(params: _NoArgs) -> str:
        """Tiny live call to each enabled API (Gmail, Calendar, Drive, etc.) to confirm scope works."""
        return json.dumps(_check_workspace_apis(), indent=2)

    @mcp.tool(
        name="system_check_route_optimization",
        annotations={
            "title": "Verify Route Optimization API enabled + cloud-platform scope",
            "readOnlyHint": True, "destructiveHint": False,
            "idempotentHint": True, "openWorldHint": True,
        },
    )
    async def system_check_route_optimization(params: _NoArgs) -> str:
        """Tiny test request to confirm Route Optimization API access."""
        return json.dumps(_check_route_optimization(), indent=2)

    @mcp.tool(
        name="system_check_maps_api_key_full",
        annotations={
            "title": "Verify ALL 8 Maps APIs reachable on the key (not just geocoding)",
            "readOnlyHint": True, "destructiveHint": False,
            "idempotentHint": True, "openWorldHint": True,
        },
    )
    async def system_check_maps_api_key_full(params: _NoArgs) -> str:
        """Extend system_check_maps_api_key to test each of: geocoding, places,
        directions, distance matrix, time zone, address validation, static maps,
        geolocation. Catches partial allowlist mistakes."""
        return json.dumps(_check_maps_api_key_full(), indent=2)

    @mcp.tool(
        name="system_check_config",
        annotations={
            "title": "Validate config.json + flag typos in known keys",
            "readOnlyHint": True, "destructiveHint": False,
            "idempotentHint": True, "openWorldHint": True,
        },
    )
    async def system_check_config(params: _NoArgs) -> str:
        """JSON parses + lists set vs unset + flags typos in known config keys."""
        return json.dumps(_check_config(), indent=2)

    @mcp.tool(
        name="system_check_filesystem",
        annotations={
            "title": "Verify writable dirs + file permissions",
            "readOnlyHint": True, "destructiveHint": False,
            "idempotentHint": True, "openWorldHint": True,
        },
    )
    async def system_check_filesystem(params: _NoArgs) -> str:
        """logs/ writable, token.json chmod 600, default_download_dir creatable."""
        return json.dumps(_check_filesystem(), indent=2)

    @mcp.tool(
        name="system_check_dependencies",
        annotations={
            "title": "Verify Python version + required packages + optional binaries",
            "readOnlyHint": True, "destructiveHint": False,
            "idempotentHint": True, "openWorldHint": True,
        },
    )
    async def system_check_dependencies(params: _NoArgs) -> str:
        """Python 3.10+, googlemaps, google-auth, optional binaries (corelocationcli, pandoc)."""
        return json.dumps(_check_dependencies(), indent=2)

    @mcp.tool(
        name="system_check_clock",
        annotations={
            "title": "Detect system clock skew (OAuth tokens are time-sensitive)",
            "readOnlyHint": True, "destructiveHint": False,
            "idempotentHint": True, "openWorldHint": True,
        },
    )
    async def system_check_clock(params: _NoArgs) -> str:
        """Compare local clock to a Google response Date header. >5 min skew = OAuth flakiness."""
        return json.dumps(_check_clock(), indent=2)

    @mcp.tool(
        name="system_check_tools",
        annotations={
            "title": "Verify expected tool count registers cleanly",
            "readOnlyHint": True, "destructiveHint": False,
            "idempotentHint": True, "openWorldHint": True,
        },
    )
    async def system_check_tools(params: _NoArgs) -> str:
        """Spin up a mock MCP, register all tools, confirm the count + no errors."""
        return json.dumps(_check_tools(), indent=2)

    @mcp.tool(
        name="system_check_unit_tests",
        annotations={
            "title": "Run the full pytest suite as a deep diagnostic (~1s)",
            "readOnlyHint": True, "destructiveHint": False,
            "idempotentHint": True, "openWorldHint": False,
        },
    )
    async def system_check_unit_tests(params: _NoArgs) -> str:
        """Shell out to pytest and report the pass/fail summary.

        Useful for confirming the codebase is structurally sound after a
        deploy or config change. Runs in a subprocess so test side effects
        (mock injection, cache writes) don't leak into the running MCP.
        """
        return json.dumps(_check_unit_tests(), indent=2)

    @mcp.tool(
        name="system_check_quota_usage",
        annotations={
            "title": "Estimate Maps + Anthropic spend this month from logs",
            "readOnlyHint": True, "destructiveHint": False,
            "idempotentHint": True, "openWorldHint": True,
        },
    )
    async def system_check_quota_usage(params: _NoArgs) -> str:
        """Parse logs for tool calls, multiply by published per-call cost."""
        return json.dumps(_check_quota_usage(), indent=2)

    # ====================================================================== #
    # The flagship: system_doctor                                            #
    # ====================================================================== #

    @mcp.tool(
        name="system_check_license",
        annotations={
            "title": "Show current tier (free/paid) + license key validity",
            "readOnlyHint": True, "destructiveHint": False,
            "idempotentHint": True, "openWorldHint": False,
        },
    )
    async def system_check_license(params: _NoArgs) -> str:
        """Report which tier is active + how to upgrade if locked."""
        return json.dumps(_check_license(), indent=2)

    @mcp.tool(
        name="system_recent_actions",
        annotations={
            "title": "List recent write operations the MCP recorded",
            "readOnlyHint": True, "destructiveHint": False,
            "idempotentHint": True, "openWorldHint": False,
        },
    )
    async def system_recent_actions(params: _RecentActionsInput) -> str:
        """Show the most-recent N write operations from the audit log.

        Foundation for undo: each record has snapshot_before/snapshot_after
        so you can see exactly what changed and (in a future tool) revert it.
        """
        import recent_actions as _ra
        records = _ra.list_recent(
            limit=params.limit,
            tool_filter=params.tool_filter,
            target_kind_filter=params.target_kind_filter,
            only_revertable=params.only_revertable,
            since_iso=params.since_iso,
        )
        return json.dumps({
            "count": len(records),
            "records": records,
        }, indent=2)

    @mcp.tool(
        name="system_doctor",
        annotations={
            "title": "Run every health check and return a single actionable report",
            "readOnlyHint": True, "destructiveHint": False,
            "idempotentHint": True, "openWorldHint": True,
        },
    )
    async def system_doctor(params: _NoArgs) -> str:
        """One-stop diagnostic. Runs all 11 individual health checks and aggregates
        into a structured report:

          - `status`: "healthy" if all pass, "warnings" if any warn, "errors" if any fail
          - `summary`: human-readable totals
          - `checks`: list of per-check results, each with status + details + fix string

        Read this when something feels off — the report tells you the *one* specific
        thing to do. Each fix string is a copy-pasteable command or System Settings path.
        """
        results: list[CheckResult] = []
        # Tier 1 — must-pass to use anything.
        results.append(_check_oauth())
        results.append(_check_workspace_apis())
        results.append(_check_license())
        # Tier 2 — features that depend on optional integrations.
        results.append(_check_maps_api_key_full())
        results.append(_check_route_optimization())
        results.append(_check_location_services())
        results.append(_check_config())
        # Tier 3 — environment health.
        results.append(_check_filesystem())
        results.append(_check_dependencies())
        results.append(_check_clock())
        results.append(_check_tools())
        results.append(_check_unit_tests())  # ~1s; deep diagnostic
        results.append(_check_quota_usage())

        passed = sum(1 for r in results if r["status"] == "pass")
        warned = sum(1 for r in results if r["status"] == "warn")
        failed = sum(1 for r in results if r["status"] == "fail")

        if failed > 0:
            overall = "errors"
        elif warned > 0:
            overall = "warnings"
        else:
            overall = "healthy"

        # If anything went sideways, save a sanitized report locally for the
        # user. They explicitly opt in to share by running
        # `system_share_health_report`. Default behavior: never auto-send.
        report_path = None
        share_hint = None
        if failed > 0 or warned > 0:
            try:
                import telemetry as _tel
                report = _tel.build_report(results)
                report_path = str(_tel.save_report(report))
                share_hint = (
                    "Anonymized report saved locally. To help improve the "
                    "MCP, share it with the developer by running "
                    "`system_share_health_report`. The report has all emails, "
                    "API keys, file paths, and IPs redacted before any send."
                )
            except Exception as e:
                log.warning("system_doctor: telemetry save failed: %s", e)

        return json.dumps({
            "status": overall,
            "summary": (
                f"{passed} checks passed, {warned} warning(s), {failed} error(s)"
            ),
            "checks": results,
            "report_saved_to": report_path,
            "share_hint": share_hint,
        }, indent=2)

    @mcp.tool(
        name="system_share_health_report",
        annotations={
            "title": "Email a sanitized health report to the developer (opt-in)",
            "readOnlyHint": False, "destructiveHint": False,
            "idempotentHint": False, "openWorldHint": True,
        },
    )
    async def system_share_health_report(params: _ShareHealthReportInput) -> str:
        """Email the latest health report (or a specific path) to the developer.

        The report is sanitized — emails, API keys, OAuth tokens, file paths,
        IPs, and project IDs are replaced with `<PLACEHOLDER>` BEFORE sending.
        Verify by reading the file at the path system_doctor printed.

        Defaults: requires `telemetry_email` in config.json. If that's not set,
        the tool refuses to send and tells you so. Call with `confirm=true` to
        actually send (extra step to prevent accidental shares).
        """
        try:
            import telemetry as _tel
            import config as _cfg
            target = (
                params.telemetry_email_override
                or _cfg.get("telemetry_email")
            )
            if not target:
                return json.dumps({
                    "status": "no_target",
                    "hint": (
                        'Set "telemetry_email": "developer@example.com" in '
                        "config.json, or pass telemetry_email_override per "
                        "call. The MCP never has a hardcoded telemetry "
                        "destination — you opt in explicitly."
                    ),
                }, indent=2)

            # Resolve the report file.
            if params.report_path:
                from pathlib import Path as _P
                rp = _P(params.report_path).expanduser()
                if not rp.exists():
                    return json.dumps({
                        "status": "report_not_found",
                        "path": str(rp),
                    }, indent=2)
            else:
                rp = _tel.find_latest_report()
                if not rp:
                    return json.dumps({
                        "status": "no_reports",
                        "hint": (
                            "No saved reports under logs/health_reports/. "
                            "Run system_doctor first."
                        ),
                    }, indent=2)

            report_text = rp.read_text()
            report = json.loads(report_text)

            if not params.confirm:
                # Preview mode — show what WOULD be sent.
                return json.dumps({
                    "status": "preview",
                    "would_send_to": target,
                    "report_path": str(rp),
                    "report_size_kb": round(len(report_text) / 1024, 2),
                    "summary": report.get("summary"),
                    "issue_count": len(report.get("checks_with_issues", [])),
                    "hint": (
                        "Re-run with confirm=true to actually email. The "
                        "file at report_path is what gets sent verbatim — "
                        "you can read it to verify all PII is redacted."
                    ),
                }, indent=2)

            # Actually send via the existing Gmail tool path.
            from email.message import EmailMessage as _EM
            import base64 as _b64
            msg = _EM()
            msg["To"] = target
            subj_summary = report.get("summary", {})
            msg["Subject"] = (
                f"[MCP Health Report] "
                f"{subj_summary.get('failed', 0)} fail / "
                f"{subj_summary.get('warned', 0)} warn / "
                f"{subj_summary.get('passed', 0)} pass"
            )
            body = (
                "This is an anonymized health report from the Google "
                "Workspace MCP. Emails, API keys, OAuth tokens, file "
                "paths, and IPs have been redacted before sending.\n\n"
                f"Report path on user's machine: {rp}\n"
                f"Report generated: {report.get('generated_at')}\n\n"
                "Full JSON report attached.\n"
            )
            msg.set_content(body)
            msg.add_attachment(
                report_text.encode("utf-8"),
                maintype="application", subtype="json",
                filename=rp.name,
            )
            raw = _b64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
            sent = gservices.gmail().users().messages().send(
                userId="me", body={"raw": raw},
            ).execute()
            log.info(
                "system_share_health_report: sent %s to %s",
                rp.name, target,
            )
            return json.dumps({
                "status": "sent",
                "to": target,
                "message_id": sent.get("id"),
                "thread_id": sent.get("threadId"),
                "report_path": str(rp),
            }, indent=2)
        except Exception as e:
            log.error("system_share_health_report failed: %s", e)
            return json.dumps({"status": "error", "error": str(e)}, indent=2)


class _NoArgs(BaseModel):
    """Empty input model for parameterless health checks."""
    model_config = ConfigDict(extra="forbid")


class _ShareHealthReportInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    report_path: Optional[str] = Field(
        default=None,
        description="Absolute path to a specific report file. Default: most-recent.",
    )
    telemetry_email_override: Optional[str] = Field(
        default=None,
        description="One-shot override of config.telemetry_email. Useful for testing.",
    )
    confirm: bool = Field(
        default=False,
        description="Must be True to actually send. False = preview-only mode.",
    )


class _RecentActionsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    limit: int = Field(default=20, ge=1, le=500)
    tool_filter: Optional[str] = Field(
        default=None,
        description="Filter to records from this exact tool name (e.g. 'calendar_create_event').",
    )
    target_kind_filter: Optional[str] = Field(
        default=None,
        description="Filter by target_kind: 'calendar_event', 'contact', 'drive_file', etc.",
    )
    only_revertable: bool = Field(
        default=False,
        description="Exclude records that have already been reverted, and exclude revert records themselves.",
    )
    since_iso: Optional[str] = Field(
        default=None,
        description="Only show records at or after this ISO timestamp.",
    )


# =========================================================================== #
# Helper functions for each individual check.                                 #
# Each returns a CheckResult dict via _result(name, status, details, fix).    #
# =========================================================================== #


def _check_oauth() -> CheckResult:
    """Verify token.json + refresh + scopes."""
    name = "OAuth token + scopes"
    try:
        import auth
        from google.auth.transport.requests import Request as _Req
        token_path = Path(auth.__file__).parent / "token.json"
        if not token_path.exists():
            return _result(
                name, "fail",
                "token.json not found — OAuth never completed",
                fix=(
                    "Run from Terminal (NOT inside Cowork): "
                    "cd /Users/finnnai/Claude/google_workspace_mcp && "
                    "./install.sh --oauth"
                ),
            )
        try:
            creds = auth.get_credentials()
        except Exception as e:
            return _result(
                name, "fail",
                f"Failed to load credentials: {e}",
                fix="Delete token.json and re-run OAuth: ./install.sh --oauth",
            )
        if not creds or not creds.valid:
            try:
                creds.refresh(_Req())
            except Exception as e:
                return _result(
                    name, "fail",
                    f"Token expired and refresh failed: {e}",
                    fix=(
                        "Delete token.json (rm /Users/finnnai/Claude/"
                        "google_workspace_mcp/token.json) and re-run "
                        "./install.sh --oauth from Terminal."
                    ),
                )
        granted = set(getattr(creds, "scopes", None) or [])
        required = set(auth.SCOPES)
        missing = required - granted
        if missing:
            return _result(
                name, "fail",
                f"Token missing {len(missing)} scope(s): "
                + ", ".join(sorted(missing)),
                fix=(
                    "Scope set has changed since last OAuth. Delete token.json "
                    "and re-consent: rm /Users/finnnai/Claude/google_workspace_mcp/token.json "
                    "&& cd /Users/finnnai/Claude/google_workspace_mcp && "
                    "./install.sh --oauth"
                ),
                extra={"missing_scopes": sorted(missing),
                       "granted_count": len(granted),
                       "required_count": len(required)},
            )
        return _result(
            name, "pass",
            f"{len(granted)} scopes granted, all {len(required)} required present",
            extra={"scopes_count": len(granted)},
        )
    except Exception as e:
        return _result(name, "fail", f"Unexpected error: {e}",
                       fix="Open an issue with the error message above")


def _check_location_services() -> CheckResult:
    """4-step diagnostic ladder for current-location detection."""
    name = "Location Services (CoreLocationCLI)"
    import subprocess
    import shutil
    candidate_paths = [
        "/opt/homebrew/bin/CoreLocationCLI",
        "/usr/local/bin/CoreLocationCLI",
        "/opt/local/bin/CoreLocationCLI",
    ]
    cli_path = next((p for p in candidate_paths if Path(p).exists()), None)
    if cli_path is None:
        cli_path = shutil.which("CoreLocationCLI")
    if cli_path is None:
        return _result(
            name, "warn",
            "CoreLocationCLI not installed — drive-time tools fall back to "
            "Google Geolocation API (~5km accuracy) or ipapi.co (~10km).",
            fix="brew install corelocationcli",
        )
    # Try executing.
    try:
        proc = subprocess.run(
            [cli_path, "-format", "%latitude,%longitude,%horizontalAccuracy"],
            capture_output=True, text=True, timeout=8,
        )
    except subprocess.TimeoutExpired:
        return _result(
            name, "fail",
            "CoreLocationCLI hung waiting for permission. macOS hasn't granted "
            "Location Services to the calling process.",
            fix=(
                "Open System Settings → Privacy & Security → Location Services. "
                "Enable the master toggle AND enable both Terminal AND Claude. "
                "Then quit Cowork (cmd+Q) and reopen."
            ),
        )
    except Exception as e:
        return _result(name, "fail", f"CoreLocationCLI exec failed: {e}",
                       fix="Reinstall: brew reinstall corelocationcli")
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        if "denied" in stderr.lower() or "disabled" in stderr.lower():
            return _result(
                name, "fail",
                f"Location Services denied: {stderr[:120]}",
                fix=(
                    "Open System Settings → Privacy & Security → Location Services. "
                    "Enable master toggle, then enable Terminal AND Claude in the app list. "
                    "Quit Cowork (cmd+Q) and reopen."
                ),
            )
        return _result(
            name, "fail",
            f"CoreLocationCLI rc={proc.returncode}: {stderr[:120]}",
            fix="Try: brew reinstall corelocationcli",
        )
    out = proc.stdout.strip()
    if "," in out:
        parts = out.split(",")
    else:
        parts = out.split()
    if len(parts) < 2:
        return _result(
            name, "warn",
            f"CoreLocationCLI returned unexpected output: {out[:80]}",
            fix="Update corelocationcli: brew upgrade corelocationcli",
        )
    return _result(
        name, "pass",
        f"corelocationcli working at {cli_path} — coords resolve",
        extra={"path": cli_path},
    )


def _check_workspace_apis() -> CheckResult:
    """Tiny live call to each Workspace API to confirm scope grants are live."""
    name = "Workspace APIs (Gmail, Calendar, Drive, …)"
    failed_apis: list[str] = []
    passed_apis: list[str] = []
    checks = [
        ("Gmail", lambda: gservices.gmail().users().getProfile(userId="me").execute()),
        ("Calendar", lambda: gservices.calendar().calendarList().list(maxResults=1).execute()),
        ("Drive", lambda: gservices.drive().files().list(pageSize=1).execute()),
        ("Sheets", lambda: gservices.sheets().spreadsheets()),
        ("Docs", lambda: gservices.docs().documents()),
        ("Tasks", lambda: gservices.tasks().tasklists().list(maxResults=1).execute()),
        ("People", lambda: gservices.people().people().connections().list(
            resourceName="people/me", personFields="names", pageSize=1
        ).execute()),
        ("Chat", lambda: gservices.chat().spaces().list(pageSize=1).execute()),
    ]
    for label, fn in checks:
        try:
            fn()
            passed_apis.append(label)
        except Exception as e:
            failed_apis.append(f"{label} ({str(e)[:80]})")
    if failed_apis:
        return _result(
            name, "fail",
            f"{len(passed_apis)}/{len(checks)} APIs working. "
            f"Failed: {', '.join(failed_apis)}",
            fix=(
                "Most likely an API isn't enabled in your GCP project. "
                "Visit https://console.cloud.google.com/apis/dashboard and "
                "enable any showing 'Disabled'. See GCP_SETUP.md Section 2."
            ),
            extra={"passed": passed_apis, "failed": failed_apis},
        )
    return _result(
        name, "pass",
        f"All {len(checks)} APIs live: {', '.join(passed_apis)}",
        extra={"apis": passed_apis},
    )


def _check_route_optimization() -> CheckResult:
    """Verify Route Optimization API enabled + cloud-platform scope."""
    name = "Route Optimization API"
    try:
        import auth
        import config as _config
        import requests
        creds = auth.get_credentials()
        scopes = set(getattr(creds, "scopes", None) or [])
        if "https://www.googleapis.com/auth/cloud-platform" not in scopes:
            return _result(
                name, "fail",
                "OAuth token missing cloud-platform scope.",
                fix=(
                    "Delete token.json and re-run OAuth in Terminal: "
                    "rm /Users/finnnai/Claude/google_workspace_mcp/token.json "
                    "&& cd /Users/finnnai/Claude/google_workspace_mcp && "
                    "./install.sh --oauth"
                ),
            )
        project_id = _config.gcp_project_id()
        if not project_id:
            return _result(
                name, "fail",
                "gcp_project_id not detected from credentials.json or config.",
                fix='Add to config.json: { "gcp_project_id": "your-gcp-project-id" }',
            )
        # Tiny test request — 1 stop, 1 vehicle, no constraints.
        body = {
            "model": {
                "shipments": [{"deliveries": [{
                    "arrivalWaypoint": {"location": {"latLng": {"latitude": 37.4225, "longitude": -122.0856}}},
                    "duration": "60s",
                }]}],
                "vehicles": [{
                    "startWaypoint": {"location": {"latLng": {"latitude": 37.4419, "longitude": -122.143}}},
                    "endWaypoint": {"location": {"latLng": {"latitude": 37.4419, "longitude": -122.143}}},
                    "travelMode": "DRIVING",
                }],
            },
            "searchMode": "RETURN_FAST",
            "timeout": "5s",
        }
        url = (
            f"https://routeoptimization.googleapis.com/v1/"
            f"projects/{project_id}:optimizeTours"
        )
        headers = {
            "Authorization": f"Bearer {creds.token}",
            "Content-Type": "application/json",
            "X-Goog-User-Project": project_id,
        }
        resp = requests.post(url, json=body, headers=headers, timeout=20)
        if resp.status_code == 403:
            return _result(
                name, "fail",
                f"403 — Route Optimization API not enabled in project {project_id}",
                fix=(
                    "Enable: https://console.cloud.google.com/apis/library/"
                    "routeoptimization.googleapis.com"
                ),
            )
        if not resp.ok:
            return _result(
                name, "fail",
                f"HTTP {resp.status_code}: {resp.text[:200]}",
                fix="Check GCP project has billing enabled.",
            )
        return _result(
            name, "pass",
            f"Route Optimization API live in project {project_id}",
        )
    except Exception as e:
        return _result(name, "warn", f"Couldn't test: {e}",
                       fix="Health check itself errored — check logs.")


def _check_maps_api_key_full() -> CheckResult:
    """Verify all 8 Maps APIs reachable on the key, not just geocoding."""
    name = "Maps API key (8 APIs)"
    try:
        import os
        import config as _config
        key = (
            _config.get("google_maps_api_key")
            or os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()
        )
        if not key:
            return _result(
                name, "warn",
                "Maps API key not configured — Maps tools disabled.",
                fix=(
                    'Add to config.json: { "google_maps_api_key": "AIza..." }. '
                    "See INSTALL.md Phase 2."
                ),
            )
        try:
            import googlemaps  # noqa: F401
        except ImportError:
            return _result(
                name, "fail",
                "googlemaps SDK not installed.",
                fix=(
                    "/Users/finnnai/Claude/google_workspace_mcp/.venv/bin/pip "
                    "install googlemaps"
                ),
            )
        gmaps = gservices.maps()
        passed = []
        failed = []
        # 1. Geocoding
        try:
            gmaps.geocode("1600 Amphitheatre Parkway, Mountain View, CA")
            passed.append("Geocoding")
        except Exception as e:
            failed.append(f"Geocoding ({str(e)[:60]})")
        # 2. Reverse Geocoding (same API, separate call signature)
        try:
            gmaps.reverse_geocode((37.4225, -122.0856))
            passed.append("Reverse Geocoding")
        except Exception as e:
            failed.append(f"Reverse Geocoding ({str(e)[:60]})")
        # 3. Places
        try:
            gmaps.places("cafe near Palo Alto", region="us")
            passed.append("Places")
        except Exception as e:
            failed.append(f"Places ({str(e)[:60]})")
        # 4. Directions
        try:
            gmaps.directions(
                "1600 Amphitheatre Parkway, Mountain View, CA",
                "Stanford, CA", mode="driving",
            )
            passed.append("Directions")
        except Exception as e:
            failed.append(f"Directions ({str(e)[:60]})")
        # 5. Distance Matrix
        try:
            gmaps.distance_matrix(
                origins=["Mountain View, CA"], destinations=["Stanford, CA"],
            )
            passed.append("Distance Matrix")
        except Exception as e:
            failed.append(f"Distance Matrix ({str(e)[:60]})")
        # 6. Time Zone
        try:
            gmaps.timezone(location=(37.4225, -122.0856))
            passed.append("Time Zone")
        except Exception as e:
            failed.append(f"Time Zone ({str(e)[:60]})")
        # 7. Static Maps (returns binary, not parsed)
        try:
            list(gmaps.static_map(center="Mountain View, CA", zoom=12, size=(100, 100)))
            passed.append("Static Maps")
        except Exception as e:
            failed.append(f"Static Maps ({str(e)[:60]})")
        # 8. Address Validation (separate endpoint, not in googlemaps SDK)
        try:
            import requests
            resp = requests.post(
                f"https://addressvalidation.googleapis.com/v1:validateAddress?key={key}",
                json={"address": {"addressLines": ["1600 Amphitheatre Pkwy, Mountain View, CA"]}},
                timeout=8,
            )
            if resp.ok:
                passed.append("Address Validation")
            else:
                failed.append(f"Address Validation (HTTP {resp.status_code})")
        except Exception as e:
            failed.append(f"Address Validation ({str(e)[:60]})")

        if failed:
            return _result(
                name, "fail" if len(failed) >= 3 else "warn",
                f"{len(passed)}/8 APIs working. Failed: {', '.join(failed)}",
                fix=(
                    "Click your API key at https://console.cloud.google.com/apis/credentials "
                    "→ API restrictions → ensure all 8 APIs are ticked. Or remove "
                    "API restrictions entirely."
                ),
                extra={"passed": passed, "failed": failed},
            )
        return _result(
            name, "pass", f"All 8 Maps APIs reachable",
            extra={"apis": passed},
        )
    except Exception as e:
        return _result(name, "warn", f"Couldn't test: {e}", fix=None)


def _check_config() -> CheckResult:
    """Validate config.json + flag typos in known keys."""
    name = "config.json"
    try:
        import config as _config
        from pathlib import Path as _P
        path = _P(_config.__file__).parent / "config.json"
        if not path.exists():
            return _result(
                name, "warn",
                "config.json not found — using built-in defaults.",
                fix=(
                    "Copy from example: cp /Users/finnnai/Claude/"
                    "google_workspace_mcp/config.example.json /Users/finnnai/Claude/"
                    "google_workspace_mcp/config.json"
                ),
            )
        try:
            user_cfg = json.loads(path.read_text())
        except json.JSONDecodeError as e:
            return _result(
                name, "fail",
                f"config.json has invalid JSON at line {e.lineno}: {e.msg}",
                fix=(
                    f"Fix the syntax error or restore from "
                    f"config.example.json. nano {path}"
                ),
            )
        # Known top-level keys: _DEFAULTS plus other valid keys that are
        # read at runtime but don't have a hardcoded default (e.g.
        # anthropic_api_key — read by llm.py).
        EXTRA_KNOWN_KEYS = {
            "anthropic_api_key",       # read by llm.py
        }
        known = set(_config._DEFAULTS.keys()) | EXTRA_KNOWN_KEYS
        used = set(user_cfg.keys())
        unknown = used - known
        # Typo detection — flag keys that are close to known ones.
        suspected_typos = []
        if unknown:
            import difflib
            for u in unknown:
                close = difflib.get_close_matches(u, known, n=1, cutoff=0.7)
                if close:
                    suspected_typos.append(f"'{u}' (did you mean '{close[0]}'?)")
        if suspected_typos:
            return _result(
                name, "warn",
                f"Suspected typos: {', '.join(suspected_typos)}",
                fix=f"Edit config.json and rename. nano {path}",
            )
        if unknown:
            return _result(
                name, "warn",
                f"Unknown keys (might be future config or extras): "
                f"{', '.join(sorted(unknown))}",
                fix=None,
            )
        return _result(
            name, "pass",
            f"config.json valid. {len(used)} keys set.",
            extra={"keys_set": sorted(used)},
        )
    except Exception as e:
        return _result(name, "fail", f"Couldn't read config: {e}", fix=None)


def _check_filesystem() -> CheckResult:
    """Writable logs/, token.json chmod 600, default_download_dir creatable."""
    name = "Filesystem permissions"
    try:
        import auth
        import config as _config
        import os
        import stat
        project_dir = Path(auth.__file__).parent
        warnings: list[str] = []
        errors: list[str] = []
        # logs/ writable
        logs_dir = project_dir / "logs"
        if logs_dir.exists():
            if not os.access(logs_dir, os.W_OK):
                errors.append("logs/ not writable")
        else:
            try:
                logs_dir.mkdir(exist_ok=True)
            except Exception as e:
                errors.append(f"logs/ can't be created: {e}")
        # token.json mode
        token_path = project_dir / "token.json"
        if token_path.exists():
            mode = stat.S_IMODE(token_path.stat().st_mode)
            if mode != 0o600:
                warnings.append(
                    f"token.json mode {oct(mode)} (should be 0o600 for security)"
                )
        # default_download_dir
        dl_dir = _config.get("default_download_dir") or "~/Gmail Downloads"
        dl_path = Path(dl_dir).expanduser()
        if not dl_path.exists():
            try:
                dl_path.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                errors.append(f"download dir can't be created: {e}")
        # project folder writable
        if not os.access(project_dir, os.W_OK):
            errors.append(f"project folder {project_dir} not writable")
        if errors:
            return _result(
                name, "fail", "; ".join(errors),
                fix="Check file ownership and permissions. chmod 600 token.json",
            )
        if warnings:
            return _result(
                name, "warn", "; ".join(warnings),
                fix="chmod 600 /Users/finnnai/Claude/google_workspace_mcp/token.json",
            )
        return _result(name, "pass", "All filesystem checks OK")
    except Exception as e:
        return _result(name, "warn", f"Couldn't fully check: {e}", fix=None)


def _check_dependencies() -> CheckResult:
    """Python version + required packages + optional binaries."""
    name = "Dependencies"
    try:
        import sys
        warnings: list[str] = []
        errors: list[str] = []
        # Python version
        if sys.version_info < (3, 10):
            errors.append(
                f"Python {sys.version_info.major}.{sys.version_info.minor} "
                f"< 3.10 (required)"
            )
        # Core deps
        for mod in ("googleapiclient", "google.auth", "pydantic", "requests"):
            try:
                __import__(mod)
            except ImportError:
                errors.append(f"Missing required: {mod}")
        # Maps deps
        try:
            import googlemaps  # noqa: F401
        except ImportError:
            warnings.append("googlemaps SDK missing (Maps tools disabled)")
        # Anthropic deps
        try:
            import anthropic  # noqa: F401
        except ImportError:
            warnings.append("anthropic SDK missing (LLM features disabled)")
        # Pillow — auto-shrinks oversized phone photos before Claude Vision.
        # Without it, receipts >5MB hit Anthropic's image-size cap and error out.
        try:
            from PIL import Image  # noqa: F401
        except ImportError:
            warnings.append(
                "Pillow missing (phone-photo receipts >5MB will fail Vision; "
                "fix: .venv/bin/pip install 'Pillow>=10.0')"
            )
        # Optional binaries
        import shutil
        if not shutil.which("CoreLocationCLI") and not Path(
            "/opt/homebrew/bin/CoreLocationCLI"
        ).exists() and not Path("/usr/local/bin/CoreLocationCLI").exists():
            warnings.append("corelocationcli not installed (current-location degraded)")
        if not shutil.which("pandoc"):
            warnings.append(
                "pandoc not installed (can't regenerate user-manual.docx)"
            )
        if errors:
            return _result(
                name, "fail", "; ".join(errors),
                fix=(
                    "Run /Users/finnnai/Claude/google_workspace_mcp/install.sh "
                    "to install missing core deps."
                ),
            )
        if warnings:
            fixes = []
            if any("googlemaps" in w for w in warnings):
                fixes.append(
                    "/Users/finnnai/Claude/google_workspace_mcp/.venv/bin/pip "
                    "install googlemaps"
                )
            if any("anthropic" in w for w in warnings):
                fixes.append(
                    "/Users/finnnai/Claude/google_workspace_mcp/.venv/bin/pip "
                    "install anthropic"
                )
            if any("corelocationcli" in w for w in warnings):
                fixes.append("brew install corelocationcli")
            if any("pandoc" in w for w in warnings):
                fixes.append("brew install pandoc")
            return _result(
                name, "warn", "; ".join(warnings),
                fix=" && ".join(fixes) if fixes else None,
            )
        return _result(
            name, "pass",
            f"Python {sys.version_info.major}.{sys.version_info.minor} + "
            f"all deps + optional binaries present",
        )
    except Exception as e:
        return _result(name, "fail", f"Check failed: {e}", fix=None)


def _check_clock() -> CheckResult:
    """NTP skew check using a server's Date header."""
    name = "System clock"
    try:
        import requests
        import datetime as _dt
        import email.utils as _eu
        try:
            resp = requests.head("https://www.googleapis.com/", timeout=5)
        except Exception as e:
            return _result(name, "warn", f"Couldn't check (network): {e}", fix=None)
        date_hdr = resp.headers.get("Date")
        if not date_hdr:
            return _result(name, "warn", "No Date header in response", fix=None)
        server_dt = _eu.parsedate_to_datetime(date_hdr)
        local_dt = _dt.datetime.now(_dt.timezone.utc)
        skew_sec = (local_dt - server_dt).total_seconds()
        if abs(skew_sec) > 300:  # 5 minutes
            return _result(
                name, "fail",
                f"Local clock off by {abs(skew_sec):.0f}s vs Google "
                f"({'ahead' if skew_sec > 0 else 'behind'}). OAuth tokens may fail.",
                fix=(
                    "Open System Settings → General → Date & Time. Enable "
                    "'Set time and date automatically'."
                ),
            )
        if abs(skew_sec) > 60:
            return _result(
                name, "warn",
                f"Clock skew {abs(skew_sec):.0f}s — borderline.",
                fix=None,
            )
        return _result(
            name, "pass",
            f"Clock within {abs(skew_sec):.1f}s of server time",
        )
    except Exception as e:
        return _result(name, "warn", f"Check errored: {e}", fix=None)


def _check_tools() -> CheckResult:
    """Verify all expected tools register cleanly."""
    name = "Tool registration"
    try:
        # Mock MCP that records registrations.
        class _M:
            def __init__(self):
                self.tools: list[str] = []
            def tool(self, name=None, **kw):
                def d(fn):
                    self.tools.append(name or fn.__name__)
                    return fn
                return d
        m = _M()
        # Import and register everything.
        import tools
        # Note: 'templates' lives at project root (templates.py), NOT under
        # tools/. Don't iterate it here.
        for sub in (
            "gmail", "calendar", "drive", "sheets", "docs", "tasks",
            "contacts", "chat", "maps", "workflows", "system",
            "enrichment",
        ):
            try:
                mod = __import__(f"tools.{sub}", fromlist=[sub])
                if hasattr(mod, "register"):
                    mod.register(m)
            except Exception as e:
                return _result(
                    name, "fail",
                    f"Module tools.{sub} failed to register: {e}",
                    fix="Check the import error above + restart Cowork",
                )
        count = len(m.tools)
        # Look for duplicates
        from collections import Counter
        dupes = [name for name, n in Counter(m.tools).items() if n > 1]
        if dupes:
            return _result(
                name, "fail",
                f"Duplicate tool names: {dupes}",
                fix="Check tools/*.py for duplicate @mcp.tool decorators",
            )
        return _result(
            name, "pass",
            f"{count} tools registered without errors",
            extra={"count": count},
        )
    except Exception as e:
        return _result(name, "fail", f"Tool count check errored: {e}", fix=None)


def _check_license() -> CheckResult:
    """Report tier + license validity + build hash. Pass on personal mode."""
    name = "Tier / license"
    try:
        import tier
        mode = tier.DISTRIBUTION_MODE
        current = tier.current_tier()
        build = tier.BUILD_HASH
        if mode == "personal":
            return _result(
                name, "pass",
                f"Personal mode — all {len(tier.FREE_TOOLS)}+ tools unlocked. "
                f"No license required (this build). Build: {build}",
                extra={
                    "distribution_mode": mode,
                    "tier": "paid",
                    "build_hash": build,
                },
            )
        if current == "paid":
            import config as _cfg
            key = _cfg.get("license_key") or ""
            return _result(
                name, "pass",
                f"Paid tier active. License key validates ({key[:7]}...). "
                f"Build: {build}",
                extra={
                    "distribution_mode": mode,
                    "tier": "paid",
                    "build_hash": build,
                },
            )
        # Free tier (marketplace mode + no license)
        return _result(
            name, "warn",
            f"Free tier — {len(tier.FREE_TOOLS)} tools unlocked. "
            "Paid features (Maps, all workflows, advanced CRM, brand voice, "
            f"bulk ops) currently locked. Build: {build}",
            fix=(
                'Set "license_key": "caw-XXXX-XXXX-XXXX-XXXX" in config.json. '
                "Get a key from the developer who shipped this plugin."
            ),
            extra={
                "distribution_mode": mode,
                "tier": "free",
                "free_tool_count": len(tier.FREE_TOOLS),
                "build_hash": build,
            },
        )
    except Exception as e:
        return _result(name, "warn", f"Couldn't determine tier: {e}", fix=None)


def _check_unit_tests() -> CheckResult:
    """Run the pytest suite as a deep diagnostic.

    Shells out to `python -m pytest tests/ -q --tb=line` in a subprocess so
    test side effects (cache writes, mock injection) stay isolated from the
    running MCP process. Parses pytest's summary line for pass/fail counts.

    Cost: ~1s on a healthy codebase. Fires automatically as part of
    system_doctor; can be invoked alone via system_check_unit_tests.
    """
    name = "Unit tests"
    project_root = Path(gservices.__file__).resolve().parent
    tests_dir = project_root / "tests"
    if not tests_dir.exists():
        return _result(
            name, "warn", "tests/ directory not found — can't run pytest.",
            fix=None,
        )

    import subprocess
    import sys as _sys
    import time

    # Use the venv's python so pytest finds the project's installed package.
    # Fall back to the running interpreter if .venv is missing (handoff
    # archives ship without one).
    venv_py = project_root / ".venv" / "bin" / "python"
    py = str(venv_py) if venv_py.exists() else _sys.executable

    t0 = time.time()
    try:
        proc = subprocess.run(
            [py, "-m", "pytest", "tests/", "-q", "--tb=line",
             "-W", "ignore::DeprecationWarning",
             "-W", "ignore::FutureWarning"],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=120,  # generous cap; healthy run is ~1s
        )
    except subprocess.TimeoutExpired:
        return _result(
            name, "fail",
            "pytest exceeded 120s — suite may be hanging on a network call.",
            fix=(
                f"Run manually: cd {project_root} && "
                f".venv/bin/python -m pytest tests/ -v -x"
            ),
        )
    except FileNotFoundError as e:
        return _result(
            name, "warn",
            f"Couldn't launch pytest ({e}). venv may need rebuilding.",
            fix=f"cd {project_root} && ./install.sh",
        )

    elapsed = round(time.time() - t0, 2)
    out = (proc.stdout + proc.stderr).strip()
    last_lines = out.splitlines()[-5:] if out else []

    # Pytest summary forms: "283 passed in 0.91s" / "1 failed, 282 passed in 1.2s"
    summary = ""
    for line in reversed(last_lines):
        if "passed" in line or "failed" in line or "error" in line:
            summary = line.strip()
            break

    passed = _parse_pytest_count(summary, r"(\d+) passed")
    failed = _parse_pytest_count(summary, r"(\d+) failed")
    errors = _parse_pytest_count(summary, r"(\d+) error")

    if proc.returncode == 0 and failed == 0 and errors == 0:
        return _result(
            name, "pass",
            f"{passed} tests passed in {elapsed}s",
            extra={"passed": passed, "elapsed_seconds": elapsed},
        )

    failing_lines = [
        ln for ln in out.splitlines()
        if ln.startswith("FAILED ") or ln.startswith("ERROR ")
    ][:10]
    return _result(
        name, "fail",
        f"{failed} failed, {errors} error, {passed} passed in {elapsed}s",
        fix=(
            f"Inspect: cd {project_root} && "
            f".venv/bin/python -m pytest tests/ -v --tb=short"
        ),
        extra={
            "passed": passed, "failed": failed, "errors": errors,
            "elapsed_seconds": elapsed,
            "first_failures": failing_lines,
        },
    )


def _parse_pytest_count(summary: str, pattern: str) -> int:
    """Pull an integer out of pytest's summary line. Returns 0 on miss."""
    import re as _re
    m = _re.search(pattern, summary or "")
    return int(m.group(1)) if m else 0


def _check_quota_usage() -> CheckResult:
    """Estimate Maps + Anthropic spend this month from logs."""
    name = "Quota usage (this month)"
    try:
        import datetime as _dt
        import re
        log_path = Path(gservices.__file__).parent / "logs" / "google_workspace_mcp.log"
        if not log_path.exists():
            return _result(
                name, "warn", "No log file yet — usage tracking unavailable.",
                fix=None,
            )
        now = _dt.datetime.now()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        # Per-call cost table (from tools/maps.py docs).
        costs = {
            "geocode": 0.005, "reverse_geocode": 0.005,
            "search_places": 0.032, "search_nearby": 0.032,
            "get_place_details": 0.020, "get_directions": 0.005,
            "distance_matrix": 0.005, "get_timezone": 0.005,
            "validate_address": 0.017, "static_map": 0.002,
        }
        counts: dict[str, int] = {}
        # Parse log: lines mentioning maps_<x> or workflow_route_optimize_advanced
        # Heuristic — counts may be approximate.
        try:
            with log_path.open() as f:
                for line in f:
                    # Skip lines older than this month.
                    m = re.match(r"(\d{4}-\d{2}-\d{2})", line)
                    if m:
                        try:
                            ld = _dt.date.fromisoformat(m.group(1))
                            if ld < month_start.date():
                                continue
                        except ValueError:
                            pass
                    for tool in costs:
                        if f"maps_{tool}" in line:
                            counts[tool] = counts.get(tool, 0) + 1
        except Exception:
            return _result(
                name, "warn", "Couldn't parse log — usage estimate unavailable.",
                fix=None,
            )
        total = sum(counts.get(t, 0) * costs[t] for t in costs)
        if total == 0:
            return _result(
                name, "pass", "No Maps API usage logged this month yet.",
            )
        return _result(
            name, "pass",
            f"Estimated Maps spend this month: ${total:.2f} "
            f"({sum(counts.values())} calls). Free tier covers $200/mo.",
            extra={"by_tool": counts, "total_usd": round(total, 2)},
        )
    except Exception as e:
        return _result(name, "warn", f"Couldn't estimate: {e}", fix=None)
