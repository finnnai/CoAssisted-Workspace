# Google Workspace MCP — User Manual

**100 practical workflows you can run through Claude Cowork. Plus guides for sharing, extending, and operating the system.**

| Field | Value |
|---|---|
| **Owner** | Alice (alice@example.com) |
| **MCP version** | 0.2.0 |
| **Total tools** | 90 across Gmail, Calendar, Drive, Sheets, Docs, Tasks, Contacts, Chat, and cross-service workflows |
| **Date authored** | 2026-04-24 |
| **Project location** | /Users/finnnai/Claude/google_workspace_mcp |

---

# Getting started

This manual covers what your Google Workspace MCP can do and how to ask for it. The MCP runs locally on your Mac as a stdio process that Claude Cowork launches on demand. Your OAuth tokens live in the project folder — nothing is shared with Anthropic, nothing leaves your machine except to call Google's APIs.

### How to use this manual

- Browse by category in the "100 workflows" section. Pick the 5–10 that match your day-to-day.
- You don't need to name tools or parameters. Claude picks the right tool from your plain-English request.
- For destructive actions (send, delete, move), append "do a dry run" to preview instead of execute.
- Templates, rules, and config files are human-editable and reload without restarts.

### Three useful habits

- **Dry-run before a batch.** The "do a dry run" suffix works on send_email, mail merge, delete, move, trash.
- **Start with a read.** "Show me …" before "Send …" helps you verify data before acting on it.
- **Let Claude compose multi-step flows.** If no single tool fits, describe the goal — Claude will chain primitives.

---

# 100 workflows

## Gmail — 25

1. Send an email to alice@example.com with subject 'Proposal' and body '…'.
2. Draft an email to Bob but don't send — save it as a draft.
3. Reply to Josh's last email saying 'Confirmed, see you Thursday.'
4. Reply-all to the most recent thread with the team.
5. Forward yesterday's invoice email to accounting@mycompany.com with a note 'please process.'
6. Search Gmail for emails from anyone at @acme.com in the last 30 days.
7. Find emails with attachments from last week.
8. Show me the full thread I'm having with Josh about the Q3 roadmap.
9. Send /Users/finnnai/Desktop/proposal.pdf as an attachment to Josh.
10. Download the PDF attachment from the invoice email to my Desktop.
11. Forward the latest Josh thread to Bob with a one-line intro.
12. Archive all newsletter emails from last week.
13. Move that phishing-looking email to Trash.
14. Find every email where I'm CC'd but haven't replied.
15. Show me my starred emails from this month.
16. Create a Gmail label called 'Priority Clients'.
17. Label every email from acme.com as 'Priority Clients'.
18. Set up a Gmail filter: anything from @vendor.com auto-gets 'Vendors' label.
19. Delete my filter that routes newsletters to 'Read later'.
20. List all my saved drafts.
21. Show me the templates I have saved.
22. Send the `cold_outreach` template to a new contact named Sarah at sarah@startup.co.
23. Send the `follow_up_after_meeting` template to Josh and Conor.
24. What send-as aliases do I have available in Gmail?
25. Show me every Gmail filter rule on my account.

## Calendar — 15

26. What's on my calendar today?
27. What meetings do I have next week?
28. Schedule a 30-min call with Josh next Tuesday at 3pm, add a Meet link.
29. Book 'Dentist Thursday 2pm' on my calendar.
30. Create a Friday 10am meeting with Josh, Conor, and me — include a Meet link.
31. Reschedule my 3pm tomorrow to 4pm.
32. Cancel my Thursday meeting with Acme.
33. Accept the invite from Sarah for next Monday.
34. Decline the Tuesday standup.
35. Tentatively accept the product review Friday.
36. When am I free next Wednesday between 10am and 4pm?
37. Find a 1-hour slot that works for me and Josh next week.
38. What meetings do I have with Josh coming up?
39. What calendars can I see?
40. Create an all-day 'PTO' event for next Friday.

## Drive — 10

41. Find every Drive file with 'budget' in the name.
42. Search Drive for docs modified in the last 7 days.
43. Read the Google Doc titled 'Q2 Roadmap'.
44. Download that PDF to my Desktop.
45. Upload /Users/finnnai/Desktop/proposal.pdf to my Drive.
46. Create a folder called 'Client Files' in Drive.
47. Move 'proposal.pdf' into the 'Client Files' folder.
48. Share the 'Q2 Plan' doc with Josh as a commenter.
49. Delete the 'temp' folder (move to Trash).
50. Make the Q3 Plan Sheet shareable with anyone in my org who has the link.

## Sheets — 5

