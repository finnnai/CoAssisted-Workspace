# Handoff — CoAssisted Workspace

*(formerly "Google Workspace MCP" — same codebase, productized.)*

**You've received a .tar.gz archive. This is your ~15-minute install.**

---

## What you're getting

**CoAssisted Workspace** is a local MCP server that gives an AI assistant (Claude Code, Claude Cowork, or any MCP-speaking tool) **182 tools** across Gmail, Calendar, Drive, Sheets, Docs, Tasks, Contacts (CRM with auto-enrichment from email signatures), Chat, Maps, plus 33 cross-service workflows including territory routing, drive-time block automation, brand-voice extraction, the receipt + project-invoice extractors, and bulk CRM operations. Runs on your Mac, talks to your Google account, nothing else touches the data.

> **Distribution note:** This handoff tarball is the **personal/trust-group build** (`DISTRIBUTION_MODE = "personal"`). Every tool works, no license key needed. The free/paid tier split only kicks in for the official plugin marketplace build — see `tier.py` if curious.

**What's in the archive:** source code, install script, example configs, starter email templates, tests, docs.
**What's NOT in the archive:** the sender's OAuth credentials or access tokens. You'll create your own — that's by design, and it's the only safe way.

**Round-trip handoff:** the archive includes `HANDOFF_LOG.md` (append-only journal of every handoff) and `HANDOFF_STATE.json` (machine-readable state). Read the latest log entry first — it tells you exactly where the previous holder left off. **Before you send the archive back, append your own entry to `HANDOFF_LOG.md`** so the next holder picks up cleanly. See *Returning the archive* near the bottom of this doc.

---

## Sending the archive (for the person doing the handoff)

Two options, depending on how you got this archive:

**Automatic (recommended):** in Claude Cowork, just ask —
> "Send the handoff archive to coworker@company.com"

That invokes `workflow_send_handoff_archive`, which finds the newest tarball in `dist/`, uploads it to Drive, shares it with your coworker as a reader, and emails them the download link with a standard explainer. No attachments — so corporate mail filters won't strip it, and Gmail's stdio pipe can't choke on base64 payload size.

**Manual:** run `make handoff` to rebuild the tarball, then share the file from `dist/` however you like — Slack, AirDrop, email attachment, etc.

---

## Unpack and install

### 1. Move the archive out of Downloads

```bash
mkdir -p ~/Developer
mv ~/Downloads/google-workspace-mcp-*.tar.gz ~/Developer/
cd ~/Developer
tar xzf google-workspace-mcp-*.tar.gz
cd google_workspace_mcp
```

**Why not run it from Downloads?** macOS treats Downloads as temporary and periodically cleans it. You want this project folder to live somewhere persistent.

### 2. Clear the Gatekeeper quarantine (if needed)

macOS flags files that came through a browser download. If `./install.sh` complains about "unidentified developer" or similar, run:

```bash
xattr -dr com.apple.quarantine .
```

Safe on this project — it only contains Python source you can read.

### 3. Do the Google Cloud setup (one-time, ~10 min)

```bash
open GCP_SETUP.md
```

End result: a `credentials.json` file in the project folder. This is **your** OAuth app — the sender cannot share theirs with you, and shouldn't.

### 4. Run the installer

```bash
./install.sh
```

Creates a Python venv, installs dependencies, copies example configs into personal configs (which you can edit later), verifies your `credentials.json` is present.

### 5. Consent to OAuth

```bash
./install.sh --oauth
```

Browser opens, you sign in with the Google account you want the MCP to act as, grant the requested scopes, Ctrl-C once it says the server is ready. This saves `token.json` which is your persistent auth.

### 6. Wire it into Claude Cowork

The installer's final output includes the exact JSON config snippet with your paths already filled in. Copy that into Cowork's MCP server config and restart Cowork.

---

## Verify it works

Three checks to run inside Cowork once you've restarted:

1. **"List my Google calendars"** — should call `calendar_list_calendars` and return your actual calendars.
2. **"Send me an email with subject 'test'"** (to your own address) — proves send works end-to-end.
3. **"Refresh CRM stats across all my contacts"** — this kicks off the slow-but-impressive one; it runs the batched Gmail lookups and updates every contact's managed fields.

If all three work, you're fully operational.

---

## Optional — tune it for your workflow

These are files you can edit after install without breaking anything:

**`config.json`** — defaults for timezone, primary calendar, from-alias, CRM window in days, retry settings. Copied from `config.example.json` during install.

**`rules.json`** — auto-tagging rules based on email domain (e.g., `@acme.com → organization: Acme Corp, tier: enterprise`). Fires automatically on contact create/update. Copied from `rules.example.json`.

