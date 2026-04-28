"""Tests for the telemetry sanitization pipeline."""

import json
from pathlib import Path

import pytest

import telemetry


def test_sanitize_email():
    s = "Failed to load creds for finnn@surefox.com — check OAuth"
    out = telemetry.sanitize_string(s)
    assert "finnn@surefox.com" not in out
    assert "<email>" in out


def test_sanitize_google_api_key():
    s = "key=AIzaSyADgye56k2Vo1Ofe0fnfQyLwrfMw-UXX-E"
    out = telemetry.sanitize_string(s)
    assert "AIzaSy" not in out
    assert "<google_api_key>" in out


def test_sanitize_anthropic_key():
    s = "Auth: sk-ant-api03-8Xg8QAo1cJYRxKJ8H9upmV4Iba7nBXVDeAY0OYJAa0HL_1CAETskAY0W8xGqCUNXRI_o"
    out = telemetry.sanitize_string(s)
    assert "sk-ant-api03" not in out
    assert "<anthropic_key>" in out


def test_sanitize_oauth_refresh_token():
    s = "refresh_token=1//0gabcdefghijk_lmnopQRSTUVwxyz1234"
    out = telemetry.sanitize_string(s)
    assert "1//0gabc" not in out
    assert "<oauth_refresh_token>" in out


def test_sanitize_user_path():
    s = "Wrote to /Users/finnnai/Claude/google_workspace_mcp/logs/foo.log"
    out = telemetry.sanitize_string(s)
    assert "finnnai" not in out
    assert "/Users/<USER>" in out


def test_sanitize_ipv4():
    s = "Connected to 172.16.254.1 at port 8080"
    out = telemetry.sanitize_string(s)
    assert "172.16.254.1" not in out
    assert "<ip>" in out


def test_sanitize_gcp_project():
    s = "Project claude-cowork-mcp-494315 has no billing"
    out = telemetry.sanitize_string(s)
    assert "claude-cowork-mcp-494315" not in out
    assert "<gcp_project>" in out


def test_sanitize_recursive_dict():
    obj = {
        "user": "finnn@surefox.com",
        "nested": {
            "key": "AIzaSyADgye56k2Vo1Ofe0fnfQyLwrfMw-UXX-E",
            "list": [
                "/Users/finnnai/something",
                {"deeply_nested": "bob@example.com"},
            ],
        },
    }
    out = telemetry.sanitize(obj)
    blob = json.dumps(out)
    assert "finnn@surefox.com" not in blob
    assert "AIzaSy" not in blob
    assert "finnnai" not in blob
    assert "bob@example.com" not in blob


def test_sanitize_preserves_non_strings():
    obj = {"count": 42, "active": True, "ratio": 0.95, "absent": None}
    out = telemetry.sanitize(obj)
    assert out == obj


def test_build_report_filters_passes(tmp_path, monkeypatch):
    """The report should only include warn/fail entries, not passes."""
    monkeypatch.setattr(
        telemetry, "gather_environment", lambda: {"os_system": "TestOS"},
    )
    monkeypatch.setattr(
        telemetry, "gather_recent_actions", lambda limit=20: [],
    )
    doctor_results = [
        {"name": "OAuth", "status": "pass", "details": "good"},
        {"name": "Maps", "status": "warn", "details": "key not set"},
        {"name": "Tools", "status": "fail", "details": "registration error"},
    ]
    rep = telemetry.build_report(doctor_results)
    assert rep["summary"]["passed"] == 1
    assert rep["summary"]["warned"] == 1
    assert rep["summary"]["failed"] == 1
    assert len(rep["checks_with_issues"]) == 2
    statuses = {c["status"] for c in rep["checks_with_issues"]}
    assert statuses == {"warn", "fail"}


def test_save_report_creates_file(tmp_path, monkeypatch):
    monkeypatch.setattr(telemetry, "__file__", str(tmp_path / "telemetry.py"))
    rep = {"foo": "bar"}
    path = telemetry.save_report(rep)
    assert path.exists()
    assert json.loads(path.read_text()) == {"foo": "bar"}
    assert path.parent.name == "health_reports"


def test_find_latest_report_returns_newest(tmp_path, monkeypatch):
    monkeypatch.setattr(telemetry, "__file__", str(tmp_path / "telemetry.py"))
    out_dir = tmp_path / "logs" / "health_reports"
    out_dir.mkdir(parents=True)
    (out_dir / "health_report_20260101T000000.json").write_text("{}")
    (out_dir / "health_report_20260201T000000.json").write_text("{}")
    (out_dir / "health_report_20260301T000000.json").write_text("{}")
    latest = telemetry.find_latest_report()
    assert latest is not None
    assert "20260301" in latest.name


def test_find_latest_report_handles_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(telemetry, "__file__", str(tmp_path / "telemetry.py"))
    assert telemetry.find_latest_report() is None
