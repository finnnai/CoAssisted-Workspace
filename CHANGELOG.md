# Changelog

All notable changes to CoAssisted Workspace are documented here. Format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this
project uses [semantic versioning](https://semver.org/spec/v2.0.0.html).

## Versioning channels

This project ships on two channels:

- **stable** — tagged GitHub releases (e.g. `v0.6.0`). Safe for daily-driver
  use. Each stable release gets a dedicated section below.
- **dev** — between-release working snapshots (e.g. `v0.6.1-dev`). Tarballs
  carry the dev suffix. Not tagged on GitHub. May change underfoot.

`_version.py` is the single source of truth for `VERSION` + `CHANNEL` +
`RELEASE_DATE`. `pyproject.toml` is hand-synced.

## Release cadence

**Stable releases ship on Fridays.** Bug fixes and small features land
on `main` as dev builds throughout the week (`X.Y.Z-dev`), then on Friday
we flip `_version.py` to plain semver + `CHANNEL=stable`, finalize the
CHANGELOG entry from the [Unreleased] section, and cut the tarball + tag
on GitHub. After the cut, the version immediately bumps to the next
`X.Y.Z-dev` so the working state stays clearly distinguishable from the
release.

Don't cut a stable mid-week unless something's actually on fire — the
predictable Friday cadence is what makes the dev/stable split useful for
testers and marketplace listings.

---

## [Unreleased] — `0.8.2-dev`

Working window for the next dev cycle. Outstanding: AP-1
Supplier Invoice EIB (still gated on Workday GL → Spend Category
map), Geotab integration, P1-7 Slack.

---

## [0.8.1] — 2026-05-01 · stable

Wave 3 ships: AP-7 (StaffWizard labor ingestion), AP-8 (master
roll-up + run-rate dashboard with baseline-deviation alerts), and
AR-9 (customer invoicing + aging buckets + collections cadence,
including end-to-end send wire-up to Gmail). Plus the AP-4 capture
sweep wire-up that was stubbed in 0.8.0.

Cut on the same day as v0.8.0 and v0.7.2 — the AP/AR build-out
came together fast once the Wave 1+2 foundations landed.

### Added — Wave 3 modules

- **AP-7 (`labor_ingest.py`)** — parses StaffWizard Overall Report
  (66-column .xls), groups shifts by (JobNumber, JobDescription)
  → resolves to project_code via project_registry, writes
  per-project Labor/Daily/{YYYY-MM-DD}_labor.xlsx workbooks with
  cost + revenue + margin totals. Auto-converts legacy .xls via
  libreoffice headless. Real-data validation: 108 April 29 shifts,
  28 project groups. 19 tests.
- **AP-8 (`master_rollup.py`)** — three-tab workbook builder
  (All Projects + PM Dashboard + Anomalies). Baseline-deviation
  model: N=30-day cold start, mean ± 2σ envelope, alerts when
  observed daily spend deviates >2σ in either direction. 7-day
  and 30-day rolling run-rates. Idempotent daily-fact recorder.
  18 tests.
- **AR-9 (`ar_invoicing.py` + `ar_send.py`)** — customer
  invoicing pipeline:
    - Invoice generation from labor rows, grouped by
      post_description for clean line items.
    - Per-customer terms (Net-15 default, Net-30, Due-on-Receipt)
      with due-date math.
    - Weekly cadence support for `billing_origin_state == "NY"`
      per the New York project rule.
    - Status transitions: draft → sent → partial → paid.
    - Aging buckets: current / 1-15 / 16-30 / 31-60 / 61-90 / 90+.
    - Collections cadence: 5-tier escalation ladder
      (courtesy_reminder → first_followup → second_followup →
      third_followup → escalation_to_legal). Won't double-send
      the same tier.
    - Send wire-up: HTML email body + Excel attachment, dispatched
      via Gmail API. Tier-appropriate templates for each reminder.
      `mark_sent` and `add_collection_event` advance state on
      successful send.
  22 tests.

### Added — AP-4 wire-up

- `ap_sweep.py` — replaces the four stubbed call sites with real
  Google API integrations: pulls new Receipts-space chat messages
  (per-space watermark to avoid re-processing), downloads Gmail
  attachments and uploads to project Drive folders with AP-6
  naming convention, marks messages read after processing, posts
  candidate-picker chat messages for ambiguous routing. Wave 2
  AP-4 is now fully operational end-to-end.

### Added — Wave 3 MCP wrappers (`tools/ap_wave3.py`)

10 new tools:
- `workflow_ingest_labor_report` — AP-7 ingest
- `workflow_record_daily_fact` — AP-8 manual fact write
- `workflow_build_master_rollup` — AP-8 three-tab workbook
- `workflow_generate_customer_invoice` — AR-9 draft invoice
- `workflow_invoice_mark_sent` — AR-9 send transition
- `workflow_invoice_apply_payment` — AR-9 payment tracking
- `workflow_ar_aging_report` — AR-9 aging buckets
- `workflow_collections_due_today` — AR-9 cadence candidates
- `workflow_send_invoice` — AR-9 actual send
- `workflow_send_collection_reminder` — AR-9 cadence-driven send

### Stats since 0.8.0

- 6 content commits + 1 MCP wrapper commit
- 4 new modules (`labor_ingest`, `master_rollup`, `ar_invoicing`, `ar_send`)
- 10 new MCP tools
- ~59 new unit tests
- ~1,800 LOC

### Open items deferred to 0.8.x and beyond

- **AP-1 — Workday Supplier Invoice EIB**: still gated on the
  GL → Spend Category map.
- **Geotab integration**: still a stub.
- **P1-7 Slack**: original v0.7.0 backlog item, deferred until
  real need surfaces.

---

## [0.8.0] — 2026-05-01 · stable

AP/AR build-out per the
`coassisted-workspace-ap-roadmap-2026-04-30` design doc, Waves 1
and 2. Cuts on the same day as v0.7.2 — Wave 1 + Wave 2 ship
together because they're tightly coupled (the classifier ladder,
EIB writer, project router, and Drive tree manager share the
project_registry as the integration point).

