# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Round-trip handoff receiver — diff an incoming archive vs the local copy.

When a collaborator sends a working tarball back, this module:
  1. Untars it to a workspace directory (default: `incoming/`).
  2. Reads the incoming `HANDOFF_LOG.md` and `HANDOFF_STATE.json`.
  3. Surfaces the latest log entry from the collaborator.
  4. Diffs files (added / modified / deleted) vs the current local tree.
  5. Returns a compact report so the receiver can decide what to merge.

The pair file is `HANDOFF_LOG.md` (human, append-only) +
`HANDOFF_STATE.json` (machine, overwritten). The `workflow_receive_handoff`
MCP tool wraps this for in-chat use.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import tarfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional


_REPO_ROOT = Path(__file__).resolve().parent

# Files that are intentionally not compared (per-machine, regenerated, etc.)
_IGNORE_PATTERNS = (
    "token.json",
    "credentials.json",
    "config.json",
    "*.pyc",
    "__pycache__",
    ".venv",
    ".git",
    "logs/",
    "scan_state.json",
    "merchant_cache.json",
    "external_feeds_cache.json",
    "briefing_actions.json",
    "vendor_followups.json",
    "draft_queue.json",
    "watched_sheets.json",
    "incoming/",
    "dist/",
    ".DS_Store",
)


def _ignored(rel_path: str) -> bool:
    for pat in _IGNORE_PATTERNS:
        if pat.endswith("/"):
            if rel_path.startswith(pat) or f"/{pat}" in rel_path:
                return True
        elif pat.startswith("*."):
            if rel_path.endswith(pat[1:]):
                return True
        else:
            if rel_path == pat or rel_path.endswith(f"/{pat}"):
                return True
    return False


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _walk_files(root: Path) -> dict[str, str]:
    """Return {relative_path: sha256} for all non-ignored files in the tree."""
    out: dict[str, str] = {}
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        if _ignored(rel):
            continue
        try:
            out[rel] = _file_sha256(p)
        except OSError:
            continue
    return out


@dataclass
class HandoffDiff:
    """File-level diff between incoming and local trees."""
    added: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    unchanged_count: int = 0

    def to_dict(self) -> dict:
        return {
            "added": self.added,
            "modified": self.modified,
            "deleted": self.deleted,
            "unchanged_count": self.unchanged_count,
            "totals": {
                "added": len(self.added),
                "modified": len(self.modified),
                "deleted": len(self.deleted),
                "unchanged": self.unchanged_count,
            },
        }


@dataclass
class HandoffReport:
    """Top-level report from `receive_handoff`."""
    archive_path: str
    extracted_to: str
    incoming_state: Optional[dict]
    incoming_log_excerpt: Optional[str]
    diff: HandoffDiff
    pick_up_here: Optional[str]
    open_tasks: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "archive_path": self.archive_path,
            "extracted_to": self.extracted_to,
            "incoming_state": self.incoming_state,
            "incoming_log_excerpt": self.incoming_log_excerpt,
            "diff": self.diff.to_dict(),
            "pick_up_here": self.pick_up_here,
            "open_tasks": self.open_tasks,
            "notes": self.notes,
        }


# --------------------------------------------------------------------------- #
# Core API
# --------------------------------------------------------------------------- #


def extract_archive(archive: Path, dest: Path) -> Path:
    """Untar `archive` into `dest`. Returns the top-level extracted directory.

    Strips a single leading directory (the typical tarball convention)
    so files land directly under `dest`. Safe against path-traversal —
    members starting with `/` or containing `..` are rejected.
    """
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)

    with tarfile.open(archive, "r:*") as tf:
        members = []
        leading: Optional[str] = None
        for m in tf.getmembers():
            name = m.name
            if name.startswith("/") or ".." in Path(name).parts:
                raise ValueError(f"Unsafe path in archive: {name}")
            head, _, tail = name.partition("/")
            if leading is None:
                leading = head
            if head == leading and tail:
                m.name = tail
                members.append(m)
            elif not tail and head == leading:
                # the top-level dir entry itself — skip
                continue
            else:
                members.append(m)
        tf.extractall(dest, members=members)
    return dest


def latest_log_entry(log_path: Path) -> Optional[str]:
    """Return the most recent `## ` section from a HANDOFF_LOG.md."""
    if not log_path.exists():
        return None
    text = log_path.read_text(encoding="utf-8", errors="replace")
    sections = []
    cur: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            if cur:
                sections.append("\n".join(cur).rstrip())
            cur = [line]
        elif cur:
            cur.append(line)
    if cur:
        sections.append("\n".join(cur).rstrip())
    return sections[-1] if sections else None


def load_state(state_path: Path) -> Optional[dict]:
    if not state_path.exists():
        return None
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def diff_trees(incoming_root: Path, local_root: Path) -> HandoffDiff:
    incoming = _walk_files(incoming_root)
    local = _walk_files(local_root)
    diff = HandoffDiff()
    for path, sha in sorted(incoming.items()):
        if path not in local:
            diff.added.append(path)
        elif local[path] != sha:
            diff.modified.append(path)
        else:
            diff.unchanged_count += 1
    for path in sorted(local.keys()):
        if path not in incoming:
            diff.deleted.append(path)
    return diff


def receive_handoff(
    archive_path: str | Path,
    incoming_dir: str | Path = "incoming",
    local_root: str | Path = _REPO_ROOT,
) -> HandoffReport:
    """Top-level entry point. Untar, parse manifest, diff vs local tree."""
    archive = Path(archive_path).expanduser().resolve()
    if not archive.exists():
        raise FileNotFoundError(f"Archive not found: {archive}")

    local = Path(local_root).expanduser().resolve()
    dest = (local / Path(incoming_dir)).resolve() if not Path(incoming_dir).is_absolute() else Path(incoming_dir).resolve()

    extract_archive(archive, dest)

    state = load_state(dest / "HANDOFF_STATE.json")
    log_excerpt = latest_log_entry(dest / "HANDOFF_LOG.md")
    diff = diff_trees(dest, local)

    pick_up = state.get("pick_up_here") if state else None
    open_tasks = state.get("open_tasks", []) if state else []

    notes: list[str] = []
    if not state:
        notes.append("⚠️  Incoming archive has no HANDOFF_STATE.json.")
    if not log_excerpt:
        notes.append("⚠️  Incoming archive has no HANDOFF_LOG.md entry.")
    if not diff.added and not diff.modified and not diff.deleted:
        notes.append("ℹ️  No file-level changes vs your local copy.")

    return HandoffReport(
        archive_path=str(archive),
        extracted_to=str(dest),
        incoming_state=state,
        incoming_log_excerpt=log_excerpt,
        diff=diff,
        pick_up_here=pick_up,
        open_tasks=open_tasks,
        notes=notes,
    )
