# Google Workspace MCP — discoverable commands
#
#   make install     # run install.sh (idempotent)
#   make auth        # run OAuth flow to save token.json
#   make test        # run pytest
#   make run         # launch the MCP server in the foreground
#   make refresh     # refresh CRM stats across all contacts (standalone script)
#   make handoff     # build a clean .tar.gz in dist/ for sharing
#   make clean       # remove .venv and __pycache__ (KEEPS your configs, token, rules)
#   make distclean   # additionally remove logs/ (does NOT touch credentials/token/configs)
#   make help        # list targets
#
# The .venv/bin/python path is used explicitly so you don't need to activate
# the venv before running a target.

VENV := .venv
PY   := $(VENV)/bin/python
PIP  := $(VENV)/bin/pip

DIST_DIR         := dist
PROJECT_DIR_NAME := $(notdir $(CURDIR))

# Pull version metadata from _version.py — single source of truth.
# Falls back gracefully on any environment where _version.py is missing.
VERSION       := $(shell python3 -c "import _version; print(_version.VERSION)" 2>/dev/null || echo "unknown")
CHANNEL       := $(shell python3 -c "import _version; print(_version.CHANNEL)" 2>/dev/null || echo "dev")
RELEASE_DATE  := $(shell python3 -c "import _version; print(_version.RELEASE_DATE)" 2>/dev/null || date +%Y-%m-%d)

# Stable releases use _version.py's RELEASE_DATE for reproducibility.
# Dev builds always stamp today's date so sequential snapshots are
# distinguishable.
# Stable: VERSION is plain semver (e.g. "0.6.1"), tarball is e.g.
#   coassisted-workspace-v0.6.1-stable-2026-05-01.tar.gz
# Dev:    VERSION already carries the "-dev" suffix (e.g. "0.6.1-dev"), so
#         we don't append channel again — would produce "-dev-dev-".
#         Tarball is e.g. coassisted-workspace-v0.6.1-dev-2026-04-28.tar.gz
STABLE_NAME   := coassisted-workspace-v$(VERSION)-stable-$(RELEASE_DATE)
DEV_NAME      := coassisted-workspace-v$(VERSION)-$(shell date +%Y-%m-%d)

# Legacy `handoff` target keeps the old date-only filename for backward
# compatibility — same content as `dev-build` semantically.
DIST_NAME     := coassisted-workspace-$(shell date +%Y-%m-%d)

.DEFAULT_GOAL := help
.PHONY: help install auth test run refresh enrich brand-voice \
        handoff release dev-build version clean distclean

help:
	@awk 'BEGIN {FS = ":.*#"} /^[a-zA-Z_-]+:.*?#/ {printf "\033[1m%-12s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install: ## Run the full install.sh bootstrap (idempotent)
	@./install.sh

auth: ## Start the OAuth flow (opens browser, saves token.json)
	@$(PY) authenticate.py

test: ## Run the pytest suite (excludes 'network' marker by default)
	@$(PY) -m pytest tests/ -v

test-fast: ## Same as test, but quiet + with 5s per-test timeout
	@$(PY) -m pytest tests/ --timeout=5 -q

test-network: ## Run only network-marker tests (live Google/Maps/Anthropic)
	@$(PY) -m pytest tests/ -v -m network --timeout=120

typecheck: ## Run mypy. Soft config (P2-2 baseline) — see pyproject.toml [tool.mypy]
	@$(PY) -m mypy . || true

typecheck-strict: ## Run mypy without --ignore-missing-imports — surfaces every gap
	@$(PY) -m mypy --strict --ignore-missing-imports . || true

run: ## Launch the MCP server (stdio transport, foreground)
	@$(PY) server.py

refresh: ## Refresh managed CRM fields across all contacts
	@$(PY) refresh_stats.py

enrich: ## Enrich saved contacts from recent inbound mail signatures (default 1 day)
	@$(PY) enrich_inbox.py

brand-voice: ## Refresh brand-voice.md from your last 90 days of sent mail
	@$(PY) refresh_brand_voice.py

handoff: ## Build a clean .tar.gz in dist/ for sharing (excludes secrets and state)
	@mkdir -p $(DIST_DIR)
	@tar \
	  --exclude='.venv' \
	  --exclude='__pycache__' \
	  --exclude='*.pyc' \
	  --exclude='logs' \
	  --exclude='credentials.json' \
	  --exclude='token.json' \
	  --exclude='config.json' \
	  --exclude='rules.json' \
	  --exclude='.git' \
	  --exclude='.pytest_cache' \
	  --exclude='dist' \
	  --exclude='.DS_Store' \
	  -czf $(DIST_DIR)/$(DIST_NAME).tar.gz \
	  -C .. $(PROJECT_DIR_NAME)
	@echo ""
	@echo "  ✓ Archive created: $(DIST_DIR)/$(DIST_NAME).tar.gz"
	@echo "    Size: $$(du -h $(DIST_DIR)/$(DIST_NAME).tar.gz | cut -f1)"
	@echo "    Contents: $$(tar -tzf $(DIST_DIR)/$(DIST_NAME).tar.gz | wc -l | tr -d ' ') files"
	@echo ""
	@echo "  Sanity check (these should be MISSING from the archive):"
	@for f in .venv logs credentials.json token.json config.json rules.json; do \
	  if tar -tzf $(DIST_DIR)/$(DIST_NAME).tar.gz 2>/dev/null | grep -q "$$f"; then \
	    echo "    ✗ LEAKED: $$f — do not ship this archive"; \
	  else \
	    echo "    ✓ excluded: $$f"; \
	  fi; \
	done
	@echo ""
	@echo "  Tell the recipient: open HANDOFF.md first."