### Added — Wave 1: Workday close path

- **AP-3: GL classifier (4-tier ladder).**
    - `gl_classifier.py` — Tier 0 / 1 / 2 / 3 routing with a
      final clearing-account fallback for the review queue.
    - `gl_merchant_map.py` — operator-confirmed merchant→GL
      learning store. Composite key (merchant, cardholder_email),
      source precedence operator > import > training so training
      noise can't clobber operator decisions, atomic writes,
      history capped at 5 events.
    - MCC table — 40 ranges → 11 GL accounts. Hand-curated from
      the existing AMEX corpus + chart of accounts. HIGH
      confidence on hit.
    - `gl_memo_classifier.py` — Naive-Bayes-lite trained on
      4,601 debit-side expense rows from
      `samples/Wolfhound Corp JEs Jan-Mar'26.xlsx`. Filters
      credit-side and non-expense rows so the model learns spend
      GL patterns, not card-payable / cash routing. MEDIUM/LOW.
    - `scripts/train_gl_memo_classifier.py` — one-shot trainer
      writing `gl_memo_index.json` (gitignored).
    - `gl_classifier_llm.py` — Claude-haiku fallback over a
      curated list of 29 AP-relevant expense GL accounts.
      ~$0.0008 per call. Strict output parsing.
    - 58 new tests across the four classifier modules.
- **AP-2: AMEX + WEX → Workday Journal EIB.**
    - `workday_journal_eib.py` — AMEX parser (41 columns,
      filters CLEARED only by default), WEX parser (60
      columns, fuel-card with vehicle/driver attribution),
      two-sheet EIB writer matching the SFNA AMEX EIB
      convention exactly.
    - Refund handling reverses dr/cr direction.
    - Real-data validation: 77 AMEX + 315 WEX April
      transactions → 100% classified, 0 fell through.
    - Memo format `{LABEL} Transactions {start}-{end} -
      {Cardholder} - {Vendor}`.
    - 22 new tests with synthetic CSV fixtures.
- **`cost_center_map.py`** — persistent cardholder/department
  → cost center mapping store. Same architectural pattern as
  `gl_merchant_map.py`. `derive_from_je_corpus()` returns
  draft suggestions for operator review.
- **MCP wrappers (`tools/ap_journal.py`)** — 7 tools:
    - `workflow_reconcile_card_statement`
    - `workflow_gl_classify_preview`
    - `workflow_gl_merchant_map_set` / `_list`
    - `workflow_gl_memo_index_status`
    - `workflow_cost_center_map_set` / `_list`

### Added — Wave 2: Capture reliability + visibility

- **AP-6: Forced project Drive tree (`ap_tree.py`).**
    - `register_new_project` — full 7-subfolder subtree creation
      + current-month bucket. Idempotent. Persists every Drive
      ID into `project_registry`.
    - `ensure_month_subtree` — lazy {YYYY-MM}/ creation under
      Receipts and Invoices. Called on every receipt write.
    - `audit_filing_tree` — daily scan for files that bypassed
      the capture pipeline. Naming-convention check.
- **AP-5: Project router (`project_router.py`).**
    - 7-tier resolution: explicit (1.00) → alias match (0.92)
      → team email (0.88) → calendar tiebreaker (0.80) →
      Geotab GPS (0.85, stub) → LLM inference → chat picker.
    - `confidence_action()` maps to: auto_file (≥0.85) /
      auto_file_flag (0.65–0.85) / chat_picker / triage.
    - 21 new tests.
- **`project_registry.py`** extended with Wave 2 fields:
  drive_folder_id + drive_subfolders, name_aliases,
  staffwizard_job_number + job_desc, assigned_team_emails,
  billing_origin_state ('NY' unlocks weekly cadence),
  billing_terms, billing_cadence, customer_email. Plus
  helpers: resolve_by_alias, resolve_by_team_email,
  resolve_by_staffwizard_job, update_drive_subfolder,
  get_drive_subfolder.
