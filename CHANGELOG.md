# Changelog

All notable changes to CoAssisted Workspace are documented here. Format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this
project uses [semantic versioning](https://semver.org/spec/v2.0.0.html).

## Versioning channels

This project ships on two channels:

- **stable** — tagged GitHub releases (e.g. `v0.6.0`). Safe for daily-driver
  use. Each stable release gets a dedicated section below.
- **dev** — between-release working snapshots (e.g. `v0.7.0-dev`). Tarballs
  carry the dev suffix. Not tagged on GitHub. May change underfoot.

`_version.py` is the single source of truth for `VERSION` + `CHANNEL` +
`RELEASE_DATE`. `pyproject.toml` is hand-synced.

---

## [Unreleased] — `0.7.0-dev`

_Will be backfilled as work lands between releases._

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
  unlock the full 183-tool experience without touching OAuth

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