**`templates/*.md`** — mail-merge templates. Three starter templates ship; edit them or add your own. No restart needed to pick up new templates.

---

## Schedule the two daily CRM jobs (recommended)

```bash
crontab -e
```

Add both lines (replace `YOUR_USERNAME` with your actual Mac username):

```
0 7 * * * /Users/YOUR_USERNAME/Developer/google_workspace_mcp/.venv/bin/python /Users/YOUR_USERNAME/Developer/google_workspace_mcp/refresh_stats.py >> /Users/YOUR_USERNAME/Developer/google_workspace_mcp/logs/refresh_stats.cron.log 2>&1
5 7 * * * /Users/YOUR_USERNAME/Developer/google_workspace_mcp/.venv/bin/python /Users/YOUR_USERNAME/Developer/google_workspace_mcp/enrich_inbox.py >> /Users/YOUR_USERNAME/Developer/google_workspace_mcp/logs/enrich_inbox.cron.log 2>&1
```

Two jobs, five minutes apart:

- **7:00am — `refresh_stats.py`** — recomputes Last Interaction + Sent/Received 60d counts on every saved contact.
- **7:05am — `enrich_inbox.py`** — parses signatures from the last 24h of inbound mail and auto-fills title, phone (E.164), website, and social URLs on matching saved contacts.

If you only want one, run just `refresh_stats.py`. The enrichment pass is cheap and keeps title/phone fresh whenever a contact emails you with an updated signature.

---

## Full reference

In order of how you'll likely use them:

- **`INSTALL.md`** — the detailed install flow (if this HANDOFF.md is too terse)
- **`README.md`** — full tool inventory (all 94) and feature reference
- **`GCP_SETUP.md`** — Google Cloud walkthrough
- `Makefile` — `make help` to see all commands

---

## Troubleshooting

Most issues are covered by the Troubleshooting section in `README.md`. The big ones:

**"python3: command not found"** — you need Python 3.10 or newer. Install from https://www.python.org/downloads/ or via Homebrew: `brew install python@3.11`.

**"Missing credentials.json"** — you skipped step 3. Do `GCP_SETUP.md`.

**"Auth error (401)"** — delete `token.json` and run `./install.sh --oauth` again.

**Tools aren't appearing in Cowork** — run `./install.sh --test` to confirm the install is healthy. If tests pass but Cowork doesn't see tools, double-check the JSON config paths are absolute and point to *your* install location, and that you actually restarted Cowork.

**"Permission denied (403)" on a specific tool** — the corresponding API isn't enabled in your GCP project. Re-check `GCP_SETUP.md` step 2 and confirm all 8 APIs have "Enable" turned into "Manage" in the console.

---

## Returning the archive

If you're sending this project back to whoever handed it to you:

1. **Update `HANDOFF_LOG.md`.** Append a new entry at the bottom following
   the format already in the file. Cover what you touched, what you left
   undone, and where the next holder should pick up.

2. **Update `HANDOFF_STATE.json`.** At minimum, refresh:
   - `last_handler` — your name + email + handed_off_at timestamp
   - `next_handler` — back to the original sender
   - `focus_area` — one-liner on what you worked on
   - `tests` — run `python3 -m pytest` and record pass/fail counts
   - `recent_changes_summary` — short bullet list
   - `pick_up_here` — concrete next step
   - `open_tasks` — anything you added or resolved

3. **Bump `_version.py`** if you shipped real features. Convention is
   `0.7.X-dev` for between-release work. Append a CHANGELOG entry under
   `[Unreleased]`.

4. **Run the tests.** `python3 -m pytest` — the test count should not
   regress. If you intentionally removed tests, document why in your
   log entry.

5. **Build a fresh tarball.** `make handoff`. The Makefile excludes
   secrets and per-machine state automatically, so you don't have to
   scrub.

6. **Send it back.** Email or share the new tarball with the original
   sender. They'll run `make handoff-receive ARCHIVE=<your.tar.gz>` (or
   call `workflow_receive_handoff` from Cowork) which untars it,
   reads your manifest, and surfaces a file-level diff vs their
   local copy so they can see exactly what you did.

---

## Privacy note

Everything this MCP does runs **locally on your Mac**. Gmail/Calendar/Drive/etc. API calls go directly from your Mac to Google's servers using your OAuth token. The sender of this archive has no visibility into anything you do with it.

If you want to revoke the MCP's access at any time: delete `token.json`, OR go to your Google Account → Security → Third-party apps → revoke the OAuth app you created in `GCP_SETUP.md`.