handoff-receive: ## Untar a returned archive ($(ARCHIVE)) and diff vs local
	@if [ -z "$(ARCHIVE)" ]; then \
	  echo "Usage: make handoff-receive ARCHIVE=path/to/incoming.tar.gz"; \
	  exit 1; \
	fi
	@$(PY) -c "import json, handoff_receive; \
report = handoff_receive.receive_handoff('$(ARCHIVE)'); \
print(json.dumps(report.to_dict(), indent=2, default=str))"

version: ## Print current version + channel + release date
	@echo "Version:       v$(VERSION)"
	@echo "Channel:       $(CHANNEL)"
	@echo "Release date:  $(RELEASE_DATE)"
	@echo ""
	@if [ "$(CHANNEL)" = "stable" ]; then \
	  echo "  → next build via: make release"; \
	else \
	  echo "  → next build via: make dev-build"; \
	  echo "  (when ready, bump _version.py to a clean semver + flip CHANNEL=stable)"; \
	fi

release: ## Build a versioned STABLE tarball (uses _version.py's RELEASE_DATE)
	@if [ "$(CHANNEL)" != "stable" ]; then \
	  echo "Refusing to cut a release while CHANNEL=$(CHANNEL)."; \
	  echo "Edit _version.py: set VERSION to clean semver (no -dev suffix)"; \
	  echo "and CHANNEL='stable', then re-run."; \
	  exit 2; \
	fi
	@$(MAKE) -s _build NAME=$(STABLE_NAME)
	@echo ""
	@echo "  Stable build ready. Next steps:"
	@echo "    1. Tag the GitHub release: git tag v$(VERSION) && git push --tags"
	@echo "    2. After tagging, bump _version.py to next 'X.Y.Z-dev' / CHANNEL='dev'"

dev-build: ## Build a versioned DEV snapshot (uses today's date as suffix)
	@$(MAKE) -s _build NAME=$(DEV_NAME)
	@echo ""
	@echo "  Dev snapshot — safe for tester sharing, not for marketplace."

# Internal target — accepts NAME=<stem-without-extension>. Don't call directly.
_build:
	@mkdir -p $(DIST_DIR)
	@tar \
	  --exclude='.venv' \
	  --exclude='__pycache__' \
	  --exclude='*.pyc' \
	  --exclude='logs' \
	  --exclude='credentials.json' \
	  --exclude='token.json' \
	  --exclude='config.json' \
	  --exclude='rules.json' \
	  --exclude='awaiting_info.json' \
	  --exclude='dm_emails.json' \
	  --exclude='merchants.json' \
	  --exclude='audit_log.jsonl' \
	  --exclude='.git' \
	  --exclude='.pytest_cache' \
	  --exclude='dist' \
	  --exclude='.DS_Store' \
	  -czf $(DIST_DIR)/$(NAME).tar.gz \
	  -C .. $(PROJECT_DIR_NAME)
	@echo ""
	@echo "  ✓ Archive: $(DIST_DIR)/$(NAME).tar.gz"
	@echo "    Size:       $$(du -h $(DIST_DIR)/$(NAME).tar.gz | cut -f1)"
	@echo "    Contents:   $$(tar -tzf $(DIST_DIR)/$(NAME).tar.gz | wc -l | tr -d ' ') files"
	@echo "    Version:    v$(VERSION) ($(CHANNEL))"
	@echo ""
	@echo "  Sanity check (these should be MISSING from the archive):"
	@for f in .venv logs credentials.json token.json config.json rules.json awaiting_info.json dm_emails.json merchants.json; do \
	  if tar -tzf $(DIST_DIR)/$(NAME).tar.gz 2>/dev/null | grep -q "$$f"; then \
	    echo "    ✗ LEAKED: $$f — do not ship this archive"; \
	  else \
	    echo "    ✓ excluded: $$f"; \
	  fi; \
	done

clean: ## Remove .venv and __pycache__ (keeps configs/token/rules)
	@rm -rf $(VENV)
	@find . -type d -name '__pycache__' -exec rm -rf {} +
	@echo "Cleaned .venv and __pycache__"

distclean: clean ## Also remove logs/ (still does NOT delete configs, token, rules)
	@rm -rf logs/
	@echo "Removed logs/"
