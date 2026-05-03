# Design docs — 6 specs

Following the audit (`mcp-audit-2026-04-29.md`). Each spec is the contract. Once you approve one, building it is mechanical. Specs are independent — pick any order.

---

## P0-2 — Vendor reply parsing improvements

### Problem
Three concrete gaps Finnn flagged. Today's `workflow_process_vendor_replies` works but loses information.

1. **Multi-message thread dedup.** A vendor replies twice in one thread → MCP processes both as separate signals → fields get overwritten by whichever parse ran second.
2. **No attachment extraction.** Vendor sends the missing W-9 as a PDF → we parse the body but never pull the file. Project Drive stays empty, AR stays parked.
3. **Unconditional auto-promotion.** Any reply with non-empty parsed fields flips `AWAITING_INFO → OPEN`. A vendor saying "I'll send it tomorrow" promotes the row with garbage data.

### Scope
Modify, don't redesign. Same tool surface, smarter internals.

### Files
- `tools/project_invoices.py::workflow_process_vendor_replies` — orchestration
- `vendor_followups.py` — add `latest_reply_ts` field to the store
- `project_invoices.py` — `_parse_vendor_reply` already returns confidence; surface it
- New: `tools/project_invoices.py::_extract_reply_attachments` (helper)
- Tests: `tests/test_project_invoices_tools.py`, `tests/test_vendor_replies.py`

### Behavior
1. **Dedup.** When scanning a thread, sort messages by timestamp ascending. Track `latest_reply_ts` in the awaiting_info entry. Only parse messages newer than `latest_reply_ts`. After processing, update `latest_reply_ts` to the timestamp of the message we acted on.

2. **Attachments.** New helper `_extract_reply_attachments(thread, since_ts)` returns a list of `{filename, mime_type, gmail_attachment_id, message_id}`. For each attachment that matches a missing field's expected mime (e.g. `application/pdf`, `image/*` for receipts, `application/pdf` or `image/*` for W-9 / COI), download and upload to the project's Drive folder under `attachments/{vendor}/{filename}`. Append the Drive URL to the row in a new `Attachments` column (create if missing).

3. **Confidence-gated promotion.** Add to `_parse_vendor_reply` return:
   - `confidence: "high" | "medium" | "low"`
   - `high` (≥0.85): all required fields parsed cleanly with strong signal (regex + LLM agree).
   - `medium` (0.55–0.85): partial parse, OR LLM says "unclear", OR fields look plausible but no second-source confirmation.
   - `low` (<0.55): boilerplate reply ("got it, will send"), no usable fields, or contradictions.

   Promotion rules:
   - HIGH → update row + flip to `OPEN` + clear awaiting_info.
   - MEDIUM → update row in place + leave `AWAITING_INFO` + add tag `NEEDS_REVIEW` to row + log to a new `review_queue.json`.
   - LOW → record reminder (via `record_reminder`) + leave row alone + don't burn a reminder slot if vendor explicitly promised follow-up.

### Acceptance criteria
- A thread with 3 vendor replies processes only the newest unseen one per run.
- A reply with a PDF attachment results in the file landing in `Drive/Project AP/{project}/attachments/{vendor}/`.
- A reply parsed at `medium` confidence keeps the row in `AWAITING_INFO` and adds it to `review_queue.json`.
- Existing 1023 tests still pass.
- New tests:
  - `test_dedup_skips_already_processed_message`
  - `test_attachment_extraction_uploads_pdf_to_drive`
  - `test_attachment_extraction_skips_non_matching_mime`
  - `test_high_confidence_promotes_row`
  - `test_medium_confidence_holds_for_review`
  - `test_low_confidence_records_reminder_no_promotion`

### Effort
**1.5–2 days.** Most of the time is in the confidence scorer — the dedup + attachment paths are mechanical.

### Open questions
- **What mime types are valid for which fields?** I'll bundle a `_FIELD_MIME_HINTS` mapping (e.g. `w9 -> [application/pdf, image/jpeg, image/png]`, `coi -> [application/pdf]`). You can amend.
- **Where does `review_queue.json` live?** Same folder as `awaiting_info.json`. Same atomic-write pattern. Surfaced via a new `workflow_list_review_queue` tool — should I include that here or scope it separate?

---

## P0-3 — Direct unit tests for thin-wrapper tool modules

### Problem
13 tool modules (`gmail`, `calendar`, `drive`, `sheets`, `docs`, `tasks`, `chat`, `contacts`, `maps`, `enrichment`, `system`, `workflows`, `handoff`) have no dedicated `tests/test_*.py` file. They're tested only indirectly through `test_tools_registration.py`. Schema-shape regressions, input-validation gaps, error-formatting changes — all slip through.

### Scope
Add a **baseline test file per module**. Not exhaustive — input-model validation + happy-path stub + one error path each. Compounds over time.

