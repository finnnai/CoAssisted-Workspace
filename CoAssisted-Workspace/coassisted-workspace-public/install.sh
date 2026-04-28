#!/usr/bin/env bash
#
# Google Workspace MCP — bootstrap installer
# ------------------------------------------
# Idempotent: safe to re-run after edits or updates.
#
# What it does:
#   1. Checks Python >= 3.10
#   2. Creates/reuses a .venv/
#   3. pip installs dependencies (package + dev-deps for tests)
#   4. Copies example config/rules files if they don't exist
#   5. Verifies credentials.json is present (and points you to GCP_SETUP.md if not)
#   6. Optionally runs the OAuth flow (interactive)
#
# Usage:
#   ./install.sh            # full install
#   ./install.sh --oauth    # after install, immediately kick off OAuth flow
#   ./install.sh --test     # install + run pytest suite
#   ./install.sh --help

set -euo pipefail

cd "$(dirname "$0")"
PROJECT_DIR="$(pwd)"
VENV_DIR="$PROJECT_DIR/.venv"
PYTHON_MIN="3.10"

# Color helpers (no-op if stdout isn't a TTY).
if [[ -t 1 ]]; then
    B="\033[1m"; G="\033[32m"; Y="\033[33m"; R="\033[31m"; D="\033[2m"; N="\033[0m"
else
    B=""; G=""; Y=""; R=""; D=""; N=""
fi

info()  { printf "${B}==>${N} %s\n" "$*"; }
ok()    { printf "${G}  ✓${N} %s\n" "$*"; }
warn()  { printf "${Y}  !${N} %s\n" "$*"; }
err()   { printf "${R}  ✗${N} %s\n" "$*"; }
muted() { printf "${D}    %s${N}\n" "$*"; }

RUN_OAUTH=0
RUN_TESTS=0
FREE_TIER=0
UPGRADE_MODE=0

for arg in "$@"; do
    case "$arg" in
        --oauth) RUN_OAUTH=1 ;;
        --test|--tests) RUN_TESTS=1 ;;
        --free) FREE_TIER=1 ;;
        --upgrade) UPGRADE_MODE=1 ;;
        -h|--help)
            sed -n '2,/^set -/p' "$0" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *)
            err "Unknown flag: $arg"
            exit 2 ;;
    esac
done

# Free-tier mode bootstraps the smallest possible install — Workspace APIs
# only, no Maps prompts, no Anthropic prompts, no location services. Total
# install time ~10 min vs ~25 min for the Full path.
if [[ "$FREE_TIER" -eq 1 ]]; then
    printf "${G}━━━━ Free-tier install (10 min, 53 tools) ━━━━${N}\n"
    printf "${D}    Skipping Maps API, Anthropic key, Route Optimization,${N}\n"
    printf "${D}    and location-services prompts. Run ./install.sh${N}\n"
    printf "${D}    --upgrade later to add the paid prereqs.${N}\n\n"
fi

# ---------------------------------------------------------------------------
# Step 1 — Python version check
# ---------------------------------------------------------------------------
info "Checking Python version"

# Prefer newer interpreters if available (macOS often ships python3=3.9).
PY_BIN=""
for cand in python3.12 python3.11 python3.10 python3; do
    if command -v "$cand" >/dev/null 2>&1; then
        v="$("$cand" -c 'import sys; print(1 if sys.version_info >= (3, 10) else 0)' 2>/dev/null || echo 0)"
        if [[ "$v" == "1" ]]; then
            PY_BIN="$(command -v "$cand")"
            break
        fi
    fi
done

if [[ -z "$PY_BIN" ]]; then
    err "Python 3.10+ not found on PATH."
    muted "Your default python3 is probably 3.9 (macOS ships that). Install 3.11 via:"
    muted "    brew install python@3.11"
    muted "Then re-run ./install.sh"
    exit 1
fi

PY_VERSION="$("$PY_BIN" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
PY_OK="$("$PY_BIN" -c "import sys; print(1 if sys.version_info >= (3, 10) else 0)")"
if [[ "$PY_OK" != "1" ]]; then
    err "Python $PY_VERSION found — need >= $PYTHON_MIN."
    exit 1
fi
ok "Python $PY_VERSION (from $PY_BIN)"

# ---------------------------------------------------------------------------
# Step 2 — venv
# ---------------------------------------------------------------------------
info "Setting up virtual environment at .venv/"

if [[ ! -d "$VENV_DIR" ]]; then
    "$PY_BIN" -m venv "$VENV_DIR"
    ok "Created .venv/"
else
    ok "Reusing existing .venv/"
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

# ---------------------------------------------------------------------------
# Step 3 — deps
# ---------------------------------------------------------------------------
info "Installing dependencies (this may take a minute on first run)"

python -m pip install --quiet --upgrade pip wheel
# Install the project editable so future code edits don't require reinstall.
# Includes the [llm] extra (anthropic SDK) so optional LLM-backed features
# work out of the box. The SDK only activates when ANTHROPIC_API_KEY is set in env.
python -m pip install --quiet -e ".[dev,llm]"
ok "Installed package, dev-deps, and LLM SDK"