- **AP-4: Capture sweep (`ap_sweep.py`).**
    - `decide_disposition` — pure routing decision per inbound
      item.
    - `run_sweep_cycle` — pulls Gmail + Chat, routes via AP-5,
      executes the disposition. Drive download / mark-read /
      chat-post call sites stubbed; routing decisions are
      deterministic and tested.
    - 11 new tests.
- **MCP wrappers (`tools/ap_tree.py`)** — 6 tools:
    - `workflow_register_new_project`
    - `workflow_audit_filing_tree`
    - `workflow_ensure_month_subtree`
    - `workflow_route_project`
    - `workflow_project_registry_list`
    - `workflow_ap_sweep_cycle`

### Sample data added (gitignored)

- `samples/Submit_Supplier_Invoice_v39.1.xlsx`
- `samples/SFNA AMEX EIB MARCH 26.xlsx`
- `samples/Extract_Ledger_Accounts.xlsx` (Workday COA, 212 accts)
- `samples/Overall Report SFOX 1777532406.xls` (StaffWizard labor)
- `samples/Amex Transactions - April.csv` (111 txns, 8 cardholders)
- `samples/Wex Fuel Transactions - April.csv` (315 txns, 46 drivers)
- `samples/Wolfhound Corp JEs Jan-Mar'26.xlsx` (17,346 JE rows)

### Open items deferred to 0.8.x and beyond

- **AP-1 — Workday Supplier Invoice EIB**: gated on the GL →
  Spend Category map from Workday config (col 113 of
  `Submit_Supplier_Invoice_v39.1`). The 17k JE training set
  doesn't carry that map cleanly. Ships when available.
- **AP-4 wire-up**: 4 stubbed call sites in `ap_sweep.py`
  (download attachment, mark read, post chat picker, chat
  ingestion) need integration with existing `tools/gmail` +
  `tools/chat` surfaces. ~1 hour follow-up commit.
- **Geotab integration**: `_geotab_tiebreaker` in
  `project_router.py` is a stub. Wires up when GEOTAB_*
  credentials land in `config.json`.
- **AR-9 (Wave 3)**: customer invoice generation, aging,
  collections cadence. Mirror of vendor follow-up loop on the
  receivables side.

### Stats since 0.7.2

- 7 content commits + 1 handoff log entry
- 12 new modules
- 13 new MCP tools
- ~112 new unit tests
- ~6,200 LOC added

---

## [0.7.2] — 2026-05-01 · stable

First stable cut after the two-week dev cycle that rebuilt the
vendor-reply parsing core, split the workflows monolith, brought
test coverage across the full thin-wrapper surface, and added the
mypy + broad-exception baselines. Everything that was in
`[Unreleased] — 0.7.2-dev` plus the threshold change below is in
this release.

### Changed
- **Bumped `large_attachment_threshold_kb` default from 500 → 22000.**
  Anything within Gmail's per-message ceiling (22MB safe) now ships as a
  real Gmail attachment. Only files above 22MB still route via Drive.
  Eliminates the recurring need to share dist tarballs through Drive when
  delivering builds to handoff recipients. Lower the threshold per-install
  if stdio buffer limits surface on the Cowork MCP channel or corporate
  mail filters bounce large `.tar.gz` / `.zip` attachments.

  Files touched:
    - `config.py` default (line 48)
    - `config.json` (line 13 + `_attachments_comment`)
    - `tools/gmail.py` docstring on `gmail_send_email`

---

## [0.7.2-dev] — 2026-04-30

### Refactored
- **P1-1 — split `tools/workflows.py` into 5 category modules.**
  7898-line monolith → 5 focused files + a shared helpers module + a
  back-compat re-export shim. Mechanical refactor; same 43 tools, same
  names, same schemas. New layout:
    - `tools/_workflow_helpers.py` (645 lines, 24 helpers + 2 module globals)
    - `tools/workflows_gmail.py` (1340 lines, 12 tools)
    - `tools/workflows_crm.py` (1622 lines, 9 tools)
    - `tools/workflows_calendar.py` (3085 lines, 15 tools)
    - `tools/workflows_chat.py` (1050 lines, 5 tools)
    - `tools/workflows_misc.py` (360 lines, 2 tools)
  `tools/workflows.py` is now a 133-line shim re-exporting helpers,
  module globals, and all input classes. `tools/__init__.py` imports
  the 5 new modules directly. Two test patches captured the structural
  change.

### Polished
- **Polish bundle (5 items in one commit, fb9fed0).**
  (1) Removed leftover `*.bak.*` files from in-session edits.
  (2) Swept 6 stale `awaiting_info.json` entries via the new
      `workflow_sweep_awaiting_info` — validates the P0-4 tool on real data.
  (3) mypy strict opt-in for `review_queue.py` and
      `vendor_response_history.py`. Tightened 11 generic dict args +
      2 float() return casts so they pass `--strict`.
  (4) Fixed 2 `datetime.utcfromtimestamp` deprecation warnings in
      `refresh_brand_voice.py` (replaced with timezone-aware
      `datetime.fromtimestamp(ts, tz=datetime.timezone.utc)`).
  (5) P1-3 deeper sample on `tools/project_invoices.py` (12 of 106 broad-except).
      10 legit, 2 swallowers fixed: brand-voice load narrowed to
      `OSError/UnicodeDecodeError` + log.debug; chat sender resolve
      gained log.debug with intent comment.
  Tests: 1293/1293 still pass.

