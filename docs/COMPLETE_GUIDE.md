---
title: CoAssisted Workspace — Complete Guide
date: 2026-04-27
---

# CoAssisted Workspace — Complete Guide

*(formerly "Google Workspace MCP")*

**Version:** 0.4.0 · **Last updated:** 2026-04-27 · **Tools:** 167 across 12 categories · **Tests:** 283 passing

This document is a one-stop merge of `README.md`, `INSTALL.md`, and `docs/USER_MANUAL.md`. Make edits in the source documents, not here.

---

# Part 1 — README

# CoAssisted Workspace

> *Formerly developed under the working title "Google Workspace MCP" — same codebase, same tools, productized.*

**CoAssisted Workspace** is a local MCP server that gives an AI assistant end-to-end control of your Gmail, Calendar, Drive, Sheets, Docs, Tasks, Contacts, and Chat — plus 33 cross-service workflows including territory routing, drive-time block automation, brand-voice extraction, and bulk CRM operations. Includes **actual send** for email (not just drafts), attachments, filters, Meet links, binary file I/O, dry-run mode, and more.

Works with **Claude Code, Claude Cowork**, and any other tool that speaks the Model Context Protocol.

Built with Python + FastMCP + OAuth 2.0. Runs locally, stdio transport, your data never leaves your Mac except to hit Google's APIs.

## Pricing tiers

| Tier | Tools | What it's for |
|---|---|---|
| **Free** | ~50 tools — all Workspace basics (send, read, organize across all 8 Google services) + system health checks + audit log | Get started, run real tasks today, decide if it's worth more |
| **Paid** | All 167 tools — **flagship Receipt Extractor** (LLM-parses inbox + PDFs + photos into a Sheet, archives PDFs to Drive, exports QuickBooks CSV), Maps × CRM × Calendar workflows, Vehicle Routing optimization, brand voice extraction, bulk operations with rollback, advanced Chat (DMs, search, attachments), full CRM (custom fields, groups, stats refresh), templates + mail merge | Daily-driver productivity, sales/CSM/ops workflows, multi-vehicle routing, expense reporting |

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
- `system_check_tools` — confirms all 167 tools register without errors.
- `system_check_quota_usage` — estimates Maps + Anthropic spend this month from logs.
- `system_check_anthropic_key` — Anthropic API key + live test.

**Junk filter** (in `junk_filter.py`): hard-fail signals include noreply local-part patterns (`noreply`, `do_not_reply`, `notifications`), cloud-vendor role accounts (`googlecloud`, `aws`, `azure`, etc.), notification/marketing sub-domains (`emailnotifications`, `accountprotection`, `e.stripe.com`), `List-Unsubscribe` / `Precedence: bulk` / `Auto-Submitted` headers. Soft signals combine body boilerplate ("please do not reply", "this is an automated message"), opt-out phrases ("unsubscribe", "view in browser"), link-to-text ratio, transactional subjects ("receipt", "your weekly digest", "thanks for upgrading"), and a 58-phrase spam-hype list ("free trial", "money back", "guaranteed", etc.). Two soft categories together classify as junk; 4+ spam-hype phrases trip on their own.

**Total: 167 tools across 12 categories.**

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

Three example templates ship by default (`cold_outreach`, `follow_up_after_meeting`, `re_engage`). Edit them or add your own — no server restart needed.

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


---

# Part 2 — INSTALL

# Install — CoAssisted Workspace

**Single linear guide. Start here. ~25 minutes end-to-end (longer if you want every optional integration).**

> This is the bootstrap flow. `README.md` is the feature reference. `GCP_SETUP.md` is the Google Cloud walkthrough. If you received this project as a `.tar.gz` from someone else, read `HANDOFF.md` first — it has a shorter "I just got this archive" flow.

---

## What you're setting up

A local MCP server that gives Claude Cowork **167 tools across 12 categories**: Gmail, Calendar, Drive, Sheets, Docs, Tasks, Contacts (with a CRM layer), Chat, Maps, cross-service Workflows, and System health checks.