51. Create a new Google Sheet called 'Sales Pipeline'.
52. Add a tab called 'Q3 Forecast' to my Sales Pipeline sheet.
53. Read rows 1-20 of my Sales Pipeline sheet.
54. Append the row `Acme | prospect | $10k | Josh` to my Sales Pipeline.
55. Overwrite A1:C3 in the Budget sheet with new data.

## Docs — 5

56. Create a Google Doc titled 'Client Brief — Acme'.
57. Read the doc with ID `1abc…xyz`.
58. Append a 'Next steps:' section to the end of that Doc.
59. Find 'TBD' in the proposal doc and replace with 'Finalized'.
60. Insert a title paragraph at the top of the doc.

## Tasks — 5

61. What Google Tasks lists do I have?
62. Show me my open tasks.
63. Create a task 'Call Bob about Q3' due tomorrow.
64. Mark 'Review contract' as complete.
65. Move that task's due date to next Monday.

## Contacts + CRM — 15

66. Look up Josh Szott in my contacts.
67. Create a contact for Bob (bob@example.com) at PartnerCo, title 'CTO'.
68. Update Josh's title to 'Head of Sales'.
69. Delete the duplicate contact for Sarah.
70. Add a note to Josh's contact: 'Met at the summit, interested in enterprise plan.'
71. Tag Conor: `stage=prospect`, `tier=growth`, `industry=HR tech`.
72. Create a contact group called 'Q2 Prospects'.
73. Add Josh and Bob to the Q2 Prospects group.
74. Who's in my Q2 Prospects group?
75. Remove Josh from Q2 Prospects.
76. When did I last email Conor?
77. Show me the last 10 emails between me and Conor.
78. Refresh the managed CRM fields (`Last Interaction`, `Sent, last 60`, `Received, last 60`) on every contact.
79. Run my auto-tagging rules against all contacts to backfill any missing tags.
80. Export all my contacts to /Users/finnnai/Desktop/contacts.csv.

## Google Chat — 10

81. List my Google Chat spaces.
82. Show me the last 20 messages in the 'Sales Team' space.
83. Post 'Just closed the Acme deal' to the Sales Team space.
84. Reply in that Chat thread with 'Agreed, great call.'
85. Edit my last Chat message to add a quick note.
86. Delete that Chat message.
87. Who's in the 'Engineering' space?
88. Download the attachment from Josh's latest Chat message to my Desktop.
89. Send Bob a direct Chat message: 'Heads up — the proposal just went out.'
90. Show me messages from Josh in the Sales Team space from last week.

## Cross-service workflows — 10

91. Export the Q3 Roadmap doc as a PDF and email it to Josh and Conor.
92. Save the PDF attachment from the Acme invoice email to my 'Invoices' folder in Drive.
93. Share the 'Q3 Plan' doc with bob@example.com as commenter and email him the link with a note.
94. Turn the email thread with Josh into a calendar event for Thursday 2pm — auto-invite the attendees from the thread.
95. Send the `cold_outreach` template to every contact in the Q2 Prospects group.
96. Send the `follow_up_after_meeting` template to Josh, Conor, and three others with first names auto-filled.
97. Do a dry run: preview what the `re_engage` template would send to every contact in Q2 Prospects.
98. Export every contact in my Q2 Prospects group to /Users/finnnai/Desktop/q2_prospects.csv.
99. Activity log is on by default — every templated email auto-appends a timestamped note to the recipient's contact.
100. Kick off a full CRM refresh: update Last Interaction and 60-day counts across all contacts in one batch.

---

# Power tips

### Dry-run mode

Every destructive or side-effecting tool accepts `dry_run=true`. The tool returns a JSON preview with the exact payload it would send (e.g., recipients, subject, rendered body) without actually performing the action. Flip `dry_run` to `true` in `config.json` to make it the global default while you're learning.

### Templates

Saved email templates live as Markdown files in the `templates/` folder. YAML frontmatter at the top defines the `subject` (and optional `html_body` and `description`); the rest of the file is the body. Placeholders like `{first_name|there}` substitute contact fields with fallback text. Three templates ship by default: `cold_outreach`, `follow_up_after_meeting`, `re_engage`. Edit them freely — no restart needed.

### Auto-tagging rules

The `rules.json` file maps email domains to contact field defaults. On every `contacts_create` and `contacts_update`, matching rules fill in blanks — they never overwrite existing values. Use `contacts_apply_rules` to backfill your existing contacts after editing `rules.json`.

### Managed CRM fields

Three fields on every contact are managed by the MCP: `Last Interaction`, `Sent, last N`, `Received, last N` (where N is your `config.crm_window_days`, default 60). These refresh on create/update and via the refresh tools. They can't be written manually — that's intentional, so the values stay truthful.

