# © 2026 CoAssisted Workspace. Licensed under MIT.
"""AP-4: No-failed-trigger capture sweep.

Wraps the Day-1 hot deploy's `ap-receipt-sweep` scheduled task with
deterministic Python logic. Each sweep cycle:

    1. Pull unread Gmail messages with the AP/Inbound label.
    2. Pull new messages from the Receipts Chat space.
    3. For each item:
         a. Run AP-5 project router (sender + subject + body + timestamp).
         b. Based on the router's recommended action, file accordingly:
              auto_file       → download attachment to project's
                                 Receipts/{YYYY-MM}/, mark as read.
              auto_file_flag  → file but add the message ID to the
                                 review queue for operator follow-up.
              chat_picker     → post a picker prompt to the Receipts
                                 Chat space, hold the item.
              triage          → download to Surefox AP/Triage/.
    4. Return a summary report (counts per action, per-item dispositions).

This module focuses on the routing-and-disposition decisions. The
actual Drive download / file-move / chat-post wiring lives in the
existing tools/drive + tools/chat surfaces — we call into them rather
than reimplementing.

Idempotency:
    Every Gmail message we touch gets the UNREAD label removed at the
    end of its processing block. If the sweep crashes mid-batch, the
    next cycle re-picks up where it left off (still-unread items).
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Optional

import project_registry
import project_router


# =============================================================================
# Result types
# =============================================================================

@dataclass
class SweepItem:
    source: str                    # "email" | "chat"
    source_id: str                 # gmail message_id or chat message_id
    sender: Optional[str]
    subject: Optional[str]
    timestamp: Optional[_dt.datetime]
    project_code: Optional[str]
    confidence: float
    tier: str
    action: str                    # auto_file | auto_file_flag | chat_picker | triage
    target_folder_id: Optional[str]  # the Drive folder we filed to (if any)
    note: str = ""


@dataclass
class SweepResult:
    items: list[SweepItem] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    started_at: str = ""
    finished_at: str = ""

    def summary_line(self) -> str:
        return (
            f"{self.counts.get('auto_file', 0)} auto-filed · "
            f"{self.counts.get('auto_file_flag', 0)} flagged · "
            f"{self.counts.get('chat_picker', 0)} awaiting picker · "
            f"{self.counts.get('triage', 0)} in Triage"
        )


# =============================================================================
# Routing decision (pure function — easy to test)
# =============================================================================

def decide_disposition(
    *,
    sender_email: Optional[str],
    subject: Optional[str],
    body: Optional[str],
    timestamp: Optional[_dt.datetime],
    use_llm: bool = True,
) -> tuple[project_router.RouteResult, str]:
    """Decide what to do with one inbound item.

    Returns (RouteResult, action_str) where action_str is one of
    'auto_file' | 'auto_file_flag' | 'chat_picker' | 'triage'.
    """
    result = project_router.route_project(
        sender_email=sender_email,
        subject=subject,
        body=body,
        timestamp=timestamp,
        use_llm=use_llm,
    )
    action = project_router.confidence_action(result)
    return result, action


def target_subfolder_for_action(
    project_code: str,
    action: str,
    *,
    when: Optional[_dt.date] = None,
) -> Optional[str]:
    """Resolve the Drive folder to drop a filed item into.

    For auto_file / auto_file_flag, returns the {Receipts/YYYY-MM/}
    folder ID for the project. For chat_picker / triage, returns None
    (caller routes to Triage/ separately).

    Lazy-creates the month bucket if missing — same idempotent behavior
    as ap_tree.ensure_month_subtree.
    """
    if action not in ("auto_file", "auto_file_flag"):
        return None
    if not project_code:
        return None
    # Defer the import to keep the module importable without the Drive
    # API stack (e.g. in unit tests that only exercise the routing
    # decision tree).
    import ap_tree
    bucket = ap_tree.ensure_month_subtree(
        project_code, when=when, kinds=("receipts",),
    )
    return bucket.get("receipts")


# =============================================================================
# Sweep cycle (orchestrator — calls into mail/chat/drive)
# =============================================================================

def run_sweep_cycle(
    *,
    email_label: str = "AP/Inbound",
    chat_space_id: str = "spaces/AAQAly0xFuE",  # Receipts (Day-1)
    triage_folder_id: str = "1wBnOtbMVBrf0B5idKq_1teOKVlAKCtTY",  # Day-1 Triage
    max_items_per_source: int = 50,
    dry_run: bool = False,
) -> SweepResult:
    """Run one sweep cycle. Returns a SweepResult with per-item dispositions.

    `dry_run=True` makes routing decisions but doesn't download files,
    move messages, or post to chat — useful for previewing what a
    cycle would do.

    The actual mail/chat/drive operations are gated through the
    existing MCP tool surfaces; if those modules aren't available
    (e.g., in unit tests with no OAuth context), the sweep returns
    an empty result rather than crashing.
    """
    started = _dt.datetime.now().astimezone().isoformat(timespec="seconds")
    result = SweepResult(started_at=started)

    items = list(_pull_email_items(email_label, max_items=max_items_per_source))
    items += list(_pull_chat_items(chat_space_id, max_items=max_items_per_source))

    for item in items:
        route_result, action = decide_disposition(
            sender_email=item.sender,
            subject=item.subject,
            body=None,  # Pull body lazily only when routing needs it
            timestamp=item.timestamp,
        )
        item.project_code = route_result.project_code
        item.confidence = route_result.confidence
        item.tier = route_result.tier
        item.action = action

        if not dry_run:
            target_folder = target_subfolder_for_action(
                route_result.project_code or "",
                action,
                when=(item.timestamp.date() if item.timestamp else None),
            )
            item.target_folder_id = target_folder
            _execute_disposition(item, route_result, triage_folder_id)

        result.counts[action] = result.counts.get(action, 0) + 1
        result.items.append(item)

    result.finished_at = _dt.datetime.now().astimezone().isoformat(timespec="seconds")
    return result


# =============================================================================
# Source readers — best-effort, no-throw
# =============================================================================

def _pull_email_items(label: str, *, max_items: int):
    """Yield SweepItems from unread Gmail with the given label.

    Best-effort: returns empty when the Gmail surface isn't available
    (no OAuth, sandbox without tokens, etc.).
    """
    try:
        from googleapiclient.discovery import build
        from auth import get_credentials
    except ImportError:
        return
    try:
        creds = get_credentials()
        service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        q = f"label:{label} is:unread -in:trash"
        resp = service.users().messages().list(
            userId="me", q=q, maxResults=max_items,
        ).execute()
        msg_refs = resp.get("messages") or []
        for ref in msg_refs:
            mid = ref.get("id")
            full = service.users().messages().get(
                userId="me", id=mid, format="metadata",
                metadataHeaders=["From", "Subject", "Date"],
            ).execute()
            headers = {
                h["name"]: h["value"]
                for h in (full.get("payload", {}).get("headers") or [])
            }
            ts = None
            try:
                from email.utils import parsedate_to_datetime
                if headers.get("Date"):
                    ts = parsedate_to_datetime(headers["Date"])
            except (TypeError, ValueError):
                pass
            yield SweepItem(
                source="email",
                source_id=mid,
                sender=headers.get("From"),
                subject=headers.get("Subject"),
                timestamp=ts,
                project_code=None,
                confidence=0.0,
                tier="",
                action="",
                target_folder_id=None,
            )
    except Exception:
        return  # Best-effort; the sweep returns whatever items it got.


def _pull_chat_items(space_id: str, *, max_items: int):
    """Yield SweepItems from new Receipts-space chat messages.

    Stub for now — the full chat ingestion path needs the same OAuth
    plumbing as email plus a watermark of "what's been processed
    already." Returning empty until that's wired keeps the sweep
    operating on email alone, which is the higher-volume channel.
    """
    return
    yield  # make this a generator


# =============================================================================
# Disposition executor
# =============================================================================

def _execute_disposition(
    item: SweepItem,
    route_result: project_router.RouteResult,
    triage_folder_id: str,
) -> None:
    """Carry out the file-or-chat action decided by the router.

    Best-effort: any failure is captured in item.note; the sweep
    continues with the remaining items.
    """
    try:
        if item.action in ("auto_file", "auto_file_flag"):
            if item.target_folder_id:
                _download_attachments_to_folder(item, item.target_folder_id)
            else:
                # Lazy bucket couldn't be created (no project record or
                # no Drive root). Fall back to Triage.
                _download_attachments_to_folder(item, triage_folder_id)
                item.note = "month bucket unavailable; fell back to Triage"
            _mark_email_read(item)
        elif item.action == "chat_picker":
            _post_picker_prompt(item, route_result)
            # Don't mark read — submitter's reply re-enters the loop.
        elif item.action == "triage":
            _download_attachments_to_folder(item, triage_folder_id)
            _mark_email_read(item)
    except Exception as e:
        item.note = f"disposition error: {e}"


def _download_attachments_to_folder(item: SweepItem, folder_id: str) -> None:
    """Download a message's attachments into a Drive folder.

    Stub — wires up to tools/gmail.py download_attachment + drive upload
    in a follow-up commit. For now records the intended target; the
    sweep result still surfaces routing decisions correctly.
    """
    item.note = (
        item.note
        + f" [stub: would download to folder_id={folder_id}]"
    ).strip()


def _mark_email_read(item: SweepItem) -> None:
    """Remove the UNREAD label so the next sweep skips this message.

    Stub — same reason as _download_attachments_to_folder. Wires up to
    tools/gmail.py modify_labels in the follow-up.
    """
    item.note = (item.note + " [stub: would mark read]").strip()


def _post_picker_prompt(
    item: SweepItem,
    route_result: project_router.RouteResult,
) -> None:
    """Post a project-picker chat message to the Receipts space.

    Stub — wires up to tools/chat.py send_message in the follow-up.
    The picker text would list `route_result.candidates` (when
    populated) plus an "Other" escape.
    """
    candidates_str = ", ".join(
        c.get("code", "?") for c in (route_result.candidates or [])
    ) or "no candidates"
    item.note = (
        item.note
        + f" [stub: would post picker: {candidates_str}]"
    ).strip()
