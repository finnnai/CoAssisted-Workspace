"""Smoke-test all message variants from the AP composer.

Renders every tier x audience x field-count combination, plus both
acknowledgement variants (initial + promotion), and writes a single
markdown report to ../dist/smoke_messages_<date>.md for eyeball review.

Usage:
    .venv/bin/python scripts/smoke_messaging.py

This does NOT touch Gmail or Chat — pure composer output. To smoke-test
the live integration (threading, sends, real sheet writes), trigger
workflow_extract_project_invoices via the MCP connector.
"""
from __future__ import annotations

import datetime as _dt
import os
import sys
from pathlib import Path

# Make the project importable.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
# IMPORTANT: do NOT add ROOT/"tools" to sys.path — tools/calendar.py
# shadows the stdlib `calendar` module and triggers a circular import.
# The `tools` package is reached via its __init__.py from the project root.

# Disable LLM so we always render the deterministic fallback for
# the smoke test (no network, no API key required).
os.environ.pop("ANTHROPIC_API_KEY", None)

from project_invoices import ExtractedInvoice  # noqa: E402
import tools.project_invoices as t_pi  # noqa: E402


def _ruler(label: str, char: str = "=") -> str:
    line = char * 78
    return f"\n{line}\n{label}\n{line}\n"


def _hr(label: str, char: str = "-") -> str:
    line = char * 60
    return f"\n{line}\n{label}\n{line}\n"


def main() -> str:
    inv_one = ExtractedInvoice(
        vendor="Acme Roofing",
        invoice_number="INV-7771",
        total=2500.00,
        currency="USD",
        invoice_date="2026-04-25",
        project_code="ALPHA",
        source_id="email:smoke-1",
    )
    inv_three_missing = ExtractedInvoice(
        vendor="Acme Roofing",
        invoice_number=None,
        total=None,
        currency="USD",
        invoice_date=None,
        project_code="ALPHA",
        source_id="email:smoke-3",
    )

    out: list[str] = []
    out.append(f"# Smoke Test — Messaging Composer\n")
    out.append(f"_Generated {_dt.datetime.now().isoformat(timespec='seconds')}_\n")
    out.append(
        "Rendered with the deterministic fallback path "
        "(LLM disabled) so output is repeatable.\n"
    )

    # --- INFO REQUESTS ---------------------------------------------------- #
    out.append(_ruler("INFO-REQUEST MESSAGES"))
    for tier in (1, 2, 3, 4):
        out.append(_hr(f"Tier {tier}", char="="))
        for audience in ("employee", "vendor"):
            for label, missing in (
                ("single missing field", ["invoice_number"]),
                (
                    "multiple missing fields",
                    ["invoice_number", "total", "due_date"],
                ),
            ):
                inv = inv_one if "single" in label else inv_three_missing
                msg = t_pi._compose_info_request(
                    inv, missing,
                    audience=audience, urgency_tier=tier,
                    recipient_name="Josh" if audience == "employee" else None,
                )
                out.append(_hr(
                    f"Tier {tier} • {audience} • {label}",
                ))
                out.append("**Subject:** " + msg["subject"] + "\n")
                out.append("**Plain text:**\n")
                out.append("```\n" + msg["plain"] + "\n```\n")
                out.append("**Chat (SMS-style):**\n")
                out.append("```\n" + (msg.get("chat") or "") + "\n```\n")
                out.append("**HTML:**\n")
                out.append("```html\n" + msg["html"] + "\n```\n")

    # --- ACKNOWLEDGEMENTS ------------------------------------------------- #
    out.append(_ruler("ACKNOWLEDGEMENT MESSAGES"))
    for label, kwargs in (
        (
            "Initial submission (clean, employee)",
            dict(is_promotion=False, audience="employee", recipient_name="Josh"),
        ),
        (
            "Initial submission (clean, vendor)",
            dict(is_promotion=False, audience="vendor"),
        ),
        (
            "Promotion (loop closed, employee)",
            dict(is_promotion=True, audience="employee", recipient_name="Josh"),
        ),
        (
            "Promotion (loop closed, vendor)",
            dict(is_promotion=True, audience="vendor"),
        ),
        (
            "Initial — no sheet link available",
            dict(is_promotion=False, audience="employee", recipient_name="Josh"),
        ),
    ):
        sheet_id = None if "no sheet" in label else "1AbCdEfGhIjK_LmNoPqRsTuVwXyZ"
        ack = t_pi._compose_acknowledgement(
            inv_one,
            sheet_id=sheet_id,
            sheet_name="ALPHA",
            doc_type="invoice",
            status="OPEN",
            **kwargs,
        )
        out.append(_hr(label, char="="))
        out.append("**Subject:** " + ack["subject"] + "\n")
        out.append("**Plain text:**\n")
        out.append("```\n" + ack["plain"] + "\n```\n")
        out.append("**Chat (SMS-style):**\n")
        out.append("```\n" + (ack.get("chat") or "") + "\n```\n")
        out.append("**HTML:**\n")
        out.append("```html\n" + ack["html"] + "\n```\n")

    # --- CADENCE ---------------------------------------------------------- #
    import vendor_followups as vf
    out.append(_ruler("REMINDER CADENCE"))
    out.append(
        f"\n- `EMAIL_REMINDER_HOURS_LADDER` = "
        f"{vf.EMAIL_REMINDER_HOURS_LADDER}\n"
    )
    out.append(f"- `MAX_REMINDERS` = {vf.MAX_REMINDERS}\n")
    out.append(f"- `CHAT_REMINDER_HOURS` = {vf.CHAT_REMINDER_HOURS}\n\n")
    out.append("Per-stage email cadence:\n\n")
    for i in range(vf.MAX_REMINDERS):
        out.append(
            f"- Reminder #{i+1}: wait `{vf._email_wait_hours(i)}`h "
            f"after the previous touch\n"
        )
    out.append(
        f"- Reminder #{vf.MAX_REMINDERS+1}+ : "
        f"`{vf._email_wait_hours(vf.MAX_REMINDERS)}` (capped — never fires)\n"
    )

    # --- THREADING -------------------------------------------------------- #
    out.append(_ruler("THREADING (manual review)"))
    out.append(
        "\nThe threading wiring can't be smoke-tested without hitting the "
        "live Chat API. To verify in production:\n\n"
        "1. Drop a test receipt into a watched chat space.\n"
        "2. Call `workflow_extract_project_invoices` via MCP.\n"
        "3. Confirm the bot's info-request appears as a threaded REPLY "
        "directly under your receipt message — not as a new top-level "
        "message in the space.\n"
        "4. Reply (vendor-style) on the same thread with the missing field.\n"
        "5. Call `workflow_process_vendor_replies`.\n"
        "6. Confirm the acknowledgement (✓ ... is now complete.) "
        "lands in the SAME thread.\n"
    )

    return "".join(out)


if __name__ == "__main__":
    report = main()
    today = _dt.date.today().isoformat()
    out_path = ROOT / "dist" / f"smoke_messages_{today}.md"
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(report, encoding="utf-8")
    print(f"Wrote {out_path}")
    print(f"({len(report):,} chars, {report.count(chr(10))} lines)")
