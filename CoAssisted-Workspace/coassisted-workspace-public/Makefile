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
DIST_NAME        := google-workspace-mcp-$(shell date +%Y-%m-%d)
PROJECT_DIR_NAME := $(notdir $(CURDIR))

.DEFAULT_GOAL := help
.PHONY: help install auth test run refresh enrich brand-voice handoff clean distclean

help:
	@awk 'BEGIN {FS = ":.*#"} /^[a-zA-Z_-]+:.*?#/ {printf "\033[1m%-12s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install: ## Run the full install.sh bootstrap (idempotent)
	@./install.sh

auth: ## Start the OAuth flow (opens browser, saves token.json)
	@$(PY) authenticate.py

test: ## Run the pytest suite
	@$(PY) -m pytest tests/ -v

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

clean: ## Remove .venv and __pycache__ (keeps configs/token/rules)
	@rm -rf $(VENV)
	@find . -type d -name '__pycache__' -exec rm -rf {} +
	@echo "Cleaned .venv and __pycache__"

distclean: clean ## Also remove logs/ (still does NOT delete configs, token, rules)
	@rm -rf logs/
	@echo "Removed logs/"
