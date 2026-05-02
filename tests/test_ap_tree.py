# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Unit tests for ap_tree.py — the AP-6 forced-tree filing module.

Covers:
  - _month_bucket_name (pure utility)
  - register_new_project — full subtree creation, idempotency, error paths
  - ensure_month_subtree — lazy expansion, cache hit, missing project
  - audit_filing_tree — flags recent non-conforming files
  - last_audit — empty when never run, returns persisted report after audit

Drive helpers (_drive_create_folder, _drive_list_children) are
monkeypatched at the module boundary so no Google API calls happen.
"""

from __future__ import annotations

import datetime as _dt

import pytest

import ap_tree
import project_registry


# -----------------------------------------------------------------------------
# Fake Drive backend — keep it simple.
# -----------------------------------------------------------------------------

class FakeDrive:
    """In-memory Drive double. Tracks folder create + list calls.

    Each folder gets an auto-incrementing ID. Listing returns folders
    keyed by parent. Files can be seeded for audit tests.
    """

    def __init__(self):
        self._counter = 0
        # parent_id -> list of {"id", "name", "mimeType", "modifiedTime", "owners"}
        self.children: dict[str, list[dict]] = {}
        self.create_calls: list[tuple[str, str | None]] = []

    def _next_id(self, prefix: str = "F") -> str:
        self._counter += 1
        return f"{prefix}{self._counter}"

    def create_folder(self, name: str, parent_id: str | None) -> str | None:
        self.create_calls.append((name, parent_id))
        new_id = self._next_id("F")
        self.children.setdefault(parent_id or "ROOT", []).append({
            "id": new_id,
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
        })
        return new_id

    def list_children(
        self, parent_id: str, *, name: str | None = None,
    ) -> list[dict]:
        kids = list(self.children.get(parent_id, []))
        if name is not None:
            kids = [k for k in kids if k.get("name") == name]
        return kids

    def seed_file(
        self,
        parent_id: str,
        *,
        name: str,
        modified_time: str,
        owners: list[str] | None = None,
    ) -> str:
        new_id = self._next_id("FILE")
        self.children.setdefault(parent_id, []).append({
            "id": new_id,
            "name": name,
            "mimeType": "application/pdf",
            "modifiedTime": modified_time,
            "owners": [{"emailAddress": e} for e in (owners or [])],
        })
        return new_id


@pytest.fixture
def fake_drive(monkeypatch):
    """Inject a FakeDrive in place of ap_tree's two Drive helpers."""
    fd = FakeDrive()
    monkeypatch.setattr(ap_tree, "_drive_create_folder", fd.create_folder)
    monkeypatch.setattr(ap_tree, "_drive_list_children", fd.list_children)
    yield fd


@pytest.fixture
def fresh_registry(tmp_path, monkeypatch):
    """Each test gets a fresh project_registry."""
    monkeypatch.setattr(project_registry, "_REGISTRY_PATH", tmp_path / "p.json")
    yield tmp_path


@pytest.fixture
def fresh_audit_path(tmp_path, monkeypatch):
    """Redirect ap_tree's audit JSON to tmp."""
    monkeypatch.setattr(ap_tree, "_AUDIT_PATH", tmp_path / "ap_tree_audit.json")
    yield tmp_path


# -----------------------------------------------------------------------------
# Pure utility — _month_bucket_name
# -----------------------------------------------------------------------------

def test_month_bucket_name_explicit_date():
    """Explicit date renders as YYYY-MM."""
    assert ap_tree._month_bucket_name(_dt.date(2026, 5, 14)) == "2026-05"
    assert ap_tree._month_bucket_name(_dt.date(2026, 1, 1)) == "2026-01"
    assert ap_tree._month_bucket_name(_dt.date(2026, 12, 31)) == "2026-12"


def test_month_bucket_name_defaults_to_today():
    """No arg → today's YYYY-MM."""
    expected = f"{_dt.date.today():%Y-%m}"
    assert ap_tree._month_bucket_name() == expected


# -----------------------------------------------------------------------------
# register_new_project — happy path
# -----------------------------------------------------------------------------

