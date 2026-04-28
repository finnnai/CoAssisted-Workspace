# CoAssisted Workspace — User Manual

*(formerly "Google Workspace MCP")*

**Version:** 0.3.0
**Last updated:** 2026-04-26
**Tools:** 162 across 12 categories (incl. flagship Receipt Extractor)

A local MCP server that gives Claude Cowork end-to-end control of your Gmail, Calendar, Drive, Sheets, Docs, Tasks, Contacts (with a CRM layer), and Chat — plus cross-service workflows, brand-voice extraction, and optional LLM-backed enrichment.

Runs locally. Your data goes to Google's APIs only. Each coworker installs their own copy with their own OAuth credentials.

---

## Quick start (15 commands to try)

Once installed and OAuth'd, ask Cowork any of these:

**Workspace basics:**
1. *"List my calendars"* — sanity check (calendar_list_calendars)
2. *"Send Josh an email saying I'm running 5 minutes late"* — gmail_send_email
3. *"Show me my saved contacts"* — contacts_list
4. *"What's my last interaction with conor@staffwizard.com?"* — contacts_last_interaction
5. *"Find a 30-minute slot next week for me, Josh, and Brian, between 10am and 4pm"* — workflow_find_meeting_slot

**CRM enrichment:**
6. *"Bulk create contacts from my sent mail in the last 30 days"* — workflow_create_contacts_from_sent_mail
7. *"Enrich my contacts from recent inbox signatures, dry-run first"* — workflow_enrich_contacts_from_recent_mail
8. *"Send the cold_outreach template to Josh, dry-run"* — gmail_send_templated_by_name

**Calendar logistics:**
9. *"Create a recurring weekly Surefox standup, Mondays 9am, 10 weeks"* — calendar_create_event with recurrence
10. *"Auto-create drive-time blocks for my next 7 days, dry-run"* — workflow_calendar_drive_time_blocks
11. *"What's my commute brief for tomorrow?"* — workflow_commute_brief (uses your current location automatically)

**Maps × CRM:**
12. *"Find contacts within 25 km of Austin, TX"* — workflow_nearby_contacts
13. *"Plan an optimal route through these 5 sales visits, two vehicles, time windows 9–12"* — workflow_route_optimize_advanced
14. *"Email allan@surefox.com a map of where to meet on Friday"* — workflow_email_with_map

**Operational:**
15. *"Send the handoff archive to allan@surefox.com"* — workflow_send_handoff_archive

---

## What's new in 0.4.0 (receipt extractor maturation)

This release matures the receipt extractor from a 4-tool MVP into a 9-tool flagship feature with a 5-tier enrichment ladder, persistent learning, multi-source ingestion, and full QuickBooks alignment.

### Multi-source ingestion
- `workflow_extract_receipts` now scans **inbox + Drive folder + Gchat space** in one pass (any combination via params). Cross-branch source-id dedup means the same receipt doesn't get extracted twice when it arrives via two paths.
- `workflow_extract_receipts_from_chat` — standalone scan for dedicated `#receipts` Gchat rooms.

### 5-tier enrichment ladder
- **Tier 0 — merchant cache** (persistent JSON at project root). Receipts from known merchants skip Tiers 2/3 entirely → $0 enrichment cost. Manual recategorizations seed `manual_correction` entries that beat any other source.
- **Tier 4 — EXIF + sender attribution.** Reads photo timestamps + GPS via Pillow; appends `[Metadata]` block to notes. When LLM date is suspect (conf<0.6 or >12mo off from EXIF), uses EXIF date and stamps the override.

### Multi-sheet support
- `workflow_list_receipt_sheets` discovers all `Receipts — *` sheets in your Drive.
- `workflow_create_receipt_sheet` spins up a new expense sheet with the 17-col header.
- The orchestrator accepts `sheet_id` OR `sheet_name`; calling without either returns a structured `needs_sheet` discovery list.
- `workflow_recategorize_receipt` and `workflow_export_receipts_qb_csv` accept `sheet_name` for parity.

### QuickBooks-native categories
Internal taxonomy collapsed from 23 hand-rolled categories to 20 QBO standard Chart of Accounts (Advertising, Auto Expense, Software Subscriptions, etc.). The QB CSV export is now a 1:1 identity mapping with no translation layer. Legacy categories migrate automatically via `LEGACY_CATEGORY_MAP`.

### Content-based dedup
`receipts.content_key()` fingerprints by normalized merchant + date + total_cents + last_4. Catches the same physical receipt arriving via 3 different file_ids (e.g. 3 photos of one Chevron purchase) before the orchestrator writes a duplicate row.

### Pillow image shrink
Phone photos (6–18MB) auto-shrink before Claude Vision (5MB API cap). New `Pillow>=10.0` dependency. Fails-soft if Pillow missing.

