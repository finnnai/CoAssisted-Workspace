# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Single source of truth for the build version.

Everything that needs to know what version we are reads from here:

  - `pyproject.toml` is hand-synced (tooling reads it for pip install)
  - `tier.BUILD_HASH` defers to VERSION + RELEASE_DATE here
  - `Makefile` reads `VERSION` for tarball filenames
  - `system_check_license` shows VERSION in its output
  - `CHANGELOG.md` documents what shipped in each version

We use a two-channel scheme:

  STABLE releases:
      VERSION = "0.6.0"          # plain semver, no suffix
      CHANNEL = "stable"
      → tarball: coassisted-workspace-v0.6.0-stable-2026-04-28.tar.gz
      → cut a GitHub release with this tag
      → land in the marketplace listing

  DEV builds (between releases):
      VERSION = "0.7.0-dev"       # next planned version + "-dev" suffix
      CHANNEL = "dev"
      → tarball: coassisted-workspace-v0.7.0-dev-2026-04-29.tar.gz
      → safe to share with testers who want the bleeding edge
      → don't tag GitHub releases; these are working snapshots

Bumping rules:
  - Cutting a stable release: change VERSION to plain semver, CHANNEL to
    "stable", update RELEASE_DATE, write a CHANGELOG.md entry, run
    `make release` to build a versioned tarball, tag GitHub.
  - After cutting stable: immediately bump VERSION to next-planned + "-dev",
    flip CHANNEL to "dev". Working builds carry that version until the next
    release.

Don't add anything to this file that imports anything else from the project.
It needs to be import-safe from tier.py, conftest.py, setup scripts, etc.
"""

VERSION: str = "0.8.3"
CHANNEL: str = "stable"  # "stable" | "dev"
RELEASE_DATE = "2026-05-01"


def is_dev_build() -> bool:
    """True if this is a between-releases dev snapshot."""
    return CHANNEL == "dev" or VERSION.endswith("-dev")


def short_version_string() -> str:
    """e.g. 'v0.6.0' for stable, 'v0.7.0-dev' for dev. UI-friendly."""
    return f"v{VERSION}"


def full_version_string() -> str:
    """e.g. 'v0.6.0 (stable, 2026-04-28)'. For diagnostic output / about boxes."""
    return f"v{VERSION} ({CHANNEL}, {RELEASE_DATE})"


def tarball_basename() -> str:
    """The filename pattern `make release` and `make dev-build` use.

    Stable: coassisted-workspace-v0.6.0-stable-2026-04-28
    Dev:    coassisted-workspace-v0.7.0-dev-2026-04-29
    """
    return f"coassisted-workspace-v{VERSION}-{CHANNEL}-{RELEASE_DATE}"