Everything runs locally on your Mac. Data flows between Claude and Google APIs only; nothing else touches it. You sign in once with your own Google account; nobody else can see your data.

---

## Prerequisites

You need:

- **macOS** (current versions tested: 13, 14, 15)
- **Python 3.10+** — check with `python3 --version`. Comes preinstalled on most Macs. If yours is older: `brew install python@3.12`.
- **Homebrew** — install from https://brew.sh if you don't have it. Used for `corelocationcli` and `pandoc` (optional).
- **A Google account** the MCP will act as (work or personal)
- **Claude Cowork** — installed and signed in
- **About 25 minutes** for the full setup

---

## The big picture

Setup happens in 5 phases. Each is independent — you can stop after Phase 1 and have a working MCP, then come back later for the optional Phases.

| Phase | What you get | Time | Required? |
|---|---|---|---|
| 1. Core install | All 8 base Workspace tools (Gmail, Calendar, Drive, etc.) — **~167 tools** | 15 min | Yes |
| 2. Maps API | 10 Maps tools + 12 Maps×CRM workflows | 5 min | Optional but recommended |
| 3. Route Optimization | Vehicle Routing Problem solver | 3 min | Only if you do multi-stop routing |
| 4. Current-location detection | Drive-time tools use your real location | 2 min | Recommended |
| 5. Telemetry opt-in | Help improve the MCP via sanitized error reports | 30 sec | Optional, default-decline |

**Easiest end-to-end:** run `setup_wizard.py` (in the project folder). Walks you through all 5 phases with skip-able prompts. Re-run anytime to add what you skipped before.

---

## Phase 1 — Core install (required, 15 min)

### Step 1.1 — Open Terminal in the project folder

```bash
cd /Users/finnnai/Claude/google_workspace_mcp
```

(Adjust path if you cloned elsewhere.)

### Step 1.2 — Get your Google credentials.json (one-time)

**This is the only part that requires Google Cloud Console.** Open `GCP_SETUP.md` and follow Sections 1, 2 (only "Enable the APIs" — skip 2c/2d for now), 3, 4. You'll end up with a `credentials.json` file you place in the project folder.

```bash
open GCP_SETUP.md
```

You'll know this is done when `ls credentials.json` returns the file (not "No such file or directory").

### Step 1.3 — Run the installer

```bash
./install.sh
```

This is **idempotent** — safe to re-run any time. It will:

1. Confirm Python 3.10+
2. Create `.venv/` (or reuse existing)
3. Install Python dependencies (FastMCP, google-api-python-client, googlemaps, anthropic, **Pillow** for receipt photo handling)
4. Copy `config.example.json` → `config.json` if missing (won't overwrite your edits)
5. Copy `rules.example.json` → `rules.json` if missing
6. Create `logs/` directory
7. Verify `credentials.json` is present

> **Note on Pillow:** receipt photos straight from a phone camera often run 6–18MB. Anthropic's Vision API caps images at 5MB. Pillow auto-shrinks oversized images before extraction. If you see `image exceeds 5 MB maximum` errors in `logs/google_workspace_mcp.log`, run `.venv/bin/pip install "Pillow>=10.0"` then restart Cowork.

### Step 1.4 — Run OAuth (in Terminal — not Cowork!)

⚠️ **This must happen in Terminal, not via Cowork.** Cowork's MCP subprocess can't open a browser tab. Running the OAuth flow inside Cowork will hang for 60 seconds then timeout.

```bash
./install.sh --oauth
```

**What happens:**
- Your default browser opens to a Google sign-in page
- Pick your `name@your-domain.com` account
- Approve the (long) list of scopes — Gmail, Calendar, Drive, Sheets, Docs, Tasks, Contacts, Chat, **Cloud Platform** (this last one is for Route Optimization)
- Browser shows "The authentication flow has completed."
- A `token.json` file is now in your project folder

If you ever change the scope list (after a future MCP upgrade): **delete `token.json` and re-run this step.**

### Step 1.5 — Wire into Claude Cowork

Add this to Cowork's MCP config:

```json
{
  "mcpServers": {
    "google-workspace": {
      "command": "/Users/finnnai/Claude/google_workspace_mcp/.venv/bin/python",
      "args": ["/Users/finnnai/Claude/google_workspace_mcp/server.py"]
    }
  }
}
```

Restart Cowork (cmd+Q the app, then relaunch — closing the window isn't enough).

### Step 1.6 — Sanity check

In Cowork, ask:

> **"List my calendars"**

Should call `calendar_list_calendars` and return your real calendars within 2-3 seconds. If it fails, jump to **Troubleshooting** below.

You're done with Phase 1 — ~167 tools work right now.

---

## Phase 2 — Maps API (optional, ~5 min, ~$1–5/month typical)

Unlocks **10 raw Maps tools + 12 Maps×CRM×Calendar workflows** including:
- Geocoding any address
- Distance matrix (driving times between many points)
- "Where to meet" venue suggestions between attendees
- Static map images attached to emails / Chat
- Auto-create "Drive to X" calendar events with traffic-aware leave-by times

Maps APIs use a **separate static API key** (not OAuth). Google offers a $200/month free credit which covers typical personal use.

### Step 2.1 — Enable billing on your GCP project

https://console.cloud.google.com/billing — Maps requires billing enabled even on free tier (Google won't issue keys without a card on file).

### Step 2.2 — Enable the 8 Maps APIs (one click each)

- Geocoding: https://console.cloud.google.com/apis/library/geocoding-backend.googleapis.com
- Places (New): https://console.cloud.google.com/apis/library/places.googleapis.com
- Directions: https://console.cloud.google.com/apis/library/directions-backend.googleapis.com
- Distance Matrix: https://console.cloud.google.com/apis/library/distance-matrix-backend.googleapis.com
- Time Zone: https://console.cloud.google.com/apis/library/timezone-backend.googleapis.com
- Address Validation: https://console.cloud.google.com/apis/library/addressvalidation.googleapis.com
- Static Maps: https://console.cloud.google.com/apis/library/static-maps-backend.googleapis.com
- **Geolocation** (for current-location detection in Phase 4): https://console.cloud.google.com/apis/library/geolocation.googleapis.com

### Step 2.3 — Create + restrict the API key

- https://console.cloud.google.com/apis/credentials → **Create Credentials → API key**
- Copy the key (starts with `AIza...`)
- Click the key → **API restrictions → Restrict key →** tick all 8 APIs from Step 2.2
- Save

### Step 2.4 — Add to config.json

```json
{ "google_maps_api_key": "AIzaSy..." }
```

### Step 2.5 — Verify

Restart Cowork, then ask:

> **"check my maps api key"**

Calls `system_check_maps_api_key` which makes a one-call test (geocode of Google HQ). Returns billing status, key validity, etc. with targeted hints if it fails.

---

## Phase 3 — Route Optimization API (optional, ~3 min, ~$0.05–$0.20 per shipment)

Only do this if you need Vehicle Routing Problem solving — multi-vehicle, time windows, capacities. Most people don't. The simpler `workflow_route_optimize_visits` (heuristic, ~$0.005 per stop) covers daily-life routing.

### Step 3.1 — Enable Route Optimization API

https://console.cloud.google.com/apis/library/routeoptimization.googleapis.com

### Step 3.2 — Already done in Phase 1

Route Optimization uses the **OAuth `cloud-platform` scope** which was included in Phase 1's OAuth flow. No additional setup needed if you completed Phase 1.

If you set up the MCP before this scope was added, **delete `token.json` and re-run `./install.sh --oauth`** to re-consent.

### Step 3.3 — Verify

Ask Cowork:

> **"workflow_route_optimize_advanced with 2 stops near me, 1 vehicle, dry_run=true"**

Should return a request body preview. If you see HTTP 403 `permission_denied`, recheck Step 3.1.

---

## Phase 4 — Current-location detection (optional, ~2 min, free)

By default, the drive-time tools use your `home_address` from `config.json` as the origin. This phase enables them to detect your **actual current location** (street-accurate via Wi-Fi triangulation) so a drive event reflects where you really are right now — not where you usually start the day.

### Step 4.1 — Install corelocationcli

```bash
brew install corelocationcli
```

### Step 4.2 — Grant Location Services to BOTH Terminal AND Claude

This is the trickiest step. The MCP subprocess inherits permission from its **parent process** (Cowork), but Terminal also needs it for command-line testing.

**Open System Settings:**

`System Settings → Privacy & Security → Location Services`

**Enable two things:**

1. The **master toggle** at the top — must be ON
2. In the app list:
   - **Terminal** (or iTerm, whichever shell you use) — for command-line testing
   - **Claude** — so Cowork's MCP subprocess can read your location

> 💡 If "Claude" isn't in the list yet: that's normal. It gets added the first time the app requests location. Easiest way: do Step 4.3 below — that triggers the macOS permission prompt for Claude.

### Step 4.3 — Verify

In Terminal:

```bash
CoreLocationCLI -format "%latitude,%longitude,%horizontalAccuracy"
```

Should print coords like `37.779534,-122.393571,15.0` (or with spaces — both formats work). If you see "Location services are disabled", repeat Step 4.2.

In Cowork, ask:

> **"departure_reminder for my next event with current_location_mode=auto"**

Look at the response — `origin_source` should say `"corelocationcli"` (~10m accuracy). If it says `"google_geolocation"` (~800m accuracy), corelocationcli isn't working — see Troubleshooting.

### Step 4.4 — Set home_address as the fallback

Even with current-location detection, you want a `home_address` for cases where detection fails (e.g. corelocationcli not installed on a coworker's Mac, no Wi-Fi for triangulation).

Add to `config.json`:

```json
{ "home_address": "1 Hacker Way, Menlo Park, CA" }
```

The drive-time tools will use this when `current_location_mode="off"` OR when auto-detection fails.

---

## Phase 5 — Telemetry opt-in (optional, ~30 sec, free)

When `system_doctor` finds a problem on your machine, you can optionally email a **sanitized** error report to the developer so the next release fixes the issue for everyone. Every send is opt-in per call — nothing transmits automatically.

### Step 5.1 — Decide whether to share

The `setup_wizard.py` walks you through this with the same explanation. If you skip it, you can enable later by editing `config.json`.

### Step 5.2 — What gets sent

Only on calls where you explicitly run `system_share_health_report` with `confirm=true`:

- Names of failed health checks (which checks went red)
- Error details with **emails, API keys, OAuth tokens, file paths, IPs, and GCP project IDs all redacted to `<PLACEHOLDER>`**
- macOS version, Python version, integration booleans (e.g. `maps_configured: true` — never the actual key value)
- Last 20 entries from `recent_actions.jsonl` (write-op audit log), also sanitized

### Step 5.3 — What NEVER gets sent

- Your actual emails, contacts, calendar events, Drive files, Chat messages
- Any API key or OAuth token
- Your name, exact location, or any data values from your tools

### Step 5.4 — Enable

In `config.json`:

```json
{ "telemetry_email": "developer@example.com" }
```

Reports always save locally first to `logs/health_reports/health_report_<timestamp>.json`. You preview each report (`system_share_health_report` with `confirm=false` is dry-run) before any send.

To revoke: remove the `telemetry_email` line from `config.json`. Past reports stay on your disk until you delete them.

---

## Optional add-ons

### Anthropic API key (for brand voice + LLM signature parsing)

Heuristic / regex defaults work for everything without a key. The Anthropic key only enables:
- Brand voice deep analysis from your sent mail (`refresh_brand_voice.py`, ~$0.05–0.15/quarterly run)
- Smart signature parsing (~$0.001 per gap-fill)

To set up:
1. Get a key at https://console.anthropic.com → Settings → API Keys
2. Add to `config.json`: `{ "anthropic_api_key": "sk-ant-api03-..." }`
3. Verify: ask Cowork **"check my anthropic key"**

### Schedule daily CRM refresh

Three cron-friendly scripts ship with the MCP:

```bash
crontab -e
0 7 * * *   /Users/finnnai/Claude/google_workspace_mcp/.venv/bin/python /Users/finnnai/Claude/google_workspace_mcp/refresh_stats.py >> /Users/finnnai/Claude/google_workspace_mcp/logs/refresh_stats.cron.log 2>&1
5 7 * * *   /Users/finnnai/Claude/google_workspace_mcp/.venv/bin/python /Users/finnnai/Claude/google_workspace_mcp/enrich_inbox.py >> /Users/finnnai/Claude/google_workspace_mcp/logs/enrich_inbox.cron.log 2>&1
0 6 1 */3 * /Users/finnnai/Claude/google_workspace_mcp/.venv/bin/python /Users/finnnai/Claude/google_workspace_mcp/refresh_brand_voice.py >> /Users/finnnai/Claude/google_workspace_mcp/logs/refresh_brand_voice.cron.log 2>&1
```

All accept `--dry-run` for safe previews.

---

## Day-to-day commands

```bash
make test              # run the pytest suite (no network)
make run               # launch MCP server (for manual poking)
make auth              # re-run OAuth (after scope changes)
make refresh           # refresh CRM stats across all contacts
make handoff           # build a tarball for coworkers (excludes secrets)
make clean             # remove .venv and __pycache__
make help              # list all targets
```

---

## What gets created in the project folder

After Phase 1 completes:

```
google_workspace_mcp/
├── .venv/                  (auto — Python sandbox)
├── credentials.json        (YOU add — OAuth client from Google Cloud)
├── token.json              (auto — saved after OAuth, chmod 600)
├── config.json             (auto from example — your personal config)
├── rules.json              (auto from example — domain auto-tagging)
├── brand-voice.md          (auto from refresh_brand_voice.py — your voice guide)
├── logs/                   (auto — file-based log output)
├── README.md               (feature reference)
├── INSTALL.md              (this file)
├── GCP_SETUP.md            (one-time Google Cloud steps)
├── HANDOFF.md              (coworker quick-start)
├── docs/USER_MANUAL.md     (end-user reference, ~30 pages)
├── Makefile                (discoverable commands)
├── install.sh              (the installer)
├── setup_wizard.py         (interactive 4-step setup wizard)
├── pyproject.toml          (package definition)
├── server.py               (MCP entrypoint)
├── auth.py · gservices.py · config.py · logging_util.py · retry.py
├── dryrun.py · rendering.py · templates.py · rules.py · crm_stats.py · errors.py
├── refresh_stats.py        (cron-friendly CRM stats refresh)
├── enrich_inbox.py         (cron-friendly inbox enrichment)
├── refresh_brand_voice.py  (cron-friendly brand voice regen)
├── tools/                  (11 modules: gmail, calendar, drive, sheets, docs,
│                            tasks, contacts, chat, maps, system, workflows,
│                            templates, enrichment)
├── templates/              (8 starter mail-merge templates in your voice)
└── tests/                  (pytest suite — no network)
```

**Files in `.gitignore`** — never committed, never in `make handoff`: `credentials.json`, `token.json`, `config.json`, `rules.json`, `logs/`, `.venv/`.

---

## Troubleshooting

Most issues fall into these buckets. Check the log file first:

```bash
tail -50 /Users/finnnai/Claude/google_workspace_mcp/logs/google_workspace_mcp.log
```

### OAuth & auth

| Symptom | Cause | Fix |
|---|---|---|
| **OAuth flow hangs forever in Cowork** | MCP subprocess can't open a browser | Run OAuth in Terminal: `./install.sh --oauth` |
| **"Auth error 401"** on any tool | Token expired or revoked | Delete `token.json`, re-run `./install.sh --oauth` |
| **"Scope has changed" on startup** | New scope added in upgrade | Delete `token.json`, re-run `./install.sh --oauth` |
| **"Permission denied 403" on Chat/Contacts** | An API isn't enabled in GCP | Re-check `GCP_SETUP.md` Section 2 |

### Cowork environment quirks

| Symptom | Cause | Fix |
|---|---|---|
| **Tool not appearing in Cowork** | MCP subprocess didn't start | Run `python server.py` in Terminal first to surface any errors |
| **`ANTHROPIC_API_KEY` "not set" even after `export` in `~/.zshrc`** | macOS GUI Cowork doesn't inherit shell env | Put the key in `config.json` (recommended) — it's gitignored anyway |
| **Cowork uses old code after edit** | MCP subprocess cached | cmd+Q Cowork (full quit, not close window), then reopen |
| **Tool times out at 60s on first call** | OAuth re-prompt waiting on user (browser) | Run in Terminal first to complete consent: `cd project && .venv/bin/python -c "import auth; auth.get_credentials()"` |

### Maps API issues

| Symptom | Cause | Fix |
|---|---|---|
| **`maps_not_configured`** | API key missing from config + env | Add to `config.json`: `{"google_maps_api_key": "AIza..."}` |
| **Geocode returns "REQUEST_DENIED"** | API not enabled or key restricted | Recheck Phase 2.2 (8 APIs) and 2.3 (key restrictions) |
| **"This API project is not authorized to use this API"** | Specific API not in key allowlist | Add it under API restrictions on the key page |
| **Maps key works but Address Validation fails** | AV is a separate endpoint Google added later | Make sure step 2.2 includes Address Validation API |

### Route Optimization (Phase 3)

| Symptom | Cause | Fix |
|---|---|---|
| **HTTP 403 `permission_denied`** | Route Optimization API not enabled | Enable at https://console.cloud.google.com/apis/library/routeoptimization.googleapis.com |
| **HTTP 401 `auth_failed`** | OAuth token missing `cloud-platform` scope | Delete `token.json`, re-run `./install.sh --oauth` |
| **`no_gcp_project_id`** | credentials.json missing project_id field | Set `gcp_project_id` in `config.json` manually |

### Current-location detection (Phase 4)

| Symptom | Cause | Fix |
|---|---|---|
| **`origin_source: "google_geolocation"` instead of `"corelocationcli"`** | corelocationcli not found OR no Location Services permission | See full diagnostic ladder below |
| **`CoreLocationCLI: ❌ Location services are disabled or location access denied`** | macOS Location Services not granted | System Settings → Privacy & Security → Location Services → enable Terminal AND Claude |
| **CoreLocationCLI works in Terminal but not in Cowork** | Cowork app missing Location Services permission | System Settings → Privacy & Security → Location Services → enable "Claude" |
| **All location methods fail (`no_origin`)** | Network blocked + no fallback | Set `home_address` in `config.json` as a backstop |

**Current-location diagnostic ladder:**

1. `which CoreLocationCLI` — should print `/opt/homebrew/bin/CoreLocationCLI` (or `/usr/local/bin/`). If empty, install: `brew install corelocationcli`.
2. `CoreLocationCLI -format "%latitude,%longitude,%horizontalAccuracy"` in Terminal — should print coords. If "Location services disabled", grant Terminal permission.
3. `cd project && .venv/bin/python -c "from tools.workflows import _resolve_current_location; print(_resolve_current_location(mode='auto'))"` — should print `source: corelocationcli`. If still `google_geolocation`, check `tail -50 logs/google_workspace_mcp.log` for the actual failure mode.
4. In Cowork, call any current-location tool. If `origin_source` shows the wrong source, Cowork itself lacks Location Services permission — grant Claude.

### Other common issues

| Symptom | Cause | Fix |
|---|---|---|
| **Attachment download "not found" with right ID** | Gmail rotates attachment IDs between calls | Use the `filename` parameter instead |
| **Mail merge dry-run shows `<not set>`** | Field is the fallback rendering | Use `contacts_update` to fill in the field, then re-run |
| **Chat tool 404 on space** | User-OAuth Chat only sees spaces you're a member of | Use the Google Chat UI to create the space first |
| **Tarball missing tools after upgrade** | Old `.pyc` files | `make clean && ./install.sh` |

---

## What you can delete safely

- `.venv/` — `install.sh` recreates it
- `logs/` — regenerated on next run
- `__pycache__/` — regenerated on import
- `token.json` — triggers re-auth (`make auth`)

## What you should NEVER delete unless you want to start over

- `credentials.json` — download from Google Cloud again if lost
- `config.json`, `rules.json` — your personal config; back these up
- `brand-voice.md` — auto-regen runs quarterly; re-run `refresh_brand_voice.py` if lost

---

## Upgrading

After receiving a new tarball or pulling code changes:

```bash
make clean        # wipe stale .venv (optional but recommended)
./install.sh      # rebuild .venv + dependencies
```

If the upgrade added scopes (rare): delete `token.json` and re-run `./install.sh --oauth`.

Your `config.json`, `rules.json`, `token.json`, and `credentials.json` are never touched by `install.sh`.


---

# Part 3 — USER_MANUAL

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

## Tool reference (167 tools across 12 categories)

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

**Maps × CRM × Calendar (16) — *NEW***
**`workflow_nearby_contacts`** (radius search ranked by distance/recency), **`workflow_route_optimize_visits`** (TSP heuristic, fast/cheap), **`workflow_route_optimize_advanced`** (full VRP via Google Route Optimization API — time windows, multi-vehicle, capacities, cost coefficients, inferred skip reasons), **`workflow_route_optimize_from_calendar`** (calendar events → VRP feasibility check), **`workflow_travel_brief`** (city + dates → contacts + slots + Doc + email), **`workflow_geocode_contacts_batch`** (one-shot bulk geocode → custom fields), **`workflow_address_hygiene_audit`** (Address Validation → Sheet of fixes), **`workflow_contact_density_map`** (territory map of saved contacts), **`workflow_meeting_midpoint`** (fair-distance venue + auto invite), **`workflow_commute_brief`** (daily leave-by note for first meeting), **`workflow_event_nearby_amenities`** (coffee/lunch/parking near an event), **`workflow_errand_route`** (lighter route-optimize heuristic), **`workflow_recent_meetings_heatmap`** (last N days of in-person events plotted), **`workflow_departure_reminder`** (live-traffic popup or sibling travel block), **`workflow_calendar_drive_time_blocks`** (bulk auto-create "🚗 Drive to X" events with smart-chain origin, Maps URL, assistant trip note, conflict alerting), **`workflow_remove_drive_time_blocks`** (companion cleanup)

### Maps (10) — *NEW*
`maps_geocode`, `maps_reverse_geocode`, `maps_search_places`, `maps_search_nearby`, `maps_get_place_details`, `maps_get_directions`, `maps_distance_matrix`, `maps_get_timezone`, `maps_validate_address`, `maps_static_map`

> **Setup:** Maps uses an API key (separate from OAuth). Run `setup_wizard.py` for the easiest path, or follow `GCP_SETUP.md` Section 2c manually. Verify with `system_check_maps_api_key`.

### System (14) — *expanded health-check suite*
**`system_doctor`** — flagship: runs all checks below in parallel and returns one structured pass/warn/fail report with specific actionable fixes per check. **Run this first when something's not working.**

`system_check_anthropic_key`, `system_check_maps_api_key`, `system_check_maps_api_key_full` (verify all 8 Maps APIs in allowlist), `system_check_oauth` (token validity + scope coverage), `system_check_workspace_apis` (live call to each enabled service), `system_check_route_optimization` (cloud-platform scope + API enabled), `system_check_location_services` (4-step CoreLocationCLI ladder), `system_check_config` (JSON validity + typo detection), `system_check_filesystem` (writable dirs, file modes), `system_check_dependencies` (Python + libs + binaries), `system_check_clock` (NTP skew detection), `system_check_tools` (verify all 167 tools register), `system_check_quota_usage` (estimated Maps + Anthropic spend this month)

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
