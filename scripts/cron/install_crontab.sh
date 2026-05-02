#!/usr/bin/env bash
# Thin shell wrapper around scripts/cron/install_crontab.py.
# Picks up the project's venv if present so croniter is available.
# All flags pass through.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VENV_PYTHON="$REPO_ROOT/.venv/bin/python"
SYS_PYTHON="$(command -v python3 || true)"

if [[ -x "$VENV_PYTHON" ]]; then
    PYTHON="$VENV_PYTHON"
elif [[ -n "$SYS_PYTHON" ]]; then
    PYTHON="$SYS_PYTHON"
    echo "warning: .venv not found, using system python — install croniter via 'pip install croniter --break-system-packages' if next-fire times come up blank" >&2
else
    echo "error: no python interpreter available" >&2
    exit 127
fi

exec "$PYTHON" "$REPO_ROOT/scripts/cron/install_crontab.py" "$@"
