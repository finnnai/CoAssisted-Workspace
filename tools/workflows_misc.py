# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Receipts + monthly-expense workflows (compositions over the receipt extractor).

Split from the legacy tools/workflows.py during P1-1
(see mcp-design-docs-2026-04-29.md). All shared helpers live
in tools/_workflow_helpers.py.
"""
from __future__ import annotations

import base64
import io
import json
from typing import Optional

from googleapiclient.http import MediaInMemoryUpload, MediaIoBaseDownload
from pydantic import BaseModel, ConfigDict, Field

import config
import crm_stats
import gservices
import rendering
import templates as templates_mod
from dryrun import dry_run_preview, is_dry_run
from errors import format_error
from logging_util import log
from tools.contacts import _flatten_person  # noqa: E402 — reuse the flattening logic

# Inline MIME builder import — we can't cleanly import from tools.gmail without
# a circular import, so we use the email stdlib directly here.
import mimetypes
from email.message import EmailMessage

# --------------------------------------------------------------------------- #
# Input models
# --------------------------------------------------------------------------- #


class ReceiptChatDigestInput(BaseModel):
    """Input for workflow_receipt_chat_digest."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    chat_space_id: str = Field(
        ..., min_length=3,
        description="Chat space to post the digest into (e.g. 'spaces/AAQA...').",
    )
    sheet_id: Optional[str] = Field(default=None)
    sheet_name: Optional[str] = Field(
        default=None,
        description="Source receipts sheet (resolved against your 'Receipts — *' sheets).",
    )
    days: int = Field(
        default=30, ge=1, le=365,
        description="Look-back window for the digest.",
    )
    dry_run: Optional[bool] = Field(default=None)


