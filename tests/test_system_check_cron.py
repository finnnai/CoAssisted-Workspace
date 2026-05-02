# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Tests for tools.system._check_cron + the install_crontab Python helpers.

The crontab itself isn't mocked — `_read_crontab` runs `crontab -l` for real,
so we test it in degraded modes (no crontab installed → WARN, parsing
errors → caught). The pure helpers (parse_entry, _PASTE_TEST_PATTERN,
humanize_delta, etc.) get straightforward unit tests.
"""

from __future__ import annotations

import datetime as _dt
import sys
import types
from pathlib import Path

import pytest

# tools.system imports gservices which needs heavy plumbing — stub it.
_fake_gservices = types.ModuleType("gservices")
_fake_gservices.__file__ = str(Path(__file__).resolve().parent.parent / "gservices.py")
sys.modules.setdefault("gservices", _fake_gservices)

from tools.system import (  # noqa: E402
    _PASTE_TEST_PATTERN,
    _parse_cron_entry,
    _read_crontab,
    _check_cron,
)


# -----------------------------------------------------------------------------
# Paste-test pattern detection
# -----------------------------------------------------------------------------

def test_paste_test_pattern_matches_minute_zero():
    """The most common artifact: leading '0' minute pasted into zsh."""
    sample = "zsh: command not found: 0\n"
    assert _PASTE_TEST_PATTERN.search(sample)


def test_paste_test_pattern_matches_two_digit_minute():
    """Cron line starting with '10' or '30' produces this artifact."""
    assert _PASTE_TEST_PATTERN.search("zsh: command not found: 10")
    assert _PASTE_TEST_PATTERN.search("zsh: command not found: 30")


def test_paste_test_pattern_does_not_match_legit_log():
    """Real cron output shouldn't false-positive."""
    assert not _PASTE_TEST_PATTERN.search(
        "2026-05-01 09:00:00 INFO  vendor_followups: 5 reminders sent"
    )


def test_paste_test_pattern_case_insensitive():
    """Some shells emit upper-case; pattern is case-insensitive."""
    assert _PASTE_TEST_PATTERN.search("ZSH: COMMAND NOT FOUND: 0")


# -----------------------------------------------------------------------------
# _parse_cron_entry
# -----------------------------------------------------------------------------

def test_parse_simple_entry():
    line = "0 7 * * * /usr/bin/python3 /home/u/refresh.py"
    parsed = _parse_cron_entry(line)
    assert parsed is not None
    assert parsed["schedule"] == "0 7 * * *"
    assert "refresh.py" in parsed["command"]


def test_parse_entry_with_log_redirect():
    line = "0 18 * * * /bin/bash -c run >> /var/log/cron.log 2>&1"
    parsed = _parse_cron_entry(line)
    assert parsed is not None
    assert parsed["log_path"] == "/var/log/cron.log"


def test_parse_entry_returns_none_for_short_line():
    assert _parse_cron_entry("0 7 * * *") is None
    assert _parse_cron_entry("") is None


def test_parse_entry_with_dow_range():
    """Cron schedules like '0 9 * * 1-5' (weekdays) parse cleanly."""
    line = "0 9 * * 1-5 /usr/bin/python3 /repo/vendor.py"
    parsed = _parse_cron_entry(line)
    assert parsed is not None
    assert parsed["schedule"] == "0 9 * * 1-5"


# -----------------------------------------------------------------------------
# _read_crontab — exercises the subprocess wrapper in isolation
# -----------------------------------------------------------------------------

def test_read_crontab_handles_missing_binary(monkeypatch):
    """When crontab isn't on PATH, return graceful failure."""
    import subprocess as sp

    def _boom(*a, **kw):
        raise FileNotFoundError("crontab")

    monkeypatch.setattr(sp, "run", _boom)
    have, lines, err = _read_crontab()
    assert have is False
    assert lines == []
    assert "PATH" in err or "binary" in err


def test_read_crontab_handles_no_crontab_installed(monkeypatch):
    """`crontab -l` exit 1 + empty stdout = 'no_crontab_installed'."""
    import subprocess as sp

    class _Result:
        returncode = 1
        stdout = ""
        stderr = "no crontab for u"

    monkeypatch.setattr(sp, "run", lambda *a, **kw: _Result())
    have, lines, err = _read_crontab()
    assert have is False
    assert err == "no_crontab_installed"


def test_read_crontab_strips_comments_and_blanks(monkeypatch):
    """Comment lines and blanks are filtered."""
    import subprocess as sp

    crontab_output = (
        "# comment line\n"
        "\n"
        "0 7 * * * /usr/bin/python3 refresh.py\n"
        "  # indented comment\n"
        "0 18 * * * /usr/bin/python3 receipts.py\n"
    )

    class _Result:
        returncode = 0
        stdout = crontab_output
        stderr = ""

    monkeypatch.setattr(sp, "run", lambda *a, **kw: _Result())
    have, lines, _err = _read_crontab()
    assert have is True
    assert len(lines) == 2
    assert "refresh.py" in lines[0]
    assert "receipts.py" in lines[1]


