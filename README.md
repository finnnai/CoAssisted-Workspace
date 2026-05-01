# CoAssisted Workspace

> *Formerly developed under the working title "Google Workspace MCP" — same codebase, same tools, productized.*

**CoAssisted Workspace** is a local MCP server that gives an AI assistant end-to-end control of your Gmail, Calendar, Drive, Sheets, Docs, Tasks, Contacts, and Chat — plus 33 cross-service workflows including territory routing, drive-time block automation, brand-voice extraction, and bulk CRM operations. Includes **actual send** for email (not just drafts), attachments, filters, Meet links, binary file I/O, dry-run mode, and more.

Works with **Claude Code, Claude Cowork**, and any other tool that speaks the Model Context Protocol.

Built with Python + FastMCP + OAuth 2.0. Runs locally, stdio transport, your data never leaves your Mac except to hit Google's APIs.

## Pricing tiers

| Tier | Tools | What it's for |
|---|---|---|
| **Free** | 53 tools — all Workspace basics (send, read, organize across all 8 Google services) + system health checks + audit log + **project-registry admin** (`workflow_register_project`, `workflow_list_projects`, `workflow_create_project_sheet` so evaluators can see the AP routing-rule shape) | Get started, run real tasks today, prototype the project-AP structure without paying, decide if it's worth more |
| **Paid** | All 238 tools — **flagship Receipt Extractor** (LLM-parses inbox + PDFs + photos into a Sheet, archives PDFs to Drive, exports QuickBooks CSV), **Project AP Pipeline** (auto-classify invoice vs. receipt, 5-tier project resolver, internal/external sender split, DM-or-email vendor follow-up loop with brand-voiced asks + project picker, automated reply parsing + row promotion, hybrid Drive layout: per-employee folders + per-project sheets + PDF archive, QuickBooks Bills CSV), Maps × CRM × Calendar workflows, Vehicle Routing optimization, brand voice extraction, bulk operations with rollback, advanced Chat (DMs, search, attachments), full CRM (custom fields, groups, stats refresh), templates + mail merge | Daily-driver productivity, sales/CSM/ops workflows, multi-vehicle routing, expense reporting, project-tracked AP with vendor follow-up loop |

Free tier is fully self-serve. Paid tier requires a license key from the developer (`caw-XXXX-XXXX-XXXX-XXXX` format). Add to `config.json`: `{ "license_key": "caw-..." }`.

> **Note for current users:** the personal/handoff distribution defaults to `DISTRIBUTION_MODE = "personal"` — every tool works as before, no license needed. The paid-tier gating only kicks in for the official plugin marketplace build.

---

## Tool inventory

**Gmail (17)**
- `gmail_send_email` — send (with attachments, HTML, send-as alias, dry-run, auto-Drive-fallback for big files)
- `gmail_create_draft` — save a draft
- `gmail_list_drafts` — browse saved drafts
- `gmail_reply_to_thread` — in-thread reply, optional reply-all
- `gmail_forward_message` — forward incl. original attachments
- `gmail_search` — Gmail query syntax
- `gmail_get_thread` — full thread with plain-text bodies
- `gmail_download_attachment` — list, fetch by ID, or fetch by **filename** (stable when Gmail rotates IDs). Auto-saves to `~/Gmail Downloads` if file exceeds the inline cap.
- `gmail_trash_message` / `gmail_untrash_message`
- `gmail_list_labels` / `gmail_create_label` / `gmail_update_label` / `gmail_delete_label` / `gmail_modify_labels`
- `gmail_list_filters` / `gmail_create_filter` / `gmail_delete_filter` — server-side auto-rules
- `gmail_list_send_as` — list aliases

**Calendar (8)**
- `calendar_list_events` / `calendar_list_calendars`
- `calendar_create_event` — supports timed/all-day, attendees, Meet auto-link, and **recurring events** via friendly `recurrence_pattern` (`daily`, `weekdays`, `weekly`, `biweekly`, `monthly`, `yearly`) plus `recurrence_count` or `recurrence_until`. Power users can pass raw `recurrence_rrule`.
- `calendar_quick_add` — natural-language event creation
- `calendar_update_event` / `calendar_delete_event`
- `calendar_respond_to_event` — RSVP accept/decline/tentative
- `calendar_find_free_busy` — busy windows across calendars

**Drive (9)**
- `drive_search_files` — Drive query language
- `drive_read_file` — Google-native files with export
- `drive_upload_text_file` / `drive_upload_binary_file`
- `drive_download_binary_file` — any non-Google file
- `drive_create_folder` / `drive_move_file` / `drive_share_file` / `drive_delete_file`

**Sheets (7)**
- `sheets_create_spreadsheet` / `sheets_list_sheets`
- `sheets_add_sheet` / `sheets_delete_sheet` — manage tabs
- `sheets_read_range` / `sheets_write_range` / `sheets_append_rows`

**Docs (4)**
- `docs_create_document` / `docs_read_document` / `docs_insert_text` / `docs_replace_text`

**Tasks (6)**
- `tasks_list_task_lists` / `tasks_list_tasks`
- `tasks_create_task` / `tasks_update_task` / `tasks_complete_task` / `tasks_delete_task`