### Loop-safe channel scanning
`BOT_FOOTER_MARKER` stamped on bot-generated reports; classifier rejects any message containing it so re-scanning a chat that has bot posts won't try to re-extract from them.

### Bugs caught + fixed (8 in this release)
MCP boot failure (param prefix), Pydantic null currency/category, account-notification false positives, Stripe activation mail leak, Stripe-hosted receipts wrongly rejected, Travel — Fuel category gap in QB map, oversized phone-photo rejection, opaque chat sender ID. See README's history if needed.

---

## What's new in 0.3.0

This release adds three big capabilities + a junk filter overhaul + 16 caught bugs fixed.

### Brand voice from your sent mail
A new `refresh_brand_voice.py` script analyzes your last 90 days of sent mail and writes a personalized voice guide (`brand-voice.md`) capturing your sentence rhythm, sign-offs, vocabulary, and punctuation habits. Two modes:

- **Heuristic** (default, free): structured stats on signoffs/openers/punctuation/vocabulary
- **LLM** (with Anthropic key, ~$0.05-0.15 per run): 8-section qualitative analysis using Claude Sonnet that quotes your actual phrasing

Cron-installable to refresh quarterly. The 8 mail-merge templates that ship with this MCP have already been refreshed to match the voice analysis (em-dashes, "— Finnn" close, no exclamation points, "Ping me" instead of "let me know").

### Calendar polish
- **Recurring events**: pass `recurrence_pattern: "daily"|"weekdays"|"weekly"|"biweekly"|"monthly"|"yearly"` to `calendar_create_event`. Combine with `recurrence_count` (e.g. 10 occurrences) or `recurrence_until` (e.g. `"2026-12-31"`). Power users can pass raw RFC 5545 `recurrence_rrule`.
- **`workflow_find_meeting_slot`** — multi-attendee, time-zone-aware free/busy lookup. Returns top N earliest slots within preferred working hours, optionally skipping weekends. Auto-includes you in the attendee list.
- **`workflow_detect_ooo`** — scans inbox for OOO auto-replies and flags matching contacts with `out_of_office: true` plus parsed return date as `ooo_until`.

### LLM-backed signature parsing (opt-in)
When regex extraction misses the title or organization on a contact's signature, optionally falls back to Claude Haiku to fill the gap (~$0.001 per call). Set `signature_parser_mode: "regex_then_llm"` in `config.json`. Detects garbage names regex sometimes pulls from notification emails ("the link above", "All rights reserved") and lets LLM override.

### Smart attachment handling
- **Outbound:** Files larger than 500KB (configurable) auto-stream to Drive, get shared with recipients, and the link is appended to the email body. Total message size is pre-checked against Gmail's 25MB ceiling.
- **Inbound:** Files larger than 5MB (configurable) auto-save to `~/Gmail Downloads` (a dedicated folder, kept separate from your cluttered system Downloads). Filename collisions get a timestamp suffix.
- **`attachments`** accepts both bare string paths AND `AttachmentSpec` dicts.

### 16 bugs caught + fixed
Parameter naming aliases (`q` → `query`, `max_results` → `limit`, `output_path` → `path`, `note` → `comment`, `from_` ↔ `from`), attachment-string coercion, junk filter (16 hard-fail signal categories + soft signals + 58-phrase spam-hype list), self-exclusion in enrichment, filename fallback for Gmail's rotating attachment IDs, contacts_search now uses `connections().list()` instead of buggy `searchContacts`. See README's troubleshooting section for the full list.

---

## Setting up the optional LLM features

You only need this if you want the brand voice deep analysis, smart signature parser, or pipeline-level LLM features. Heuristic / regex defaults work for everything without a key.

**1. Get an Anthropic API key**

- Go to https://console.anthropic.com → Settings → API Keys → Create Key
- Set up billing (Settings → Billing — new accounts get $5 free)
- Copy the key (starts with `sk-ant-api03-...`)

**2. Add to your config**

Easiest: paste into `config.json`:
```json
{ "anthropic_api_key": "sk-ant-api03-..." }
```

`config.json` is gitignored AND excluded from `make handoff` tarballs — keys never travel. Each coworker uses their own.

