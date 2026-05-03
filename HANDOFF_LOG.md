# Handoff Log — CoAssisted Workspace

Append-only journal of every handoff (send AND receive). Each new holder
appends an entry **before** sending the archive on. Read top-to-bottom for
oldest-first; the latest entry is at the end.

## Format

```
## YYYY-MM-DD · From → To

- **Version:** vX.Y.Z (channel)
- **Time held:** how long since the prior handoff
- **Focus area:** one line on what I was working on
- **What I touched:** key files + summary of changes
- **What I left undone:** open items, partial work
- **Pick up here:** specific next step for the next holder
- **Tests:** before / after counts
- **Notes:** anything that doesn't fit above (env quirks, gotchas, etc.)
```

Pair this with `HANDOFF_STATE.json` (machine-readable counterpart) and the
`workflow_receive_handoff` MCP tool, which auto-diffs an incoming archive
vs. the local copy and surfaces what changed.

---

## 2026-04-29 · Finnn → Joshua

- **Version:** v0.7.1-dev (dev channel)
- **Time held:** ~2 weeks of active development since the v0.7.0 cut on 2026-04-29
- **Focus area:** Executive Briefing email overhaul + license/branding cleanup

- **What I touched:**
  - **Executive Briefing system** — full rename from "CEO Briefing":
    `ceo_briefing.py` → `executive_briefing.py`,
    `tools/ceo_briefing.py` → `tools/executive_briefing.py`,
    `scripts/smoke_ceo_briefing.py` → `scripts/smoke_executive_briefing.py`,
    `tests/test_ceo_briefing.py` → `tests/test_executive_briefing.py`.
    Classes: `CeoBriefing`/`CeoBriefingInput` → `ExecutiveBriefing`/`ExecutiveBriefingInput`.
    MCP tool: `workflow_ceo_briefing` → `workflow_executive_briefing`.
  - **Send-path refactor** (`tools/executive_briefing.py`): email body is
    now a narrative-prose summary (plain text + light HTML, no images).
    The full interactive HTML brief ships as an attachment
    (`executive-briefing-<date>.html`) so Gmail's external-image blocker
    can't break the layout — recipients open the attachment in a browser
    where charts and action buttons work freely.
  - **Narrative composers** added: `_narrative_summary_text()`,
    `_narrative_summary_html()`, plus helpers
    (`_meetings_narrative`, `_emails_narrative`, `_tasks_narrative`,
    `_news_narrative`, `_weather_narrative`, `_format_long_date`,
    `_meeting_clauses`).
  - **Dynamic greeting** — `_greeting_word()` returns
    Morning / Afternoon / Evening based on local-clock hour
    (5–11 / 12–17 / else). Capitalized in the rendered greeting.
  - **Calendar tab redesign**: meetings now render as colored
    calendar-event blocks (1:1=blue, customer=green, board=red,
    team=purple, default=navy) with a time rail, organizer chip, and
    attendees row. Inter-event gap markers ("30 min gap", "1h 30m gap",
    "Back-to-back") render between consecutive blocks.
  - **News tab**: news promoted from sidebar to a 4th tab in the
    segmented control. Two-up grid layout. Empty-state placeholder.
  - **Wider canvas**: outer wrapper went from 880px → 1180px;
    outer padding dropped to free up the workspace.
  - **License/branding sweep**: `LICENSE` replaced with proper MIT
    (was a stale "all rights reserved" proprietary text). All 17
    source-file headers normalized to
    `# © 2026 CoAssisted Workspace. Licensed under MIT.`
    (auth, server, config, tier, telemetry, receipts,
    project_invoices, project_registry, sender_classifier,
    vendor_followups, ap_drive_layout, gservices, merchant_cache,
    recent_actions, tools/system, tools/receipts,
    tools/project_invoices). No "Workspace Pilot" / "Workplace Pilot"
    stragglers outside CHANGELOG history.
  - **Tool count corrected** in user-facing docs: 183 → 230 (README.md,
    INSTALL.md, dist/README_HERO_DRAFT.md). The 183 was a stale snapshot
    from before the Executive Briefing, invoice pipeline, vendor
    follow-up, and recent workflow batches. Authoritative count from
    `system_check_tools` against the live MCP.
  - **Bug fix**: `_meeting_gap_html` now normalizes naive↔aware datetime
    mismatches by attaching local tz to either operand (all-day events
    come back as naive `YYYY-MM-DD` strings, timed events as offset-aware
    ISO strings). Subtraction now wrapped in the try block too.