def test_register_new_project_creates_full_subtree(fake_drive, fresh_registry):
    """A new project gets folder + 7 subfolders + month buckets stamped."""
    # Pre-create the AP root + Projects parent.
    ap_root = fake_drive.create_folder("Surefox AP", None)
    projects_parent = fake_drive.create_folder("Projects", ap_root)

    result = ap_tree.register_new_project(
        project_name="Test Eagle",
        code="TE1",
        client="Test Client LLC",
        ap_root_folder_id=ap_root,
    )
    assert "error" not in result
    assert result["project_code"] == "TE1"
    assert result["drive_folder_id"]
    assert result["already_existed"] is False
    assert result["registered"] is True

    sub = result["subfolder_ids"]
    # All 7 base subfolders present + non-None.
    for key in (
        "receipts", "invoices", "labor",
        "statements_amex", "statements_wex",
        "workday_supplier", "workday_journal",
    ):
        assert sub.get(key), f"missing subfolder {key}"
    # Month buckets stamped under receipts + invoices for current month.
    bucket = _dt.date.today().strftime("%Y-%m").replace("-", "_")
    assert sub.get(f"receipts_{bucket}")
    assert sub.get(f"invoices_{bucket}")

    # Registry record persisted.
    rec = project_registry.get("TE1")
    assert rec is not None
    assert rec["name"] == "Test Eagle"
    assert rec["client"] == "Test Client LLC"


def test_register_new_project_is_idempotent(fake_drive, fresh_registry):
    """Re-registering reuses the existing Drive folder, doesn't duplicate."""
    ap_root = fake_drive.create_folder("Surefox AP", None)
    fake_drive.create_folder("Projects", ap_root)

    first = ap_tree.register_new_project(
        project_name="Idempotent",
        code="IDM",
        ap_root_folder_id=ap_root,
    )
    create_count_after_first = len(fake_drive.create_calls)

    second = ap_tree.register_new_project(
        project_name="Idempotent",
        code="IDM",
        ap_root_folder_id=ap_root,
    )
    create_count_after_second = len(fake_drive.create_calls)

    # Same project folder ID returned both times.
    assert first["drive_folder_id"] == second["drive_folder_id"]
    assert second["already_existed"] is True
    # Second run shouldn't have created anything new — the existing
    # subtree is reused.
    assert create_count_after_second == create_count_after_first


def test_register_new_project_errors_without_parent_info(fresh_registry):
    """Calling without ap_root or projects_parent surfaces an error."""
    result = ap_tree.register_new_project(
        project_name="Floating",
        code="FLT",
    )
    assert "error" in result
    assert "ap_root_folder_id" in result["error"]


def test_register_new_project_errors_when_projects_folder_missing(
    fake_drive, fresh_registry,
):
    """If `Projects/` doesn't exist under the AP root, surface a clear error."""
    # Create AP root but NOT a Projects child under it.
    ap_root = fake_drive.create_folder("Surefox AP", None)
    result = ap_tree.register_new_project(
        project_name="Test",
        code="TST",
        ap_root_folder_id=ap_root,
    )
    assert "error" in result
    assert "Projects" in result["error"]


# -----------------------------------------------------------------------------
# ensure_month_subtree
# -----------------------------------------------------------------------------

def test_ensure_month_subtree_unknown_project(fake_drive, fresh_registry):
    """An unregistered project returns None for every kind."""
    result = ap_tree.ensure_month_subtree("DOES_NOT_EXIST")
    assert result == {"receipts": None, "invoices": None}


def test_ensure_month_subtree_creates_missing_bucket(fake_drive, fresh_registry):
    """Lazy expansion — first call creates the YYYY-MM folder."""
    # Set up via register_new_project so subfolders exist.
    ap_root = fake_drive.create_folder("Surefox AP", None)
    fake_drive.create_folder("Projects", ap_root)
    ap_tree.register_new_project(
        project_name="Lazy", code="LZY", ap_root_folder_id=ap_root,
    )

    # Ask for a month bucket far in the future — won't be cached.
    future = _dt.date(2029, 6, 1)
    result = ap_tree.ensure_month_subtree("LZY", when=future)
    assert result["receipts"]
    assert result["invoices"]

    # The cache key landed in the registry.
    rec = project_registry.get("LZY")
    subs = rec.get("drive_subfolders") or {}
    assert subs.get("receipts_2029_06") == result["receipts"]
    assert subs.get("invoices_2029_06") == result["invoices"]


def test_ensure_month_subtree_uses_cache_hit(fake_drive, fresh_registry):
    """A second call for the same month doesn't create new folders."""
    ap_root = fake_drive.create_folder("Surefox AP", None)
    fake_drive.create_folder("Projects", ap_root)
    ap_tree.register_new_project(
        project_name="Cached", code="CCH", ap_root_folder_id=ap_root,
    )

    when = _dt.date(2030, 7, 1)
    first = ap_tree.ensure_month_subtree("CCH", when=when)
    create_count_after_first = len(fake_drive.create_calls)

    second = ap_tree.ensure_month_subtree("CCH", when=when)
    create_count_after_second = len(fake_drive.create_calls)

    assert first == second  # same IDs returned
    assert create_count_after_second == create_count_after_first  # no new creates