- **Polish round 2 — broad-except cleanup on `chat.py` + `workflows_calendar.py`.**
  12-sample sweep (every-5th of the 23 + 32 broad-except handlers).
  10 legit (uniform tool-boundary `format_error`; `chat.py` is fully
  consistent; calendar workflows specialize `RuntimeError` first).
  2 fixed:
    - `tools/workflows_calendar.py:597` — `find_meeting_slot` `getProfile`
      fallback was `except Exception: me = ""`. Now logs at warning so
      OAuth/scope drift surfaces instead of degrading silently.
    - `tools/workflows_calendar.py:2798` — `zoneinfo.ZoneInfo()` was
      catching all of `Exception`. Narrowed to
      `(ZoneInfoNotFoundError, ValueError)` + log.warning so bad tz
      typos are visible.
  Cumulative across P1-3 + 2 polish rounds: **55 samples reviewed,
  12 swallowers fixed** out of the 558-handler population. Pattern
  confirmed: ~83% of broad-except handlers in production are warranted
  (tool-boundary `format_error` is the dominant legitimate use).

### Added
- **P1-3 — broad-exception audit + 6 fixes.** Sampled every-18th-occurrence
  across the 558 production-code broad-except handlers; 31 samples
  classified. 81% legitimate (tool-boundary `format_error` handlers,
  API failure handlers with logs, defensive fallback chains). 19% (6
  occurrences) swallow-ish — fixed in place: 2 narrowed to `ImportError`,
  3 add `log.warning`/`log.debug` for visibility, 1 also needed a missing
  `log` import in `ap_drive_layout.py`. Findings + recommended patterns
  documented in `p1-3-exception-audit-2026-04-30.md`. Tests: 1293/1293
  still pass.

- **P2-2 — mypy added to dev workflow.** `[tool.mypy]` config in
  pyproject.toml with a soft baseline (ignore_missing_imports,
  check_untyped_defs=False, no_implicit_optional, warn_unused_ignores).
  `make typecheck` and `make typecheck-strict` Makefile targets.
  Baseline: P0-2/P1-4/P1-5 modules (review_queue,
  vendor_response_history, vendor_followups) all clean. Existing 61
  top-level source files: 150 errors in 26 files (mostly missing
  return types, Optional-vs-None mismatches, missing `requests`
  stubs). Cleanup is incremental — flip `[[tool.mypy.overrides]]
  strict=true` per-module as files get touched.

- **P1-4 — smarter reminder cadence (per-vendor history + day-of-week
  + US federal holidays).** New module `vendor_response_history.py`
  records `(request_sent_at, replied_at)` pairs per lowercased vendor
  email, rolling window of 20, median computed only after 3+ pairs.
  `adaptive_wait_hours()` maps median latency to next reminder window:
  `<12hr → 24hr`, `12-48hr → 72hr`, `>=48hr → 120hr`, cold-start → default.
  Bundled `us_federal_holidays.json` (2026-2030, 55 dates).
  `vendor_followups.due_for_reminder()` now: (1) calls per-vendor
  adaptive wait, (2) pushes Sat/Sun/holiday reminder moments to next
  business-day 9am local. `workflow_process_vendor_replies`
  auto-records pairs on HIGH/MEDIUM confidence outcomes; LOW (deferral)
  replies are skipped so the median reflects real responsiveness.
  +21 tests.

- **P1-5 — snooze + bulk actions + escalation trail.** 5 new tools,
  brings total 233 -> 238. `vendor_followups.py` gains a `snoozed_until`
  field + an `events: list[]` timeline auto-populated by
  `register_request` (ASK), `record_reminder` (REMINDER tier N),
  `mark_resolved` (RESOLVED), `snooze`/`unsnooze` (SNOOZED/UNSNOOZED).
  `due_for_reminder()` now skips entries snoozed into the future. New
  helpers: `snooze`, `unsnooze`, `append_event`, `get_trail`. Tools:
    - `workflow_snooze_awaiting_info` — pause reminders until a date
    - `workflow_unsnooze_awaiting_info` — clear snooze early
    - `workflow_bulk_resolve_awaiting_info` — mark many resolved
    - `workflow_bulk_promote_review_queue` — bulk-approve medium-
      confidence entries from review_queue.json
    - `workflow_get_escalation_trail` — fetch timeline (json or
      compact one-line text: '2026-04-12 ASK · 2026-04-19 R1 · ...')
  +26 tests, total 1246 -> 1272.