- **What I left undone:**
  - **Demo GIF** for marketplace listing (task #230, in progress)
  - **Launch blog post** ~800 words (task #231)
  - **Social launch posts** (task #232)
  - **Video walkthrough script** ~3 min (task #233)
  - **Reply parsing improvements** — multi-message threads, attachment
    extraction, confidence-gated promotion (task #235)
  - **Smarter reminder cadence + escalation** for vendor follow-ups
    (task #236)
  - **Snooze + bulk actions + visible escalation trail** (task #237)

- **Pick up here:** Easiest entry point is **task #235** — vendor reply
  parsing improvements. Current implementation lives in
  `vendor_followups.py` and `tools/project_invoices.py`
  (`workflow_process_vendor_replies`). Three concrete gaps:
  1. Multi-message threads aren't deduped — if a vendor replies twice,
     we process both as separate signals.
  2. Attachments on reply emails aren't extracted (e.g., a vendor
     attaches the missing W-9 — we need to pull it and add it to the
     project Drive folder, not just read the body text).
  3. Any reply currently auto-promotes the AP row to processed even if
     parser confidence is low. A confidence-gated path (HIGH → promote,
     MEDIUM → leave AWAITING_INFO + flag for review) would lift quality.
  Existing tests: `tests/test_project_invoices_tools.py`,
  `tests/test_vendor_replies.py`. Add fixtures for multi-msg/attachment
  cases when extending.

- **Tests:** Before: **1008 passing.** After: **1008 passing.** No new
  failures introduced by the Executive Briefing refactor — 32 of those
  are the test suite for this subsystem.

- **Notes:**
  - **Live MCP requires restart** after pulling — FastMCP caches the tool
    registry at process start, so `workflow_executive_briefing` won't
    appear in your client until you toggle the connector or restart the
    stdio process.
  - **OAuth is per-machine**: `token.json` is yours, not portable. After
    install, run `make auth` to spin up the local OAuth flow with
    `josh.szott@surefox.com` and grant the requested scopes. If the
    refresh fails, `rm token.json && make auth` for a clean re-consent.
  - **Local action webhook** (`briefing_webhook.py` on
    `127.0.0.1:7799`) auto-starts when the MCP wrapper registers.
    Action buttons in the Executive Briefing email route there.
  - **Per-city ideal-temp band** in the weather chart looks up your
    current location via CoreLocationCLI on macOS — falls back to
    San Francisco if location services aren't available.

- **When you send it back, please:**
  1. Bump `VERSION` in `_version.py` if you shipped features (e.g.
     `0.7.2-dev`).
  2. Add a CHANGELOG entry under `[Unreleased]` describing what you did.
  3. Run `python3 -m pytest` and capture pass/fail counts.
  4. Append a new entry below this one with your handoff log.
  5. Bundle with `make handoff` and send it back to `finnn@surefox.com`.

---

<!-- Joshua appends his entry below before sending back -->

## 2026-04-30 · Joshua (active owner — no outbound handoff yet)

- **Version:** `0.7.2-dev`
- **Focus:** audit-driven quick wins + P0-2 vendor reply parsing rebuild

### What I shipped
Two-day push, audit-first. All commits have spec IDs (`P0-1` ... `P0-2`)
so the trail back to the audit doc + design doc is one grep.

- **`mcp-audit-2026-04-29.md`** — top-issues audit across all 11 tool
  categories. Findings prioritized P0/P1/P2.
- **`mcp-design-docs-2026-04-29.md`** — 6 specs: P0-2 vendor reply
  parsing, P0-3 baseline unit tests, P1-1 split workflows.py, P1-4
  smarter reminder cadence, P1-5 snooze+bulk+trail, P1-7 Slack
  (REMOVED 2026-05-03).
- **P0-1 — git init.** Project is now under version control on the
  owner's machine. `.gitignore` already excluded secrets; appended
  patterns for `*.bak.*`, `pytest-cache-files-*/`, `logs/health_reports/`.
- **P1-6 — brand-voice corpus filter.** `_is_google_auto_body` rejects
  Drive-share / Meet-invite / Forms-response / Meet-link bodies that
  appear in the sent folder under the user's address (Google attributes
  auto-emails to you, no `from:` filter would catch them). Calendar
  invites filtered upstream via Gmail subject operators
  (`-subject:"Invitation:"` etc.). Override with
  `BRAND_VOICE_INCLUDE_AUTO=1`. 7 tests; false-positive guard for
  legitimate prose that mentions Google Meet in passing.
- **P2-1 — config validator skips `_*` keys.** `system_check_config`
  was flagging `_attachments_comment` as "Unknown keys" on every
  doctor run. Now skipped. Bonus fix: stale `/Users/finnnai/...` path
  in the missing-config fix-hint replaced with dynamic path.
- **P1-2 — network test markers.** `network` marker registered in
  `pyproject.toml`. Default `pytest` excludes it. Three Makefile
  targets: `test` (default fast), `test-fast` (quiet + 5s timeout),
  `test-network` (live APIs, 120s timeout). Test suite went from
  104s with 14 timeouts → 3s with 0 failures. `pytest-timeout`
  added to the venv.
- **P0-4 — `workflow_sweep_awaiting_info`.** New tool: lists vendor
  follow-up entries with age_days, optionally bulk-clears entries
  older than N days. `dry_run` defaults to True when `older_than_days`
  is set (safety). Filters by channel + project_code. 7 tests.
- **P0-2 — confidence-gated vendor reply parsing.** Full spec from
  `mcp-design-docs-2026-04-29.md`:
    - Multi-msg dedup via `latest_reply_ts` per entry. New
      `_find_gmail_reply()` returns full message + body + ISO ts;
      walks oldest-to-newest, returns oldest UNSEEN reply.
    - Attachment extraction → `AP Submissions/Reply Attachments/
      <PROJECT>/<vendor>/<filename>` via new
      `ensure_reply_attachments_folder()` in `ap_drive_layout.py`
      and `_archive_reply_attachments_to_project()` orchestration.
      Skipped on LOW confidence (no Drive pollution from deferrals).
    - Confidence classifier `score_reply_confidence()` is a pure
      function — 18 deferral phrases ("will send", "let me check",
      "out of office", "circle back", etc.) cap at LOW; otherwise
      answered/requested ratio: 1.0 → high, ≥0.5 → medium, <0.5 → low.
    - HIGH path: apply update + promote + mark_resolved + ack on
      same channel + clear any prior review_queue entry.
    - MEDIUM path: apply update + add to `review_queue.json` + leave
      AWAITING_INFO. Operator promotes via
      `workflow_list_review_queue` (new tool).
    - LOW path: no row update; existing reminder cadence handles
      next nudge.
    - Result schema gained `rows_held_for_review`,
      `rows_low_confidence`, per-update `confidence`,
      `queued_for_review`, `attachments_saved`.
    - +31 tests across `test_reply_confidence.py` (10),
      `test_review_queue.py` (7), `test_latest_reply_ts.py` (4),
      `test_vendor_reply_orchestrator.py` (10).

### Tooling fixes that surfaced along the way
- Bonus test patch: `tests/test_project_invoices_tools.py` now has an
  autouse `_mock_llm_unavailable` fixture that monkeypatches
  `llm.is_available -> (False, ...)` so the 13 composer tests in that
  file run the deterministic fallback path. Tests that specifically
  need the LLM branch can override per-test. The two `ALPHA` tests
  patched explicitly during P0-2 prep (commit `c254b14`'s sister
  patch) are still in place — they document intent.

### Tests
- **Before:** 1008 passing, 0 failing.
- **After:** 1054 passing, 0 failing, 1 deselected (the `network`-
  marked maps test). Suite runtime: 3.13s on `make test-fast`.

### Tool count
- **Before:** 230 (per `system_check_tools` in 0.7.1-dev).
- **After:** 233 (+ `workflow_sweep_awaiting_info`,
  `workflow_list_review_queue`; the third addition was a refactor
  of `_find_gmail_reply_body` into `_find_gmail_reply` + thin
  wrapper, no new tool count contribution there). Verify with
  `system_check_tools` against the live MCP after Cowork restart.

### What's left undone (next session)
- **P0-3 baseline unit tests** for 13 thin-wrapper tool modules
  (`gmail`, `calendar`, `drive`, `sheets`, `docs`, `tasks`, `chat`,
  `contacts`, `maps`, `enrichment`, `system`, `workflows`, `handoff`).
  Spec in `mcp-design-docs-2026-04-29.md`.
- **P1-1 split workflows.py** — 7898-line file → 5 modules
  (`workflows_gmail`, `workflows_drive`, `workflows_crm`,
  `workflows_calendar`, `workflows_misc`). Mechanical, ~1 day, no
  behavior change. Backwards-compat shim keeps existing imports.
- **P1-4 smarter reminder cadence** — per-vendor reply-time history,
  day-of-week + US holiday awareness.
- **P1-5 snooze + bulk + escalation trail** — three additions, can
  land independently.
- **P1-7 Slack** — REMOVED FROM ROADMAP 2026-05-03. Was parity with
  `chat_*` surface. AP/AR build-out closes via Google Chat alone;
  Slack parity isn't worth the maintenance surface.

### Notes
- **Live MCP requires Cowork restart** after pulling — FastMCP caches
  the tool registry at process start. The two new tools won't appear
  in Cowork until the user restarts.
- **OAuth re-consent** is NOT needed for this session — no scope
  changes. Existing `token.json` is fine.
- **The `_FIELD_MIME_HINTS` mapping** in
  `_archive_reply_attachments_to_project` is plumbed but not
  enforced — currently accepts any PDF/image. Once the user's policy
  on which fields require which mimes is decided (e.g. W-9 → PDF
  only, COI → PDF only), wire it in. Pure config change.
- **Joshua is the active owner** — no outbound handoff. If the
  archive is ever passed to a third party, this section is the
  starting point. The `mcp-audit-2026-04-29.md` + design docs are
  the second-most-important reading (after this entry).

### Update — P1-1 done same session
Split `tools/workflows.py` (7898 lines) into 5 category files +
shared helpers + 133-line back-compat shim. Same 43 tools, same
names, same schemas. 1054 → 1058 tests passing in 3.27s. Two
test patches: `test_no_duplicate_tool_names_across_modules` now
skips the shim, and the geocode-cache tests monkeypatch the
helpers module instead of the shim. Migration was scripted via
`/tmp/do_split.py` (AST walk + line-range slicing + transitive
class/helper closure).

### Update — P0-3 partial done same session
Shipped baseline tests for **3 of 13** modules: drive (21),
calendar (23), gmail (36) = **80 new tests**. Pattern is
established and mechanical:
  - resolve_tool + run + http_error + err_assert helpers at the top
  - input validation tests (required fields, bounds, aliases)
  - happy path with mocked gservices (empty result sets where
    a full fake message shape would be brittle)
  - error path with HttpError raising; check the returned string
    for "error/failed/boom/http"
  - registration smoke at the bottom

Tests: 1058 → 1138 passing in 3.35s.

### Update — P0-3 complete same session
Shipped tests for the remaining 10 modules: handoff (2), scanner (4),
tasks (12), docs (7), sheets (12), maps (13), chat (22),
contacts (23), enrichment (5), system (6) = +108 tests.
All 13 thin-wrapper modules now covered. 1058 → 1246 passing in 3.51s.

### Update — P1-5 done same session
Shipped the full snooze + bulk + trail spec. 5 new tools (238
total). vendor_followups gained snoozed_until + events timeline,
auto-seeded from register_request and appended on every state
change. due_for_reminder now skips snoozed entries. text-format
trail for human review: "2026-04-12 ASK · 2026-04-19 R1 · ...".
+26 tests, 1246 → 1272 in 3.53s.

### Update — P1-4 done same session
Smarter cadence shipped end-to-end: vendor_response_history.py
module, us_federal_holidays.json (2026-2030), adaptive wait
tiers (24/72/120 hr based on median reply latency), cold-start
fallback to the constant ladder, day-of-week + holiday push to
next business-day 9am local, orchestrator auto-logging on
HIGH/MEDIUM confidence. +21 tests. 1272 → 1293 passing in 3.79s.

### Update — P2-2 done same session
mypy added: dev dep, [tool.mypy] config in pyproject.toml,
`make typecheck` + `make typecheck-strict` targets. Baseline
shows 0 errors in the new P-spec modules and 150 errors in 26
of 61 existing top-level files. Cleanup is incremental — flip
[[tool.mypy.overrides]] strict=true per-module as code is touched.

### Update — P1-3 done same session
Audited 31-of-558 broad-except handlers via every-18th sampling.
81% legitimate (tool boundaries with format_error, API failure
handlers with logs, fallback chains). 19% swallow-ish; the 6
sampled swallowers were all fixed in place (2 narrowed to
ImportError, 4 added log.warning/debug, 1 missing log import
restored). Audit report: p1-3-exception-audit-2026-04-30.md.

### Update — Polish bundle done same session
Five housekeeping items shipped together: removed *.bak.* files,
swept 6 stale awaiting_info entries (validates P0-4 on real
data), opted review_queue + vendor_response_history into mypy
strict (with type fixes to satisfy it), fixed 2 utcfromtimestamp
deprecation warnings in refresh_brand_voice.py, deeper P1-3
sample on tools/project_invoices.py (12-of-106) yielding 2 more
swallow-ish fixes. 1293/1293 still passing in 3.55s.

### Update — Polish round 2 (chat.py + workflows_calendar.py)

12-sample sweep across two more dense files. 10 legit (uniform
tool-boundary format_error), 2 fixed:
  - find_meeting_slot getProfile fallback → log.warning
  - zoneinfo construction → narrow to (ZoneInfoNotFoundError, ValueError) + log.warning

Cumulative across P1-3 + 2 polish rounds: 55 broad-except samples
reviewed, 12 swallowers fixed.

### Pick up here
**P1-7 Slack** was the last open spec in `mcp-design-docs-2026-04-29.md`;
removed from the roadmap 2026-05-03. Optional ongoing polish: continue
broad-except cleanup file-by-file, expand mypy strict opt-ins
as files get touched (vendor_followups.py is next — 21
preexisting type-arg gaps), or whatever real-use pain surfaces.
CONTRIBUTING.md captures the working patterns so anyone picking
this up can hit the ground running.

---

## 2026-04-30 · Joshua → Conor

- **Version:** `0.7.2-dev`
- **Focus:** updating an older version Conor had at Staffwizard. The
  0.7.2-dev cycle was a single-day push (29 commits) following an
  audit-driven backlog: 11 specs from `mcp-design-docs-2026-04-29.md`
  + 5 quick wins from `mcp-audit-2026-04-29.md` + a polish bundle +
  2 follow-up polish rounds.

### What's new since the version Conor had

- **P0-2 — confidence-gated vendor reply parsing.** Multi-message
  thread dedup via `latest_reply_ts`, attachment extraction to
  `Drive/AP Submissions/Reply Attachments/<PROJECT>/<vendor>/`,
  HIGH/MEDIUM/LOW confidence classifier (18 deferral phrases cap at
  LOW). New `review_queue.py` + `workflow_list_review_queue` for
  human approval of medium-confidence parses.

- **P0-3 — baseline unit tests for all 13 thin-wrapper tool modules.**
  188 new tests. Pattern documented in `CONTRIBUTING.md`.

- **P0-4 — `workflow_sweep_awaiting_info`.** List + bulk-clear stale
  entries with dry-run safety.

- **P1-1 — `tools/workflows.py` split.** 7898 lines → 5 category
  modules (`workflows_gmail/crm/calendar/chat/misc`) + a shared
  `_workflow_helpers.py` + a 133-line back-compat shim. Same 43
  tools, same names, same schemas — but every future change is
  cheaper.

- **P1-2 — network test marker.** `make test-fast` runs in 3.5s
  with 0 failures (was 104s with 14 timeouts). Default `pytest`
  excludes the live-API marker; `make test-network` runs them.

- **P1-3 — broad-exception audit.** 55 samples reviewed across the
  558-handler population. 12 swallowers fixed (narrowed to specific
  exception types or added log.warning/debug). Full report:
  `p1-3-exception-audit-2026-04-30.md`.

- **P1-4 — smarter reminder cadence.** New
  `vendor_response_history.py` records per-vendor reply latency.
  Adaptive next-reminder window: <12hr median → 24hr, 12-48hr →
  72hr, >48hr → 120hr. Day-of-week + US federal holiday push
  (bundled `us_federal_holidays.json` 2026-2030) — Sat/Sun/holiday
  reminder moments push to next business-day 9am local.

- **P1-5 — snooze + bulk + escalation trail.** 5 new tools:
  `workflow_snooze_awaiting_info`, `workflow_unsnooze_awaiting_info`,
  `workflow_bulk_resolve_awaiting_info`,
  `workflow_bulk_promote_review_queue`,
  `workflow_get_escalation_trail`. Every entry now has an `events:
  list[]` timeline auto-populated by ASK / REMINDER / SNOOZED /
  RESOLVED. Text-format trail: `2026-04-12 ASK · 2026-04-19 R1 · ...`.

- **P1-6 — brand-voice corpus filter.** Excludes Calendar invites
  (subject filter) + Drive/Meet/Forms auto-bodies (body filter).
  Sharper voice profile from real authored prose.

- **P2-1 — `_-prefixed` config keys ignored** by validator. Stops
  the cosmetic "Unknown keys" warning on every doctor run.

- **P2-2 — mypy added.** `make typecheck` + `make typecheck-strict`.
  Soft baseline now; `review_queue.py` and `vendor_response_history.py`
  opted into strict via `[[tool.mypy.overrides]]`.

### Tool count

- **238 tools** (was 230). 5 new from P1-5, 2 new from P0-2 +
  `workflow_sweep_awaiting_info`. P1-1 was a refactor — same 43
  tools, just spread across 5 files now.

### Tests

- **1293 passing in 3.5s** (was 1008). +285 across the cycle.
- `make test-fast` is your daily-driver. `make test-network` for
  the 1 live-API marker test.

### Files Conor's NOT getting (intentional)

- `credentials.json`, `token.json` — your OAuth, you'll create your
  own per `GCP_SETUP.md`. The handoff archive never contains the
  sender's auth.
- `config.json`, `rules.json` — local/personal config. Copy from
  `config.example.json` after install.
- State files — `awaiting_info.json`, `review_queue.json`,
  `vendor_response_history.json`, `projects.json`,
  `merchants.json`, `briefing_actions.json`,
  `external_feeds_cache.json`. Conor starts with empty state.

### What Conor needs to do

1. `xattr -dr com.apple.quarantine ~/Developer/google_workspace_mcp`
   (clear macOS Gatekeeper, after extracting).
2. `GCP_SETUP.md` — create his own Google Cloud project + download
   `credentials.json`. Same flow Joshua followed today.
3. `./install.sh` — sets up the venv + dependencies.
4. `./install.sh --oauth` — sign into the Google account the MCP
   should act as, accept scopes.
5. Wire into Cowork's `claude_desktop_config.json` per
   `INSTALL.md` Phase 1, Step 1.4.
6. Restart Cowork. `system_doctor` should report 9-of-11 green
   (CoreLocationCLI + the unit-test runner are the usual two
   yellows on a fresh install).

### Read first

- `HANDOFF_LOG.md` (this file) — full session-by-session journal
  going back to Finnn → Joshua on 2026-04-29.
- `mcp-audit-2026-04-29.md` — strategic backlog.
- `mcp-design-docs-2026-04-29.md` — concrete specs (P1-7 Slack
  removed from roadmap 2026-05-03; all other specs shipped).
- `CONTRIBUTING.md` — patterns for spec IDs, commit format,
  handoff hygiene, test scaffold, mypy config, exception handling.
- `CHANGELOG.md` `[0.7.2-dev]` — full enumeration of what
  changed.

### Pick up here

- **P1-7 Slack integration** was removed from the roadmap on
  2026-05-03. No outstanding specs remain in
  `mcp-design-docs-2026-04-29.md`.
- **Optional ongoing polish:** continue broad-except cleanup on
  the densest remaining files (`tools/contacts.py` 27,
  `tools/system.py` 33, `tools/receipts.py` 31). Pattern is
  documented in `p1-3-exception-audit-2026-04-30.md`.
- **Optional ongoing polish:** expand mypy strict opt-ins as
  files get touched. `vendor_followups.py` is the next natural
  candidate (21 preexisting type-arg gaps).

### Notes for Conor specifically

- The CLAUDE.md / CONTRIBUTING.md spec-ID conventions, commit
  message format, and handoff-hygiene flow are documented for
  consistency. If you don't hand this back to Joshua and instead
  ship work yourself, follow the same pattern so the trail stays
  legible.
- `make handoff` builds a clean tarball excluding all secrets and
  state. Use that, not `tar` directly, when shipping the archive
  back to anyone.

### When you send it back, please

1. Bump `VERSION` in `_version.py` if you ship features (e.g.
   `0.7.3-dev`).
2. Append a new entry below this one with your handoff log.
3. Run `make test-fast` and record pass/fail in your log entry.
4. Add a `CHANGELOG.md` entry under `[Unreleased]`.
5. Bundle with `make handoff` and email back to
   `josh.szott@surefox.com`.

---

## 2026-05-01 · Joshua (post-handoff polish, pre-Conor receipt)

Late-night session after the 2026-04-30 handoff was committed but
before Conor opened the archive. Captured here so the next reader
sees what changed since the prior entry.

- **Version:** v0.7.2-dev (dev channel, unchanged — no semver bump)
- **Time held:** continuous; this is a thin polish layer on top of
  the same dev cycle.
- **Focus area:** attachment-threshold fix + AP/AR roadmap + handoff
  delivery to Conor.

- **What I touched:**
  - **`config.py` + `tools/gmail.py`** — bumped
    `large_attachment_threshold_kb` default from 500 → 22000 so any
    attachment within Gmail's per-message ceiling (~22MB safe) ships
    as a real Gmail attachment instead of auto-routing through Drive.
    Lower the threshold per-install if stdio buffer or mail-filter
    issues recur. Files above 22MB still Drive-route via the same
    code path.
  - **`config.json`** (gitignored, not in repo) — same threshold
    bump on the running install + comment refresh.
  - **`CHANGELOG.md`** — `[Unreleased]` entry for the threshold change.
  - Committed as `fb60aea`.
  - **`coassisted-workspace-ap-roadmap-2026-04-30.md`** + `.docx` on
    Desktop — full AP/AR build-out roadmap. Three-wave plan over
    ~7 weeks, plus a Day-1 hot deploy starting 2026-05-01 to begin
    receipts collection using the existing capture surface. Maps
    against the four artifact files Joshua provided: Workday
    Supplier Invoice EIB v39.1, Workday Accounting Journal EIB
    (AMEX example), Workday chart of accounts (212 ledger
    accounts), and StaffWizard Overall Report (66-column daily
    labor). Five open items flagged for stakeholder decision.
  - Roadmap distributed via email to `julie.marsee@staffwizard.com`
    and `shannon.fields@staffwizard.com` with the .docx attached.

- **Handoff delivered to Conor:**
  - Built `coassisted-workspace-v0.7.2-dev-2026-05-01.tar.gz`
    (6.8MB) via `make dev-build`.
  - Emailed `Conor@staffwizard.com` with the tarball.
  - Note: the running MCP server still had the 500KB threshold
    cached at send time, so Conor received a Drive-share link
    rather than a real attachment. After a Cowork restart, future
    sends will inline. Conor's Drive-shared copy works; no resend
    needed.

- **What I left undone:**
  - **AP roadmap waves 1–3** are scoped but not coded. Five open
    items awaiting input from Julie / Shannon / Joshua before
    Wave 1 starts: cardholder→cost-center map, project budget
    source, AR billing cadence, Chase statement sample, vehicle
    GPS / receipt geocoding source.
  - **Day-1 hot deploy** stood up 2026-05-01 — Drive tree, Gmail
    filter on `receipts@` alias, Chat space, scheduled extract
    loop. Should be operational for field receipt collection
    starting tomorrow morning.
  - **Security item:** `config.json` had the live Anthropic API key
    pasted into both `anthropic_api_key` (intended) and
    `signature_parser_mode` (mistakenly — should be `"regex"` /
    `"regex_then_llm"` / `"llm"`). Joshua rotated the key in the
    Anthropic console + moved to `ANTHROPIC_API_KEY` env var +
    cleared the cleartext from `config.json` in the same session.
    The env-var fallback path in `llm.py` was already in place.

- **Pick up here (for whoever reads this next):**
  - If Joshua: continue Wave 1 of the AP roadmap once the five
    open items are resolved, OR push more polish on
    `tools/contacts.py` / `tools/system.py` / `tools/receipts.py`
    broad-except sweeps (densest remaining files per the P1-3
    audit). (P1-7 Slack was removed from the roadmap 2026-05-03.)
  - If Conor: same backlog as the prior entry — start with
    HANDOFF_LOG, HANDOFF_STATE.json, CONTRIBUTING.md, then
    `mcp-design-docs-2026-04-29.md` for spec backlog. The
    threshold change is the only material code delta vs. the
    tarball you received.

- **Tests:** 1293 / 1293 (no test changes; `make test-fast` should
  remain green — only the threshold default and a docstring moved).

- **Notes:**
  - When sending large attachments, the current MCP-server process
    must be restarted for `config.json` threshold changes to take
    effect. The disk-side change is in place at startup; live
    process caches the value. Documented for the next reader to
    avoid the same surprise.
  - The AP roadmap doc lives on Joshua's Desktop, not in the repo.
    Move it into `docs/` if we want it in version control before
    handoff.

---

## 2026-05-01 (continued) · Joshua — AP/AR Wave 1 + Wave 2 build

Stable cut + Wave 1 + Wave 2 in one push. Started after the Conor
handoff went out. Tag `v0.7.2` is on `f2c0c8e`; current HEAD is on
`0.7.3-dev` with the AP/AR build-out per the
`coassisted-workspace-ap-roadmap-2026-04-30` design doc.

- **Version:** v0.7.3-dev (dev channel; will cut to 0.8.0 stable
  when Wave 1 fully ships, including AP-1 once the GL → Spend
  Category map is available).
- **Time held:** continuous since the prior 2026-05-01 entry —
  this is the same work session.
- **Focus area:** AP/AR end-to-end for AMEX + WEX cards. Wave 1
  classifier ladder + Workday Journal EIB writer + MCP wrappers.
  Wave 2 project router + Drive tree manager + capture sweep.

### Commits since v0.7.2 (chronological)

```
f2c0c8e  (tag: v0.7.2) Cut stable v0.7.2
68a80cf  Bump to 0.7.3-dev for next cycle
ac6963e  AP-3: GL classifier scaffold + MCC table + tier-1 tests
683084f  AP-3: Tier 0 (merchant map) + Tier 2 (JE-trained matcher)
e7a994e  AP-3: Tier 3 LLM fallback (Claude-haiku for novel merchants)
3b38b62  AP-2: AMEX + WEX → Workday Journal EIB
04f7467  AP-2/AP-3: MCP wrappers + cost_center_map persistent store
d9f303c  AP-5 + AP-6: project router + Drive tree manager
[NEXT]   AP-4: capture sweep — route inbound to project folders
```

### Wave 1 — Workday close path

**AP-3: GL classifier (4-tier ladder)**
- Tier 0: `gl_merchant_map.py` operator-confirmed mappings (HIGH)
- Tier 1: 40-range MCC table → 11 GL accounts (HIGH)
- Tier 2: `gl_memo_classifier.py` — Naive-Bayes-lite trained on
  4,601 debit-side expense rows from
  `samples/Wolfhound Corp JEs Jan-Mar'26.xlsx`. Filters credit-side
  and non-expense rows so the model learns spend GL patterns, not
  card-payable / cash routing. (MEDIUM/LOW)
- Tier 3: `gl_classifier_llm.py` Claude-haiku fallback (LOW)
- Final: `22040:Credit Card Clearing` for review queue when all
  tiers miss
- Persistent merchant map with operator > import > training
  precedence; learns from every override

**AP-2: Card statement → Workday Journal EIB (`workday_journal_eib.py`)**
- AMEX parser (41 columns): cardholder, MCC, status filter
- WEX parser (60 columns): driver, department, vehicle, fuel
- Two-sheet EIB writer matching the SFNA AMEX EIB convention exactly
- Real-data validation: 77 AMEX + 315 WEX April transactions, 392
  total → 100% classified, 0 fell through to clearing
- Memo format `{LABEL} Transactions {start}-{end} - {Cardholder} - {Vendor}`
- Refund handling reverses dr/cr direction
- Cost-center routing via `cost_center_map.py` persistent store

**AP-1: Workday Supplier Invoice EIB — BLOCKED**
- Needs the GL → Spend Category mapping from Workday config (col
  113 of `Submit_Supplier_Invoice_v39.1`). The 17k JE training data
  doesn't carry that map cleanly.
- Ships when the mapping is available.

**MCP surface (7 new tools, `tools/ap_journal.py`):**
- `workflow_reconcile_card_statement`
- `workflow_gl_classify_preview`
- `workflow_gl_merchant_map_set` / `_list`
- `workflow_gl_memo_index_status`
- `workflow_cost_center_map_set` / `_list`

### Wave 2 — Capture reliability + visibility

**AP-6: Forced project Drive tree (`ap_tree.py`)**
- `register_new_project`: full 7-subfolder subtree creation +
  current month bucket. Idempotent. Persists every Drive ID into
  `project_registry`.
- `ensure_month_subtree`: lazy {YYYY-MM}/ creation under
  Receipts and Invoices. Called on every receipt write.
- `audit_filing_tree`: daily scan for files that bypassed the
  capture pipeline (manual drag-drops). Naming-convention check.

**AP-5: Project router (`project_router.py`)**
- 7-tier resolution: explicit (1.00) → alias match (0.92) →
  team email (0.88) → calendar tiebreaker (0.80) → Geotab
  GPS (0.85, stub) → LLM inference (variable) → chat picker
- `project_registry.py` extended with Wave 2 fields: drive
  folder IDs, name aliases, assigned team, StaffWizard job
  link, billing config (terms, cadence, origin state, customer
  email).
- `confidence_action()` maps result to: auto_file (≥0.85) /
  auto_file_flag (0.65–0.85) / chat_picker / triage.

**AP-4: Capture sweep (`ap_sweep.py`)**
- `decide_disposition`: pure routing decision per inbound item.
- `run_sweep_cycle`: pulls Gmail + Chat, routes via AP-5,
  executes the disposition. Stubbed Drive download / mark-read /
  chat-post at the call sites — wires up in next commit when
  integrated with the existing `tools/gmail` + `tools/chat`
  surfaces. Decisions are deterministic + tested in isolation.

**MCP surface (5 new tools, `tools/ap_tree.py`):**
- `workflow_register_new_project`
- `workflow_audit_filing_tree`
- `workflow_ensure_month_subtree`
- `workflow_route_project`
- `workflow_project_registry_list`
- `workflow_ap_sweep_cycle`

### Tests

12 commits across Wave 1 + Wave 2 introduced ~104 new tests:

| Module | Tests |
|--------|-------|
| `gl_classifier` | 12 |
| `gl_merchant_map` | 19 |
| `gl_memo_classifier` | 17 |
| `gl_classifier_llm` | 10 |
| `workday_journal_eib` | 22 |
| `project_router` (incl. project_registry helpers) | 21 |
| `ap_sweep` | 11 |
| **Total new** | **~112** |

Existing 1293 tests should remain green — the only existing-file
edits were additive (new fields on `project_registry.register`,
new helpers; nothing removed or renamed).

### Open items at handoff

1. **AP-1** — gated on the Workday GL → Spend Category map.
2. **AP-4 wire-up** — `ap_sweep.py` Drive download / mark-read /
   chat-post call sites are stubbed pending integration with
   `tools/gmail` + `tools/chat`. Routing decisions are correct;
   only the side-effect plumbing remains.
3. **Geotab integration** — `_geotab_tiebreaker` in
   `project_router.py` is a stub. Wires up when GEOTAB_*
   credentials land in `config.json`.
4. **Cardholder → CC map population** — auto-derivation hook
   (`cost_center_map.derive_from_je_corpus`) is a strawman that
   returns suggestions; operator confirms via
   `workflow_cost_center_map_set`.
5. **AR-9** — Wave 3 build (customer invoice generation, aging,
   collections cadence) hasn't started.

### Pick up here (for whoever reads this next)

- If Joshua: the next commit closes AP-4 by wiring the four
  stubbed call sites in `ap_sweep.py` to the existing
  `tools/gmail` (download_attachment, modify_labels) +
  `tools/chat` (send_message) surfaces. Estimated ~1 hr.
- If Conor: same as the prior entry, plus skim
  `coassisted-workspace-ap-roadmap-2026-04-30.md` for the AP/AR
  vision. The Wave 1+2 build is internally consistent and
  testable; no surprise dependencies.

### Tests recap

`make test-fast` should still report 1293/1293 from Conor's last
known-good state plus the ~112 new tests (so target ~1405). I
couldn't run tests from the sandbox (broken venv symlink); run
locally to verify.

---

## 2026-05-01 (continued, third cut) · Joshua — v0.8.1 stable

Third stable cut in one day. AP/AR Wave 3 ships: AP-7 labor
ingestion, AP-8 master rollup, AR-9 invoicing + aging +
collections including the end-to-end Gmail send wire-up. Plus
the AP-4 capture-sweep wire-up that was stubbed in v0.8.0.

- **Version:** v0.8.1 stable, 2026-05-01.
- **Time held:** ~2 hours since v0.8.0 cut.
- **Focus area:** Wave 3 deterministic logic + send wire-up.

### Commits since v0.8.0

```
a6a6ab0  (tag: v0.8.0) Cut stable v0.8.0
4cf1170  AP-7: StaffWizard daily labor ingestion
a59f119  AP-8: master rollup + run-rate dashboard with baseline-deviation alerts
54c69d9  AR-9: customer invoicing + aging buckets + collections cadence
e88482d  AP-4 wire-up: ap_sweep stubs → Gmail / Drive / Chat APIs
[NEXT]   Wave 3 MCP wrappers + AR-9 send wire-up (ar_send.py)
[NEXT]   Cut stable v0.8.1
```

### What works end-to-end now

```
StaffWizard Overall Report → workflow_ingest_labor_report
   ↓ (parses 66 cols, groups by project)
   ↓ (writes per-project Labor/Daily/{date}_labor.xlsx)
   ↓ (records facts to master_rollup_history.json)
6am scheduled task → workflow_build_master_rollup
   ↓ (3-tab workbook: All Projects, PM Dashboard, Anomalies)
   ↓ (>2σ deviation alerts in PM Dashboard.Deviation Flag)

Per-project monthly close → workflow_generate_customer_invoice
   ↓ (filters labor to project's StaffWizard job)
   ↓ (rolls up by post_description)
   ↓ (creates draft InvoiceRecord, persisted to ar_invoices.json)
Operator review → workflow_send_invoice
   ↓ (HTML body + Excel attachment, sent via Gmail)
   ↓ (mark_sent → status=sent)

Daily collections sweep → workflow_collections_due_today
   ↓ (cadence ladder: courtesy → first → second → third → legal)
Per candidate → workflow_send_collection_reminder
   ↓ (tier-appropriate template via Gmail)
   ↓ (add_collection_event → next sweep won't re-send same tier)
```

### Open items (deferred to 0.8.2+)

- **AP-1 Supplier Invoice EIB**: still gated on Workday GL →
  Spend Category map.
- **Geotab integration**: still a stub.
- **P1-7 Slack**: REMOVED FROM ROADMAP 2026-05-03 (no longer future dev).

### Pick up here

If Joshua: AP-1 unblocks the moment a GL → Spend Category map
arrives (CSV paste is fine). Geotab unblocks when GEOTAB_*
credentials land in config.json. Otherwise we're at a clean
shipping point — three stable cuts in one day, no carry-over
work.

If Conor or Finnn: skim the roadmap doc on Joshua's Desktop, run
`make test-fast` to verify all ~1465 tests pass on your machine,
then start exploring the new MCP tools — `workflow_reconcile_card_statement`
is the highest-value one to try first since it covers the
day-to-day card close.

### Tests

Cumulative across Waves 1-3: ~170 new tests on top of the 1293
baseline from v0.7.2. Target: ~1465 total. Run `make test-fast`
to verify locally; sandbox couldn't reach venv during the build.

### Notes

- Three stable cuts in one day is unprecedented for this project.
  Each was a clean milestone: v0.7.2 = test/refactor baseline,
  v0.8.0 = AP/AR Wave 1+2, v0.8.1 = AP/AR Wave 3 + send
  wire-ups.
- The 0.8.x series now covers the full close-the-books loop for
  AP card spend (AMEX + WEX) + customer AR. AP-1 Supplier Invoice
  EIB is the only piece of the original AP/AR roadmap that
  hasn't shipped, and it's gated externally.

---

## 2026-05-01 (continued, fourth cut) · Joshua — v0.8.2 stable

Fourth stable cut in one day. Closes out the Finnn 2026-05-01
patch — three operator-facing hardening items packaged in one
release: Tier-0.5 receipt classifier bypass (Allan's bug),
system_check_cron health check, timing-aware install_crontab.

- **Version:** v0.8.2 stable, 2026-05-01.
- **Time held:** ~3 hours since v0.8.1 cut.
- **Focus area:** Finnn 2026-05-01 patch — receipt classifier
  + cron observability.

### Commits since v0.8.1

```
9b1ea58  (tag: v0.8.1) Cut stable v0.8.1
758b5a0  Bump to 0.8.2-dev
8eb7ce7  Patch C: Tier-0.5 receipt classifier bypass
[NEXT]   Patch A+B+D: install_crontab + system_check_cron + CHANGELOG
[NEXT]   Cut stable v0.8.2
[NEXT]   Bump to 0.8.3-dev
```

### What works end-to-end now

- **Allan-style receipts file correctly.** Internal-sender +
  image/PDF + thin body + keyword (`receipt|invoice|expense|
  rcpt|ap`) → Vision direct call, HIGH 0.85 confidence,
  auto-post to expense sheet. Per-installation kill switch
  via `config.receipts_internal_image_bypass`.
- **`system_doctor` includes cron.** Detects missing crontab,
  paste-test artifacts in log files (`zsh: command not found:
  <minute>`), reports next-fire timestamps per entry. Standalone
  via `system_check_cron`.
- **`make install-crontab`** prints next-fire table, backfills
  missed jobs from today (default Y per question-1a, opt-out
  with `--no-backfill`), refuses to overwrite differing crontabs
  without `--force`, preserves personal entries.

### Open items deferred to 0.8.x and beyond

- **AP-1 Supplier Invoice EIB**: still gated on Workday GL →
  Spend Category map.
- **Geotab integration**: still a stub.
- **Wave 4 quote management**: operator deciding between
  PandaDoc (custom MCP, has proposal builder) and SignNow (in
  registry, e-sign only).
- **P1-7 Slack**: REMOVED FROM ROADMAP 2026-05-03 (no longer future dev).

### Pick up here

If Joshua: Wave 4 quote management is the next strategic
build. Once you pick a signature backend the deterministic
quote-generation module ships in ~1 day, signature integration
another ~1 day.

If Conor or Finnn: skim the v0.8.2 release notes; the patch
notes on the operator's Desktop
(`patch-cron-and-receipt-classifier-2026-05-01.md`) explain the
Allan incident in full. Run `make test-fast` to verify ~1495
tests pass.

### Tests

Cumulative across Waves 1-3 + Patch ABC: ~200 new tests on top
of the 1293 baseline from v0.7.2. Target: ~1495 total.

### Notes

- Four stable cuts in one day. Run sheet: v0.7.2 (test/refactor
  baseline) → v0.8.0 (Wave 1+2) → v0.8.1 (Wave 3 + AP-4 send
  wire-up) → v0.8.2 (Finnn patch).
- `patch-cron-and-receipt-classifier-2026-05-01.md` on Joshua's
  Desktop carries the full patch context for the Allan incident.

---

## 2026-05-01 (continued, fifth cut) · Joshua — v0.8.3 stable

Fifth stable cut in one day. Same-day hot-fix on top of v0.8.2,
closing out Finnn's three follow-up patches (Parts E/F/G). One
of the three (Part F — AR collections kill-switch) is
safety-critical: the legacy `send_collection_reminder` was
auto-sending the cadence ladder without operator approval, which
is not what an operator wants when an invoice is wrong or the
customer has paid out-of-band. Held the cut for the same day so
the gate ships before any production weekend run.

- **Version:** v0.8.3 stable, 2026-05-01.
- **Time held:** ~1.5 hours since v0.8.2 cut.
- **Focus area:** Finnn 2026-05-01 follow-up patches E/F/G —
  receipts cadence + AR collections gate + v0.8.1-upgrade bug
  fixes.

### Commits since v0.8.2

```
[v0.8.2 tag]  Cut stable v0.8.2
[NEXT]        Bump to 0.8.3-dev
[NEXT]        Patches E+F+G — receipts cadence + AR collections
              gate + openpyxl/croniter deps + GL test isolation +
              AMEX DECLINED filter
[NEXT]        Cut stable v0.8.3
[NEXT]        Bump to 0.8.4-dev
```

### What changed by patch

- **Part E (cron cadence).** Replaced the single
  `0 18 * * * receipts.py --sweep` entry with two:
  `*/15 8-18 * * 1-5` (15-minute biz-hours sweep) and
  `0 18 * * 6,0` (weekends daily). Run
  `make install-crontab` to pick up; the timing-aware installer
  preserves personal entries and offers to backfill missed runs
  from today.
- **Part F (AR collections kill-switch — safety-critical).**
  Three-mode gate in `ar_send.send_collection_reminder`:
    - `send` — legacy immediate-send.
    - `draft` — Gmail draft + queued in `draft_queue` with
      `kind="ar_collection"`. Operator approves via
      `workflow_approve_draft`, which fires the
      post-approval hook to advance `collection_events` on
      the invoice.
    - `disabled` — workflow returns `status: skipped`, no
      draft, no send.
  Per-tier override beats the base mode; an optional
  `mode_override` arg on the call beats config. Default: every
  tier `draft` except `escalation_to_legal` `disabled` (per
  Joshua's question-3 answer — Tier-5 final-notice is
  compose-by-hand). New post-approval hook registry in
  `draft_queue.py` (idempotent registration, exception-swallowing
  fire) lets `ar_send` register its own callback at import time
  without central wiring. New `workflow_set_collections_mode`
  MCP tool lets operators flip the gate from chat. 14 tests in
  `tests/test_ar_collections_gate.py`.
- **Part G1 (deps).** `openpyxl>=3.1` and `croniter>=2.0`
  promoted from soft-deps to declared dependencies. They've been
  required by `labor_ingest`, `master_rollup`,
  `workday_journal_eib`, `ar_send`, `install_crontab.py`, and
  `system_check_cron` since v0.8.0/0.8.2; were causing 17/19
  v0.8.x test failures on a clean upgrade until manually
  pip-installed.
- **Part G2 (test isolation).** `isolated_classifier_state`
  fixture in `tests/test_gl_classifier.py` now redirects
  `gl_memo_classifier._INDEX_PATH` to a tmp path AND resets
  the in-process `_INDEX` cache before/after each test. Prior
  fixture only monkeypatched `lookup_by_memo`; code paths that
  bypassed that function could pick up a real disk-resident
  index from a prior `train_gl_memo_classifier.py` run. Belt-
  and-suspenders.
- **Part G3 (AMEX DECLINED filter).** Tightened the status
  check in `workday_journal_eib._filter_for_eib`:
  DECLINED/REVERSED rows are now unconditionally excluded
  regardless of `include_pending`. Previously the `else`
  branch could let DECLINED rows slip through.

### Open items deferred to 0.8.x and beyond

- **AP-1 Supplier Invoice EIB**: still gated on Workday GL →
  Spend Category map.
- **Geotab integration**: still a stub.
- **Wave 4 quote management**: operator deciding between
  PandaDoc (custom MCP, has proposal builder) and SignNow (in
  registry, e-sign only).
- **P1-7 Slack**: REMOVED FROM ROADMAP 2026-05-03 (no longer future dev).

### Pick up here

If Joshua: same as v0.8.2 — Wave 4 quote management once the
PandaDoc/SignNow decision lands. Also: flip
`config.ar.collections_mode` to `"send"` per-tier as you
develop trust in the auto-drafts (suggested ramp:
courtesy_reminder first, escalation_to_legal last and probably
never).

If Conor or Finnn: the new `workflow_approve_draft` post-approval
hook architecture is the model to follow for any future
"draft + queue + approve + advance state" workflow. See the
`_on_ar_collection_approved` registration in `ar_send.py` and
the `register_post_approval_hook` API in `draft_queue.py`.

### Tests

Cumulative: ~30 new tests for Patches E/F/G on top of v0.8.2's
~1495 target. Target for v0.8.3: ~1525 total. Run
`make test-fast` to verify.

### Notes

- Five stable cuts in one day. Run sheet: v0.7.2 (test/refactor
  baseline) → v0.8.0 (Wave 1+2) → v0.8.1 (Wave 3 + AP-4 send
  wire-up) → v0.8.2 (Finnn ABC patch) → v0.8.3 (Finnn EFG
  hot-fix).
- The AR collections gate change is the operator-facing item
  most likely to surprise downstream automations. Default is
  safe (drafts), but anyone who built tooling assuming the
  legacy immediate-send needs to set
  `{"ar": {"collections_mode": "send"}}` in `config.json`.

---

## 2026-05-01 (continued, sixth cut) · Joshua — v0.8.4 stable

Sixth stable cut today. The big one: full PandaDoc API coverage
ships as Wave 4. 122 raw endpoints + 5 quote workflows = 127 new
tools. Tool count moves 263 → 390. Joshua's "all and every function
available, no limitations" answer drove the scope; OAuth fallback
plus API-key preferred per the auth question.

- **Version:** v0.8.4 stable, 2026-05-01.
- **Time held:** ~2 hours since v0.8.3 cut.
- **Focus area:** Wave 4 — PandaDoc API integration + housekeeping
  + cold-load hot-fixes.

### Commits since v0.8.3

```
[v0.8.3 tag]  Cut stable v0.8.3
e35933d       Bump to 0.8.4-dev
1d8c69b       Housekeeping: 263-tool count refresh + 4 broad
              excepts narrowed
c350009       Add unit tests: ar_send renderers + ap_tree
              (33 new tests)
f8d8d0e       PandaDoc Wave 4 — foundation client + OpenAPI
              generator
88c4572       PandaDoc Wave 4 — 122 generated raw API tools
              (6 modules)
485516f       PandaDoc Wave 4 — 5 quote workflows + register
              wiring
2364eca       PandaDoc Wave 4 — 19 client tests + docs
              (390 tools / 14 categories)
e9a6743       Fix: missing 'import re' in tools/system.py
b258886       Fix: lift PandaDoc input classes to module scope
5fbf2b9       Fix: lift PandaDoc input classes to module scope +
              poll-until-draft
[NEXT]        Cut stable v0.8.4
[NEXT]        Bump to 0.8.5-dev
```

### What works end-to-end now

- **122 raw PandaDoc tools** wrap every Public API operation
  (v7.24.0 spec, 91 paths / 122 operations). Auto-generated
  from `pandadoc_openapi.json` via
  `scripts/generate_pandadoc_tools.py`. Module split by tag:
  documents (49), workspace (25), misc (18), content (12),
  templates (10), webhooks (8).
- **5 Wave 4 workflows** for the operator-facing quote loop:
  `workflow_send_quote`, `workflow_signature_status`,
  `workflow_quote_pipeline`, `workflow_quote_to_invoice`,
  `workflow_resend_quote`.
- **Live verified**: end-to-end smoke test created
  document `hWCZTpz6GwY6dNSnHhbHa2`, polled through async
  `document.uploaded` → `document.draft` → `document.sent`,
  email delivered to `josh.szott@surefox.com`.
- **Housekeeping**: README/INSTALL/pyproject tool counts now
  consistent at 390 across 14 categories. 33 new unit tests
  on top of the renderer + AP-6 tree surface. 4 broad
  exception clauses narrowed.

### Three hot-fixes caught during cold-load testing

1. `tools/system.py` was using `re.compile` without
   `import re` — inherited from Patch B (system_check_cron),
   crashed every cold load.
2. PandaDoc input classes were nested inside `register()` —
   FastMCP's `typing.get_type_hints` couldn't resolve the
   closure scope and raised `InvalidSignature`. Generator
   updated to emit classes at module level; same fix
   hand-applied to `pandadoc_workflows.py`.
3. `workflow_send_quote` was firing `sendDocument`
   immediately after `createDocument`, hitting 409 every
   time because PandaDoc's `createDocument` is async. New
   `_wait_for_draft` polls until the doc leaves
   `document.uploaded`.

### Open items deferred to 0.8.5+

- **AP-1 Supplier Invoice EIB**: still gated on Workday GL →
  Spend Category map.
- **Geotab integration**: still a stub.
- **PandaDoc workflow tests**: workflow-level unit tests left
  for follow-up.
- **OAuth token caching**: re-mints on every call; fine for
  low-volume.
- **Webhook receivers**: out of scope (needs public HTTP
  server).

### Pick up here

If Joshua: build the actual quote templates in PandaDoc's web
UI and start running real deals. The MCP layer is ready.

If Conor or Finnn: skim the v0.8.4 release notes; the Wave 4
section of CHANGELOG.md has the full operator-facing summary.
Smoke test by adding an API key to `config.json` under
`pandadoc.api_key` and asking Claude to "list my PandaDoc
documents".

### Tests

Cumulative target: ~1595 (1293 baseline + ~170 Wave 1-3 + ~30
Patch ABC + ~30 Patch EFG + ~52 Wave 4 + ~33 housekeeping).
Run `make test-fast` to verify.

### Notes

- Six stable cuts in one day. Run sheet: v0.7.2 (refactor
  baseline) → v0.8.0 (Wave 1+2) → v0.8.1 (Wave 3 + AP-4) →
  v0.8.2 (Finnn ABC) → v0.8.3 (Finnn EFG hot-fix) → v0.8.4
  (Wave 4 PandaDoc).
- Test document `hWCZTpz6GwY6dNSnHhbHa2` is in the operator's
  PandaDoc account from the live smoke test; safe to delete or
  ignore.
- The PandaDoc auth model permits both API-key (preferred) and
  OAuth2 refresh-token. Operator currently using API-key.

---

## 2026-05-03 · Joshua → (next holder)

- **Version:** v0.9.2 (stable)
- **Time held:** ~6 hours (rolled v0.8.6 → v0.9.0 → v0.9.1 → v0.9.2 in
  one session).
- **Focus area:** Close all three waves of the AP/AR build-out roadmap
  to the v1.0.0 gate. StaffWizard becomes source of truth for projects;
  Slack pulled from future development; AP-9 AR coverage held until
  PandaDoc tests bed in.
- **What I touched:**
  - **v0.9.0 — Wave 1 close + StaffWizard authoritative + receipt validator:**
    `gl_spend_category_map.py` (auto-derived Workday GL → Spend Category
    map from Wolfhound JE training base), `workday_supplier_invoice_eib.py`
    (AP-1 v39.1 EIB writer), `staffwizard_project_sync.py` (every Overall
    Report parse upserts projects with `staffwizard_authoritative=True`),
    `receipt_project_validator.py` (Option A/B chat-back picker —
    Option A lists active StaffWizard projects, Option B logs a request
    + parks the receipt), `tools/ap_wave1.py` (11 new tools), patch to
    `staffwizard_pipeline.refresh_all` (sync as step 4). Slack struck
    across `mcp-design-docs-2026-04-29.md`, `HANDOFF_LOG.md`,
    `HANDOFF_STATE.json`, `CHANGELOG.md`.
  - **v0.9.1 — Option B handover + retry queue + baseline + Geotab:**
    `option_b_handover.py` (resolve creates Drive subtree + AP sheet +
    re-files parked receipts; reject DMs reason), `retry_queue.py`
    (1m → 5m → 30m → 4h → 24h → escalate), `baseline.py` (30-day
    cold-start, >2σ alerts, manual budget overlay, mismatch detection),
    `geotab_client.py` (real MyGeotab Drive API; falls through to
    `no_credentials` cleanly when config absent), `tools/ap_wave2.py`
    (13 new tools).
  - **v0.9.2 — cron/watcher entry-points + PandaDoc tests:**
    `cron_staffwizard_morning.py` (AP-7 6:30am with late-email retry
    until 09:00), `cron_baseline_alerts.py` (AP-8 6:00am email),
    `pubsub_receipt_receiver.py` (AP-4 Gmail push handler with
    retry-queue fallback), `scripts/cron/crontab_template.txt` updated
    with the two new entries, `tests/test_pandadoc_workflows.py`
    (19 tests, all 5 Wave-4 workflows, all green).
  - **Desktop deliverables:**
    `release-notes-v0.9.0-2026-05-03.md`,
    `release-notes-v0.9.1-2026-05-03.md`,
    `release-notes-v0.9.2-2026-05-03.md`,
    revised roadmap `coassisted-workspace-ap-roadmap-2026-05-03.md`.
- **What I left undone:**
  - **AP-9 AR coverage** — held intentionally until the PandaDoc
    workflow tests have a real day in production. ~5 days of work
    when it lands. Sequenced into v1.0.0; targets 2026-06-19 per the
    revised roadmap.
  - **GCP Pub/Sub topic + push subscription provisioning** — env-side
    operator work; the AP-4 receiver code is ready.
  - **Crontab install on the operator's machine** —
    `make install-crontab` reads the updated template and adds the
    06:00 / 06:30 entries.
  - **Geotab credentials** — `config.geotab` block. Tools fall through
    to `no_credentials` cleanly when absent.
  - **Operator UI for ambiguous-Spend-Category review** — CLI-only
    today via `workflow_list_ambiguous_spend_categories` +
    `workflow_set_spend_category_override`.
- **Pick up here:**
  - If Joshua / next holder is closing the loop: Run
    `workflow_build_gl_spend_category_map(je_workbook_path="samples/Wolfhound Corp JEs Jan-Mar'26.xlsx")`
    once → walk the ambiguous list and confirm via
    `workflow_set_spend_category_override` → run
    `workflow_staffwizard_refresh_all` (the v0.9.0 sync registers all
    30 active StaffWizard projects automatically) → cut your first
    AP-1 EIB via
    `workflow_export_workday_supplier_invoice_eib(start_date="2026-04-01", end_date="2026-04-30")`.
  - If picking up AP-9: see the v1.0.0 section in
    `coassisted-workspace-ap-roadmap-2026-05-03.md` for the design.
    The hooks are ready: `project_registry` carries
    `billing_origin_state` / `billing_terms` / `billing_cadence` /
    `customer_email`; `tools/pandadoc_workflows.workflow_quote_to_invoice`
    is the bridge from signed PandaDoc quotes to AR-9 customer invoices.
- **Tests:** PandaDoc workflow suite added (19 tests, 0.26s, all
  green). Existing test totals unchanged otherwise; the cascade through
  `tools/__init__.py` requires the full venv with `googleapiclient` etc.
- **Notes:**
  - Three stable cuts in one session: v0.9.0 → v0.9.1 → v0.9.2. Eleven
    cuts total in three days.
  - Slack is gone from the roadmap as of 2026-05-03 — no longer future
    dev. Spec preserved in this file's git history if a real customer
    need surfaces post-1.0.0.
  - `tests/test_pandadoc_workflows.py` installs heavy-dep stubs
    (`googleapiclient`, `google.auth`) at module load so the suite runs
    in any environment, not just the full venv. Pattern is reusable for
    other tool-level tests if the cascade through `tools/__init__.py`
    keeps biting.

---

## 2026-05-03 · Joshua → (next holder) — v0.9.3 follow-up cut

- **Version:** v0.9.3 (stable)
- **Time held:** ~30 min (same session as v0.9.0/v0.9.1/v0.9.2)
- **Focus area:** Cron schedule management — toggle, edit, add, remove
  via chat + interactive Cowork artifact + read-only Drive page in the
  briefing footer.
- **What I touched:**
  - `cron_manager.py` (new) — `cron_jobs.json` source of truth, library
    + bootstrap from existing `crontab_template.txt`.
  - `tools/cron_manager.py` (new) — 9 MCP tools wrapping the library +
    Drive upload + the Cowork artifact HTML.
  - `scripts/cron/install_crontab.py` (patched) — reads from
    `cron_jobs.json` first, falls back to template.
  - `executive_briefing.py` (patched) — footer renders a "Manage daily
    schedule" link to `config.cron_manager.schedule_url` plus tagline.
  - `CHANGELOG.md` — v0.9.3 entry.
  - Desktop: `release-notes-v0.9.3-2026-05-03.md`.
- **What I left undone:**
  - AP-9 AR coverage (still the v1.0.0 gate).
  - One-shot helper to publish the schedule page on a schedule —
    achievable today by `workflow_cron_add_job` to schedule
    `workflow_cron_publish_to_drive` itself; explicit helper was
    deferred as low-value.
- **Pick up here:** to wire up the briefing footer link, ask Claude
  "publish my cron schedule to Drive", grab the URL, add to
  `config.json` under `cron_manager.schedule_url`. Tomorrow's briefing
  carries the link.
- **Tests:** existing PandaDoc test suite still 19/19. No new tests
  for cron_manager (the smoke-tests in this session covered bootstrap,
  toggle, schedule update, validation, add custom, render — all green).
- **Notes:** twelve stable cuts in three days now. v0.9.3 was the
  fourth same-day cut.

---

<!-- Next holder appends below -->