def test_ensure_month_subtree_kinds_filter(fake_drive, fresh_registry):
    """Custom `kinds=` returns only the requested keys."""
    ap_root = fake_drive.create_folder("Surefox AP", None)
    fake_drive.create_folder("Projects", ap_root)
    ap_tree.register_new_project(
        project_name="Kinds", code="KND", ap_root_folder_id=ap_root,
    )
    out = ap_tree.ensure_month_subtree(
        "KND", when=_dt.date(2031, 8, 1), kinds=("receipts",),
    )
    assert "receipts" in out
    assert "invoices" not in out


# -----------------------------------------------------------------------------
# audit_filing_tree + last_audit
# -----------------------------------------------------------------------------

def test_last_audit_returns_none_when_never_run(fresh_audit_path):
    """No prior audit → None."""
    assert ap_tree.last_audit() is None


def test_audit_flags_recent_non_conforming_file(
    fake_drive, fresh_registry, fresh_audit_path,
):
    """A recent file with a non-conforming name lands in suspicious."""
    ap_root = fake_drive.create_folder("Surefox AP", None)
    fake_drive.create_folder("Projects", ap_root)
    ap_tree.register_new_project(
        project_name="Audit", code="AUD", ap_root_folder_id=ap_root,
    )
    # Drop a sketchy file in the receipts subfolder, modified just now.
    rec = project_registry.get("AUD")
    receipts_id = rec["drive_subfolders"]["receipts"]
    now_iso = _dt.datetime.now().astimezone().isoformat()
    fake_drive.seed_file(
        receipts_id,
        name="my_random_drop.pdf",  # does NOT match the YYYY-MM-DD_..._receipt regex
        modified_time=now_iso,
        owners=["intern@surefox.com"],
    )

    report = ap_tree.audit_filing_tree(age_threshold_minutes=60)
    assert report["suspicious_files"] >= 1
    assert "AUD" in report["findings"]
    suspicious = report["findings"]["AUD"]["suspicious"]
    assert any(s["name"] == "my_random_drop.pdf" for s in suspicious)
    # Last audit pulls back what we just persisted.
    last = ap_tree.last_audit()
    assert last is not None
    assert last["suspicious_files"] == report["suspicious_files"]


def test_audit_ignores_conforming_filename(
    fake_drive, fresh_registry, fresh_audit_path,
):
    """A file that matches the AP-6 naming regex is NOT flagged."""
    ap_root = fake_drive.create_folder("Surefox AP", None)
    fake_drive.create_folder("Projects", ap_root)
    ap_tree.register_new_project(
        project_name="Clean", code="CLN", ap_root_folder_id=ap_root,
    )
    rec = project_registry.get("CLN")
    receipts_id = rec["drive_subfolders"]["receipts"]
    now_iso = _dt.datetime.now().astimezone().isoformat()
    fake_drive.seed_file(
        receipts_id,
        name="2026-05-01_AMEX_42.18_receipt.pdf",
        modified_time=now_iso,
    )
    report = ap_tree.audit_filing_tree(age_threshold_minutes=60)
    assert report.get("findings", {}).get("CLN") is None


def test_audit_ignores_old_files(
    fake_drive, fresh_registry, fresh_audit_path,
):
    """Files older than the age threshold are not in scope."""
    ap_root = fake_drive.create_folder("Surefox AP", None)
    fake_drive.create_folder("Projects", ap_root)
    ap_tree.register_new_project(
        project_name="Old", code="OLD", ap_root_folder_id=ap_root,
    )
    rec = project_registry.get("OLD")
    receipts_id = rec["drive_subfolders"]["receipts"]
    # Modified well before the cutoff.
    old_iso = (_dt.datetime.now().astimezone() - _dt.timedelta(hours=24)).isoformat()
    fake_drive.seed_file(
        receipts_id,
        name="ancient_drop.pdf",  # non-conforming, but old
        modified_time=old_iso,
    )
    report = ap_tree.audit_filing_tree(age_threshold_minutes=60)
    assert report.get("findings", {}).get("OLD") is None


def test_audit_persists_report_atomically(
    fake_drive, fresh_registry, fresh_audit_path,
):
    """Running audit twice leaves the latest report on disk."""
    ap_root = fake_drive.create_folder("Surefox AP", None)
    fake_drive.create_folder("Projects", ap_root)
    ap_tree.register_new_project(
        project_name="Persist", code="PER", ap_root_folder_id=ap_root,
    )
    r1 = ap_tree.audit_filing_tree(age_threshold_minutes=60)
    r2 = ap_tree.audit_filing_tree(age_threshold_minutes=60)
    last = ap_tree.last_audit()
    assert last is not None
    # Each run regenerates audited_at; the latest persisted matches r2.
    assert last["audited_at"] == r2["audited_at"]
