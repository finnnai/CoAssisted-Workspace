# © 2026 CoAssisted Workspace contributors. Licensed under MIT — see LICENSE.
"""Local MCP server exposing Gmail, Calendar, Drive, Sheets, and Docs.

Run via:
    python server.py

Uses stdio transport — designed to be launched as a subprocess by Claude Cowork.
"""

from __future__ import annotations

import sys

from mcp.server.fastmcp import FastMCP

import tools

mcp = FastMCP("google_workspace_mcp")
tools.register_all(mcp)


def main() -> None:
    """Entrypoint. Runs the MCP server over stdio."""
    # IMPORTANT for stdio: never print to stdout (it corrupts the protocol).
    # Route any stray prints to stderr just in case.
    sys.stdout.flush()
    mcp.run()


if __name__ == "__main__":
    main()