### Files (new)
- `tests/test_gmail_tools.py`
- `tests/test_calendar_tools.py`
- `tests/test_drive_tools.py`
- `tests/test_sheets_tools.py`
- `tests/test_docs_tools.py`
- `tests/test_tasks_tools.py`
- `tests/test_chat_tools.py`
- `tests/test_contacts_tools.py`
- `tests/test_maps_tools.py`
- `tests/test_enrichment_tools.py`
- `tests/test_handoff_tools.py`
- `tests/test_workflows_tools.py` (system + workflows are big — separate spec needed)

### Pattern (per file)
```python
def test_{tool_name}_input_validation():
    """Input model rejects bad input, accepts good input."""
    bad = {Tool}Input.model_validate({...invalid...})  # expect ValidationError
    good = {Tool}Input(**{minimal_valid_payload})

def test_{tool_name}_happy_path(monkeypatch):
    """Mock gservices.{client}() to return a canned response.
    Call the tool fn. Assert return shape."""

def test_{tool_name}_error_returns_format_error(monkeypatch):
    """Mock gservices to raise googleapiclient.errors.HttpError.
    Assert the tool returns format_error(...) JSON."""
```

### Priority order
Highest-line-count + highest-risk first:
1. `gmail` (1456 LOC, 17 tools, send-side critical)
2. `chat` (1765 LOC, 18 tools, send-side critical)
3. `contacts` (1560 LOC, CRM-side critical)
4. `calendar` (605 LOC, 8 tools, RSVP-critical)
5. `drive` (550 LOC, 9 tools, file-write-critical)
6. The rest in any order

### Acceptance criteria
- Each new test file has minimum 3 tests per tool (validation + happy + error).
- Total new tests: ~150–200 across the bundle.
- Coverage report shows non-zero coverage on `tools/{module}.py` for every module.
- All tests run in <5s combined (no live API).

### Effort
**Per-module: 3–5 hours** for the high-line-count files, 1–2 hours for the smaller ones. **Total bundle: 4–5 days** if done in one push, but modular — incremental landings work fine.

---

## P1-1 — Split `tools/workflows.py` (7898 lines, 44 tools)

### Problem
Single file has 44 cross-service workflow tools. Maintainability cliff — every change touches the same 8k-line file. Imports get tangled, merge conflicts pile up, finding the right tool definition takes effort.

### Scope
Mechanical split into 5 modules. No behavior change. Backwards-compat shim so external imports don't break during transition.

### New file layout
- `tools/workflows_gmail.py` — anything that mostly orchestrates Gmail (save attachments to Drive, mail-merge, signature management, draft from CRM)
- `tools/workflows_drive.py` — Drive-side compositions (bulk-share, folder templating, audit cleanup)
- `tools/workflows_crm.py` — contact/CRM loops (refresh stats, enrichment passes, group ops, find-by-attribute)
- `tools/workflows_calendar.py` — calendar compositions (defrag, free/busy aggregation, recurring helpers)
- `tools/workflows_misc.py` — anything that doesn't fit the above (often genuinely cross-service)

### Migration
1. Make a list of all 44 tools in `tools/workflows.py` with a one-line description.
2. Assign each to one of the 5 buckets.
3. Move the tool + its input model + any module-private helpers to the right new file.
4. Each new file gets a `register(mcp)` function.
5. `server.py` registration loop already walks `tools/__init__.py` — add the 5 new modules there.
6. **Backwards-compat shim:** keep `tools/workflows.py` as a stub that imports from the 5 new modules, so any external code that did `from tools.workflows import register` still works during transition. Add a deprecation comment.

### Files to update
- New: `tools/workflows_{gmail,drive,crm,calendar,misc}.py`
- Modified: `tools/workflows.py` (becomes a thin re-export)
- Modified: `tools/__init__.py` (if it has an explicit list)
- Modified: `server.py` if it does explicit registration

### Acceptance criteria
- Same 44 tools register, same names, same schemas.
- `system_check_tools` returns same count.
- `make test-fast` passes unchanged.
- Each new file is <2000 lines (probably <1500).
- Old file is <100 lines (just the shim).

### Effort
**1 day** including review + ensuring no circular imports.

### Risk
Low. Mechanical move. Main risk: shared private helpers that two new files both need — fix by promoting them to a `tools/_workflow_helpers.py`.

---

## P1-4 — Smarter reminder cadence

### Problem
Today's escalation tier is purely a function of `reminder_count` (0=tier1, 1=tier3, 2+=tier4). A vendor who replies in <2hr 90% of the time gets the same 48hr nudge as one who takes a week. Bad signal-to-noise.

### Scope
Track per-vendor response-time history. Compute the next reminder time from the vendor's typical reply latency. Add day-of-week awareness (don't send reminders on Sundays). Use the existing `briefing_actions.json` infra for scheduling — don't introduce a new daemon.

### Files
- New: `vendor_response_history.py` — rolling-window stats per vendor email
- Modified: `vendor_followups.py::_email_wait_hours` — replace constant table with per-vendor lookup
- Modified: `vendor_followups.py::register_request` / `record_reminder` — record `acted_at` timestamps to history
- Modified: `tools/project_invoices.py::workflow_process_vendor_replies` — when a vendor replies, log `(vendor_email, request_sent_at, replied_at)` to history