# -----------------------------------------------------------------------------
# _check_cron — end-to-end, covers the WARN/PASS branches
# -----------------------------------------------------------------------------

def test_check_cron_warns_when_no_crontab(monkeypatch):
    import subprocess as sp

    class _Result:
        returncode = 1
        stdout = ""
        stderr = ""

    monkeypatch.setattr(sp, "run", lambda *a, **kw: _Result())
    result = _check_cron()
    assert result["status"] == "warn"
    assert "make install-crontab" in (result.get("fix") or "")


def test_check_cron_warns_on_paste_test_signature(monkeypatch, tmp_path):
    """A log file containing the zsh artifact triggers the WARN path."""
    import subprocess as sp

    log_file = tmp_path / "cron_test.log"
    log_file.write_text("zsh: command not found: 0\n")

    cron_line = (
        f"0 7 * * * /usr/bin/python3 /repo/refresh.py "
        f">> {log_file} 2>&1"
    )

    class _Result:
        returncode = 0
        stdout = cron_line + "\n"
        stderr = ""

    monkeypatch.setattr(sp, "run", lambda *a, **kw: _Result())
    result = _check_cron()
    assert result["status"] == "warn"
    assert "paste-test" in result["message"].lower()
    assert str(log_file) in (result.get("fix") or "")


def test_check_cron_passes_with_clean_logs(monkeypatch, tmp_path):
    """Crontab installed, no paste-test artifacts → PASS."""
    import subprocess as sp

    log_file = tmp_path / "cron_clean.log"
    log_file.write_text("2026-05-01 07:00:00 OK\n")

    cron_line = (
        f"0 7 * * * /usr/bin/python3 /repo/refresh.py "
        f">> {log_file} 2>&1"
    )

    class _Result:
        returncode = 0
        stdout = cron_line + "\n"
        stderr = ""

    monkeypatch.setattr(sp, "run", lambda *a, **kw: _Result())
    result = _check_cron()
    assert result["status"] == "pass"
    assert "1 cron" in result["message"] or "scheduled" in result["message"]


# -----------------------------------------------------------------------------
# install_crontab.py — pure helpers (no subprocess / OS coupling)
# -----------------------------------------------------------------------------

def test_install_crontab_helpers_import():
    """The script imports without immediately running its main()."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "cron"))
    import install_crontab  # noqa: F401
    # If it imports, the module-level constants are reachable.
    assert install_crontab.PROJECT_ROOT.exists()
    assert install_crontab.TEMPLATE_PATH.name == "install_crontab_template.txt"


def test_humanize_delta_rounds_short_durations():
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "cron"))
    from install_crontab import humanize_delta
    assert humanize_delta(_dt.timedelta(seconds=42)) == "42s"
    assert humanize_delta(_dt.timedelta(seconds=120)) == "2m"
    assert humanize_delta(_dt.timedelta(hours=2, minutes=15)) == "2h 15m"
    assert humanize_delta(_dt.timedelta(days=3, hours=4)) == "3d 4h"


def test_parse_entry_in_install_script():
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "cron"))
    from install_crontab import parse_entry
    line = "0 7 * * * /repo/.venv/bin/python /repo/refresh_stats.py >> /repo/logs/r.log 2>&1"
    parsed = parse_entry(line)
    assert parsed is not None
    assert parsed["label"] == "refresh_stats"
    assert parsed["log_path"] == "/repo/logs/r.log"


def test_load_template_substitutes_home_and_venv(tmp_path, monkeypatch):
    """Template substitution swaps $HOME and $VENV_PYTHON for real paths."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "cron"))
    import install_crontab as ic

    fake_template = tmp_path / "tpl.txt"
    fake_template.write_text(
        "# header\n0 7 * * * $VENV_PYTHON $HOME/refresh_stats.py\n"
    )
    monkeypatch.setattr(ic, "TEMPLATE_PATH", fake_template)
    monkeypatch.setattr(
        ic, "PROJECT_ROOT", Path("/fake/repo")
    )
    monkeypatch.setattr(
        ic, "VENV_PYTHON", Path("/fake/repo/.venv/bin/python")
    )
    lines = ic.load_template()
    assert len(lines) == 1
    assert "/fake/repo/refresh_stats.py" in lines[0]
    assert "/fake/repo/.venv/bin/python" in lines[0]
    assert "$HOME" not in lines[0]
    assert "$VENV_PYTHON" not in lines[0]
