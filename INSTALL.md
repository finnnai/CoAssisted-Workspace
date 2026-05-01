# Install — CoAssisted Workspace

**Two install paths. Pick the one that fits how you'll use it.**

| Path | Time | What you get | Best for |
|---|---|---|---|
| 🟢 **Free** (`./install.sh --free`) | **~10 min** | All 53 free-tier tools — Workspace basics + system health + project-AP admin (register projects, see routing-rule shape) | Evaluating, casual use, marketplace single-click install |
| 🔵 **Full** (`./install.sh`) | ~25 min | All 238 tools — adds Maps × CRM workflows, VRP routing, Receipt + Project-AP extractors, vendor follow-up loop, current-location detection | Daily-driver, sales/CSM/ops, AP automation, expense reporting |

**The Free Path skips:** Maps API setup (10 Maps APIs), Anthropic API key, Route Optimization API, location services. Plenty for sending/reading mail, scheduling events, Drive/Sheets/Docs work, running `system_doctor`, and previewing the project-AP routing structure before paying. Run `./install.sh --upgrade` later to add the paid prereqs without re-doing OAuth.

> This is the bootstrap flow. `README.md` is the feature reference. `GCP_SETUP.md` is the Google Cloud walkthrough. If you received this project as a `.tar.gz` from someone else, read `HANDOFF.md` first — it has a shorter "I just got this archive" flow.

---

## What you're setting up

A local MCP server that gives Claude Cowork **182 tools across 13 categories**: Gmail, Calendar, Drive, Sheets, Docs, Tasks, Contacts (with a CRM layer), Chat, Maps, cross-service Workflows, the Receipt + Project-Invoice extractors, and System health checks.

Everything runs locally on your Mac. Data flows between Claude and Google APIs only; nothing else touches it. You sign in once with your own Google account; nobody else can see your data.

---

## Prerequisites

You need:

- **macOS** (current versions tested: 13, 14, 15)
- **Python 3.10+** — check with `python3 --version`. Comes preinstalled on most Macs. If yours is older: `brew install python@3.12`.
- **A Google account** the MCP will act as (work or personal)
- **Claude Cowork** — installed and signed in

**Free Path also needs:**
- A Google Cloud project with **8 Workspace APIs enabled** (Gmail, Calendar, Drive, Sheets, Docs, Tasks, People, Chat) and an OAuth `credentials.json`. Walkthrough: `GCP_SETUP.md` Sections 1–3 only.

**Full Path adds:**
- **Homebrew** (`brew install` for `corelocationcli`)
- **Maps API key** with 8 Maps APIs allowlisted (`GCP_SETUP.md` Section 2c)
- **Anthropic API key** for the LLM-driven Receipt + Project-AP extractors
- ~15 extra minutes vs. Free

---

## The big picture

Setup happens in 5 phases. Each is independent — you can stop after Phase 1 and have a working MCP, then come back later for the optional Phases.

| Phase | What you get | Time | Required? |
|---|---|---|---|
| 1. Core install | All 8 base Workspace tools (Gmail, Calendar, Drive, etc.) — **~182 tools** | 15 min | Yes |
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

You're done with Phase 1 — ~182 tools work right now.

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