### Behavior
- For each vendor, store the last 20 (request, reply) pairs in `vendor_response_history.json`.
- Compute median reply time. If <12hr median, next reminder fires at 24hr. If 12–48hr median, next at 72hr. If >48hr median, next at 120hr.
- Day-of-week: never schedule reminders for Sat/Sun. Push to Mon 9am local. Use `default_timezone` from `config.json`.
- Holiday awareness: read US federal holidays from a small JSON. (Skip federal-holiday-only Mondays.)
- Cold-start: vendors with <3 historical replies use the existing constant table (tier1=24h, tier2=48h, tier3=72h, tier4=120h).

### Acceptance criteria
- A vendor with median reply 4hr gets next reminder at 24hr (instead of 48hr default).
- A vendor with median reply 60hr gets next reminder at 120hr (instead of 48hr default).
- A reminder due at Sat 11am gets pushed to Mon 9am local.
- A reminder due on July 4 gets pushed to next business day.
- Cold-start vendors (no history) use existing constants.
- New tests: 10–15 covering history rolling, median calc, day-of-week push, holiday push, cold-start fallback.

### Effort
**2 days.** History store is straightforward. Day-of-week + holiday logic is the trickier part.

### Open questions
- **Holiday calendar source.** Bundle a static JSON for US holidays through 2030, or pull from `python-holidays` package? I'd ship the static — fewer deps.
- **Workspace-org-wide holidays** (e.g. "company holidays")? Out of scope for v1 — could come from `config.json` later.

---

## P1-5 — Snooze + bulk actions + visible escalation trail

### Problem
Right now an AP row in `AWAITING_INFO` has no way to say "wait 2 weeks before nudging again." No way to bulk-promote 5 reviewed rows. No human-readable "we asked Acme on April 12, no reply, escalated April 19, escalated April 24" log surfaced anywhere.

### Scope
Three additions, can land independently.

### A. Snooze
- New tool `workflow_snooze_awaiting_info` — accept `content_key`, `until_date` (ISO), optional `reason`. Sets a `snoozed_until` field on the entry. The reminder scheduler skips snoozed entries until that timestamp passes.
- New tool `workflow_unsnooze_awaiting_info` — accept `content_key`. Clears `snoozed_until`.
- Modified: `vendor_followups.py::due_for_reminder` — exclude snoozed entries.

### B. Bulk actions
- New tool `workflow_bulk_resolve_awaiting_info` — accept `content_keys: list[str]`, optional `reason`. Atomically marks resolved.
- New tool `workflow_bulk_promote_review_queue` — accept `content_keys`. For each: lift `NEEDS_REVIEW` tag, flip to `OPEN`, clear `review_queue.json` entry.

### C. Escalation trail
- Modified: `vendor_followups.py::register_request` — add `events: []` to entry.
- Each `record_reminder` / status change appends `{ts, action, tier, message_id_sent}` to `events`.
- New tool `workflow_get_escalation_trail` — accept `content_key`, return formatted human-readable timeline.
- Auto-write a copy of the trail into a per-row "Activity" column on the project sheet on every event. (Compact format: one line per event, latest first.)

### Files
- Modified: `vendor_followups.py` (events field, snooze fields, due-for-reminder filter)
- Modified: `tools/project_invoices.py` (5 new tools registered)
- Tests: `tests/test_snooze_bulk.py` new file

### Acceptance criteria
- A snoozed entry with `until_date=2026-05-15` doesn't appear in `due_for_reminder()` output until that date.
- Bulk-resolve clears 5 entries in one call atomically (all-or-nothing on file write).
- Each entry's `events` list grows by exactly 1 entry per state change.
- The Activity column on a project sheet shows the trail formatted as "2026-04-12 ASK · 2026-04-19 R1 · 2026-04-24 R2 — final".

### Effort
**2 days.** A and C are 0.5 day each. B is 1 day (atomicity needs care — file write under contention).

---

## P1-7 — Slack integration  ❌ REMOVED FROM ROADMAP 2026-05-03

Slack support was scoped here on 2026-04-29 and deferred through v0.8.x. Removed from future development on 2026-05-03 per Joshua's call: the AP/AR build-out closes via Google Chat alone, and Slack parity isn't worth the maintenance surface. If a real customer need surfaces post-1.0.0, the spec lives in this file's git history.

---

## How to use these specs

1. Read the one(s) you want to tackle next.
2. If you want to amend scope, tell me which spec and what to change. I'll update.
3. When you're ready to build, say "build P0-2" (or whichever). I follow the spec exactly. Anything not in the spec, I ask.

If we kept this rate going (5 quick wins in one session, ~3 hours), the realistic path is:
- **Session 2:** P0-2 + P1-4 (vendor reply parsing + smarter cadence) — ~4 days of work compressed
- **Session 3:** P0-3 (test bundle) + P1-1 (split workflows.py) — ~6 days of work compressed
- **Session 4:** P1-5 (snooze/bulk/trail) + P1-7 (Slack) — ~7 days of work compressed