### Mail-merge partial failures

Batch sends don't abort on individual failures. Each recipient returns an independent status: `sent`, `failed`, or `skipped`. To halt on first error, pass `stop_on_first_error=true`.

---

# Sharing the MCP with a coworker

When a teammate wants the same capabilities, you don't share your credentials — you hand them the code and point them at their own Google Cloud project. The "handoff" function packages everything they need into a clean archive without leaking your secrets.

## Why handoff (and why it's separate from your install)

- Your OAuth tokens act on your Gmail/Calendar/etc. A coworker must have their own token, with their own GCP project, or they'd be acting as you.
- The archive excludes every secret: `credentials.json`, `token.json`, `config.json`, `rules.json`, and `logs/`.
- What's shipped is source code, install script, docs, and example templates. Fresh install on their machine.

## How to create the archive

In Terminal, from the project folder:

```bash
cd /Users/finnnai/Claude/google_workspace_mcp
make handoff
```

Output lands in `dist/` with a date-stamped filename like `google-workspace-mcp-2026-04-24.tar.gz`. The `make` target prints the path and runs a self-check to prove no secrets leaked into the archive.

## What to send the coworker

- The .tar.gz file (AirDrop, email, Slack DM, shared Drive — anywhere).
- A one-line note: "Open HANDOFF.md first." That doc is inside the archive and walks them through setup.

## What the coworker does on their end

Steps they follow (from HANDOFF.md):

1. Move the .tar.gz out of Downloads (macOS rotates Downloads) and extract somewhere persistent like `~/Developer`.
2. Clear macOS quarantine on the extracted folder if prompted: `xattr -dr com.apple.quarantine .`
3. Open `GCP_SETUP.md` and create their own Google Cloud project (10 min). This step cannot be skipped or shared — OAuth credentials are personal to the user and project.
4. Run `./install.sh` to bootstrap the Python environment.
5. Run `./install.sh --oauth` to consent with their own Google account and save their `token.json`.
6. Edit `~/Library/Application Support/Claude/claude_desktop_config.json` to add an `mcpServers` block pointing at their install path.
7. Quit and reopen Claude Cowork — their 90 tools are now live.

### A short summary you can paste when you send the archive

> Hi — attached is the Google Workspace MCP archive. It gives Claude Cowork about 90 tools across Gmail, Calendar, Drive, Chat, and more. Extract it somewhere persistent, open HANDOFF.md, and follow the steps. You'll need to create your own Google Cloud project in the process (the README walks through it). Takes about 15 minutes of hands-on time plus a couple of minutes waiting for installs.

---

# Extending the MCP: Adding a new tool

## Why you'd add one

- A Google API capability you use regularly isn't wrapped yet (e.g., Gmail "snooze", Calendar "working hours", Drive "file comments").
- You find yourself asking Claude for the same multi-step combination often. A dedicated tool is faster and more consistent than repeated composition.
- You want a convenience wrapper around an existing tool (e.g., `send_weekly_update_email` that hard-codes your recipient list and subject format).

## The signal that it's time

Count how often you compose the same 3+ tool calls in a row. If that combination fires more than a few times a week, the glue logic belongs inside a single tool. Fewer round-trips, tighter error handling, and Claude doesn't need to rebuild the orchestration from context each time.

## How to add one

Every tool lives inside a service module at `tools/<service>.py`. The module is already wired into the server registration — you just add a function and an input model.

### Step 1 — Pick the right module

- `tools/gmail.py`, `tools/calendar.py`, `tools/drive.py`, `tools/sheets.py`, `tools/docs.py`, `tools/tasks.py`, `tools/contacts.py`, `tools/chat.py` — one per Google service.
- `tools/workflows.py` — for cross-service or composed tools (email → drive, doc → PDF → email, etc.).

### Step 2 — Add a Pydantic input model

At the top of the module with the other models. Required fields use `Field(...)`; optional fields use `Field(default=None, description='...')`. Use clear descriptions — they become part of the tool's schema that Claude sees.

```python
class SnoozeEmailInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    message_id: str = Field(..., description='Gmail message ID to snooze.')
    until: str = Field(..., description='ISO 8601 datetime when the email should return to inbox.')
```

### Step 3 — Add the async tool function

Register with `@mcp.tool`. The name should be snake_case with service prefix (`gmail_`, `calendar_`, etc.). Annotations describe the tool's behavior for Claude.

```python
@mcp.tool(
    name='gmail_snooze',
    annotations={
        'title': 'Snooze a Gmail message',
        'readOnlyHint': False,
        'destructiveHint': False,
        'idempotentHint': True,
        'openWorldHint': True,
    },
)
async def gmail_snooze(params: SnoozeEmailInput) -> str:
    """Snooze a Gmail message until the given datetime."""
    try:
        # Your Gmail API call here. Use _service().
        return json.dumps({'status': 'snoozed', 'id': params.message_id})
    except Exception as e:
        return format_error(e)
```

