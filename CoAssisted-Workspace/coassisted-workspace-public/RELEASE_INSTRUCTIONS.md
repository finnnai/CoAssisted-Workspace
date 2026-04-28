# Release Instructions — GitHub + Anthropic Marketplace

This is the public-ready copy of CoAssisted Workspace, scrubbed of personal data and licensed MIT. Follow these steps to push to GitHub and submit to the Anthropic Claude Code plugin marketplace.

---

## Step 1 — Push to GitHub (5 min)

### Create the empty repo on GitHub

1. Go to https://github.com/new
2. Repository name: `coassisted-workspace`
3. Visibility: **Public**
4. Owner: `finnnai` (your account)
5. **Do NOT** initialize with a README, .gitignore, or LICENSE — we already have ours
6. Click **Create repository**

### Push from this folder

```bash
cd ~/Claude/coassisted-workspace-public

# One-time git setup
git init
git branch -M main
git add .
git commit -m "Initial public release — CoAssisted Workspace v1.0.0

183 tools across Gmail, Calendar, Drive, Sheets, Docs, Tasks, Contacts (CRM),
Chat, and Maps. Includes the LLM-driven Receipt Extractor and the full
Project-AP pipeline with sender classification, brand-voiced vendor
follow-up loop, automated reply parsing, hybrid Drive layout, and QuickBooks
Bills CSV export.

Free tier: 53 tools (Workspace basics + project-AP admin).
Paid tier: full feature set, gated via license_key in marketplace mode.

MIT licensed."

git remote add origin https://github.com/finnnai/coassisted-workspace.git
git push -u origin main
```

If `git push` asks for a password — GitHub disabled HTTPS passwords years ago. Use a personal access token from https://github.com/settings/tokens (create one with `repo` scope).

### Verify

Visit https://github.com/finnnai/coassisted-workspace — you should see:
- LICENSE (MIT)
- README.md (renders with the tier table at the top)
- All source + tests
- `.claude-plugin/plugin.json` (the marketplace manifest)
- `.github/workflows/test.yml` (CI runs on push)

CI will start automatically. First run takes ~2 minutes.

---

## Step 2 — Submit to Anthropic Marketplace (15 min)

Anthropic accepts plugin submissions through their console:

### A) Submit via Console (recommended)

1. Visit **https://platform.claude.com/plugins/submit**
2. Sign in with your Anthropic Console account
3. Submission form fields:
   - **Plugin name**: `coassisted-workspace`
   - **Repository URL**: `https://github.com/finnnai/coassisted-workspace`
   - **Manifest path**: `.claude-plugin/plugin.json`
   - **Categories**: Productivity, AP Automation, Google Workspace
   - **Description**: copy from `plugin.json` `description` field
   - **License**: MIT
4. Submit. Anthropic reviews for security + quality before listing.

### B) Or — list yourself in a community marketplace

If the official directory is slow to review (it can take 1-3 weeks), you can also list immediately on community marketplaces:

- **claude-plugins-official** (anthropic-managed): https://github.com/anthropics/claude-plugins-official — open a PR adding your plugin to the index.
- **cc-marketplace** (community): https://github.com/ananddtyagi/cc-marketplace — same flow.

For both, you'd add an entry to their top-level `marketplace.json` (or equivalent) pointing at your repo's `.claude-plugin/plugin.json`. Their READMEs have the exact contribution flow.

### C) Self-host as a "custom marketplace"

You can also publish your own marketplace manifest at any public URL. Users add it via:
```
/plugin marketplace add https://your-domain.com/marketplace.json
```
This works without Anthropic's review process — useful for paid-license-key gated distribution.

---

## Step 3 — Post-launch

Once your plugin is live in the marketplace:

- **Monitor** the GitHub Issues + Discussions tabs. Most plugin user feedback lands there.
- **Tag releases** — `git tag v1.0.1 && git push --tags` for each release.
- **Bump version** in both `.claude-plugin/plugin.json` AND `tier.py` (`BUILD_HASH`) on each release.
- **Watch CI** — the `test.yml` workflow runs on every PR. Don't merge red builds.

---

## What's in this copy that's different from your working folder

| What | Working folder | Public copy |
|---|---|---|
| `LICENSE` | Proprietary | MIT |
| `tier.py` `DISTRIBUTION_MODE` | `personal` | `marketplace` |
| Personal data files (`projects.json`, `merchants.json`, `awaiting_info.json`, `brand-voice.md`) | Real data | Excluded; `.example` versions only |
| `.gitignore` | Existing | Adds the personal data files to the deny list |
| `.claude-plugin/plugin.json` | Not present | Marketplace manifest |
| `.github/workflows/test.yml` | Not present | CI for Python 3.10/3.11/3.12 |
| `CONTRIBUTING.md`, `SECURITY.md` | Not present | Standard OSS docs |
| Test fixtures | Real names (Joshua Szott, Conor Boatman, etc) | Anonymized (Alice Smith, Bob Jones, example.com) |
| Source-code references to `surefox.com`, real emails | Hardcoded | Generic / config-driven |

Your **original `~/Claude/google_workspace_mcp/` folder is untouched** — keep using it for your live AP loop.

---

## If you find a problem in the public copy

The fast path is: fix it in this folder, commit, push. Then re-sync any common changes to your working folder manually — they're fully separate now.