(Alternatively, export `ANTHROPIC_API_KEY` in your shell. Note that macOS GUI Cowork doesn't always inherit shell env vars — the config-file path is more reliable.)

**3. Verify**

In Cowork: *"check my anthropic key"* — invokes `system_check_anthropic_key` which makes one tiny test call. If it fails, the response includes targeted setup instructions (no billing, revoked key, network blocked, etc.).

**4. Optionally enable smart signature parsing**

```json
{ "signature_parser_mode": "regex_then_llm" }
```

Three modes:
- `"regex"` (default) — regex only, free
- `"regex_then_llm"` — regex first, LLM fills gaps when title/org missing (~$0.001 per gap-fill)
- `"llm"` — always run both, merge

Restart Cowork after config changes.

**Cost ceiling estimate:**

| Feature | Frequency | Cost |
|---|---|---|
| `system_check_anthropic_key` | Setup only | $0.0001/check |
| `refresh_brand_voice.py` (LLM mode) | Quarterly | $0.05-0.15/run |
| Smart signature parsing | Daily cron | $0-1/month (depends on traffic) |

For a typical user: **$1-15 per year** if all LLM features are enabled.

---

## Cron jobs

Three scripts ship with this MCP, all designed for cron:

| Script | Cadence | Purpose |
|---|---|---|
| `refresh_stats.py` | Daily | Recompute `Last Interaction` + `Sent/Received last 60` for every saved contact |
| `enrich_inbox.py` | Daily | Parse signatures from new inbound mail, enrich matching contacts |
| `refresh_brand_voice.py` | Quarterly | Regenerate `brand-voice.md` from your last 90 days of sent mail |

Add these to `crontab -e`:

```
0 7 * * *   /Users/YOUR_USER/Claude/google_workspace_mcp/.venv/bin/python /Users/YOUR_USER/Claude/google_workspace_mcp/refresh_stats.py >> /Users/YOUR_USER/Claude/google_workspace_mcp/logs/refresh_stats.cron.log 2>&1
5 7 * * *   /Users/YOUR_USER/Claude/google_workspace_mcp/.venv/bin/python /Users/YOUR_USER/Claude/google_workspace_mcp/enrich_inbox.py >> /Users/YOUR_USER/Claude/google_workspace_mcp/logs/enrich_inbox.cron.log 2>&1
0 6 1 */3 * /Users/YOUR_USER/Claude/google_workspace_mcp/.venv/bin/python /Users/YOUR_USER/Claude/google_workspace_mcp/refresh_brand_voice.py >> /Users/YOUR_USER/Claude/google_workspace_mcp/logs/refresh_brand_voice.cron.log 2>&1
```

All three scripts accept `--dry-run` for safe previews and self-rotate their cron logs at 10MB.

---

## Tool reference (182 tools across 13 categories)


### Gmail (17)
`gmail_send_email`, `gmail_create_draft`, `gmail_list_drafts`, `gmail_reply_to_thread`, `gmail_forward_message`, `gmail_search`, `gmail_get_thread`, `gmail_download_attachment`, `gmail_trash_message`, `gmail_untrash_message`, `gmail_list_labels`, `gmail_create_label`, `gmail_update_label`, `gmail_delete_label`, `gmail_modify_labels`, `gmail_list_filters`, `gmail_create_filter`, `gmail_delete_filter`, `gmail_list_send_as`

### Calendar (8)
`calendar_list_events`, `calendar_list_calendars`, `calendar_create_event` (with recurrence), `calendar_quick_add`, `calendar_update_event`, `calendar_delete_event`, `calendar_respond_to_event`, `calendar_find_free_busy`

### Drive (9)
`drive_search_files`, `drive_read_file`, `drive_upload_text_file`, `drive_upload_binary_file` (streaming for large files), `drive_download_binary_file` (auto-save for large files), `drive_create_folder`, `drive_move_file`, `drive_share_file`, `drive_delete_file`

### Sheets (7)
`sheets_create_spreadsheet`, `sheets_list_sheets`, `sheets_add_sheet`, `sheets_delete_sheet`, `sheets_read_range`, `sheets_write_range`, `sheets_append_rows`

### Docs (4)
`docs_create_document`, `docs_read_document`, `docs_insert_text`, `docs_replace_text`

### Tasks (6)
`tasks_list_task_lists`, `tasks_list_tasks`, `tasks_create_task`, `tasks_update_task`, `tasks_complete_task`, `tasks_delete_task`

### Chat (18)
**Tier 1 — workflow gap closers** (just added):
`chat_send_dm` (find-or-create + send by email in one call), `chat_send_to_space_by_name` (find a space by display name), `chat_search` (cross-space message search), `chat_who_is_in_dm` (identify the other party in a DM)

**Tier 2 — bigger projects:**
`chat_send_attachment` (upload + send a file, 200MB cap), `chat_recent_activity` (catch-up tool: spaces with new messages)

**Tier 3 — polish:**
`chat_react_to_message` (emoji reactions), `chat_get_thread` (full reply thread)

**Original Chat tools:** `chat_list_spaces`, `chat_find_or_create_dm`, `chat_get_space`, `chat_list_messages`, `chat_get_message`, `chat_send_message`, `chat_update_message`, `chat_delete_message`, `chat_list_members`, `chat_download_attachment`

> **Note:** Google Chat needs a one-time GCP "Chat App" registration on top of just enabling the API. See `GCP_SETUP.md` Step 2b. The most common gotcha is the **HTTP endpoint URL** field — use `https://YOUR_DOMAIN/api/chat-webhook` as a placeholder; Google validates the format only, not reachability.

### Contacts / CRM (20)
`contacts_search`, `contacts_list`, `contacts_get`, `contacts_create`, `contacts_update`, `contacts_delete`, `contacts_add_note`, `contacts_set_custom_field`, `contacts_list_groups`, `contacts_create_group`, `contacts_add_to_group`, `contacts_remove_from_group`, `contacts_list_group_members`, `contacts_last_interaction`, `contacts_recent_interactions`, `contacts_refresh_crm_stats`, `contacts_refresh_all_crm_stats`, `contacts_apply_rules`, `contacts_export_csv`, `contacts_import_csv`

### Templates & mail merge (6)
`gmail_list_templates`, `gmail_get_template`, `gmail_send_templated`, `gmail_send_templated_by_name`, `gmail_send_mail_merge`, `gmail_send_mail_merge_by_name`

### Workflows (29)
`workflow_save_email_attachments_to_drive`, `workflow_email_doc_as_pdf`, `workflow_share_drive_file_via_email`, `workflow_email_thread_to_event`, `workflow_send_handoff_archive`, `workflow_create_contacts_from_sent_mail`, `workflow_enrich_contact_from_inbox`, `workflow_enrich_contacts_from_recent_mail`, `workflow_find_meeting_slot`, `workflow_detect_ooo`, `workflow_chat_digest`, `workflow_chat_to_contact_group`, `workflow_email_with_map`, `workflow_meeting_location_options`, `workflow_chat_with_map`, `workflow_chat_share_place`, `workflow_chat_meeting_brief`

### Receipts (9) — *flagship paid feature*
`workflow_extract_receipts` (orchestrator: inbox + Drive folder + Gchat space, pass any combination via `chat_space_id` / `drive_folder_id`), `workflow_extract_receipts_from_chat` (standalone Chat sweep), `workflow_extract_one_receipt` (single email or Drive file by ID), `workflow_recategorize_receipt` (edit one row's category — also seeds the merchant cache with a `manual_correction` so future receipts from the same vendor get the right category for free), `workflow_export_receipts_qb_csv` (QB-importable CSV; categories are 1:1 with QBO Chart of Accounts), `workflow_list_receipt_sheets` (auto-discover all your `Receipts — *` sheets), `workflow_create_receipt_sheet` (create a new expense sheet with the 17-col header), `workflow_list_known_merchants` (audit the persistent cache), `workflow_forget_merchant` (drop one cache entry).

**Architecture:** 5-tier enrichment ladder fires when LLM confidence < 0.6:
- **Tier 0 — Merchant cache** (`merchants.json` in project root, 365-day TTL). Free.
- **Tier 1 — LLM extraction** via Claude Haiku 4.5 (Vision for PDF/image, text for plain bodies).
- **Tier 2 — Maps Places** verifies merchant at receipt's address.
- **Tier 3 — Anthropic web_search** identifies merchant type when Maps doesn't help.
- **Tier 4 — EXIF + sender attribution.** Reads photo timestamp + GPS via Pillow, stamps a `[Metadata]` block onto notes. If LLM date is suspect, prefers EXIF date. Sender comes from email From or Chat sender (resolved via People API when `displayName` is null).

**Dedup:** source-id (Gmail message_id / Drive file_id / `chat:<space>/<msg>`) + content-key (normalized merchant + date + total_cents + last_4) catches the same physical receipt arriving via multiple paths.

**20 expense categories** match QBO standard Chart of Accounts 1:1 (Advertising, Auto Expense, Bank Service Charges, Computer & Equipment, Contract Labor, Dues & Subscriptions, Insurance, Legal & Professional Fees, Meals, Office Supplies, Postage & Delivery, Printing & Reproduction, Rent Expense, Repairs & Maintenance, Software Subscriptions, Taxes & Licenses, Telephone Expense, Travel, Utilities, Miscellaneous Expense). The QB CSV export is now a 1:1 identity mapping.

**Loop-safe:** classifier rejects messages containing `BOT_FOOTER_MARKER` so re-scanning a chat that contains the bot's own report posts won't re-extract them.

Privacy: full PANs never extracted; `last_4` redacted by default before persisting (config `receipts_redact_payment_details`).

### Project Spend (7) — *unified per-project sheet for invoices + receipts*
`workflow_extract_project_invoices` (flagship invoice path — scans inbox + Drive folder + Gchat space, auto-classifies invoice vs. receipt, resolves project_code via the 5-tier registry ladder, routes to per-project sheet or parks in `Project Invoices — Needs Project Assignment`), `workflow_extract_project_receipts` (receipt sibling — scans inbox + Drive folder + Gchat space using the existing receipt classifier, runs the 5-tier enrichment ladder, appends `doc_type='receipt'` rows to the same sheet), `workflow_register_project` (bootstrap a project with sender emails / chat space IDs / filename regex patterns / default billable / default markup; creates the per-project sheet on first call), `workflow_list_projects` (inventory + counts), `workflow_create_project_sheet` (re-create a project's sheet — idempotent), `workflow_move_invoice_to_project` (move any row by content_key or row_number; works for invoices and receipts; source row cleared, counts re-tallied), `workflow_export_project_invoices_qb_csv` (QB Bills-importable CSV — invoices only; default filter `OPEN` + `APPROVED`).

**5-tier project resolver:** explicit `project_code` (1.00) → filename regex (0.95) → sender email (0.90) → chat space (0.85) → LLM inference (variable). Resolutions below 0.65 confidence get parked.

**Unified sheet schema:** 27 columns. `doc_type` (invoice|receipt) sits at index 1; everything else is shared. For receipts the invoice-specific fields (due_date, invoice_number, po_number, payment_terms, bill_to, remit_to) stay blank, status is `PAID`, days_outstanding is `0`. Invoice status lifecycle: `OPEN → APPROVED → PAID` (or `DISPUTED` / `VOID`). Dedup: source_id + content_key — invoices use `vendor|invoice_number|cents`, receipts use `merchant|date|cents|last_4` (from `receipts.content_key`).

**Billable + markup math:** every row carries `billable` + `markup_pct` (overlaid from project defaults at extract time). `invoiceable_amount = total × (1 + markup_pct/100)` when `billable=true`; otherwise blank. Use this column to drive client billing — it sums cleanly across both invoices (`OPEN`/`APPROVED` for upcoming bills) and receipts (`PAID` for already-incurred costs).

**Maps × CRM × Calendar (16) — *NEW***
**`workflow_nearby_contacts`** (radius search ranked by distance/recency), **`workflow_route_optimize_visits`** (TSP heuristic, fast/cheap), **`workflow_route_optimize_advanced`** (full VRP via Google Route Optimization API — time windows, multi-vehicle, capacities, cost coefficients, inferred skip reasons), **`workflow_route_optimize_from_calendar`** (calendar events → VRP feasibility check), **`workflow_travel_brief`** (city + dates → contacts + slots + Doc + email), **`workflow_geocode_contacts_batch`** (one-shot bulk geocode → custom fields), **`workflow_address_hygiene_audit`** (Address Validation → Sheet of fixes), **`workflow_contact_density_map`** (territory map of saved contacts), **`workflow_meeting_midpoint`** (fair-distance venue + auto invite), **`workflow_commute_brief`** (daily leave-by note for first meeting), **`workflow_event_nearby_amenities`** (coffee/lunch/parking near an event), **`workflow_errand_route`** (lighter route-optimize heuristic), **`workflow_recent_meetings_heatmap`** (last N days of in-person events plotted), **`workflow_departure_reminder`** (live-traffic popup or sibling travel block), **`workflow_calendar_drive_time_blocks`** (bulk auto-create "🚗 Drive to X" events with smart-chain origin, Maps URL, assistant trip note, conflict alerting), **`workflow_remove_drive_time_blocks`** (companion cleanup)

### Maps (10) — *NEW*
`maps_geocode`, `maps_reverse_geocode`, `maps_search_places`, `maps_search_nearby`, `maps_get_place_details`, `maps_get_directions`, `maps_distance_matrix`, `maps_get_timezone`, `maps_validate_address`, `maps_static_map`

> **Setup:** Maps uses an API key (separate from OAuth). Run `setup_wizard.py` for the easiest path, or follow `GCP_SETUP.md` Section 2c manually. Verify with `system_check_maps_api_key`.

### System (14) — *expanded health-check suite*
**`system_doctor`** — flagship: runs all checks below in parallel and returns one structured pass/warn/fail report with specific actionable fixes per check. **Run this first when something's not working.**

`system_check_anthropic_key`, `system_check_maps_api_key`, `system_check_maps_api_key_full` (verify all 8 Maps APIs in allowlist), `system_check_oauth` (token validity + scope coverage), `system_check_workspace_apis` (live call to each enabled service), `system_check_route_optimization` (cloud-platform scope + API enabled), `system_check_location_services` (4-step CoreLocationCLI ladder), `system_check_config` (JSON validity + typo detection), `system_check_filesystem` (writable dirs, file modes), `system_check_dependencies` (Python + libs + binaries), `system_check_clock` (NTP skew detection), `system_check_tools` (verify all 182 tools register), `system_check_quota_usage` (estimated Maps + Anthropic spend this month)

---

## 100 workflow examples

### Email & messaging (15)
1. Send an email to one person
2. Send to multiple recipients with cc + bcc
3. Send with a file attached (auto-Drive-fallback for large files)
4. Reply to a thread (preserves thread headers)
5. Reply-all to a thread
6. Forward a message with a personal note
7. Save an email as a draft
8. Trash a message and restore later
9. Search Gmail with native operators (`from:`, `has:attachment`, `newer_than:`)
10. Get the full body of a thread
11. Download a specific attachment by ID OR filename
12. Send the same templated email to a contact group (mail merge)
13. Send a templated email with `{first_name}`, `{title}`, `{organization}` placeholders
14. Create a Gmail filter that auto-applies a label to mail from a specific sender
15. Create, rename, or delete user labels

### Calendar (12)
16. List your calendars
17. List events in a date range
18. Create a one-off event
19. Create a recurring event (`recurrence_pattern: "weekly"`)
20. Create a recurring event with end-date (`recurrence_until: "2026-12-31"`)
21. Create an event with auto-Meet link
22. Use natural language: "Coffee with Brian Friday 3pm"
23. Update an event (change time, location, description, attendees)
24. RSVP to an event you're invited to
25. Find a meeting slot when multiple people are free
26. Convert an email thread into a calendar invite (`workflow_email_thread_to_event`)
27. Delete an event

### Drive (10)
28. Search Drive: "name contains 'budget'"
29. Read a Google Doc as plain text or HTML
30. Upload a binary file (PDF, image, etc.)
31. Upload via base64 (no local file needed)
32. Download a Drive file (auto-saves to `~/Gmail Downloads` if large)
33. Create a folder
34. Move a file to a folder
35. Share a file with someone via email + Drive permissions in one call
36. Trash a file (recoverable for 30 days)
37. Find all files of a certain MIME type

### Contacts / CRM (20)
38. List all your saved contacts (paginated)
39. Search contacts by name/email/org substring
40. Create a contact with name, email, org, title, custom fields
41. Update a contact (any field)
42. Add a timestamped note to a contact (never overwrites previous notes)
43. Set a custom field (`stage: "prospect"`, `tier: "enterprise"`)
44. Create a contact group ("Q4 Prospects", "Customers")
45. Add or remove contacts from a group
46. List members of a group (flat records, ready for mail merge)
47. Get most recent message exchange with a contact
48. Get N most recent messages between you and a contact
49. Refresh the three managed CRM fields on one contact
50. Refresh managed fields on every saved contact (batched)
51. Apply auto-tagging rules to all contacts (rules.json)
52. Bulk-create contacts from your sent mail
53. Enrich one contact from their newest inbound email signature
54. Bulk enrich contacts from recent inbox sweep
55. Detect contacts who are out of office (auto-reply scan)
56. Export all contacts to a CSV
57. Import contacts from a CSV (create or update by email)

### Sheets (7)
58. Create a new spreadsheet
59. List all tabs in a spreadsheet
60. Add or delete a tab
61. Read a range (`Sheet1!A1:D100`)
62. Write a 2D array to a range (overwrites)
63. Append rows to a table
64. Round-trip: create → write → read → append

### Docs (4)
65. Create a new Google Doc
66. Insert text at a position (or append)
67. Read the document as plain text
68. Find-and-replace text in a doc

### Tasks (6)
69. List all task lists
70. List tasks in a list (optionally including completed)
71. Create a task with title + notes + due date
72. Update a task
73. Mark a task complete
74. Delete a task

### Templates & mail merge (8)
75. List all available templates
76. Read a template's source
77. Send a template inline (subject + body specified at call time)
78. Send a saved template by name to one contact
79. Send a saved template by name to a contact group (mail merge)
80. Use placeholders: `{first_name|fallback}`, `{title|<not set>}`
81. Customize template body before sending
82. Verify template renders before sending (`dry_run: true`)

### Brand voice (5)
83. Run `make brand-voice` to generate `brand-voice.md` from your sent mail
84. Use heuristic mode (free): `python refresh_brand_voice.py --mode heuristic`
85. Use LLM mode (rich, ~$0.05): default when `anthropic_api_key` is set
86. Customize the analysis window: `--days 180` for half a year
87. Hand-edit `brand-voice.md` below the divider — preserved across regeneration

### Cross-service workflows (10)
88. Email a Google Doc as a PDF in one call
89. Save all attachments from an email to a Drive folder
90. Share a Drive file with someone + email them the link
91. Convert an email thread to a calendar event with auto-attendees
92. Find a meeting slot for 3+ attendees in their preferred hours
93. Send the MCP handoff archive to a coworker (auto-Drive + email)
94. Bulk-populate contacts from sent mail with auto-tagging rules
95. Enrich every contact's title/phone/website from their last inbound email
96. Detect OOO across your contacts and flag in CRM
97. Daily cron: refresh stats + enrich inbox + (quarterly) refresh voice

### Maps × CRM × Calendar (16)
A. *"Find contacts within 25 km of Austin, TX"* — workflow_nearby_contacts
B. *"Plan optimal route through 5 sales visits"* — workflow_route_optimize_visits (heuristic) or workflow_route_optimize_advanced (full VRP)
C. *"VRP with 2 vehicles, time windows 9-12, capacities 60 each"* — workflow_route_optimize_advanced
D. *"Bulk geocode every contact's address into custom fields"* — workflow_geocode_contacts_batch
E. *"Audit every contact's address through Address Validation, write Sheet"* — workflow_address_hygiene_audit
F. *"Static map of where my contacts cluster"* — workflow_contact_density_map
G. *"Find a fair midpoint cafe between Apple Park and Salesforce Tower + auto-create invite"* — workflow_meeting_midpoint
H. *"Travel brief — Austin May 15-17, contacts in area, calendar gaps"* — workflow_travel_brief
I. *"Daily commute brief — when do I need to leave for my first meeting?"* — workflow_commute_brief (uses current location)
J. *"Coffee/lunch/parking near my Friday lunch event"* — workflow_event_nearby_amenities
K. *"Optimal route through 5 errand addresses"* — workflow_errand_route
L. *"Heatmap of last 60 days of in-person meetings"* — workflow_recent_meetings_heatmap
M. *"Add a leave-by reminder to my next event using live traffic"* — workflow_departure_reminder
N. *"Auto-create drive-time blocks for my next 7 days"* — workflow_calendar_drive_time_blocks (smart-chain origin, color 4 Flamingo, 30-min reminder)
O. *"Remove all auto-created drive blocks"* — workflow_remove_drive_time_blocks
P. *"Feasibility check on this week's calendar — can I make every meeting?"* — workflow_route_optimize_from_calendar

### Raw Maps (10)
Q. *"Geocode '1 Hacker Way'"* — maps_geocode (returns lat/lng + canonical address)
R. *"Reverse geocode 37.4225, -122.0856"* — maps_reverse_geocode
S. *"Italian restaurants near Palo Alto"* — maps_search_places
T. *"Cafes within 1500m of these coords"* — maps_search_nearby
U. *"Get full details for this place_id"* — maps_get_place_details (hours, reviews, photos)
V. *"Driving directions from A to B with traffic"* — maps_get_directions
W. *"Distance matrix: 3 origins × 4 destinations"* — maps_distance_matrix
X. *"What timezone is this lat/lng in?"* — maps_get_timezone
Y. *"Validate '350 5th Ave NY 10118' and get ZIP+4"* — maps_validate_address
Z. *"PNG map of San Francisco Tower with markers, save to disk"* — maps_static_map

### System (3)
98. Verify `ANTHROPIC_API_KEY` works (live test, ~$0.0001)
99. List `gmail_list_send_as` to find aliases for `from_alias`
100. Inspect tool errors with `format_error()` patterns in logs

---

## Configuration reference

Edit `config.json` (auto-created from `config.example.json` during install):

```json
{
  "default_timezone": "America/Los_Angeles",
  "default_calendar_id": "primary",
  "default_from_alias": null,
  "dry_run": false,
  "log_level": "INFO",
  "crm_window_days": 60,
  "log_sent_emails_to_contacts": true,

  "anthropic_api_key": null,
  "signature_parser_mode": "regex",

  "google_maps_api_key": null,
  "auto_validate_contact_addresses": false,

  "gcp_project_id": null,
  "home_address": null,

  "large_attachment_threshold_kb": 500,
  "gmail_max_message_kb": 22528,
  "max_inline_download_kb": 5120,
  "default_download_dir": "~/Gmail Downloads",

  "retry": {
    "max_attempts": 4,
    "initial_backoff_seconds": 1.0,
    "max_backoff_seconds": 30.0
  }
}
```

| Key | Default | Notes |
|---|---|---|
| `default_timezone` | `null` | IANA name, e.g. `"America/Los_Angeles"`. Falls back to Google account default. |
| `default_calendar_id` | `"primary"` | Used when no `calendar_id` arg is passed |
| `default_from_alias` | `null` | Email alias for sends |
| `dry_run` | `false` | Global kill-switch — overrides per-call args |
| `crm_window_days` | `60` | Window for `Sent, last N` and `Received, last N` |
| `log_sent_emails_to_contacts` | `true` | Append timestamped activity notes to contact biographies on send |
| `anthropic_api_key` | `null` | Optional; enables brand voice LLM analysis + smart signature parsing |
| `signature_parser_mode` | `"regex"` | `"regex"`, `"regex_then_llm"`, or `"llm"` |
| **`google_maps_api_key`** | `null` | **Required for Maps tools + Maps×CRM workflows.** See `GCP_SETUP.md` Section 2c. |
| `auto_validate_contact_addresses` | `false` | Auto-canonicalize on `contacts_create` / `contacts_update` via Address Validation API |
| **`gcp_project_id`** | `null` (auto-detected) | **Required for Route Optimization API.** Auto-detected from `credentials.json` — override only if your OAuth project differs. |
| **`home_address`** | `null` | **Default origin for `workflow_commute_brief`, `workflow_departure_reminder`, `workflow_calendar_drive_time_blocks`** when no per-call origin and current-location detection fails. |
| `large_attachment_threshold_kb` | `500` | Threshold for outbound Drive-fallback |
| `gmail_max_message_kb` | `22528` | Pre-check vs Gmail's 25MB ceiling |
| `max_inline_download_kb` | `5120` | Cap on base64 returned by download tools |
| `default_download_dir` | `"~/Gmail Downloads"` | Auto-save location for large downloads |

**Per-tool runtime parameters that override config:**
- `current_location` (string address) + `current_location_mode` ("auto" | "manual" | "home" | "off") on `workflow_commute_brief`, `workflow_departure_reminder`, `workflow_calendar_drive_time_blocks`
- `home_address` per-call override on the same 3 tools
- `cost_per_hour` + `cost_per_km` per-vehicle on `workflow_route_optimize_advanced`

---

## Troubleshooting

> Full troubleshooting reference lives in `INSTALL.md`. Highlights below.

### OAuth & auth

- **OAuth flow hangs forever in Cowork** — MCP subprocess can't open a browser. Run OAuth in **Terminal**: `./install.sh --oauth`.
- **"Auth error 401"** — Delete `token.json`, re-run `./install.sh --oauth`.
- **"Scope has changed" on startup** — Same fix: delete `token.json` and re-run OAuth. Happens after MCP upgrades that add scopes (e.g. cloud-platform for Route Optimization).

### Cowork environment quirks

- **Tool not showing up in Cowork** — Restart Cowork (cmd+Q the app, not just close window). Also check `python server.py` runs cleanly in Terminal first.
- **`ANTHROPIC_API_KEY` "not set" even after `export` in `~/.zshrc`** — macOS GUI Cowork doesn't inherit shell env. Put the key in `config.json` instead (recommended — it's gitignored).
- **Cowork uses old code after edit** — MCP subprocess cached. Hard-quit Cowork (cmd+Q) and reopen.

### Maps API

- **`maps_not_configured`** — `google_maps_api_key` missing from `config.json`. See `GCP_SETUP.md` Section 2c.
- **Geocode "REQUEST_DENIED"** — API not enabled in GCP, or key restricted. Recheck the 8-API allowlist.
- **`workflow_route_optimize_advanced` returns 403 `permission_denied`** — Route Optimization API not enabled. Visit https://console.cloud.google.com/apis/library/routeoptimization.googleapis.com.
- **`workflow_route_optimize_advanced` returns 401 `auth_failed`** — OAuth token missing `cloud-platform` scope. Delete `token.json`, re-run OAuth.

### Current-location detection

- **`origin_source: "google_geolocation"` instead of `"corelocationcli"`** — corelocationcli not found in PATH OR macOS Location Services not granted. See `INSTALL.md` Phase 4 for the diagnostic ladder.
- **`CoreLocationCLI: ❌ Location services are disabled`** — Open System Settings → Privacy & Security → Location Services → enable Terminal AND Claude.
- **`no_origin` error on commute_brief** — All location methods failed AND no `home_address` set. Add `home_address` to `config.json` as backstop.

### Other common issues

- **Attachment download "not found" with the right ID** — Gmail rotates attachment IDs. Use the `filename` parameter instead.
- **Mail merge dry-run shows `<not set>`** — Field is the fallback rendering. Use `contacts_update` to fill in the field, then re-run.
- **`workflow_enrich_contacts_from_recent_mail` auto-creates too many contacts** — Run with `only_existing_contacts: true` first, OR seed contacts via `workflow_create_contacts_from_sent_mail` first.
- **Chat tool 403 / 404 on space** — User-OAuth Chat only sees spaces you're a member of. Use the Google Chat UI to create the space first, then come back.
- **`workflow_route_optimize_advanced` skipped a stop** — Check `inferred_reasons[]` in the response — `competing_window`, `exceeds_capacities`, `low_skip_penalty` etc. tell you exactly why.

---

## Privacy & security

- Everything runs **locally on your Mac**. Google API calls go directly from your machine using your OAuth token.
- `credentials.json`, `token.json`, `config.json`, `rules.json`, and `logs/` are gitignored AND excluded from `make handoff` — secrets never travel.
- `token.json` is `chmod 600`.
- To revoke access: delete `token.json`, OR Google Account → Security → Third-party apps → revoke this app.
- Your Anthropic key (when used) goes only to `api.anthropic.com`. It's never logged.

---

## Related files

- **`README.md`** — installer-focused overview
- **`HANDOFF.md`** — coworker quickstart for fresh installs
- **`INSTALL.md`** — detailed install walkthrough
- **`GCP_SETUP.md`** — Google Cloud setup (one-time)
- **`brand-voice.md`** — your auto-generated voice guide (created by `refresh_brand_voice.py`)
- **`config.example.json`** — example config with all knobs documented
- **`templates/*.md`** — 8 mail merge templates in your voice

For the latest source: see `dist/google-workspace-mcp-*.tar.gz` after running `make handoff`.