### Step 4 — Quit Cowork, reopen it

The MCP is installed in editable mode (`pip install -e`). No reinstall needed — just a Cowork restart so it picks up the new tool. Use `make test` first to confirm nothing broke.

### Quality checklist

- Clear Pydantic model with descriptions on every field.
- Error handling via `format_error(e)` — returns a user-friendly string instead of a stack trace.
- Dry-run support for destructive operations (use `is_dry_run()` and `dry_run_preview()`).
- A unit test in `tests/` if the function has pure logic (rendering, parsing, validation).

---

# Extending the MCP: Adding a new workflow

## Tool vs workflow

A "tool" calls one Google API endpoint. A "workflow" chains multiple tools or API calls into one logical operation — "save email attachment to Drive" needs Gmail (get attachment) plus Drive (upload file). Workflows live in `tools/workflows.py` and are surfaced to Claude with the `workflow_` prefix.

## Why workflows matter

- Fewer round-trips between Claude and the MCP on common multi-step patterns.
- Atomic error handling — if step 2 fails after step 1 succeeded, the workflow returns a structured partial-success report instead of a half-broken state.
- Claude picks `workflow_email_doc_as_pdf` instead of composing `export_pdf` + `send_with_attachment`. Result: more reliable, faster, less LLM reasoning needed.

## The four built-in workflows

- `workflow_save_email_attachments_to_drive` — fetch Gmail attachments, upload to Drive.
- `workflow_email_doc_as_pdf` — export Google Doc as PDF, send as email attachment.
- `workflow_share_drive_file_via_email` — grant permission and email the link.
- `workflow_email_thread_to_event` — convert a Gmail thread into a Calendar invite with auto-extracted attendees.

## When to add a new one

- You're repeatedly chaining 3+ tool calls with the same structure.
- The composition has non-trivial data transformation (parsing, aggregation, format conversion).
- Partial failure needs to be reported in a domain-aware way (e.g., "sent to 8 of 10 recipients, 2 bounced").

## How to add one

### Step 1 — Open `tools/workflows.py`

Define a Pydantic input model next to the existing ones. Workflow models often have richer params — lists of targets, dry-run flags, partial-failure behavior toggles.

### Step 2 — Write the async function

Use the helper service getters already in the file: `_gmail()`, `_drive()`, `_calendar_svc()`. For People API, call `gservices.people()` directly. Chain the calls, catching exceptions per-step so one failure doesn't abort the whole workflow.

```python
@mcp.tool(name='workflow_my_pattern', annotations={...})
async def workflow_my_pattern(params: MyInput) -> str:
    try:
        if is_dry_run(params.dry_run):
            return dry_run_preview('workflow_my_pattern', {...})
        # Step 1: fetch from service A
        # Step 2: transform
        # Step 3: write to service B
        # Step 4: return structured status (success + per-step details)
        return json.dumps({'status': 'ok', 'details': {...}}, indent=2)
    except Exception as e:
        log.error('workflow_my_pattern failed: %s', e)
        return format_error(e)
```

### Step 3 — Handle partial success

Workflows that operate on lists (recipients, files, contacts) should accumulate per-item results rather than aborting on the first failure. Caller-visible output should be a structured summary: total, sent/failed/skipped counts, and per-item detail.

### Step 4 — Test with a dry run first

Every workflow should honor `dry_run` and return a meaningful preview — typically showing what would happen to each item in the batch. Ship dry-run support before real execution. It's cheaper than cleaning up a mistake.

---

# Closing notes

This system is meant to be extended. The architecture — one module per service, workflows for compositions, a template library, auto-tagging rules — is designed so adding capability doesn't require rebuilding anything. When you notice friction in your daily use, that's the cue to add a tool or a workflow.

## Where to go when something breaks

- `README.md` has a Troubleshooting section covering the common 401/403/scope issues.
- `logs/google_workspace_mcp.log` captures errors from the MCP (rotates at 5MB × 3 backups).
- `make test` runs the 48-test pytest suite to verify pure-function correctness.
- Delete `token.json` + `make auth` to re-consent if your OAuth grant breaks.

## When to hand off to Claude directly

This manual is a reference — not a script to follow. If you want a specific outcome, describe the outcome, not the steps. Claude is better at picking tools than you are at remembering tool names. Your role is judging whether the result is right; Claude's role is assembling the calls.

---

*End of manual — 2026-04-24 — Google Workspace MCP v0.2.0*