- **P0-3 complete — baseline unit tests for all 13 thin-wrapper tool
  modules.** 188 new tests across 13 files (drive 21, calendar 23,
  gmail 36, handoff 2, scanner 4, tasks 12, docs 7, sheets 12,
  maps 13, chat 22, contacts 23, enrichment 5, system 6) covering
  input-model validation + error-path mocking + registration smoke.
  Pattern: resolve_tool + run + http_error + err_assert scaffold.
  Chat/contacts/system focus on input validation since their happy-
  path mocks require heavy cross-cutting setup (cross-domain DM
  resolution, People API tree walking, doctor's live network calls).
  Suite: 1058 → 1246 passing in 3.51s.
- **`workflow_sweep_awaiting_info`** (P0-4) — list and optionally
  bulk-clear stale entries from `awaiting_info.json`. Filters by
  channel (gmail/chat) and project_code. When `older_than_days` is
  set, dry_run defaults to True for safety; pass dry_run=False to
  apply. 7 tests.
- **`workflow_list_review_queue`** (part of P0-2) — list medium-
  confidence vendor replies queued for human approval. Bulk
  promote/forget actions. Promote also calls `_vf.mark_resolved`
  on the underlying awaiting_info entry.
- **`review_queue.py`** — new atomic-write store for medium-
  confidence replies. Same pattern as `vendor_followups.py`.
- **`score_reply_confidence(parsed, fields_requested, body)`**
  (P0-2) — pure function classifier returning "high" / "medium" /
  "low". Detects 18 deferral phrases ("will send", "let me check",
  "out of office", etc.) that cap confidence at low even when
  fields parse cleanly. 10 tests.
- **`_archive_reply_attachments_to_project`** (P0-2) — extracts
  PDF/image attachments from vendor replies into
  `AP Submissions/Reply Attachments/<PROJECT>/<vendor>/`. Plumbed
  but mime-gating off by default — accepts any PDF or image.
- **`ensure_reply_attachments_folder(project, vendor)`** in
  `ap_drive_layout.py` — Drive folder helper for the new tree.
- **`update_latest_reply_ts(content_key, ts)`** in
  `vendor_followups.py` — dedup helper. Stores the timestamp of
  the newest reply we've already processed.
- **`_find_gmail_reply(thread_id, sent_at_iso, after_ts_iso)`**
  in `tools/project_invoices.py` — richer replacement for
  `_find_gmail_reply_body`. Returns the full message + body +
  timestamp so the orchestrator can dedup and extract attachments
  in one walk. Walks oldest-to-newest and returns the oldest
  unseen reply. Old `_find_gmail_reply_body` retained as a thin
  wrapper for backwards compat.
- **Network-dependent test marker** (P1-2) — `network` marker in
  `pyproject.toml`. Default `pytest` excludes it. New Makefile
  targets: `test`, `test-fast`, `test-network`. Default suite
  went from 104s/14-failures to 3s/0-failures.
- **Brand-voice corpus auto-email filter** (P1-6) —
  `_is_google_auto_body` rejects Drive-share / Meet-invite /
  Forms-response bodies. Calendar invites filtered upstream via
  Gmail subject operators. Override with
  `BRAND_VOICE_INCLUDE_AUTO=1`. 7 tests.

### Changed
- **`workflow_process_vendor_replies`** (P0-2) — confidence-gated
  promotion path. HIGH → update + promote + mark_resolved + ack.
  MEDIUM → update in place + queue for review + leave
  AWAITING_INFO. LOW → no update; reminder cadence handles next
  nudge. Dedups multi-message threads via `latest_reply_ts`.
  Pulls reply attachments into project Drive on HIGH/MEDIUM.
  Result schema gained `rows_held_for_review`,
  `rows_low_confidence`, per-update `confidence`, `queued_for_review`,
  `attachments_saved`. 10 orchestrator tests.
- **`vendor_followups.register_request`** — record now includes
  `latest_reply_ts: None`.
- **`tools/system.py::_check_config`** (P2-1) — keys starting with
  `_` are conventionally inline JSON comments and are skipped by
  the validator. Stops `_attachments_comment` showing up as
  "Unknown keys" on every doctor run. Also fixed a stale
  `/Users/finnnai/...` path in the missing-config fix-hint.
- **`tests/test_project_invoices_tools.py`** — autouse fixture
  mocks `llm.is_available -> (False, ...)` so composer tests run
  the deterministic fallback path. Tests that specifically need
  the LLM branch can override per-test. The two `ALPHA` tests
  patched in 0.7.2-dev kept their explicit `with patch(...)`
  for clarity.

### Build
- Bumped to `0.7.2-dev`. `_version.py` + `pyproject.toml` synced.
- Local git history initialized (P0-1). Project is now under
  version control on the user's machine.
- Added `pytest-timeout` to dev dependencies.

---

## [0.7.1-dev] — 2026-04-29

### Changed — docs / branding / licensing
- **Tool count corrected to 230** in README.md, INSTALL.md, dist/README_HERO_DRAFT.md
  (previously stated 183 — stale snapshot from before the Executive Briefing, invoice
  pipeline, vendor follow-up, and recent workflow batches landed). Authoritative
  count comes from `system_check_tools` against the live MCP.
- **LICENSE replaced with proper MIT** (was a proprietary "all rights reserved"
  text despite being switched in spirit to MIT earlier; the file itself was
  never updated). Copyright line now reads "© 2026 CoAssisted Workspace".
- **Source-file headers normalized** to `# © 2026 CoAssisted Workspace. Licensed under MIT.`
  across all 17 modules that still carried the old "Licensed for non-redistribution
  use only" wording (auth.py, server.py, config.py, tier.py, telemetry.py,
  receipts.py, project_invoices.py, project_registry.py, sender_classifier.py,
  vendor_followups.py, ap_drive_layout.py, gservices.py, merchant_cache.py,
  recent_actions.py, tools/system.py, tools/receipts.py, tools/project_invoices.py).