**Chat (18)** *(via Google Chat API)*
- `chat_list_spaces` / `chat_get_space` — DMs, group chats, rooms you're in
- `chat_find_or_create_dm` — look up an existing DM with someone by email, or auto-create one. Removes the "open Chat in browser to seed a DM" friction.
- `chat_send_dm` — sugar tool: find-or-create + send in one call. Pass an email + text and it just works.
- `chat_send_to_space_by_name` — find a space by display-name substring (case-insensitive) and send. Avoids memorizing `spaces/AAAA...` IDs.
- `chat_search` — search messages across spaces by text/sender/date.
- `chat_who_is_in_dm` — identify the other party in a 1:1 DM (works around the API's privacy filter on member listings).
- `chat_send_attachment` — upload + send a file (path or base64). 200MB cap.
- `chat_recent_activity` — list spaces with new messages since a cutoff. Catch-up tool.
- `chat_react_to_message` — emoji reactions (Unicode characters, not shortcodes).
- `chat_get_thread` — fetch every message in a reply thread, oldest-first.
- `chat_list_messages` / `chat_get_message` — time-windowed message reads
- `chat_send_message` — send (with optional thread reply + thread_key idempotency)
- `chat_update_message` / `chat_delete_message` — edit or remove your own messages
- `chat_list_members` — list people/bots in a space
- `chat_download_attachment` — list or fetch message attachments (auto-saves large ones to `~/Gmail Downloads`); Drive-linked attachments redirect you to `drive_download_binary_file`

Cross-domain DMs require the recipient's Workspace to allow external Chat — see `GCP_SETUP.md` Step 2b for the Chat App registration walkthrough (the most-stuck-on step in setup).

**Contacts / CRM (20)** *(via People API + Gmail)*
- `contacts_search` / `contacts_list` / `contacts_get`
- `contacts_create` / `contacts_update` / `contacts_delete`
- `contacts_add_note` — timestamped note appended to biography (never overwrites)
- `contacts_set_custom_field` — key/value tags (e.g. `stage=prospect`)
- `contacts_list_groups` / `contacts_create_group` — segmentation
- `contacts_add_to_group` / `contacts_remove_from_group` / `contacts_list_group_members`
- `contacts_last_interaction` / `contacts_recent_interactions` — Gmail history for a given email
- `contacts_refresh_crm_stats` / `contacts_refresh_all_crm_stats` — recompute managed fields (batched Gmail calls)
- `contacts_apply_rules` — run auto-tagging rules across one or all contacts
- `contacts_export_csv` / `contacts_import_csv` — round-trip data to CSV

**Templates & mail merge (6)** *(in gmail_ namespace, cross-service)*
- `gmail_list_templates` / `gmail_get_template` — manage templates in `templates/*.md`
- `gmail_send_templated` — inline-template send to one contact
- `gmail_send_templated_by_name` — send a saved template to one contact
- `gmail_send_mail_merge` — batch inline-template send
- `gmail_send_mail_merge_by_name` — batch saved-template send

**Workflows (33)** *(cross-service)*
- `workflow_save_email_attachments_to_drive`
- `workflow_email_doc_as_pdf` — export + attach + send
- `workflow_share_drive_file_via_email` — grant access + email the link
- `workflow_email_thread_to_event` — thread → calendar invite with attendees auto-extracted
- `workflow_send_handoff_archive` — one-call handoff: find latest tarball, upload to Drive, share, email the link to coworkers
- `workflow_create_contacts_from_sent_mail` — bulk-populate saved contacts from your sent mail history. Dedupes, auto-applies tagging rules, optional inline enrichment from inbound signatures.
- `workflow_enrich_contact_from_inbox` — enrich one saved contact by parsing their most recent inbound email signature.
- `workflow_enrich_contacts_from_recent_mail` — sweep recent inbox, group by sender, enrich every matching saved contact. Junk filter prevents auto-creating contacts for noreply / marketing senders. Self-exclusion keeps your own address out.
- `workflow_find_meeting_slot` — multi-attendee free/busy lookup. Returns top N earliest slots within preferred hours, optionally skipping weekends.
- `workflow_detect_ooo` — scan inbox for OOO auto-replies and flag contacts with `out_of_office: true` plus `ooo_until: <date>`.
- `workflow_chat_digest` — daily Chat recap emailed to you with markdown summary + JSON attachment. LLM-summarizes via Claude when an API key is set; falls back to raw per-space groupings otherwise. Cron-friendly.
- `workflow_chat_to_contact_group` — Chat-flavored mail merge: send personalized DMs to every member of a CRM contact group. Renders templates with `{first_name}` etc., auto-creates DM spaces, optionally logs activity to each contact.
- `workflow_email_with_map` — send an email with a static map image attached. Useful for "where to meet" follow-ups. ~$0.002 per send.
- `workflow_meeting_location_options` — given attendee addresses, geocode each → compute centroid → search for nearby venues → distance-matrix to each attendee → rank by max travel time. Returns top N fairest options.
- `workflow_chat_with_map` — Chat parallel to `workflow_email_with_map`. Send a Chat message to a space (or DM by email) with a static map attached. Auto-resolves DMs.
- `workflow_chat_share_place` — share a Place (restaurant, office, etc.) as a rich Chat card with name, address, hours, rating, phone, website, "Open in Maps" link, and optional map. Pulls all info from a place_id.
- `workflow_chat_meeting_brief` — flagship: DM each attendee a personalized "here's the venue, here's a map, here's how long it'll take YOU to get there" message. Combines distance matrix + Chat DM resolution + CRM activity logging. The Maps + Chat + CRM trifecta in one tool.

**Maps × CRM × Calendar** *(new — territory + travel + commute intelligence)*
- `workflow_nearby_contacts` — "I'm in Austin Thu/Fri — who should I see?" Radius search over saved contacts ranked by distance or recency. Optional live travel time.
- `workflow_route_optimize_visits` — *(fast/cheap heuristic)* Order a list of stops for shortest driving day via Distance Matrix + nearest-neighbor TSP. ~$0.005 per stop, single vehicle, no time windows.
- `workflow_route_optimize_advanced` — *(full VRP solver)* Calls Google's Route Optimization API with time windows, multi-vehicle, capacities, service times, skip penalties, and configurable cost coefficients (cost_per_hour + cost_per_km). ~$0.05–$0.20 per shipment. Returns inferred reasons for any skipped stops (capacity exceeded, before/after shifts, competing windows, low penalty). Use when you need real Vehicle Routing Problem solving (sales rep daily routes, deliveries, field service). Setup: see `GCP_SETUP.md` Section 2d.
- `workflow_route_optimize_from_calendar` — *(VRP feasibility check)* Pulls a date range of calendar events with locations, treats each as a stop with `latest_arrival = event.start`, and runs them through the Route Optimization API. Optional `additional_stops` to fit around existing meetings. Use as upfront feasibility analysis before creating drive-time blocks — surfaces which calendar events are physically impossible to attend given the rest of the day.
- `workflow_travel_brief` — City + dates → contacts in radius, calendar gap analysis, optional Google Doc + email delivery.
- `workflow_geocode_contacts_batch` — One-shot bulk geocode every contact's address into `lat`/`lng`/`geocoded_at` custom fields. Run once for fast spatial queries afterward.
- `workflow_address_hygiene_audit` — Validate every contact's address via Address Validation API; produces a Google Sheet of VALID / SUSPECT / INVALID rows with suggested replacements.
- `workflow_contact_density_map` — Static map of where saved contacts cluster (territory planning visual). Group + region filters supported.
- `workflow_meeting_midpoint` — Two attendees → fair midpoint cafe/restaurant ranked by travel-time symmetry; optional auto-create calendar invite.
- `workflow_commute_brief` — Daily "leave by" note for your first in-person meeting given live traffic. Origin auto-detected from current location (CoreLocationCLI → Google Geolocation API → ipapi.co), with manual override + home fallback. Returnable, emailable, or self-DM via Chat.
- `workflow_event_nearby_amenities` — Coffee/lunch/parking near a calendar event; optional auto-append summary into the event description.
- `workflow_errand_route` — Lighter-weight route-optimize for plain addresses (no contact resolution).
- `workflow_recent_meetings_heatmap` — Static map of where last N days of in-person meetings happened.
- `workflow_departure_reminder` — Live-traffic "leave by" reminder added to a future event (popup) or as a sibling "Travel to X" calendar block. Origin auto-detected from current location with home fallback.
- `workflow_calendar_drive_time_blocks` — Bulk: scan upcoming calendar, auto-create "🚗 Drive to X" events for every meeting with a real location. Smart-chain origin (prev meeting → next; first drive uses **current location** if detectable, else `home_address`). Destination as event location for tap-to-navigate. Description has Maps directions URL + structured "assistant trip note" JSON. Surfaces overlap conflicts and back-to-back impossibilities without auto-resolving. Idempotent via `extendedProperties.private.driveBlockFor`.
- `workflow_remove_drive_time_blocks` — Companion cleanup: removes drive blocks created by the workflow (matches `extendedProperties.private.createdBy`). Won't touch manually-created drive events.

**Maps (10)** *(via Google Maps Platform, API key — not OAuth)*
- `maps_geocode` — address → lat/lng + canonical formatted address
- `maps_reverse_geocode` — lat/lng → human-readable address
- `maps_search_places` — text search ("Italian restaurants near Palo Alto")
- `maps_search_nearby` — places near coords filtered by type/keyword
- `maps_get_place_details` — full info (hours, phone, website, reviews) for a Place ID
- `maps_get_directions` — driving / walking / transit / cycling directions, optional traffic
- `maps_distance_matrix` — distance + duration between many origin/destination pairs
- `maps_get_timezone` — IANA timezone for a coordinate at a given moment
- `maps_validate_address` — clean + canonicalize a free-form address (Address Validation API)
- `maps_static_map` — render a PNG map image (auto-saves large outputs to `~/Gmail Downloads`)

Setup: see `GCP_SETUP.md` Section 2c. Cost: ~$5/1000 for most APIs; $200/month free credit covers typical use.

**Receipts (9)** — *flagship paid feature*
- **`workflow_extract_receipts`** — end-to-end: scan inbox + Drive folder + Gchat space (any combination), parse via Claude (text + PDF + image), categorize, dedupe, append to your selected Google Sheet, archive PDFs to Drive, optionally export QuickBooks CSV. Pass `chat_space_id` to add Chat as a source; pass `drive_folder_id` for Drive; default scans inbox.
- `workflow_extract_receipts_from_chat` — standalone Chat sweep when you have a dedicated `#receipts` space and want to scan only that.
- `workflow_extract_one_receipt` — extract a single email or Drive file by ID.
- `workflow_recategorize_receipt` — edit a row's category. Reads merchant from column C and writes a `manual_correction` to the merchant cache so future receipts from the same vendor get the corrected category for free.
- `workflow_export_receipts_qb_csv` — build a QuickBooks-importable CSV (date filters, custom account mapping). Categories now map 1:1 to QBO Chart of Accounts — no translation layer.
- `workflow_list_receipt_sheets` — auto-discover all your `Receipts — *` sheets (sorted by recency, with row counts).
- `workflow_create_receipt_sheet` — create a new expense sheet (`Receipts — {name}`) with the 17-column header pre-populated.
- `workflow_list_known_merchants` — inventory the persistent merchant cache (what's been learned, by which source).
- `workflow_forget_merchant` — drop one cache entry to force re-verification.

Both `workflow_recategorize_receipt` and `workflow_export_receipts_qb_csv` accept either `sheet_id` (explicit) or `sheet_name` (resolved against your Drive's `Receipts — *` sheets). The orchestrator returns a `needs_sheet` discovery list when you call it without specifying a target sheet.

**5-tier enrichment ladder** for low-confidence receipts (only fires when LLM confidence < 0.6):
1. **Tier 0 — Merchant cache** (`merchants.json` in project root, 365-day TTL). Free. Manual corrections beat web/Maps results.
2. **Tier 1 — LLM extraction** via Claude Haiku 4.5 (text + Vision for PDF/image).
3. **Tier 2 — Maps Places** verifies the merchant at the receipt's address.
4. **Tier 3 — Anthropic web_search** identifies merchant type when Maps doesn't help.
5. **Tier 4 — EXIF + sender attribution.** Reads photo `DateTimeOriginal` + GPS via Pillow, stamps a `[Metadata]` block onto notes. If LLM date is suspect (conf<0.6 OR >12mo off from EXIF), uses EXIF date and flags the override. Sender comes from email From or chat sender (resolved via People API when `displayName` is null).

Tiers 2/3 successes auto-write to the merchant cache so the next receipt from the same vendor pays $0 to enrich.

**Two dedup mechanisms:** source-id (Gmail message_id / Drive file_id / `chat:<space>/<msg>`) catches re-scans. Content-key (normalized merchant + date + total_cents + last_4) catches the same physical receipt arriving via multiple file_ids — e.g. 3 photos of one Chevron purchase don't create 3 sheet rows.

**Loop-safe channel scanning:** the classifier rejects messages containing `BOT_FOOTER_MARKER`, so re-scanning a chat that contains the bot's own expense-report posts won't try to extract receipts from them.

Privacy: full card numbers are NEVER extracted. `last_4` is redacted before persisting (config `receipts_redact_payment_details`, default `true`). Cost: ~$0.0005 per text-only receipt, ~$0.005 per PDF/image, ~$0.015 for low-conf rows that escalate to web search. Typical 30-day scan with 50 receipts ≈ $0.05–$0.50.

**Project Spend (7)** — *unified per-project sheet for invoices + receipts*

Each project's sheet now carries BOTH unpaid bills (invoices) and already-paid receipts. The `doc_type` column distinguishes them. Single source of truth for project spend → cleaner client billing, no chasing two sheets.

- **`workflow_extract_project_invoices`** — flagship invoice path: scan inbox + Drive folder + Gchat space, auto-classify invoice vs. receipt, extract via Claude (text + PDF + image), resolve `project_code` via the 5-tier registry ladder, route to that project's sheet (or park in `Project Invoices — Needs Project Assignment` if unresolved). Dedupes by source_id AND `vendor|invoice_number|cents` content key.
- **`workflow_extract_project_receipts`** — receipt sibling: scans inbox + Drive folder + Gchat space using the existing receipt classifier (STRONG/BROAD sender model + money-pattern body match), runs the 5-tier enrichment ladder, resolves project, appends `doc_type='receipt'` rows to the same sheet. Same source_id + `merchant|date|cents|last_4` dedup as the regular receipt extractor.
- `workflow_register_project` — bootstrap a project with routing rules: sender emails, chat space IDs, filename regex patterns, default billable, default markup %. Creates the per-project sheet on first call.
- `workflow_list_projects` — inventory of registered projects + routing rules + counts.
- `workflow_create_project_sheet` — re-create the sheet for a registered project (idempotent — no-op if the existing sheet still works).
- `workflow_move_invoice_to_project` — move a single row from one project's sheet to another. Works for receipts and invoices. Identify the row by `content_key` (preferred, survives row deletion) or `row_number`. Source row gets cleared; counts re-tally.
- `workflow_export_project_invoices_qb_csv` — export a project's **invoices only** (skips `doc_type='receipt'` rows) as a QuickBooks Bills-importable CSV (default filter: `OPEN` + `APPROVED`). Optional date range, save to Drive folder or return base64.
- **`workflow_send_vendor_reminders`** — bulk reminder loop for outstanding vendor info requests. Cadence: chat = no wait, email = 24h between reminders. Hard cap of 2 reminders per row. Brand-voiced nudges on the original thread.
- **`workflow_process_vendor_replies`** — scans threads with outstanding requests for vendor replies, LLM-parses the answered fields, updates the parked sheet row in place, and (if quality guard now passes) flips status `AWAITING_INFO → OPEN`.

**Vendor info-request loop.** When the quality guard fires (missing invoice number / total out of range / etc.) and `request_missing_info=True` (default), the orchestrator replies on the original Gmail thread or Chat space with a brand-voiced ask listing the specific missing fields line-by-line. The row goes to the project sheet with status `AWAITING_INFO`. The vendor's reply gets parsed by `workflow_process_vendor_replies` and the row is automatically promoted to `OPEN` once it passes the guard. `vendor_followups.py` tracks the outstanding asks (atomic-write JSON, same pattern as `merchant_cache.py`).

**5-tier project resolution ladder** (highest authority first):
1. **Explicit** `project_code` in the call — confidence 1.00
2. **Filename regex** matches a project's `filename_patterns` (e.g. `^INV-ALPHA-`) — 0.95
3. **Sender email** matches a project's `sender_emails` — 0.90
4. **Chat space** matches a project's `chat_space_ids` — 0.85
5. **LLM inference** over invoice content + project list — variable

Resolutions below 0.65 confidence get parked in the Needs Review sheet so you can reclassify with `workflow_move_invoice_to_project`. The auto-classifier (`project_invoices.classify_document`) keeps the invoice path quiet for receipt-shaped emails — they fall through to the receipt extractor instead.

**Unified project sheet schema (27 columns):** `logged_at, doc_type, invoice_date, due_date, vendor, invoice_number, po_number, category, subtotal, tax, total, currency, billable, markup_pct, invoiceable_amount, status, days_outstanding, payment_terms, project_code, bill_to, remit_to, source_kind, source_id, invoice_link, confidence, notes, content_key`. `doc_type` is `invoice` or `receipt`. For receipts the invoice-specific fields (due_date, invoice_number, po_number, payment_terms, bill_to, remit_to) stay blank; status defaults to `PAID` and days_outstanding to `0`. Invoice status lifecycle: `OPEN → APPROVED → PAID` (or `DISPUTED`/`VOID`).

**System (14)** *(health checks + diagnostics)*
- **`system_doctor`** — runs every check below in parallel, returns one structured pass/warn/fail report with specific actionable fixes. **Run this first when something's not working.**
- `system_check_oauth` — verifies token.json valid + lists granted scopes vs required. Detects "scope changed" issues.
- `system_check_workspace_apis` — tiny live call to each Workspace API (Gmail/Calendar/Drive/Sheets/Docs/Tasks/People/Chat) to confirm scope grants are alive.
- `system_check_maps_api_key` / `_full` — basic vs full 8-API allowlist verification.
- `system_check_route_optimization` — Route Optimization API enabled + cloud-platform scope present, with a tiny live request.
- `system_check_location_services` — 4-step diagnostic ladder for current-location detection (corelocationcli installed → executes → permission granted).
- `system_check_config` — JSON validity + typo detection on known keys.
- `system_check_filesystem` — writable logs/, token.json chmod 600, default_download_dir.
- `system_check_dependencies` — Python version, required + optional deps.
- `system_check_clock` — NTP skew vs Google (>5min skew = OAuth flakiness).
- `system_check_tools` — confirms all 182 tools register without errors.
- `system_check_quota_usage` — estimates Maps + Anthropic spend this month from logs.
- `system_check_anthropic_key` — Anthropic API key + live test.

**Junk filter** (in `junk_filter.py`): hard-fail signals include noreply local-part patterns (`noreply`, `do_not_reply`, `notifications`), cloud-vendor role accounts (`googlecloud`, `aws`, `azure`, etc.), notification/marketing sub-domains (`emailnotifications`, `accountprotection`, `e.stripe.com`), `List-Unsubscribe` / `Precedence: bulk` / `Auto-Submitted` headers. Soft signals combine body boilerplate ("please do not reply", "this is an automated message"), opt-out phrases ("unsubscribe", "view in browser"), link-to-text ratio, transactional subjects ("receipt", "your weekly digest", "thanks for upgrading"), and a 58-phrase spam-hype list ("free trial", "money back", "guaranteed", etc.). Two soft categories together classify as junk; 4+ spam-hype phrases trip on their own.

**Total: 182 tools across 13 categories.**

---

## Setup — quick start

See **`INSTALL.md`** for the full linear walkthrough. TL;DR:

```bash
# 1. One-time: Google Cloud project + 8 enabled APIs + credentials.json  (~10 min)
open GCP_SETUP.md

# 2. Bootstrap the Python environment and configs
./install.sh

# 3. OAuth consent (saves token.json)
./install.sh --oauth     # or: make auth

# 4. Wire into Cowork's MCP config (snippet in INSTALL.md), restart Cowork
```

The installer is idempotent — safe to re-run. Your `config.json`, `rules.json`, `credentials.json`, and `token.json` are never overwritten.

`Makefile` targets: `make install`, `make auth`, `make test`, `make run`, `make refresh`, `make clean`, `make help`.

---

## Sanity tests

Once connected, try:

1. **Real send to Josh:**
   > "Use gmail_send_email to send to josh.szott@surefox.com: subject 'Real send from MCP', body 'This time it actually goes out.'"

2. **Quick-add a calendar event:**
   > "Use calendar_quick_add for 'Coffee with Josh next Tuesday 3pm'."

3. **Send a doc as PDF:**
   > "Use workflow_email_doc_as_pdf to send doc ID `<id>` to josh.szott@surefox.com."

4. **Save attachments to Drive:**
   > "Find the latest email from Josh with attachments and use workflow_save_email_attachments_to_drive to save them."

---

## Template library

Saved email templates live in `templates/*.md`. Each file has YAML frontmatter (subject, optional html_body, optional description) and a plain-text body — both support `{placeholder|fallback}` substitution.

Example `templates/follow_up.md`:

```
---
subject: "Great connecting, {first_name|there}"
description: Post-meeting follow-up
---
Hi {first_name|there},

Recap of what we discussed and my next steps:

1. [Action item 1]
2. [Action item 2]

Thanks,
Finnn
```

Use it with `gmail_send_templated_by_name` or `gmail_send_mail_merge_by_name`:

```
gmail_send_templated_by_name \
    template_name="follow_up" \
    recipient={resource_name: "people/c123"}
```

Three example templates ship by default (`outbound_cold_outreach`, `outbound_follow_up_after_meeting`, `outbound_re_engage`). Edit them or add your own — no server restart needed.

## Auto-tagging rules

Optional `rules.json` fills contact fields in automatically based on email domain. Example:

```json
{
  "domain_rules": {
    "acme.com":    {"organization": "Acme Corp", "tier": "enterprise"},
    "startupx.io": {"organization": "StartupX",  "tier": "growth"}
  }
}
```

Rules fire on `contacts_create` and `contacts_update`. Existing fields are never overwritten — rules only fill blanks. Any non-recognized key (not `first_name`, `last_name`, `organization`, or `title`) becomes a userDefined custom field.

`contacts_apply_rules` sweeps all saved contacts and applies any rules that now match. Use this after editing `rules.json` to backfill existing contacts.

`rules.json` is in `.gitignore`. Copy `rules.example.json` as a starting point.

## Activity log on contacts

When you send via `gmail_send_templated` / `_by_name` or `gmail_send_mail_merge` / `_by_name`, the MCP will look up the recipient in your saved contacts and append a timestamped activity note to their biography (the notes field in Google Contacts):

```
[2026-04-23 14:32] Sent: "Following up, Josh" (template: follow_up_after_meeting)
```

Controlled by `log_sent_emails_to_contacts` in `config.json` (default `true`), or per-call via the `log_to_contact` flag on each send tool. Recipients that aren't saved contacts are silently skipped — activity log never blocks sending.

## CSV import/export

```
contacts_export_csv path="/Users/finnnai/Downloads/contacts.csv"
contacts_import_csv path="/Users/finnnai/Downloads/contacts_edited.csv"
```

Export includes one column per discovered `custom.<key>` field plus the managed CRM fields (toggleable). Import treats `email` as the matching key — existing contacts are updated, new ones are created. Managed keys in CSV columns are ignored on import.

## CRM / mail merge workflow

**The mental model:**

1. **Contacts** hold structured data: names, emails, org, title, plus notes (`biographies`) and custom tags (`userDefined`). Everything mail merge needs lives on the contact.
2. **Contact Groups** are segments ("Q2 Prospects", "Active Clients"). You build a group, add contacts, then send to the whole group.
3. **Notes** and **custom fields** are additive. `contacts_add_note` always appends; `contacts_set_custom_field` updates one key without touching others.
4. **Mail merge** uses `{placeholder}` syntax. Fallback via `{field|default}`.

**Template syntax**

```
Hi {first_name|there},

Following up on our conversation about {organization}. Based on your current
stage ({custom.stage|prospect}), I thought this might be useful.
```

Placeholders resolved from the contact:
- `first_name`, `last_name`, `full_name`, `email`
- `organization`, `title`
- Any `userDefined` key, either as `{custom.key}` or `{key}` (promoted to top-level for convenience)

Escape literal braces with `{{` and `}}`.

**End-to-end example**

```
# 1. Create a segment
contacts_create_group name="Q2 Prospects"          → resource_name "contactGroups/abc"

# 2. Tag a contact and add to the group
contacts_set_custom_field resource_name="people/c1" key="stage" value="prospect"
contacts_add_note resource_name="people/c1" note="Chatted at the conference."
contacts_add_to_group group_resource_name="contactGroups/abc"
                     contact_resource_names=["people/c1"]

# 3. Preview the mail merge (dry-run)
gmail_send_mail_merge \
    subject="Following up, {first_name|there}" \
    body="Hi {first_name|there}, ..." \
    group_resource_name="contactGroups/abc" \
    dry_run=true

# 4. Actually send
gmail_send_mail_merge \
    subject="Following up, {first_name|there}" \
    body="Hi {first_name|there}, ..." \
    group_resource_name="contactGroups/abc"
```

**Partial-failure handling**

By default, `gmail_send_mail_merge` keeps going if one recipient fails — each gets a `status` of `sent`, `failed`, or `skipped` in the response. Set `stop_on_first_error=true` to abort on first failure instead.

---

## Managed CRM fields (auto-populated)

Every contact created or updated through the MCP carries three fields in its `userDefined` list that the MCP manages automatically from your Gmail activity:

| Field key | Example value | Meaning |
|---|---|---|
| `Last Interaction` | `Sent - 2026-04-23 - 14:32` | Timestamp + direction of the most recent email between you and this contact. 24-hour clock, in your configured timezone. |
| `Sent, last N` | `+7` | Count of emails you've sent *to* this contact in the last N days. |
| `Received, last N` | `+12` | Count of emails you've received *from* this contact in the last N days. |

**N is configurable.** Set `crm_window_days` in `config.json` (default: 60). The labels follow the window automatically — set it to 30 and contacts will start carrying `Sent, last 30` and `Received, last 30` on next refresh. Any stale `Sent, last 60` entries are cleaned up automatically.

**When they refresh:**

| Trigger | Scope |
|---|---|
| `contacts_create` | Just the new contact. |
| `contacts_update` | That one contact. |
| `contacts_refresh_crm_stats` | That one contact, on demand. |
| `contacts_refresh_all_crm_stats` | Up to `limit` contacts in one call. |
| `refresh_stats.py` (cron) | All contacts, paginated. |

**These are read-only from the MCP's point of view.** `contacts_set_custom_field` rejects writes to any of the three keys with a clear error. You *can* still edit them manually in the Google Contacts UI, but the next refresh will overwrite your edit — that's the intended behavior of a managed field.

**Use them in mail merge templates:**

```
Hi {first_name|there},

It's been a while since our last chat on {Last Interaction|no record}.
```

Any `userDefined` key is available as a top-level placeholder, so `{Last Interaction}` works directly in `gmail_send_templated` / `gmail_send_mail_merge`.

**Daily refresh via cron (recommended):**

```bash
# Edit your crontab
crontab -e

# Add (runs every morning at 7am)
0 7 * * * /Users/finnnai/Claude/google_workspace_mcp/.venv/bin/python /Users/finnnai/Claude/google_workspace_mcp/refresh_stats.py >> /Users/finnnai/Claude/google_workspace_mcp/logs/refresh_stats.cron.log 2>&1
```

`refresh_stats.py` is a standalone Python script that paginates through every contact, recomputes the three fields, and exits non-zero if anything failed (so cron can surface errors).

**Timezone:** the `Last Interaction` timestamp uses `config.default_timezone` if set in `config.json`. Otherwise it uses your Mac's local timezone. Example config:

```json
{ "default_timezone": "America/Los_Angeles" }
```

---

## Config reference (config.json)

All keys optional; defaults used if absent.

```json
{
  "default_timezone": "America/Los_Angeles",
  "default_calendar_id": "primary",
  "default_from_alias": "finnn@surefox.com",
  "dry_run": false,
  "log_level": "INFO",
  "crm_window_days": 60,
  "log_sent_emails_to_contacts": true,
  "retry": {
    "max_attempts": 4,
    "initial_backoff_seconds": 1.0,
    "max_backoff_seconds": 30.0
  }
}
```

Lookup order for any value:
1. Tool argument (always wins)
2. `config.json`
3. Hard-coded default

---

## Dry-run mode

Every destructive or side-effecting tool accepts a `dry_run` argument. When true (or when `config.dry_run` is globally true), the tool returns a preview JSON (`{"status": "dry_run", "tool": "...", "would_do": {...}}`) instead of performing the action.

Great for testing automations end-to-end without spamming your inbox or calendar.

---

## Logging

Structured logs go to `logs/google_workspace_mcp.log`. Rotates at 5 MB × 3 backups. Nothing written to stdout (protected — stdio transport).

Change verbosity via `config.log_level`: `DEBUG`, `INFO`, `WARNING`, `ERROR`.

---

## Retry behavior

429s and 5xx responses auto-retry with exponential backoff + jitter, up to `retry.max_attempts` (default 4). 4xx errors fail fast — your input is wrong, retrying won't help.

Config in `config.json:retry` (see above).

---

## Troubleshooting

**"Missing credentials.json"** — Follow `GCP_SETUP.md` and put the downloaded file in the project folder.

**"Permission denied (403)"** — The API for that service isn't enabled in your GCP project. Re-check step 2 of `GCP_SETUP.md`.

**"Auth error (401)"** — Delete `token.json` and run `python server.py` again to re-consent. (Also happens if you add new scopes and haven't re-authenticated.)

**"Scope has changed" on startup** — You updated `auth.py:SCOPES` (or I did). Delete `token.json` and run `python server.py` to reconsent with the new scopes.

**"insufficient authentication scopes" on contacts_create / contacts_update / contacts_delete** — CRM write tools need the full `contacts` scope, not just `contacts.readonly`. If you set up this MCP before CRM features were added, delete `token.json` and re-consent.

**Chat tool returns 403 "The caller does not have permission"** — user-OAuth Chat API can only touch spaces you're already a member of. Creating a new space or adding members through the API requires admin/chat-app permissions you don't have as a regular user. Use the Google Chat UI for those ops; use the MCP for everything within spaces you're in.

**Chat tool returns 404 on a space you know exists** — double-check the space resource name (e.g. `spaces/AAAAxxxx`). Get it from `chat_list_spaces` first; don't try to construct it manually from a URL.

**Contacts search returns empty** — People API indexes on first call. Run the search twice. (The tool tries to warm it up automatically, but not all accounts respond fast enough.)

**Tool not showing up in Cowork** — Restart Cowork after editing the MCP config. Check `python server.py` runs cleanly in a terminal first.

---

## Running tests

```bash
cd /Users/finnnai/Claude/google_workspace_mcp
pip install pytest
python3 -m pytest tests/ -v
```

48 unit tests cover the pure-function parts (template rendering, CRM stats merging, auto-tagging rules, contact flattening, frontmatter parsing). No network — they run offline.

## Scheduling the daily/quarterly automation jobs

Three standalone scripts ship with this project, all designed for cron. Each is idempotent, has a `--dry-run` flag for safe previews, and self-rotates its cron log file at 10MB.

- **`refresh_stats.py`** — refreshes the three managed CRM fields (Last Interaction, Sent/Received 60d) on every saved contact. Runs in ~seconds per 100 contacts via batched Gmail calls.
- **`enrich_inbox.py`** — sweeps recent inbound mail (default 24h window), parses each sender's signature, and updates matching saved contacts with their latest title, phone (E.164), website, and social URLs. Junk filter prevents marketing/noreply senders from polluting your CRM.
- **`refresh_brand_voice.py`** — analyzes your last 90 days of sent mail and writes/refreshes `brand-voice.md` — your personalized voice guide for templates and Cowork prompts. Heuristic mode is free; LLM mode (when `ANTHROPIC_API_KEY` or `anthropic_api_key` in config is set) produces a richer 8-section markdown guide for ~$0.05 per run. Hand-edits below the divider in `brand-voice.md` are preserved across regeneration.

**Cron setup (recommended):**

```bash
crontab -e
```

Add the three lines (replace `YOUR_USER` with your username):

```
0 7 * * *   /Users/YOUR_USER/Claude/google_workspace_mcp/.venv/bin/python /Users/YOUR_USER/Claude/google_workspace_mcp/refresh_stats.py >> /Users/YOUR_USER/Claude/google_workspace_mcp/logs/refresh_stats.cron.log 2>&1
5 7 * * *   /Users/YOUR_USER/Claude/google_workspace_mcp/.venv/bin/python /Users/YOUR_USER/Claude/google_workspace_mcp/enrich_inbox.py >> /Users/YOUR_USER/Claude/google_workspace_mcp/logs/enrich_inbox.cron.log 2>&1
0 6 1 */3 * /Users/YOUR_USER/Claude/google_workspace_mcp/.venv/bin/python /Users/YOUR_USER/Claude/google_workspace_mcp/refresh_brand_voice.py >> /Users/YOUR_USER/Claude/google_workspace_mcp/logs/refresh_brand_voice.cron.log 2>&1
```

That gives you:
- 7:00am daily — managed CRM stats refresh
- 7:05am daily — inbox signature enrichment
- 6:00am every 3 months — brand voice regeneration from your last 90 days

**Each script has `--help` for its own flags.** All three accept `--dry-run` for safe previews.

**Cowork scheduled-task alternative:** create scheduled tasks that call the equivalent MCP tools (`contacts_refresh_all_crm_stats`, `workflow_enrich_contacts_from_recent_mail`). The advantage is you see results surface in your Cowork session. The cron path runs even when Cowork is closed.

---

## Brand voice + LLM-backed features

This MCP supports several features that benefit from an Anthropic API key. **All are optional** — every feature has a regex/heuristic fallback that works without an API key.

**To enable**: set `ANTHROPIC_API_KEY` in your shell env OR add `"anthropic_api_key": "sk-ant-..."` to `config.json` (the config-file path is more reliable on macOS GUI Cowork because of env-propagation quirks). Run `system_check_anthropic_key` from inside Cowork to verify.

| Feature | What it does | Cost per run |
|---|---|---|
| **Brand voice extraction** (`refresh_brand_voice.py`) | Analyzes your sent mail with Claude Sonnet to produce an 8-section voice guide. Replaces the lightweight heuristic mode. | ~$0.05-0.15 |
| **Smart signature parsing** (`signature_parser_mode: "regex_then_llm"` or `"llm"` in config.json) | When regex misses title or organization on a contact's signature, calls Claude Haiku to fill the gap. Detects garbage names (e.g. "the link above") and lets LLM override. | ~$0.001 per gap-fill |
| **System verification** (`system_check_anthropic_key`) | One-tap test that the key is set + valid + billable. Used during setup. | ~$0.0001 per check |

`config.json` knobs that control LLM behavior:
- `anthropic_api_key` — paste-able fallback when env vars don't propagate
- `signature_parser_mode` — `"regex"` (default, free), `"regex_then_llm"`, or `"llm"`
- `crm_window_days` — window for the managed Sent/Received tallies (default 60)

---

## Attachment handling — automatic for any size

The MCP transparently handles attachments from "smaller than 1KB" to "5GB+" without you needing to think about it.

**Outbound (`gmail_send_email`):**
- Files under `large_attachment_threshold_kb` (default 500KB) inline normally.
- Files larger auto-upload to Drive via streaming (chunked, never fully in RAM — works for huge files), share with every recipient as `reader` (no Drive notification, you send one deliberate email), and append the share link to the body.
- Total inline size is pre-checked against `gmail_max_message_kb` (default 22.5MB) — if it would exceed Gmail's 25MB ceiling, all attachments downgrade to Drive.
- `attachments` accepts both bare strings (`["/path/to/file.pdf"]`) and full AttachmentSpec dicts (`[{"path": "...", "filename": "...", "mime_type": "..."}]`).

**Inbound (`gmail_download_attachment`, `chat_download_attachment`, `drive_download_binary_file`):**
- Files under `max_inline_download_kb` (default 5MB) return as base64 inline.
- Larger files **auto-save** to `default_download_dir` (default `~/Gmail Downloads`) — a dedicated folder kept separate from your cluttered system Downloads. The tool returns the path you can open immediately.
- If a download would collide with an existing file, a timestamp suffix is added (`report.pdf` → `report.20260425-002358.pdf`).
- All `default_download_dir` configurable per-coworker in `config.json`.

## Security notes

- `credentials.json`, `token.json`, `config.json`, and `logs/` are all in `.gitignore`.
- `token.json` is written `chmod 600`.
- Scopes are broad by design (full Gmail/Drive/Calendar/Tasks) to avoid constant re-consent. Tighten in `auth.py:SCOPES` if you want least-privilege.
- Delete `token.json` at any time to fully revoke local auth.

---

## Extending

New tool in an existing service? Add a function in the relevant `tools/*.py` with a Pydantic input model, register it inside that module's `register()`. Nothing else to wire.

New service? Create `tools/<service>.py` with a `register(mcp)` function, then add the import + call in `tools/__init__.py`. If the service needs a new scope, add to `auth.py:SCOPES` and bump the GCP_SETUP doc.

New workflow? Add it in `tools/workflows.py`. Use the internal helpers (`_gmail()`, `_drive()`, `_calendar_svc()`) so everything shares one auth state.