class MonthlyExpenseReportInput(BaseModel):
    """Input for workflow_monthly_expense_report."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    month: str = Field(
        ...,
        description="Target month as 'YYYY-MM' (e.g. '2026-04'). Filters rows whose date falls in this month.",
    )
    recipient_email: str = Field(
        ...,
        description="Email address to send the report to (e.g. accountant@firm.com).",
    )
    sheet_id: Optional[str] = Field(default=None)
    sheet_name: Optional[str] = Field(
        default=None,
        description="Source receipts sheet (resolved against your 'Receipts — *' sheets).",
    )
    drive_folder_id: Optional[str] = Field(
        default=None,
        description="Drive folder for the QB CSV. Auto-creates 'CoAssisted Receipts' if not set.",
    )
    dry_run: Optional[bool] = Field(default=None)


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #


def register(mcp) -> None:
    @mcp.tool(
        name="workflow_receipt_chat_digest",
        annotations={
            "title": "Post a deduplicated expense digest to a Gchat space",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,  # creates a chat message each call
            "openWorldHint": False,
        },
    )
    async def workflow_receipt_chat_digest(
        params: ReceiptChatDigestInput,
    ) -> str:
        """Read receipts from a sheet, dedupe by content_key, post a formatted
        digest to a Gchat space. Stamps the BOT_FOOTER_MARKER so re-scans
        won't re-extract the digest as a receipt.
        """
        try:
            import datetime as _dt
            import receipts as _r
            from tools.receipts import (
                _resolve_sheet, _existing_sheet_content_keys,
            )
            sheets = gservices.sheets()
            chat = gservices.chat()

            sheet_id, _title, err = _resolve_sheet(
                params.sheet_id, params.sheet_name,
            )
            if err is not None:
                return json.dumps(err, indent=2)

            # Read all rows; filter by date in window.
            resp = sheets.spreadsheets().values().get(
                spreadsheetId=sheet_id, range="A2:Q",
            ).execute()
            rows = resp.get("values", []) or []
            cutoff = (
                _dt.date.today() - _dt.timedelta(days=params.days)
            ).isoformat()

            # Dedupe by content_key. Carry first-seen row only.
            seen: set[str] = set()
            unique_rows: list[dict] = []
            grand_total = 0.0
            by_category: dict[str, dict] = {}
            for row in rows:
                row = row + [""] * (17 - len(row))
                date = row[1] or ""
                merchant = row[2] or ""
                total_str = row[3] or ""
                category = row[5] or "Miscellaneous Expense"
                last_4 = row[10] or ""
                if not merchant or not total_str:
                    continue
                if date and date < cutoff:
                    continue
                try:
                    total = float(total_str)
                except ValueError:
                    continue
                key = _r.content_key(merchant, date, total, last_4)
                if not key or key in seen:
                    continue
                seen.add(key)
                unique_rows.append({
                    "date": date or "(no date)",
                    "merchant": merchant,
                    "total": total,
                    "category": category,
                })
                grand_total += total
                bucket = by_category.setdefault(
                    category, {"total": 0.0, "count": 0},
                )
                bucket["total"] += total
                bucket["count"] += 1

            unique_rows.sort(key=lambda r: r["date"], reverse=True)

            # Build the digest text (Gchat markdown).
            lines = [
                "*📊 Receipt Digest*\n",
                f"_Window: last {params.days} days_",
                f"_Source: {_title}_\n",
                "*📈 Summary*",
                f"• Unique purchases: *{len(unique_rows)}*",
                f"• Grand total: *${grand_total:,.2f} USD*\n",
                "*🏷️ By Category*",
            ]
            for cat, info in sorted(by_category.items(),
                                    key=lambda x: -x[1]["total"]):
                lines.append(
                    f"• {cat}: *${info['total']:,.2f}* ({info['count']} receipts)"
                )
            lines.append("\n*📋 Detail*")
            lines.append("```")
            for r in unique_rows[:20]:  # top 20 most recent
                merchant = (r["merchant"] or "")[:24]
                lines.append(
                    f"{r['date']:<12}{merchant:<26}${r['total']:>10,.2f}  {r['category']}"
                )
            if len(unique_rows) > 20:
                lines.append(f"  …and {len(unique_rows) - 20} more")
            lines.append("```\n")
            lines.append(f"_— {_r.BOT_FOOTER_MARKER}_")
            text = "\n".join(lines)

            if is_dry_run(params.dry_run):
                return dry_run_preview("workflow_receipt_chat_digest", {
                    "would_post_to": params.chat_space_id,
                    "preview": text,
                    "stats": {
                        "unique_purchases": len(unique_rows),
                        "grand_total": round(grand_total, 2),
                        "categories": len(by_category),
                    },
                })

            sent = chat.spaces().messages().create(
                parent=params.chat_space_id,
                body={"text": text},
            ).execute()
            return json.dumps({
                "status": "sent",
                "message_name": sent.get("name"),
                "stats": {
                    "unique_purchases": len(unique_rows),
                    "grand_total": round(grand_total, 2),
                    "categories": len(by_category),
                },
            }, indent=2)
        except Exception as e:
            log.error("workflow_receipt_chat_digest failed: %s", e)
            return format_error(e)

    @mcp.tool(
        name="workflow_monthly_expense_report",
        annotations={
            "title": "Build month's QB CSV + email it to a recipient",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def workflow_monthly_expense_report(
        params: MonthlyExpenseReportInput,
    ) -> str:
        """End-to-end month-end close: filter receipts by month, export QB
        CSV to Drive, email recipient with the link + summary."""
        try:
            import datetime as _dt
            from tools.receipts import (
                _resolve_sheet, _ensure_drive_folder, _archive_pdf_to_drive,
            )
            import receipts as _r
            import csv
            import io as _io

            sheet_id, sheet_title, err = _resolve_sheet(
                params.sheet_id, params.sheet_name,
            )
            if err is not None:
                return json.dumps(err, indent=2)

            archive_folder_id = _ensure_drive_folder(
                params.drive_folder_id
                or config.get("receipts_drive_folder_id"),
                default_name="CoAssisted Receipts",
            )

            sheets = gservices.sheets()
            resp = sheets.spreadsheets().values().get(
                spreadsheetId=sheet_id, range="A:Q",
            ).execute()
            data_rows = resp.get("values", []) or []
            if len(data_rows) < 2:
                return json.dumps({"status": "empty_sheet"}, indent=2)
            header, body_rows = data_rows[0], data_rows[1:]

            # Filter to the requested month (YYYY-MM)
            month_prefix = params.month + "-"
            account_map = config.get("receipts_qb_account_map") or None
            buf = _io.StringIO()
            w = csv.writer(buf)
            w.writerow(_r.QB_CSV_COLUMNS)
            included = 0
            grand_total = 0.0
            by_category: dict[str, float] = {}
            for r in body_rows:
                r = r + [""] * (len(header) - len(r))
                row_dict = dict(zip(header, r))
                date = row_dict.get("date") or ""
                if not date.startswith(month_prefix):
                    continue
                try:
                    total = float(row_dict.get("total") or 0)
                except ValueError:
                    total = 0.0
                rec = _r.ExtractedReceipt(
                    date=date or None,
                    merchant=row_dict.get("merchant") or None,
                    total=total if total else None,
                    currency=row_dict.get("currency") or "USD",
                    category=row_dict.get("category") or "Miscellaneous Expense",
                    location=row_dict.get("location") or None,
                    notes=row_dict.get("notes") or None,
                    source_kind=row_dict.get("source_kind") or "",
                    source_id=row_dict.get("source_id") or None,
                )
                w.writerow(_r.receipt_to_qb_row(rec, account_map=account_map))
                included += 1
                grand_total += float(rec.total or 0)
                by_category[rec.category] = (
                    by_category.get(rec.category, 0) + float(rec.total or 0)
                )
            csv_bytes = buf.getvalue().encode("utf-8")

            if is_dry_run(params.dry_run):
                return dry_run_preview("workflow_monthly_expense_report", {
                    "month": params.month,
                    "rows": included,
                    "grand_total": round(grand_total, 2),
                    "by_category": {k: round(v, 2) for k, v in by_category.items()},
                    "would_email": params.recipient_email,
                })

            # Upload CSV to Drive
            drive_link = _archive_pdf_to_drive(
                gservices.drive(), archive_folder_id,
                f"qb_export_{params.month}.csv",
                csv_bytes, "text/csv",
            )

            # Compose + send email
            from email.message import EmailMessage
            cat_lines = "\n".join(
                f"  • {k}: ${v:,.2f}"
                for k, v in sorted(by_category.items(), key=lambda x: -x[1])
            )
            body = (
                f"Hi,\n\n"
                f"Monthly expense report for {params.month} attached as a "
                f"QuickBooks-importable CSV.\n\n"
                f"Summary:\n"
                f"  • Receipts: {included}\n"
                f"  • Grand total: ${grand_total:,.2f}\n"
                f"  • By category:\n{cat_lines}\n\n"
                f"CSV download (Drive): {drive_link}\n\n"
                f"— sent by CoAssisted Workspace receipt extractor"
            )
            msg = EmailMessage()
            msg["To"] = params.recipient_email
            msg["Subject"] = f"Expense report — {params.month}"
            msg.set_content(body)
            raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
            sent = gservices.gmail().users().messages().send(
                userId="me", body={"raw": raw},
            ).execute()

            return json.dumps({
                "status": "ok",
                "month": params.month,
                "rows_included": included,
                "grand_total": round(grand_total, 2),
                "by_category": {k: round(v, 2) for k, v in by_category.items()},
                "csv_drive_link": drive_link,
                "email_message_id": sent.get("id"),
                "recipient": params.recipient_email,
            }, indent=2)
        except Exception as e:
            log.error("workflow_monthly_expense_report failed: %s", e)
            return format_error(e)