- Branding sweep verified: no stray "Workspace Pilot" / "Workplace Pilot"
  references outside CHANGELOG history.

---

## [0.7.0] — 2026-04-29

The phased-roadmap release. Ships the entire 36-workflow shortlist
(P0 through P7) with the eight infrastructure pieces that unlock them.
Phased so each layer can be live-tested independently.

### Added — P0 (no new infra, 5 quick wins)
- **Reply-all guard (#71)** — `gmail_check_reply_all` analyzes a draft
  for unnecessary reply-all. Detection signals: single-target greeting,
  ack-only body, FYI opening, CC-fanout. Verdict synthesis: safe / warn
  / block. Sender-self filtered from recipient counts.
- **Access audit (#21)** — `drive_access_audit` classifies every grant
  on a file/folder as self / internal / subsidiary / external / public /
  domain-wide. Risk flags + scoring: anyone_with_link, public_writable,
  external_owner, external_writer, domain_writable, deleted_account.
  Includes `diff_reports()` for before/after comparisons.
- **Morning brief (#1)** — `workflow_morning_brief` composes today's
  calendar + inbox needs-reply + AP outstanding + stale relationships
  into a top-5 ranking. High-stakes meetings, VIP unread, and AP
  overdue items dominate. JSON or markdown output.
- **Schedule defrag (#6)** — `workflow_schedule_defrag` finds
  fragmented gaps below the useful-block threshold + pairs of
  fragments that, if the meeting between them moved, would unlock
  a contiguous focus block.
- **NDA / contract bundle generator (#20)** — `workflow_contract_bundle`
  searches Drive for NDA/MSA/SOW/agreement/contract files matching a
  year + type filter, packs them into a ZIP, generates a Doc index
  with parsed counterparties and clickable links.

### Added — P1 (background scanner + 7 cadence-driven workflows)
- **Background scanner core** — `scanner.py` with named-check registry,
  TTL-based cadence, atomic-write state in `scan_state.json`, force-run +
  run-due dispatchers. MCP tools: `workflow_run_scanner`,
  `workflow_list_scanner_checks`.
- **7 P1 checks** registered with the scanner:
  - `p1_inbox_auto_snooze` (#2) — newsletter / promo / transactional
    classifier, cadence 4h
  - `p1_stale_relationship_digest` (#7) — weekly digest of 60d+ stale
    contacts
  - `p1_reciprocity_flag` (#8) — weekly send/receive ratio scan
  - `p1_send_later_followup` (#13) — hourly check for queued sends +
    auto-followups
  - `p1_sunday_week_ahead` (#22) — weekly week-ahead summary brief
  - `p1_retention_sweep` (#37) — daily sweep for financial-mail
    retention candidates
  - `p1_end_of_day_shutdown` (#38) — daily EOD task carryover + thread
    snooze brief

### Added — P2 (brand voice composer lift-out + draft queue + 8 workflows)
- **`brand_voice.py`** — shared LLM-backed composer (lifted from AP).
  10 intents × 5 audiences. LLM via Anthropic Haiku when API key
  configured; clean template fallback otherwise. Deterministic
  (temperature=0). Variant seeding via md5 hashing.
- **`draft_queue.py`** — generic draft-then-review queue. Atomic-write
  JSON sidecar. Lifecycle: PENDING → APPROVED → SENT (or DISCARDED).
  MCP tools: `workflow_compose_draft`, `workflow_list_drafts`,
  `workflow_approve_draft`, `workflow_discard_draft`,
  `workflow_edit_draft`.
- **8 P2 workflows** riding both infra pieces:
  - `workflow_auto_draft_inbound` (#15) — score inbox threads,
    auto-draft replies for needs-reply ones
  - `workflow_rsvp_with_alternatives` (#24) — decline conflicts +
    propose 2-3 alternate slots from free/busy
  - `workflow_ghost_agenda` (#25) — draft a 3-bullet agenda for
    empty-description meetings you organized
  - `workflow_birthday_check` (#26) — daily birthday-of-the-day
    detection + brand-voiced note
  - `workflow_intro_followups` (#40) — intros without follow-through
    in N days → gentle nudge
  - `workflow_cross_thread_context` (#43) — surface other open threads
    with the same person while drafting (passive, no compose)
  - `workflow_meeting_poll` (#74) — find common free slots across
    invitees + draft poll email
  - `workflow_translate_reply` (#77) — detect inbound language,
    reply in the same language

### Added — P3 (external feeds + watched-sheet schema + 4 workflows)
- **`external_feeds.py`** — TTL-cached adapter layer for trusted
  external sources. Adapters for GSA per-diem (FY2026 fallback table
  for major US cities), IRS standard mileage rates (2024-26),
  FX rates. Frozen-mode for tests + offline.
- **`watched_sheets.py`** — generic configurable rules registry. Same
  atomic-write pattern as vendor_followups. Recognized families:
  license, retention, recurring, focus, deadline. Caller-defined
  families also supported. Plus `licenses_expiring()` helper.
- **4 P3 workflows**:
  - `workflow_per_diem` (#62) — GSA per-diem calc (75% M&IE on travel
    days, full inner days)
  - `workflow_mileage_log` (#61) — drive-blocks → IRS-deductible
    mileage entries with quarterly rollup
  - `workflow_license_reminders` (#36) — licenses approaching
    expiration with crossed-threshold buckets (90/60/30/14/7d)
  - `workflow_dsr_collate` (#47) — GDPR/CCPA Data Subject Request
    aggregator across Gmail, Calendar, Drive, Contacts
- Plus 3 watched-sheet management tools.

### Added — P4 (CRM-as-event-sink + 3 workflows)
- **`crm_events.py`** — per-contact event timeline. Atomic-write
  JSON keyed by email. Helpers: `last_event`, `days_since_last_event`,
  `count_events`, `find_intro_acceptance`. Recognized event kinds:
  email_sent / email_received / email_substantive, meeting, intro_made,
  intro_accepted, vendor_invoice, vendor_onboarded, vip_alert.
- **3 P4 workflows**:
  - `workflow_vip_escalations` (#3) — VIP-sender filter with 4h dedup
    against recent alerts. Records vip_alert events.
  - `workflow_record_message_event` + `workflow_calibrated_staleness`
    (#27) — substantive vs ack-only discrimination so 60d staleness
    fires on real conversation drought, not on "thanks!" replies
  - `workflow_vendor_onboarding` (#41) — detects new vendors from CRM
    history, builds checklist plan (W-9, COI, NDA, banking, MSA),
    staggered due dates
- Plus 2 generic CRM event tools.

### Added — P5 (join-across-sheets primitive + 4 AP analytics)
- **`sheet_join.py`** — lite SQL-on-sheets engine. Engine + Query
  fluent API: filter, where, project, inner_join, left_join,
  group_by + agg. Helpers: safe_float (handles "$1,234.56" and "(500)"),
  parse_date, IQR + Tukey fences.
- **4 P5 analytics workflows**:
  - `workflow_project_spend_dashboard` (#9) — YTD, last-30d vs prev-30d
    delta, top vendors, percent-of-budget
  - `workflow_project_pnl` (#29) — spend + revenue + margin per project
  - `workflow_duplicate_invoices` (#55) — same vendor + ±tolerance %
    amount + ±N day window
  - `workflow_ap_anomalies` (#90) — per-vendor IQR baseline + 4×
    median fold-test (catches the small-sample IQR pathology where
    an outlier pulls Q3 up enough to mask itself)

### Added — P6 (Travel suite, 3 workflows)
- `workflow_travel_classify` — flight/hotel confirmation classifier
- `workflow_travel_auto_package` (#16) — flight + hotel → calendar
  blocks (in dest TZ) + drive-time blocks to/from airport + per-diem
  estimate from external feeds
- `workflow_trip_expense_packager` (#33) — receipts in trip window
  → grouped by category + currency-converted via FX cache + draft
  AP submission email
- `workflow_receipt_photo_prompt` (#96) — daily prompt during trip
  windows, 18:30 ± 90min default, per-day dedup

### Added — P7 (Knowledge layer, 2 workflows)
- `workflow_wiki_rebuild` + `workflow_wiki_search` (#19) — TF-IDF
  inverted index over pre-fetched threads. Subject terms get triple
  weight. Stop-word filtered. Returns ranked passages with snippet
  context + source citations.
- `workflow_doc_diff` (#46) — line-level diff between two doc
  versions with plain-English summary bullets + severity rating
  (minor / moderate / major)

### Tests
- **975 passing** (up from 667 in 0.6.x)
- 308 new tests across the 8 new infra modules + 36 workflow logic
  functions
- 7 smoke scripts validating end-to-end synthetic-data scenarios
- All adapters have frozen-mode fallback (no live API calls in tests)

### Bug fixes caught during the build
- License-threshold semantics flipped — now returns smallest bucket
  ≥ days_left (e.g. 45d → 60-day bucket, not 90)
- AP anomaly detection — Tukey IQR pathology on small samples (outlier
  pulls Q3 high enough to mask itself); fixed via hybrid IQR + 4× fold
- Contract bundle regex broke on underscore separators — fixed with
  separator normalization step before matching
- Reply-all guard test mis-expected for two-name greeting

---

## [0.6.0] — 2026-04-28

The AP-automation release. Adds invoice + receipt extraction with project
routing, vendor follow-up loop, threaded chat replies, acknowledgements,
auto-sharing, and subsidiary-domain support.

### Added — AP automation
- **Invoice extractor** + 5-tier project resolver (filename pattern,
  sender email, chat space, explicit code, fallback heuristic) with
  per-project sheet routing
- **Receipt extractor extended** to project-AP path — same routing as
  invoices, hybrid `doc_type` column
- **Vendor follow-up loop** — `vendor_followups.py` tracks outstanding
  info-requests, `workflow_send_vendor_reminders` ramps tier 2 → 3 → 4
  on a per-stage email cadence (24h / 48h / 48h), and
  `workflow_process_vendor_replies` LLM-parses replies to fill missing
  fields and promote rows from `AWAITING_INFO` → `OPEN`
- **Acknowledgement on every successful submission** — bot replies
  threaded back to the original receipt with a bulleted summary
  (vendor / invoice # / date / total / project / status), a clickable
  sheet link, and `*Need your help*` placeholders for missing fields
- **Auto-share project sheet** with internal/subsidiary submitters
  (idempotent, reader-only, gated on `sender_classifier.classify().internal`)
- **Threaded chat replies** — bot's info-requests + reminders + acks
  thread back to the original receipt message in the space, and
  AP DMs use a stable `threadKey` per employee so all back-and-forth
  stacks in one thread

### Added — composer + brand voice
- 4-tier reminder ladder (initial / nudge / circle-back / final) with
  audience-aware tone (`employee` vs `vendor`) and 3 voice variants per
  (audience, tier) chosen deterministically by source-id hash
- Tier 4 final reminder appends *"Without a reply, I'll have to flag
  this for review"* to the closing line — soft consequence, no
  manager escalation
- Condensed SMS-style chat variant for all info-requests (no
  greeting / sign-off / reference footer; uses `*single-asterisk*`
  bold for Google Chat)
- Reference footer (`For reference: vendor / total / date`) below
  `Thanks!` in email, bolded with `<b>` in HTML
- LLM prompt locked to ONLY ask for fields in the explicit
  `missing_fields` list (no more inventing "PO Number" / "Description
  of work" extras)

### Added — sender classification
- `sender_classifier.py` — internal vs external detection with
  4 tiers: auto-derived user domain, `config.internal_domains`,
  `config.subsidiary_domains`, and Gmail send-as aliases
- `subsidiary_domains` config slot — wires sister-orgs into the
  internal flow (DM-first follow-up, ack threading, auto-share);
  staffwizard.com and xenture.com are pre-configured

### Added — Drive layout
- Hybrid AP-submissions folder structure: per-employee folders +
  master roll-up + per-employee-per-project sheets + PDF archive
  of original submissions
- One-shot migrator for legacy project sheets

### Added — installation
- `./install.sh --free` 10-minute install path (53 tools, no API
  keys required, AP routing-rule preview)
- `./install.sh --upgrade` — adds Anthropic + Maps API keys to
  unlock the full 230-tool experience without touching OAuth

### Added — versioning
- `_version.py` — single source of truth for `VERSION` + `CHANNEL` +
  `RELEASE_DATE`
- `make release` and `make dev-build` — versioned tarball builds
- This `CHANGELOG.md` 🎉

### Changed
- Renamed Workspace Pilot → CoAssisted Workspace
- Replaced proprietary LICENSE with MIT
- `format_error()` accepts optional name prefix — was masking real
  exceptions when called with `(name, e)` from workflow handlers
- Composer tone: tier 3 softened (no "before I escalate" / "manager"
  / "EOD" language); each tier supplies its own verb so prose flows
  cleanly ("I'm missing X" / "still need X" instead of "still need
  missing X")
- Chat sends switched from Slack-style `<url|label>` to plain URLs
  so Google Chat auto-detects them as clickable links

### Fixed
- Sheet-share link in chat acks now actually clicks through (was
  broken `<url|label>` syntax)
- Composer no longer produces double-words like "still need missing
  invoice number"
- Vendor name now reads naturally in tiers 2–4 ("Quick follow-up on
  the Acme Roofing invoice" not "on the Acme Roofing")
- Vendor opener variant 2 reworked from "Thanks for sending — just"
  to a clean standalone sentence
- Info-request send no longer gated on `content_key` — falls back to
  `src:<source_id>` tracking key when invoice_number (the field that
  feeds the content key) is itself missing, which was exactly the case
  we needed to fire on
- Bullet field labels bolded in both HTML (`<b>`) and plain
  (`**md**`) for visual emphasis

### Tests
- 652 tests passing (up from 283 in 0.5.x)
- New: composer tier ladder, ack format, threading, auto-share,
  subsidiary classification, format_error backwards-compat

---

## [0.5.x] — earlier 2026-04

See git history. Marketplace submission filed at the end of this
window.

## [0.2.0] — 2026 baseline

Initial CoAssisted Workspace fork from prior Workspace Pilot work.
90 tools spanning Gmail / Calendar / Drive / Sheets / Docs / Tasks /
Contacts (CRM) / Chat.
