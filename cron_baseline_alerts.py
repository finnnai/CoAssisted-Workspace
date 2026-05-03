#!/usr/bin/env python3
# © 2026 CoAssisted Workspace. Licensed under MIT.
"""AP-8 baseline-alerts cron — runs daily at 6:00am.

Walks every active project, refreshes its baseline, checks deviations
and budget burn, and emails any alerts to the configured operator list
(config.baseline.alert_recipients).

Cold-start projects (less than 30 days observed) report informationally
but don't trigger emails — those gate until the baseline is ready.

Usage (from cron):
    0 6 * * *  $VENV_PYTHON $HOME/cron_baseline_alerts.py

Env vars:
    COASSISTED_BASELINE_ALERT_TO  comma-separated recipients (overrides config)
    COASSISTED_BASELINE_DRY_RUN   if "1", skip the email send and print only
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import sys
import traceback


def _recipients() -> list[str]:
    env = os.environ.get("COASSISTED_BASELINE_ALERT_TO", "").strip()
    if env:
        return [r.strip() for r in env.split(",") if r.strip()]
    try:
        import config  # type: ignore
        block = config.get("baseline", {}) or {}
        recips = block.get("alert_recipients") or []
        return [r for r in recips if r]
    except Exception:
        return []


def _dry_run() -> bool:
    return os.environ.get("COASSISTED_BASELINE_DRY_RUN", "").strip() == "1"


def _format_alert_block(alerts: list[dict]) -> str:
    if not alerts:
        return "No alerts. Every active project is within baseline + budget."
    by_severity: dict[str, list[dict]] = {}
    for a in alerts:
        sev = a.get("severity", "info")
        by_severity.setdefault(sev, []).append(a)
    lines: list[str] = []
    for sev in ("critical", "warning", "info"):
        bucket = by_severity.get(sev) or []
        if not bucket:
            continue
        lines.append(f"--- {sev.upper()} ({len(bucket)}) ---")
        for a in bucket:
            lines.append(
                f"  [{a.get('project_code', '?')}] {a.get('type', '?')}: "
                f"{a.get('message', '(no message)')}"
            )
    return "\n".join(lines)


def _send_email(recipients: list[str], subject: str, body: str) -> dict:
    """Use the in-tree gservices.gmail_service to send. Returns the
    {to: message_id} dict.
    """
    import base64
    from email.message import EmailMessage
    from email.utils import formatdate

    import gservices  # type: ignore

    gmail = gservices.gmail_service()
    sent: dict[str, str] = {}
    for to in recipients:
        msg = EmailMessage()
        msg["To"] = to
        msg["Subject"] = subject
        msg["Date"] = formatdate(localtime=True)
        msg.set_content(body)
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
        result = gmail.users().messages().send(userId="me", body={"raw": raw}).execute()
        sent[to] = result.get("id", "")
    return sent


def main() -> int:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        import baseline as _baseline
    except Exception:  # pragma: no cover
        print("FATAL: cannot import baseline", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return 1

    today = _dt.date.today()
    print(f"[{_dt.datetime.now().astimezone().isoformat(timespec='seconds')}] "
          f"checking baseline alerts for {today.isoformat()}")

    try:
        alerts = _baseline.check_alerts(today=today)
    except Exception as e:
        print(f"FATAL: check_alerts raised: {type(e).__name__}: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return 1

    # Strip cold_start informational entries from the email body — they're
    # not actionable and clutter the daily mail.
    actionable = [a for a in alerts if a.get("severity") != "info"]
    body = _format_alert_block(alerts)

    print(f"alerts={len(alerts)} actionable={len(actionable)}")
    print(body)

    if not actionable:
        return 0  # Nothing to email — clean exit.

    recipients = _recipients()
    if not recipients:
        print(
            "WARN: actionable alerts present but no recipients configured "
            "(config.baseline.alert_recipients or "
            "COASSISTED_BASELINE_ALERT_TO env)",
            file=sys.stderr,
        )
        return 0

    if _dry_run():
        print(f"DRY RUN — would email {len(recipients)} recipient(s).")
        return 0

    try:
        critical = sum(1 for a in actionable if a.get("severity") == "critical")
        subject = (
            f"Surefox baseline alerts — {today.isoformat()} — "
            f"{len(actionable)} actionable" + (f" ({critical} critical)" if critical else "")
        )
        sent = _send_email(recipients, subject, body)
        print(f"emailed: {json.dumps(sent, indent=2)}")
        return 0
    except Exception as e:
        print(f"FATAL: email send raised: {type(e).__name__}: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