# Verify Pillow specifically. Without it, phone-photo receipts (6–18MB) hit
# Anthropic's 5MB Vision cap and the receipt extractor errors out. Pillow is
# in pyproject.toml dependencies, so this should already be in the venv —
# but this safety net catches a venv that pre-dates the dep being added.
if python -c "from PIL import Image" 2>/dev/null; then
    ok "Pillow installed (phone-photo receipts will auto-shrink before Vision)"
else
    warn "Pillow not importable — installing"
    python -m pip install --quiet "Pillow>=10.0"
    ok "Pillow installed"
fi

# ---------------------------------------------------------------------------
# Step 4 — example configs
# ---------------------------------------------------------------------------
info "Checking user-editable config files"

copy_example() {
    local example="$1"
    local target="$2"
    if [[ -f "$target" ]]; then
        ok "$(basename "$target") already exists — not touching it"
    else
        cp "$example" "$target"
        ok "Created $(basename "$target") (copy of $(basename "$example"))"
    fi
}

[[ -f "$PROJECT_DIR/config.example.json" ]] && copy_example config.example.json config.json
[[ -f "$PROJECT_DIR/rules.example.json"  ]] && copy_example rules.example.json  rules.json

mkdir -p "$PROJECT_DIR/logs"

# ---------------------------------------------------------------------------
# Step 5 — credentials.json check
# ---------------------------------------------------------------------------
info "Checking Google Cloud OAuth credentials"

if [[ ! -f "$PROJECT_DIR/credentials.json" ]]; then
    warn "credentials.json is missing."
    muted "Follow GCP_SETUP.md to create a Google Cloud project, enable the 8 APIs,"
    muted "and download an OAuth client JSON. Save it as:"
    muted "    $PROJECT_DIR/credentials.json"
    muted "Then re-run ./install.sh --oauth  to finish setup."
    exit 0
else
    ok "credentials.json found"
fi

# ---------------------------------------------------------------------------
# Step 6 — optional: OAuth / tests
# ---------------------------------------------------------------------------
if [[ "$RUN_TESTS" == "1" ]]; then
    info "Running tests"
    python -m pytest tests/ -q
    ok "Tests passed"
fi

if [[ "$RUN_OAUTH" == "1" ]]; then
    info "Kicking off OAuth flow (dedicated script — browser window will open)"
    # authenticate.py runs the OAuth flow only and then exits. We intentionally
    # DO NOT run server.py here because server.py uses stdio for MCP protocol
    # and would conflict with OAuth's stdout/URL prints.
    python authenticate.py
    if [[ -f "$PROJECT_DIR/token.json" ]]; then
        ok "token.json saved"
    else
        warn "token.json not written — OAuth did not complete."
    fi
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
if [[ "$FREE_TIER" -eq 1 ]]; then
cat <<EOF

${B}Free-tier install complete (53 tools).${N}

${B}You only need the OAuth flow now — total time so far ~2 min.${N}
Run:
  ${B}./install.sh --oauth${N}

What you get without spending another dollar:
  • All Workspace basics (Gmail, Calendar, Drive, Sheets, Docs, Tasks)
  • Contacts read + Chat read
  • Project-AP admin (register projects, see routing structure)
  • system_doctor + audit log
  • workflow_save_email_attachments_to_drive

When you're ready for the full 183-tool experience (Receipt Extractor,
Project-AP pipeline, Maps × CRM workflows, VRP routing), run:
  ${B}./install.sh --upgrade${N}
That'll prompt for Anthropic + Maps API keys and won't touch your OAuth.
EOF
else
cat <<EOF

${B}Install complete.${N}

${B}Recommended next step — run the setup wizard:${N}
  ${D}$VENV_DIR/bin/python setup_wizard.py${N}

The wizard walks you through:
  • Google Workspace OAuth (required)
  • Anthropic API key (optional — unlocks brand voice + LLM signature parsing)
  • Google Maps API key (optional — unlocks 10 maps_* tools)
  • Cron jobs (optional — daily refresh + enrichment)

Each step is skippable, and you can re-run the wizard anytime to add what
you skipped.

${B}Or do it manually:${N}
  1. If you haven't yet: ${B}./install.sh --oauth${N}  to consent and save token.json
  2. Add this MCP to Claude Cowork's config:

     ${D}{
       "mcpServers": {
         "google-workspace": {
           "command": "$VENV_DIR/bin/python",
           "args": ["$PROJECT_DIR/server.py"]
         }
       }
     }${N}

  3. Restart Cowork. 90 tools will appear under gmail_, calendar_, drive_,
     sheets_, docs_, tasks_, contacts_, chat_, and workflow_ prefixes.

Useful commands:
  ${B}make test${N}          # run pytest
  ${B}make run${N}           # start the MCP locally (foreground)
  ${B}make refresh${N}       # refresh CRM stats across all contacts
  ${B}make clean${N}         # remove .venv and __pycache__

Docs:
  ${B}INSTALL.md${N}         # this install flow in document form
  ${B}GCP_SETUP.md${N}       # Google Cloud project walkthrough
  ${B}README.md${N}          # feature reference and tool inventory
EOF
fi
